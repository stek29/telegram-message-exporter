"""Postbox parsing helpers for Telegram Desktop databases."""

from __future__ import annotations

import enum
import io
import struct
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

from hashing import murmur_hash
from models import Message


class ByteReader:
    """Binary reader with configurable endianness."""

    def __init__(self, buffer: io.BytesIO, endian: str = "<") -> None:
        self.endian = endian
        self.buf = buffer

    def read_fmt(self, fmt: str) -> int:
        """Read and unpack a value for the given format."""
        fmt = self.endian + fmt
        data = self.buf.read(struct.calcsize(fmt))
        return struct.unpack(fmt, data)[0]

    def read_int8(self) -> int:
        """Read a signed 8-bit integer."""
        return self.read_fmt("b")

    def read_uint8(self) -> int:
        """Read an unsigned 8-bit integer."""
        return self.read_fmt("B")

    def read_int32(self) -> int:
        """Read a signed 32-bit integer."""
        return self.read_fmt("i")

    def read_uint32(self) -> int:
        """Read an unsigned 32-bit integer."""
        return self.read_fmt("I")

    def read_int64(self) -> int:
        """Read a signed 64-bit integer."""
        return self.read_fmt("q")

    def read_uint64(self) -> int:
        """Read an unsigned 64-bit integer."""
        return self.read_fmt("Q")

    def read_bytes(self) -> bytes:
        """Read a byte sequence prefixed by its length."""
        length = self.read_int32()
        return self.buf.read(length)

    def read_str(self) -> str:
        """Read a UTF-8 string prefixed by length."""
        return self.read_bytes().decode("utf-8")

    def read_short_bytes(self) -> bytes:
        """Read a byte sequence prefixed by uint8 length."""
        length = self.read_uint8()
        return self.buf.read(length)

    def read_short_str(self) -> str:
        """Read a short UTF-8 string prefixed by uint8 length."""
        return self.read_short_bytes().decode("utf-8")

    def read_double(self) -> float:
        """Read a double-precision float."""
        return self.read_fmt("d")


class MessageDataFlags(enum.IntFlag):
    """Bit flags describing message metadata fields."""

    GLOBALLY_UNIQUE_ID = 1 << 0
    GLOBAL_TAGS = 1 << 1
    GROUPING_KEY = 1 << 2
    GROUP_INFO = 1 << 3
    LOCAL_TAGS = 1 << 4
    THREAD_ID = 1 << 5


class FwdInfoFlags(enum.IntFlag):
    """Bit flags describing forward info fields."""

    SOURCE_ID = 1 << 1
    SOURCE_MESSAGE = 1 << 2
    SIGNATURE = 1 << 3
    PSA_TYPE = 1 << 4
    FLAGS = 1 << 5


class MessageFlags(enum.IntFlag):
    """Bit flags describing message direction and state."""

    UNSENT = 1
    FAILED = 2
    INCOMING = 4
    TOP_INDEXABLE = 16
    SENDING = 32
    CAN_BE_GROUPED_INTO_FEED = 64
    WAS_SCHEDULED = 128
    COUNTED_AS_INCOMING = 256


class MessageTags(enum.IntFlag):
    """Message tag categories."""

    PHOTO_OR_VIDEO = 1 << 0
    FILE = 1 << 1
    MUSIC = 1 << 2
    WEB_PAGE = 1 << 3
    VOICE_OR_INSTANT_VIDEO = 1 << 4
    UNSEEN_PERSONAL_MESSAGE = 1 << 5
    LIVE_LOCATION = 1 << 6
    GIF = 1 << 7
    PHOTO = 1 << 8
    VIDEO = 1 << 9
    PINNED = 1 << 10


@dataclass(frozen=True)
class MessageIndex:
    """Index for a Telegram Postbox message entry."""

    peer_id: int
    namespace: int
    message_id: int
    timestamp: int

    @classmethod
    def from_bytes(cls, payload: bytes) -> "MessageIndex":
        """Deserialize message index bytes into a structured index."""
        reader = ByteReader(io.BytesIO(payload), endian=">")
        peer_id = reader.read_int64()
        namespace = reader.read_int32()
        timestamp = reader.read_int32()
        message_id = reader.read_int32()
        return cls(peer_id, namespace, message_id, timestamp)

    def as_bytes(self) -> bytes:
        """Serialize the message index back to bytes."""
        return struct.pack(
            ">qiii", self.peer_id, self.namespace, self.timestamp, self.message_id
        )


class PostboxDecoder:
    """Decoder for Postbox key/value payloads."""

    registry: dict[int, type] = {}

    @classmethod
    def register_decoder(cls, target: type) -> type:
        """Register a type hash decoder for Postbox objects."""
        cls.registry[murmur_hash(target.__name__.encode("utf-8"))] = target
        return target

    class ValueType(enum.Enum):
        """Postbox value encoding types."""

        INT32 = 0
        INT64 = 1
        BOOL = 2
        DOUBLE = 3
        STRING = 4
        OBJECT = 5
        INT32_ARRAY = 6
        INT64_ARRAY = 7
        OBJECT_ARRAY = 8
        OBJECT_DICTIONARY = 9
        BYTES = 10
        NIL = 11
        STRING_ARRAY = 12
        BYTES_ARRAY = 13

    def __init__(self, data: bytes) -> None:
        self.reader = ByteReader(io.BytesIO(data), endian="<")
        self.size = len(data)

    def decode_root_object(self) -> Optional[Any]:
        """Decode the root object for the payload."""
        _, value = self.get(self.ValueType.OBJECT, "_")
        return value

    def get(
        self, value_type: Optional["PostboxDecoder.ValueType"], key: str
    ) -> tuple[Any, Any]:
        """Fetch a typed value by key."""
        for entry_key, entry_type, entry_value in self.iter_kv():
            if entry_key != key:
                continue
            if value_type is None:
                return entry_type, entry_value
            if entry_type == value_type:
                return entry_type, entry_value
            if entry_type == self.ValueType.NIL:
                return entry_type, None
        return None, None

    def iter_kv(self) -> Iterable[tuple[str, "PostboxDecoder.ValueType", Any]]:
        """Iterate over key/value records in this payload."""
        self.reader.buf.seek(0, io.SEEK_SET)
        while self.reader.buf.tell() < self.size:
            key = self.reader.read_short_str()
            value_type, value = self.read_value()
            yield key, value_type, value

    def read_value(self) -> tuple["PostboxDecoder.ValueType", Any]:
        """Read a single value from the reader."""
        value_type = self.ValueType(self.reader.read_uint8())
        handler = {
            self.ValueType.INT32: self.reader.read_int32,
            self.ValueType.INT64: self.reader.read_int64,
            self.ValueType.BOOL: lambda: self.reader.read_uint8() != 0,
            self.ValueType.DOUBLE: self.reader.read_double,
            self.ValueType.STRING: self.reader.read_str,
            self.ValueType.OBJECT: self._read_object,
            self.ValueType.INT32_ARRAY: lambda: self._read_array(
                self.reader.read_int32
            ),
            self.ValueType.INT64_ARRAY: lambda: self._read_array(
                self.reader.read_int64
            ),
            self.ValueType.OBJECT_ARRAY: lambda: self._read_array(self._read_object),
            self.ValueType.OBJECT_DICTIONARY: self._read_object_dict,
            self.ValueType.BYTES: self.reader.read_bytes,
            self.ValueType.NIL: lambda: None,
            self.ValueType.STRING_ARRAY: lambda: self._read_array(self.reader.read_str),
            self.ValueType.BYTES_ARRAY: lambda: self._read_array(
                self.reader.read_bytes
            ),
        }[value_type]
        return value_type, handler()

    def _read_array(self, reader_fn):
        length = self.reader.read_int32()
        return [reader_fn() for _ in range(length)]

    def _read_object(self) -> Any:
        type_hash = self.reader.read_int32()
        data_len = self.reader.read_int32()
        data = self.reader.buf.read(data_len)
        if type_hash in self.registry:
            decoder = self.__class__(data)
            return self.registry[type_hash](decoder)
        decoder = self.__class__(data)
        payload = {key: val for key, _, val in decoder.iter_kv()}
        payload["@type"] = type_hash
        return payload

    def _read_object_dict(self) -> list[tuple[Any, Any]]:
        length = self.reader.read_int32()
        return [(self._read_object(), self._read_object()) for _ in range(length)]


class TelegramMediaAction:
    """Simple placeholder for action media types."""

    class Type(enum.Enum):
        """Known action types."""

        UNKNOWN = 0

    def __init__(self, decoder: PostboxDecoder) -> None:
        raw = {key: val for key, _, val in decoder.iter_kv()}
        raw_type = raw.get("_rawValue", 0)
        self.type = self.Type(raw_type)
        raw.pop("_rawValue", None)
        self.payload = raw

    def __repr__(self) -> str:
        return f"{self.type} {self.payload}"

    def as_dict(self) -> dict[str, Any]:
        """Return a serializable representation of the action."""
        return {"type": self.type.name, "payload": self.payload}


def read_intermediate_fwd_info(reader: ByteReader) -> Optional[dict[str, Any]]:
    """Decode forward info section from a message payload."""
    info_flags = FwdInfoFlags(reader.read_int8())
    if info_flags == 0:
        return None

    author_id = reader.read_int64()
    date_value = reader.read_int32()

    source_id = reader.read_int64() if FwdInfoFlags.SOURCE_ID in info_flags else None

    src_peer = None
    src_namespace = None
    src_message_id = None
    if FwdInfoFlags.SOURCE_MESSAGE in info_flags:
        src_peer = reader.read_int64()
        src_namespace = reader.read_int32()
        src_message_id = reader.read_int32()

    signature = reader.read_str() if FwdInfoFlags.SIGNATURE in info_flags else None
    psa_type = reader.read_str() if FwdInfoFlags.PSA_TYPE in info_flags else None
    flags = reader.read_int32() if FwdInfoFlags.FLAGS in info_flags else None

    return {
        "author": author_id,
        "date": date_value,
        "src_id": source_id,
        "src_msg_peer": src_peer,
        "src_msg_ns": src_namespace,
        "src_msg_id": src_message_id,
        "signature": signature,
        "psa_type": psa_type,
        "flags": flags,
    }


def read_intermediate_message(payload: bytes) -> Optional[dict[str, Any]]:
    """Decode a Postbox message payload to a structured dict."""
    reader = ByteReader(io.BytesIO(payload))
    message_type = reader.read_int8()
    if message_type != 0:
        return None

    reader.read_uint32()  # stableId
    reader.read_uint32()  # stableVer

    data_flags = MessageDataFlags(reader.read_uint8())
    if MessageDataFlags.GLOBALLY_UNIQUE_ID in data_flags:
        reader.read_int64()
    if MessageDataFlags.GLOBAL_TAGS in data_flags:
        reader.read_uint32()
    if MessageDataFlags.GROUPING_KEY in data_flags:
        reader.read_int64()
    if MessageDataFlags.GROUP_INFO in data_flags:
        reader.read_uint32()
    if MessageDataFlags.LOCAL_TAGS in data_flags:
        reader.read_uint32()
    if MessageDataFlags.THREAD_ID in data_flags:
        reader.read_int64()

    flags = MessageFlags(reader.read_uint32())
    tags = MessageTags(reader.read_uint32())

    fwd_info = read_intermediate_fwd_info(reader)

    author_id = None
    if reader.read_int8() == 1:
        author_id = reader.read_int64()

    text = reader.read_str()

    attributes_count = reader.read_int32()
    for _ in range(attributes_count):
        _ = reader.read_bytes()

    embedded_media_count = reader.read_int32()
    for _ in range(embedded_media_count):
        _ = reader.read_bytes()

    referenced_media_ids = []
    for _ in range(reader.read_int32()):
        namespace = reader.read_int32()
        message_id = reader.read_int64()
        referenced_media_ids.append((namespace, message_id))

    return {
        "flags": flags,
        "tags": tags,
        "author_id": author_id,
        "fwd": fwd_info,
        "text": text,
        "referenced_media_ids": referenced_media_ids,
    }


def iter_postbox_messages(
    rows: Iterable[tuple[bytes, bytes]],
    peer_id: Optional[int] = None,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    limit: Optional[int] = None,
) -> list[Message]:
    """Iterate Postbox message rows and build normalized messages."""
    messages: list[Message] = []
    for key, value in rows:
        if not isinstance(key, (bytes, bytearray)) or not isinstance(
            value, (bytes, bytearray)
        ):
            continue
        idx = MessageIndex.from_bytes(key)
        if peer_id is not None and idx.peer_id != peer_id:
            continue
        if start_ts is not None and idx.timestamp < start_ts:
            continue
        if end_ts is not None and idx.timestamp > end_ts:
            continue

        msg = read_intermediate_message(value)
        if not msg:
            continue
        text = msg.get("text") or ""
        if not text:
            continue

        incoming = MessageFlags.INCOMING in msg["flags"]
        timestamp = datetime.fromtimestamp(idx.timestamp) if idx.timestamp else None
        messages.append(
            Message(
                timestamp=timestamp,
                text=text,
                outgoing=None if incoming is None else not incoming,
                peer_id=idx.peer_id,
                author_id=msg.get("author_id"),
            )
        )
        if limit and len(messages) >= limit:
            break
    return messages


def parse_peer_key(raw_key: Any) -> Optional[int]:
    """Parse peer key into an integer peer id."""
    if isinstance(raw_key, int):
        return raw_key
    if isinstance(raw_key, (bytes, bytearray)) and len(raw_key) == 8:
        return struct.unpack(">q", raw_key)[0]
    return None


def peer_display(peer: Any) -> Optional[str]:
    """Build a display name from a peer payload."""
    if not isinstance(peer, dict):
        return None
    if "fn" in peer or "ln" in peer:
        return f"{peer.get('fn', '')} {peer.get('ln', '')}".strip()
    if "t" in peer:
        return str(peer.get("t"))
    if "un" in peer:
        return f"@{peer.get('un')}"
    return None


def list_peers_postbox(
    rows: Iterable[tuple[bytes, bytes]],
    term: Optional[str],
) -> list[tuple[str, int, str]]:
    """List peer ids and names from Postbox t2 rows."""
    results: list[tuple[str, int, str]] = []
    for key, value in rows:
        peer_id = parse_peer_key(key)
        if peer_id is None or not isinstance(value, (bytes, bytearray)):
            continue
        try:
            data = PostboxDecoder(value).decode_root_object()
        except (ValueError, TypeError):
            continue
        display = peer_display(data)
        if not display:
            continue
        if term and term.lower() not in display.lower():
            continue
        results.append(("t2", peer_id, display))
    return results


def load_peer_map(rows: Iterable[tuple[bytes, bytes]]) -> dict[int, str]:
    """Load peer mapping from Postbox t2 rows."""
    peer_map: dict[int, str] = {}
    for key, value in rows:
        peer_id = parse_peer_key(key)
        if peer_id is None or not isinstance(value, (bytes, bytearray)):
            continue
        try:
            data = PostboxDecoder(value).decode_root_object()
        except (ValueError, TypeError):
            continue
        display = peer_display(data)
        if display:
            peer_map[peer_id] = display
    return peer_map
