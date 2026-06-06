"""Postbox parsing helpers for Telegram Desktop databases."""

from __future__ import annotations

import enum
import io
import json
import logging
import struct
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .hashing import murmur_hash, persistent_hash32
from .models import Attachment, ForwardInfo, Message, PeerInfo, PeerKind, ReplyInfo
from .schema import (
    POSTBOX_FIELD_ALIASES,
    POSTBOX_MEDIA_HELPER_TYPES,
    POSTBOX_MEDIA_TYPES,
    POSTBOX_MESSAGE_ATTRIBUTE_TYPES,
    PostboxMetadataKey,
    PostboxTable,
    TelegramMediaActionType,
)
from .db import iter_postbox_message_rows_for_peer

logger = logging.getLogger(__name__)


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


class MessageForwardFlags(enum.IntFlag):
    """Flags stored inside decoded forward information."""

    IS_IMPORTED = 1 << 0


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
    COPY_PROTECTED = 512
    IS_FORUM_TOPIC = 1024
    REACTIONS_ARE_POSSIBLE = 2048


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
    UNSEEN_REACTION = 1 << 11
    VOICE = 1 << 12
    ROUND_VIDEO = 1 << 13
    POLLS = 1 << 14
    UNSEEN_POLL_VOTE = 1 << 15


class GlobalMessageTags(enum.IntFlag):
    """Global message tag categories."""

    CALLS = 1 << 0
    MISSED_CALLS = 1 << 1


class LocalMessageTags(enum.IntFlag):
    """Device-local message tag categories."""

    OUTGOING_LIVE_LOCATION = 1 << 0
    OUTGOING_DELIVERED_TO_SERVER = 1 << 1


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


class MediaEntryType(enum.IntEnum):
    """Storage mode used by entries in the Postbox message-media table."""

    DIRECT = 0
    MESSAGE_REFERENCE = 1


@dataclass(frozen=True)
class MediaEntry:
    """Decoded value from the Postbox message-media table."""

    entry_type: MediaEntryType
    media: Optional[Any] = None
    message_index: Optional[MessageIndex] = None
    reference_count: Optional[int] = None


class PostboxDecoder:
    """Decoder for Postbox key/value payloads."""

    registry: dict[int, type] = {}

    @classmethod
    def register_decoder(cls, target: type) -> type:
        """Register a type hash decoder for Postbox objects."""
        cls.registry[murmur_hash(target.__name__.encode("utf-8"))] = target
        return target

    @classmethod
    def register_named_decoder(cls, type_name: str, target: type) -> None:
        """Register a decoder under an explicit Swift type name."""
        cls.registry[murmur_hash(type_name.encode("utf-8"))] = target

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


@dataclass(frozen=True)
class PostboxObject:
    """Named Postbox object whose fields are retained without interpretation."""

    type_name: str
    payload: dict[str, Any]

    @property
    def fields(self) -> dict[str, Any]:
        """Return payload fields with current Telegram source names."""
        aliases = POSTBOX_FIELD_ALIASES.get(self.type_name, {})
        fields = {aliases.get(key, key): value for key, value in self.payload.items()}
        for field_name in ("file_id", "image_id", "webpage_id"):
            value = fields.get(field_name)
            if isinstance(value, bytes) and len(value) == 12:
                fields[field_name] = struct.unpack("<iq", value)
        return fields

    def as_dict(self) -> dict[str, Any]:
        """Return a serializable representation."""
        return {
            "type": self.type_name,
            "payload": self.payload,
            "fields": self.fields,
        }


def _named_object_decoder(type_name: str):
    def decode(decoder: PostboxDecoder) -> PostboxObject:
        payload = {key: val for key, _, val in decoder.iter_kv()}
        return PostboxObject(type_name=type_name, payload=payload)

    return decode


def _decode_peer_ids_from_buffer(value: Any) -> list[int]:
    """Decode a PeerId-encoded bytes blob into a list of peer ids.

    The format is a fixed 8-byte little-endian int64 per peer id, with
    no length prefix (length is ``len(value) // 8``). Used by
    ``TelegramMediaAction.addedMembers`` / ``.removedMembers`` and the
    ``requestedPeer`` action. Returns ``[]`` for non-bytes input or
    input whose length is not a multiple of 8.
    """
    if not isinstance(value, (bytes, bytearray)):
        return []
    raw = bytes(value)
    if len(raw) % 8 != 0:
        return []
    count = len(raw) // 8
    return list(struct.unpack(f"<{count}q", raw))


@PostboxDecoder.register_decoder
class TelegramMediaAction:
    """Decoded Telegram service-message action."""

    Type = TelegramMediaActionType

    def __init__(self, decoder: PostboxDecoder) -> None:
        raw = {key: val for key, _, val in decoder.iter_kv()}
        raw_type = raw.get("_rawValue", 0)
        self.type = self.Type(raw_type)
        raw.pop("_rawValue", None)
        self.payload = raw
        aliases = POSTBOX_FIELD_ALIASES.get("TelegramMediaAction", {})
        for raw_key, value in list(raw.items()):
            alias = aliases.get(raw_key)
            if alias and alias != raw_key and alias not in self.payload:
                self.payload[alias] = value

    def __repr__(self) -> str:
        return f"{self.type} {self.payload}"

    def as_dict(self) -> dict[str, Any]:
        """Return a serializable representation of the action."""
        return {
            "type": self.type.name,
            "raw_type": int(self.type),
            "payload": self.payload,
        }


for _type_name in (
    *POSTBOX_MEDIA_TYPES,
    *POSTBOX_MEDIA_HELPER_TYPES,
    *POSTBOX_MESSAGE_ATTRIBUTE_TYPES,
):
    if _type_name != TelegramMediaAction.__name__:
        PostboxDecoder.register_named_decoder(
            _type_name, _named_object_decoder(_type_name)
        )


def read_media_entry(payload: bytes) -> MediaEntry:
    """Decode a value from Postbox table t6 (message media)."""
    reader = ByteReader(io.BytesIO(payload))
    entry_type = MediaEntryType(reader.read_int8())
    if entry_type == MediaEntryType.DIRECT:
        media = PostboxDecoder(reader.read_bytes()).decode_root_object()
        reference_count = reader.read_int32()
        return MediaEntry(
            entry_type=entry_type,
            media=media,
            reference_count=reference_count,
        )

    peer_id = reader.read_int64()
    namespace = reader.read_int32()
    message_id = reader.read_int32()
    timestamp = reader.read_int32()
    return MediaEntry(
        entry_type=entry_type,
        message_index=MessageIndex(peer_id, namespace, message_id, timestamp),
    )


def _media_id(media: Any) -> Optional[tuple[int, int]]:
    if not isinstance(media, PostboxObject):
        return None
    for field_name in ("file_id", "image_id", "webpage_id"):
        value = media.fields.get(field_name)
        if (
            isinstance(value, tuple)
            and len(value) == 2
            and all(isinstance(part, int) for part in value)
        ):
            return value
    return None


def _resource_cache_key(resource: Any) -> Optional[str]:
    if not isinstance(resource, PostboxObject):
        return None
    fields = resource.fields
    if resource.type_name == "CloudDocumentMediaResource":
        if fields.get("datacenter_id") is None or fields.get("file_id") is None:
            return None
        return (
            f"telegram-cloud-document-{fields.get('datacenter_id')}-"
            f"{fields.get('file_id')}"
        )
    if resource.type_name == "CloudPhotoSizeMediaResource":
        if (
            fields.get("datacenter_id") is None
            or fields.get("photo_id") is None
            or fields.get("size_spec") is None
        ):
            return None
        return (
            f"telegram-cloud-photo-size-{fields.get('datacenter_id')}-"
            f"{fields.get('photo_id')}-{fields.get('size_spec')}"
        )
    if resource.type_name == "CloudDocumentSizeMediaResource":
        if (
            fields.get("datacenter_id") is None
            or fields.get("document_id") is None
            or fields.get("size_spec") is None
        ):
            return None
        return (
            f"telegram-cloud-document-size-{fields.get('datacenter_id')}-"
            f"{fields.get('document_id')}-{fields.get('size_spec')}"
        )
    if resource.type_name == "CloudFileMediaResource":
        if any(
            fields.get(field_name) is None
            for field_name in ("datacenter_id", "volume_id", "local_id", "secret")
        ):
            return None
        return (
            f"telegram-cloud-file-{fields.get('datacenter_id')}-"
            f"{fields.get('volume_id')}-{fields.get('local_id')}-"
            f"{fields.get('secret')}"
        )
    if resource.type_name == "CloudPeerPhotoSizeMediaResource":
        datacenter_id = fields.get("datacenter_id")
        size_spec = fields.get("size_spec")
        if datacenter_id is None or size_spec is None:
            return None
        suffix = (
            f"{size_spec}-{fields.get('volume_id') or 0}-{fields.get('local_id') or 0}"
        )
        photo_id = fields.get("photo_id")
        if photo_id is not None:
            return f"telegram-peer-photo-size-{datacenter_id}-{photo_id}-{suffix}"
        return f"telegram-peer-photo-size-{datacenter_id}-{suffix}"
    if resource.type_name == "CloudStickerPackThumbnailMediaResource":
        datacenter_id = fields.get("datacenter_id")
        if datacenter_id is None:
            return None
        suffix = f"{fields.get('volume_id') or 0}-{fields.get('local_id') or 0}"
        thumb_version = fields.get("thumb_version")
        if thumb_version is not None:
            return (
                f"telegram-stickerpackthumbnail-{datacenter_id}-"
                f"{thumb_version}-{suffix}"
            )
        return f"telegram-stickerpackthumbnail-{datacenter_id}-{suffix}"
    if resource.type_name == "LocalFileMediaResource":
        file_id = fields.get("file_id")
        return f"telegram-local-file-{file_id}" if file_id is not None else None
    if resource.type_name == "LocalFileReferenceMediaResource":
        random_id = fields.get("random_id")
        return f"local-file-{random_id}" if random_id is not None else None
    if resource.type_name == "HttpReferenceMediaResource":
        url = fields.get("url")
        return f"http-{persistent_hash32(url)}" if isinstance(url, str) else None
    if resource.type_name == "WebFileReferenceMediaResource":
        url = fields.get("url")
        size = fields.get("size")
        if size is None:
            size = fields.get("legacy_size")
        access_hash = fields.get("access_hash")
        if not isinstance(url, str) or size is None or access_hash is None:
            return None
        return f"proxy-{persistent_hash32(url)}-{size}-{access_hash}"
    if resource.type_name == "SecretFileMediaResource":
        file_id = fields.get("file_id")
        datacenter_id = fields.get("datacenter_id")
        if file_id is None or datacenter_id is None:
            return None
        return f"secret-file-{file_id}-{datacenter_id}"
    if resource.type_name == "SecureFileMediaResource":
        file_id = fields.get("file_id")
        return f"telegram-secure-file-{file_id}" if file_id is not None else None
    if resource.type_name == "WallpaperDataResource":
        slug = fields.get("slug")
        return f"wallpaper-{slug}" if isinstance(slug, str) else None
    return None


def peer_photo_cache_key(representations: Any) -> Optional[str]:
    """Return the small (80x80) peer photo cache key, or ``None``.

    Telegram stores a peer's profile photo on the ``t2`` (PEER) record as
    ``ph: [TelegramMediaImageRepresentation]``; each entry has a resource of
    type ``CloudPeerPhotoSizeMediaResource`` whose ``size_spec`` is ``0`` for
    the small 80x80 variant and ``1`` for the full-size 640x640 variant. We
    prefer the small variant for chat avatars.
    """
    if not isinstance(representations, (list, tuple)):
        return None
    for entry in representations:
        if not isinstance(entry, PostboxObject):
            continue
        resource = entry.fields.get("resource")
        if not isinstance(resource, PostboxObject):
            continue
        if resource.fields.get("size_spec") == 0:
            return _resource_cache_key(resource)
    return None


TELEGRAM_MEDIA_VIDEO_FLAG_INSTANT_ROUND_VIDEO = 1


def _file_attribute_data(
    attributes: Any,
) -> tuple[
    Optional[str],
    bool,
    bool,
    bool,
    Optional[int],
    Optional[int],
    Optional[str],
]:
    filename = None
    is_voice = False
    is_sticker = False
    is_round_video = False
    width = None
    height = None
    sticker_emoji = None
    if not isinstance(attributes, list):
        return (
            filename,
            is_voice,
            is_sticker,
            is_round_video,
            width,
            height,
            sticker_emoji,
        )
    for attribute in attributes:
        if not isinstance(attribute, PostboxObject):
            continue
        fields = attribute.payload
        attribute_type = fields.get("t")
        if attribute_type == 0 and isinstance(fields.get("fn"), str):
            filename = fields["fn"]
        elif attribute_type in (1, 10):
            is_sticker = True
            alt_text = fields.get("dt")
            if isinstance(alt_text, str) and alt_text:
                sticker_emoji = alt_text
        elif attribute_type == 4:
            width = fields.get("w")
            height = fields.get("h")
            flags = fields.get("f")
            if (
                isinstance(flags, int)
                and flags & TELEGRAM_MEDIA_VIDEO_FLAG_INSTANT_ROUND_VIDEO
            ):
                is_round_video = True
        elif attribute_type == 5:
            is_voice = bool(fields.get("iv"))
    return (
        filename,
        is_voice,
        is_sticker,
        is_round_video,
        width,
        height,
        sticker_emoji,
    )


@dataclass(frozen=True)
class _FileResourceBuckets:
    """Categorized resources extracted from a ``TelegramMediaFile``.

    The iOS Postbox layout groups resources by the role they play in
    rendering. The main file lives under ``resource`` (and may have
    transcoded alternatives under ``alternative_representations``).
    Image previews and video thumbnails live under their own keys
    (``preview_representations``, ``video_thumbnails``,
    ``video_cover``) and are typically the only thing locally cached
    for files the user has never opened.

    Preview entries carry the wrapping object's ``width``/``height`` so
    callers can size the rendered preview without re-walking the source
    payload.
    """

    main: tuple[PostboxObject, ...] = ()
    main_alternates: tuple[PostboxObject, ...] = ()
    preview_images: tuple[tuple[PostboxObject, Optional[int], Optional[int]], ...] = ()
    preview_videos: tuple[tuple[PostboxObject, Optional[int], Optional[int]], ...] = ()
    source_path: Optional[str] = None


def _bucket_resource(
    buckets: list[PostboxObject],
    seen: set[str],
    resource: Any,
) -> None:
    if not isinstance(resource, PostboxObject):
        return
    key = _resource_cache_key(resource)
    if not key or key in seen:
        return
    seen.add(key)
    buckets.append(resource)


def _bucket_previews(
    buckets: list[tuple[PostboxObject, Optional[int], Optional[int]]],
    seen: set[str],
    items: Any,
) -> None:
    """Pick up ``TelegramMediaImageRepresentation`` wrappers for previews.

    The resource inside a preview representation is typically a
    ``CloudPhotoSizeMediaResource`` (for cloud-cached photo sizes), but
    Telegram also uses ``CloudDocumentSizeMediaResource`` for medium
    thumbnails of non-photo documents (e.g. a PNG sent as a document).
    Both kinds are valid image previews — the resource type doesn't
    determine the semantic role, the field it was pulled from does.
    """
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, PostboxObject):
            continue
        resource = item.fields.get("resource")
        if not isinstance(resource, PostboxObject):
            continue
        if resource.type_name not in (
            "CloudPhotoSizeMediaResource",
            "CloudDocumentSizeMediaResource",
        ):
            continue
        key = _resource_cache_key(resource)
        if not key or key in seen:
            continue
        seen.add(key)
        width = item.fields.get("width")
        height = item.fields.get("height")
        buckets.append((resource, width, height))


def _bucket_video_thumbnails(
    buckets: list[tuple[PostboxObject, Optional[int], Optional[int]]],
    seen: set[str],
    items: Any,
) -> None:
    """Pick up ``VideoThumbnail`` wrappers for video thumbnail previews."""
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, PostboxObject):
            continue
        resource = item.fields.get("resource")
        if not isinstance(resource, PostboxObject):
            continue
        if resource.type_name != "CloudDocumentSizeMediaResource":
            continue
        key = _resource_cache_key(resource)
        if not key or key in seen:
            continue
        seen.add(key)
        width = item.fields.get("width")
        height = item.fields.get("height")
        buckets.append((resource, width, height))


def _file_resource_buckets(fields: dict[str, Any]) -> _FileResourceBuckets:
    """Split a ``TelegramMediaFile.fields`` payload into resource buckets."""
    main: list[PostboxObject] = []
    main_alternates: list[PostboxObject] = []
    preview_images: list[tuple[PostboxObject, Optional[int], Optional[int]]] = []
    preview_videos: list[tuple[PostboxObject, Optional[int], Optional[int]]] = []
    seen_main: set[str] = set()
    seen_alternate: set[str] = set()
    seen_preview_image: set[str] = set()
    seen_preview_video: set[str] = set()
    source_path: Optional[str] = None

    def remember_source(resource: Any) -> None:
        nonlocal source_path
        if (
            source_path is None
            and isinstance(resource, PostboxObject)
            and isinstance(resource.fields.get("local_file_path"), str)
        ):
            source_path = resource.fields["local_file_path"]

    primary = fields.get("resource")
    if isinstance(primary, PostboxObject):
        _bucket_resource(main, seen_main, primary)
        remember_source(primary)

    for alternative in fields.get("alternative_representations") or []:
        if not isinstance(alternative, PostboxObject):
            continue
        alt_resource = alternative.fields.get("resource")
        if isinstance(alt_resource, PostboxObject):
            _bucket_resource(main_alternates, seen_alternate, alt_resource)
            remember_source(alt_resource)
        _bucket_previews(
            preview_images,
            seen_preview_image,
            alternative.fields.get("preview_representations"),
        )
        _bucket_video_thumbnails(
            preview_videos,
            seen_preview_video,
            alternative.fields.get("video_thumbnails"),
        )

    _bucket_previews(
        preview_images, seen_preview_image, fields.get("preview_representations")
    )
    _bucket_video_thumbnails(
        preview_videos, seen_preview_video, fields.get("video_thumbnails")
    )
    video_cover = fields.get("video_cover")
    if isinstance(video_cover, PostboxObject):
        _bucket_previews(
            preview_images,
            seen_preview_image,
            video_cover.fields.get("representations"),
        )
        _bucket_video_thumbnails(
            preview_videos,
            seen_preview_video,
            video_cover.fields.get("video_representations"),
        )

    return _FileResourceBuckets(
        main=tuple(main),
        main_alternates=tuple(main_alternates),
        preview_images=tuple(preview_images),
        preview_videos=tuple(preview_videos),
        source_path=source_path,
    )


def _file_resource_keys(
    buckets: _FileResourceBuckets,
) -> tuple[Optional[str], tuple[str, ...], Optional[str]]:
    """Return ``(main_key, alternate_keys, source_path)`` for a file payload."""
    main_keys: list[str] = []
    for resource in (*buckets.main, *buckets.main_alternates):
        key = _resource_cache_key(resource)
        if key and key not in main_keys:
            main_keys.append(key)
    if not main_keys:
        return None, (), buckets.source_path
    return main_keys[0], tuple(main_keys[1:]), buckets.source_path


def _pick_best_preview_image(
    entries: tuple[tuple[PostboxObject, Optional[int], Optional[int]], ...],
) -> Optional[Attachment]:
    """Pick the largest image preview (by area) and return it as an Attachment."""
    best_key: Optional[str] = None
    best_area = -1
    best_width: Optional[int] = None
    best_height: Optional[int] = None
    for resource, width, height in entries:
        key = _resource_cache_key(resource)
        if not key:
            continue
        area = 0
        if (
            isinstance(width, int)
            and not isinstance(width, bool)
            and isinstance(height, int)
            and not isinstance(height, bool)
        ):
            area = width * height
        if area > best_area or (area == best_area and best_key is None):
            best_key = key
            best_area = area
            best_width = width
            best_height = height
    if best_key is None:
        return None
    return Attachment(
        kind="image",
        mime_type="image/jpeg",
        cache_key=best_key,
        alternate_cache_keys=(),
        width=best_width,
        height=best_height,
    )


def _pick_best_preview_video(
    entries: tuple[tuple[PostboxObject, Optional[int], Optional[int]], ...],
) -> Optional[Attachment]:
    """Pick the best video thumbnail preview and return it as an Attachment.

    Prefers the entry whose resource's ``size_spec`` is ``"f"`` (full
    size) when present, else the first available. ``VideoThumbnail``
    files are real videos (mp4/webm), so the kind is ``"video"`` and
    the mime type matches what iOS stores for round-video previews.
    """
    fallback_key: Optional[str] = None
    fallback_width: Optional[int] = None
    fallback_height: Optional[int] = None
    full_key: Optional[str] = None
    full_width: Optional[int] = None
    full_height: Optional[int] = None
    for resource, width, height in entries:
        key = _resource_cache_key(resource)
        if not key:
            continue
        if resource.fields.get("size_spec") == "f":
            full_key = key
            full_width = width
            full_height = height
            break
        if fallback_key is None:
            fallback_key = key
            fallback_width = width
            fallback_height = height
    chosen_key = full_key or fallback_key
    if chosen_key is None:
        return None
    chosen_width = full_width if full_key else fallback_width
    chosen_height = full_height if full_key else fallback_height
    return Attachment(
        kind="video",
        mime_type="video/mp4",
        cache_key=chosen_key,
        alternate_cache_keys=(),
        width=chosen_width,
        height=chosen_height,
    )


def _webpage_attachments(media: PostboxObject) -> list[Attachment]:
    """Build attachments for a ``TelegramMediaWebpage`` media object.

    Loaded-content fields are flat-inlined into the parent payload
    (per ``TelegramMediaWebpage.encode`` in the iOS source — when
    ``content`` is ``.Loaded``, every key of
    ``TelegramMediaWebpageLoadedContent`` is written into the same
    encoder as the parent). Pending pages have only ``pendingDate`` /
    ``pendingUrl``.
    """
    payload = media.payload
    fields = media.fields

    image_obj = fields.get("image")
    file_obj = fields.get("file")
    has_image = isinstance(image_obj, PostboxObject)
    has_file = isinstance(file_obj, PostboxObject)
    title = _maybe_str(fields.get("title"))
    description = _maybe_str(fields.get("text"))

    canonical_url = _maybe_str(fields.get("url")) or _maybe_str(
        fields.get("pending_url")
    )

    if not (title or description or has_image or has_file):
        if canonical_url is None:
            return []
        return [Attachment(kind="webpage", url=canonical_url)]

    metadata: dict[str, Any] = {}
    site_name = _maybe_str(fields.get("website_name"))
    if site_name is not None:
        metadata["site_name"] = site_name
    if title is not None:
        metadata["title"] = title
    if description is not None:
        metadata["description"] = description
    author = _maybe_str(fields.get("author"))
    if author is not None:
        metadata["author"] = author
    raw_duration = fields.get("duration")
    if (
        isinstance(raw_duration, int)
        and not isinstance(raw_duration, bool)
        and raw_duration >= 0
    ):
        metadata["duration"] = int(raw_duration)
    raw_content_type = payload.get("ty")
    if isinstance(raw_content_type, str) and raw_content_type:
        metadata["content_type"] = raw_content_type
    display_url = _maybe_str(fields.get("display_url"))
    if display_url is not None:
        metadata["display_url"] = display_url
    embed_url = _maybe_str(fields.get("embed_url"))
    if embed_url is not None:
        metadata["embed_url"] = embed_url
    embed_type = _maybe_str(fields.get("embed_type"))
    if embed_type is not None:
        metadata["embed_type"] = embed_type
    raw_ew = fields.get("embed_width")
    raw_eh = fields.get("embed_height")
    if (
        isinstance(raw_ew, int)
        and not isinstance(raw_ew, bool)
        and isinstance(raw_eh, int)
        and not isinstance(raw_eh, bool)
    ):
        metadata["embed_width"] = int(raw_ew)
        metadata["embed_height"] = int(raw_eh)
    if "ipd" in payload or "ip" in payload:
        metadata["has_instant_page"] = True

    preview_image: Optional[Attachment] = None
    if has_image:
        image_attachments = media_attachments(image_obj)
        if image_attachments:
            preview_image = image_attachments[0]
    preview_video: Optional[Attachment] = None
    if has_file:
        file_attachments = media_attachments(file_obj)
        if file_attachments:
            preview_video = file_attachments[0]
    return [
        Attachment(
            kind="webpage",
            url=canonical_url,
            metadata=metadata,
            preview_image=preview_image,
            preview_video=preview_video,
        )
    ]


def _chat_theme_emoticon(blob: Any) -> Optional[str]:
    """Decode a ``setChatTheme`` chatTheme blob and return the emoticon.

    The blob is a Postbox-encoded ``ChatTheme`` whose top-level keys are
    ``type`` (Int32: 0 for emoticon, 1 for gift) and ``emoticon`` (String
    for type 0). Returns the emoticon string for type 0, or ``None`` for
    type 1 (gift) and any unresolvable / malformed blob.
    """
    if not isinstance(blob, (bytes, bytearray)) or len(blob) == 0:
        return None
    try:
        root = PostboxDecoder(bytes(blob)).decode_root_object()
    except (ValueError, TypeError, struct.error):
        return None
    payload = getattr(root, "payload", None)
    if not isinstance(payload, dict):
        return None
    raw_type = payload.get("type")
    if raw_type != 0:
        return None
    emoticon = payload.get("emoticon")
    if isinstance(emoticon, str) and emoticon:
        return emoticon
    return None


def _action_attachment(media: "TelegramMediaAction") -> Attachment:
    """Build an :class:`Attachment` for a decoded ``TelegramMediaAction``.

    The raw Postbox payload is preserved verbatim under
    ``metadata["payload"]`` and the integer discriminator is exposed as
    ``metadata["raw_type"]``. A best-effort human-readable
    ``metadata["summary"]`` is computed for action types that do not
    need peer-name resolution; actions that reference peers store a
    fallback summary here and the renderer (in ``exporters.py``)
    rebuilds the final string with resolved names. ``filename`` keeps
    the raw enum name for CSV back-compat.
    """
    raw_type = int(media.type)
    payload = dict(media.payload)
    extra: dict[str, Any] = {}
    if media.type is TelegramMediaActionType.SET_CHAT_THEME:
        emoticon = _chat_theme_emoticon(payload.get("chatTheme"))
        if emoticon is not None:
            extra["emoticon"] = emoticon
    if media.type is TelegramMediaActionType.CUSTOM_TEXT:
        entities = payload.get("ent")
        if isinstance(entities, list):
            extra["entity_count"] = len(entities)
    summary = _best_effort_action_summary(media, extra)
    metadata: dict[str, Any] = {"raw_type": raw_type, "payload": payload}
    if extra:
        metadata.update(extra)
    metadata["summary"] = summary
    return Attachment(
        kind="action",
        filename=media.type.name,
        metadata=metadata,
    )


def _best_effort_action_summary(
    media: "TelegramMediaAction", extra: dict[str, Any]
) -> str:
    """Build a peer-independent fallback summary for a service action."""
    payload = media.payload
    t = media.type
    if t is TelegramMediaActionType.GROUP_CREATED:
        title = payload.get("title")
        return (
            f"Group {title} was created"
            if isinstance(title, str)
            else "Group was created"
        )
    if t is TelegramMediaActionType.TITLE_UPDATED:
        title = payload.get("title")
        return (
            f"Title changed to {title}" if isinstance(title, str) else "Title changed"
        )
    if t is TelegramMediaActionType.PINNED_MESSAGE_UPDATED:
        return "Pinned a message"
    if t is TelegramMediaActionType.PEER_JOINED:
        return "Member joined"
    if t is TelegramMediaActionType.JOINED_BY_REQUEST:
        return "Member joined via request"
    if t is TelegramMediaActionType.JOINED_BY_LINK:
        return "Member joined via invite link"
    if t is TelegramMediaActionType.ADDED_MEMBERS:
        return "Added members"
    if t is TelegramMediaActionType.REMOVED_MEMBERS:
        return "Removed members"
    if t is TelegramMediaActionType.PHONE_CALL:
        is_video = payload.get("vc")
        kind = "Video call" if is_video else "Voice call"
        return f"📞 {kind}"
    if t is TelegramMediaActionType.MESSAGE_AUTOREMOVE_TIMEOUT_UPDATED:
        return "Auto-delete updated"
    if t is TelegramMediaActionType.CUSTOM_TEXT:
        text = payload.get("text")
        if isinstance(text, str) and text:
            snippet = text if len(text) <= 40 else text[:37] + "..."
            return snippet
        return "Custom text"
    if t is TelegramMediaActionType.SET_CHAT_THEME:
        emoticon = extra.get("emoticon")
        if isinstance(emoticon, str) and emoticon:
            return f"Theme: {emoticon}"
        return "Theme updated"
    if t is TelegramMediaActionType.GROUP_PHONE_CALL:
        return "🎙️ Voice chat"
    if t is TelegramMediaActionType.INVITE_TO_GROUP_PHONE_CALL:
        return "Invited members to a voice chat"
    if t is TelegramMediaActionType.CONFERENCE_CALL:
        return "📹 Conference call"
    return media.type.name


def media_attachments(media: Any) -> list[Attachment]:
    """Convert a decoded Postbox media object to export attachments."""
    if isinstance(media, TelegramMediaAction):
        return [_action_attachment(media)]
    if not isinstance(media, PostboxObject):
        return []
    fields = media.fields
    if media.type_name == "TelegramMediaFile":
        resource = fields.get("resource")
        resource_fields = resource.fields if isinstance(resource, PostboxObject) else {}
        (
            attr_filename,
            is_voice,
            is_sticker,
            is_round_video,
            width,
            height,
            sticker_emoji,
        ) = _file_attribute_data(fields.get("attributes"))
        filename = resource_fields.get("file_name") or attr_filename
        mime_type = fields.get("mime_type")
        if is_sticker:
            kind = "sticker"
        elif is_voice:
            kind = "voice"
        elif (
            is_round_video
            and isinstance(mime_type, str)
            and mime_type.startswith("video/")
        ):
            kind = "video_message"
        elif isinstance(mime_type, str) and mime_type.startswith("video/"):
            kind = "video"
        elif isinstance(mime_type, str) and mime_type.startswith("audio/"):
            kind = "audio"
        elif isinstance(mime_type, str) and mime_type.startswith("image/"):
            kind = "image"
        else:
            kind = "file"
        buckets = _file_resource_buckets(fields)
        cache_key, alternate_cache_keys, source_path = _file_resource_keys(buckets)
        preview_image = _pick_best_preview_image(buckets.preview_images)
        preview_video = _pick_best_preview_video(buckets.preview_videos)
        logger.debug(
            "TelegramMediaFile kind=%s mime=%s main=%s alternates=%s "
            "preview_image=%s preview_video=%s source_path=%s",
            kind,
            mime_type,
            cache_key,
            alternate_cache_keys,
            preview_image.cache_key if preview_image else None,
            preview_video.cache_key if preview_video else None,
            source_path,
        )
        logger.debug(
            "TelegramMediaFile fields=%s",
            {
                key: (
                    type(value).__name__
                    if not isinstance(value, PostboxObject)
                    else f"PostboxObject({value.type_name})"
                )
                for key, value in fields.items()
            },
        )
        for alt in fields.get("alternative_representations") or []:
            if isinstance(alt, PostboxObject):
                alt_resource = alt.fields.get("resource")
                logger.debug(
                    "  alternative_representation resource=%s key=%s",
                    type(alt_resource).__name__
                    if not isinstance(alt_resource, PostboxObject)
                    else alt_resource.type_name,
                    _resource_cache_key(alt_resource)
                    if isinstance(alt_resource, PostboxObject)
                    else None,
                )
        for vt in fields.get("video_thumbnails") or []:
            if isinstance(vt, PostboxObject):
                vt_resource = vt.fields.get("resource")
                logger.debug(
                    "  video_thumbnail resource=%s key=%s",
                    type(vt_resource).__name__
                    if not isinstance(vt_resource, PostboxObject)
                    else vt_resource.type_name,
                    _resource_cache_key(vt_resource)
                    if isinstance(vt_resource, PostboxObject)
                    else None,
                )
        for pr in fields.get("preview_representations") or []:
            if isinstance(pr, PostboxObject):
                pr_resource = pr.fields.get("resource")
                logger.debug(
                    "  preview_representation resource=%s key=%s width=%s height=%s",
                    type(pr_resource).__name__
                    if not isinstance(pr_resource, PostboxObject)
                    else pr_resource.type_name,
                    _resource_cache_key(pr_resource)
                    if isinstance(pr_resource, PostboxObject)
                    else None,
                    pr.fields.get("width"),
                    pr.fields.get("height"),
                )
        raw_size = fields.get("size")
        if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size < 0:
            size = None
        else:
            size = raw_size
        return [
            Attachment(
                kind=kind,
                filename=filename,
                mime_type=mime_type,
                cache_key=cache_key,
                alternate_cache_keys=alternate_cache_keys,
                source_path=source_path,
                width=width,
                height=height,
                size=size,
                sticker_emoji=sticker_emoji,
                preview_image=preview_image,
                preview_video=preview_video,
            )
        ]

    if media.type_name == "TelegramMediaImage":
        representations = fields.get("representations")
        candidates = [
            representation
            for representation in representations or []
            if isinstance(representation, PostboxObject)
        ]
        candidates.sort(
            key=lambda representation: (
                int(representation.fields.get("width") or 0)
                * int(representation.fields.get("height") or 0)
            ),
            reverse=True,
        )
        if not candidates:
            return [Attachment(kind="image")]
        representation = candidates[0]
        representation_fields = representation.fields
        cache_keys: list[str] = []
        alternate_dimensions: dict[str, tuple[Optional[int], Optional[int]]] = {}
        for candidate in candidates:
            key = _resource_cache_key(candidate.fields.get("resource"))
            if not key or key in cache_keys:
                continue
            cache_keys.append(key)
            width = candidate.fields.get("width")
            height = candidate.fields.get("height")
            if isinstance(width, int) and isinstance(height, int):
                alternate_dimensions[key] = (width, height)
        preview_video: Optional[Attachment] = None
        for video_representation in fields.get("video_representations") or []:
            if not isinstance(video_representation, PostboxObject):
                continue
            video_key = _resource_cache_key(video_representation.fields.get("resource"))
            if not video_key:
                continue
            if video_key not in cache_keys:
                cache_keys.append(video_key)
            if preview_video is None:
                preview_video = Attachment(
                    kind="video",
                    mime_type="video/mp4",
                    cache_key=video_key,
                    alternate_cache_keys=(),
                    width=video_representation.fields.get("width"),
                    height=video_representation.fields.get("height"),
                )
        # The iOS TelegramMediaImageRepresentation and CloudPhotoSizeMediaResource
        # do not persist a mime type in the database — the server infers it on
        # upload. Assume JPEG for photos rendered through this path.
        largest_resource = representation_fields.get("resource")
        largest_resource_fields = (
            largest_resource.fields
            if isinstance(largest_resource, PostboxObject)
            else {}
        )
        raw_resource_size = largest_resource_fields.get("size")
        if (
            isinstance(raw_resource_size, int)
            and not isinstance(raw_resource_size, bool)
            and raw_resource_size >= 0
        ):
            image_size: Optional[int] = int(raw_resource_size)
        else:
            image_size = None
        return [
            Attachment(
                kind="image",
                mime_type="image/jpeg",
                cache_key=cache_keys[0] if cache_keys else None,
                alternate_cache_keys=tuple(cache_keys[1:]),
                width=representation_fields.get("width"),
                height=representation_fields.get("height"),
                size=image_size,
                preview_video=preview_video,
                alternate_dimensions=alternate_dimensions,
            )
        ]

    if media.type_name == "TelegramMediaWebpage":
        return _webpage_attachments(media)

    if media.type_name == "TelegramMediaPoll":
        metadata = _extract_poll_metadata(fields)
        if metadata is None:
            return [Attachment(kind=media.type_name)]
        return [
            Attachment(
                kind="poll", filename=metadata.get("question"), metadata=metadata
            )
        ]

    if media.type_name == "TelegramMediaContact":
        return [_contact_attachment(fields)]
    if media.type_name == "TelegramMediaDice":
        return [_dice_attachment(fields)]
    if media.type_name == "TelegramMediaExpiredContent":
        return [_expired_content_attachment(fields)]
    if media.type_name == "TelegramMediaGame":
        return [_game_attachment(fields)]
    if media.type_name == "TelegramMediaGiveaway":
        return [_giveaway_attachment(fields)]
    if media.type_name == "TelegramMediaGiveawayResults":
        return [_giveaway_results_attachment(fields)]
    if media.type_name == "TelegramMediaInvoice":
        return [_invoice_attachment(media, fields)]
    if media.type_name == "TelegramMediaLiveStream":
        return [_live_stream_attachment(fields)]
    if media.type_name == "TelegramMediaMap":
        return [_map_attachment(fields)]
    if media.type_name == "TelegramMediaPaidContent":
        return [_paid_content_attachment(fields)]
    if media.type_name == "TelegramMediaStory":
        return [_story_attachment(fields)]
    if media.type_name == "TelegramMediaTodo":
        return [_todo_attachment(fields)]
    if media.type_name == "TelegramMediaUnsupported":
        return [_unsupported_attachment()]

    return [Attachment(kind=media.type_name)]


def _contact_attachment(fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaContact`` payload."""
    first_name = _maybe_str(fields.get("first_name")) or ""
    last_name = _maybe_str(fields.get("last_name")) or ""
    phone_number = _maybe_str(fields.get("phone_number")) or ""
    raw_peer_id = fields.get("peer_id")
    peer_id: Optional[int] = None
    if (
        isinstance(raw_peer_id, int)
        and not isinstance(raw_peer_id, bool)
        and raw_peer_id != 0
    ):
        peer_id = int(raw_peer_id)
    vcard_data = _maybe_str(fields.get("vcard"))
    display_name = " ".join(part for part in (first_name, last_name) if part).strip()
    metadata: dict[str, Any] = {
        "first_name": first_name,
        "last_name": last_name,
        "phone_number": phone_number,
        "has_vcard": vcard_data is not None,
        "peer_id": peer_id,
    }
    return Attachment(
        kind="contact",
        filename=display_name or phone_number or None,
        metadata=metadata,
        vcard_data=vcard_data,
    )


def _dice_attachment(fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaDice`` payload."""
    emoji = _maybe_str(fields.get("emoji")) or "🎲"
    raw_value = fields.get("value")
    value: Optional[int] = None
    if isinstance(raw_value, int) and not isinstance(raw_value, bool):
        value = int(raw_value)
    raw_ton = fields.get("ton_amount")
    ton_amount: Optional[int] = None
    if isinstance(raw_ton, int) and not isinstance(raw_ton, bool) and raw_ton != 0:
        ton_amount = int(raw_ton)
    metadata: dict[str, Any] = {
        "emoji": emoji,
        "value": value,
        "ton_amount": ton_amount,
    }
    return Attachment(kind="dice", filename=emoji, metadata=metadata)


def _expired_content_attachment(fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaExpiredContent`` payload.

    The ``data`` field is a Postbox Int32 enum:
    ``0``=image, ``1``=file, ``2``=voice, ``3``=video.
    """
    raw = fields.get("data")
    data_int: Optional[int] = None
    if isinstance(raw, int) and not isinstance(raw, bool):
        data_int = int(raw)
    label_map = {0: "image", 1: "file", 2: "voice", 3: "video"}
    label = label_map.get(data_int, "media") if data_int is not None else "media"
    metadata: dict[str, Any] = {"data": data_int, "label": label}
    return Attachment(
        kind="expired_content",
        filename=f"expired {label}",
        metadata=metadata,
    )


def _game_attachment(fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaGame`` payload.

    The embedded ``image`` and ``file`` are read into nested
    :class:`Attachment` objects via :func:`media_attachments` so their
    cache keys can be picked up by :func:`copy_message_media` — but
    only one ``Attachment`` is returned here (the game itself), with
    the nested media referenced through ``metadata`` for renderers
    that need to inline a thumbnail.
    """
    title = _maybe_str(fields.get("title")) or ""
    name = _maybe_str(fields.get("name")) or ""
    description = _maybe_str(fields.get("description")) or ""
    image_obj = fields.get("image")
    file_obj = fields.get("file")
    image_attachment: Optional[Attachment] = None
    if (
        isinstance(image_obj, PostboxObject)
        and image_obj.type_name == "TelegramMediaImage"
    ):
        nested = media_attachments(image_obj)
        if nested:
            image_attachment = nested[0]
    file_attachment: Optional[Attachment] = None
    if (
        isinstance(file_obj, PostboxObject)
        and file_obj.type_name == "TelegramMediaFile"
    ):
        nested = media_attachments(file_obj)
        if nested:
            file_attachment = nested[0]
    metadata: dict[str, Any] = {
        "title": title,
        "description": description,
        "name": name,
        "image": (
            {
                "cache_key": image_attachment.cache_key,
                "width": image_attachment.width,
                "height": image_attachment.height,
            }
            if image_attachment is not None
            else None
        ),
        "file": (
            {
                "cache_key": file_attachment.cache_key,
                "filename": file_attachment.filename,
            }
            if file_attachment is not None
            else None
        ),
    }
    return Attachment(
        kind="game",
        filename=title or name or None,
        metadata=metadata,
        preview_image=image_attachment,
    )


def _giveaway_attachment(fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaGiveaway`` payload."""
    raw_channel_ids = fields.get("channel_peer_ids")
    channel_peer_ids: list[int] = []
    if isinstance(raw_channel_ids, list):
        for value in raw_channel_ids:
            if isinstance(value, int) and not isinstance(value, bool):
                channel_peer_ids.append(int(value))
    raw_countries = fields.get("countries")
    countries: list[str] = []
    if isinstance(raw_countries, list):
        for value in raw_countries:
            if isinstance(value, str):
                countries.append(value)
    raw_quantity = fields.get("quantity")
    quantity: Optional[int] = None
    if isinstance(raw_quantity, int) and not isinstance(raw_quantity, bool):
        quantity = int(raw_quantity)
    raw_months = fields.get("premium_months")
    premium_months: Optional[int] = None
    if isinstance(raw_months, int) and not isinstance(raw_months, bool):
        premium_months = int(raw_months)
    raw_stars = fields.get("stars_amount")
    stars_amount: Optional[int] = None
    if isinstance(raw_stars, int) and not isinstance(raw_stars, bool):
        stars_amount = int(raw_stars)
    raw_until = fields.get("until_date")
    until_date: Optional[int] = None
    if isinstance(raw_until, int) and not isinstance(raw_until, bool):
        until_date = int(raw_until)
    prize_description = _maybe_str(fields.get("prize_description"))
    metadata: dict[str, Any] = {
        "channel_peer_ids": channel_peer_ids,
        "countries": countries,
        "quantity": quantity,
        "premium_months": premium_months,
        "stars_amount": stars_amount,
        "until_date": until_date,
        "prize_description": prize_description,
    }
    return Attachment(kind="giveaway", filename="Giveaway", metadata=metadata)


def _giveaway_results_attachment(fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaGiveawayResults`` payload."""
    raw_winners = fields.get("winners_count")
    winners_count: Optional[int] = None
    if isinstance(raw_winners, int) and not isinstance(raw_winners, bool):
        winners_count = int(raw_winners)
    raw_unclaimed = fields.get("unclaimed_count")
    unclaimed_count: Optional[int] = None
    if isinstance(raw_unclaimed, int) and not isinstance(raw_unclaimed, bool):
        unclaimed_count = int(raw_unclaimed)
    raw_additional = fields.get("additional_channels_count")
    additional_channels_count: Optional[int] = None
    if isinstance(raw_additional, int) and not isinstance(raw_additional, bool):
        additional_channels_count = int(raw_additional)
    raw_months = fields.get("premium_months")
    premium_months: Optional[int] = None
    if isinstance(raw_months, int) and not isinstance(raw_months, bool):
        premium_months = int(raw_months)
    raw_stars = fields.get("stars_amount")
    stars_amount: Optional[int] = None
    if isinstance(raw_stars, int) and not isinstance(raw_stars, bool):
        stars_amount = int(raw_stars)
    metadata: dict[str, Any] = {
        "winners_count": winners_count,
        "unclaimed_count": unclaimed_count,
        "additional_channels_count": additional_channels_count,
        "premium_months": premium_months,
        "stars_amount": stars_amount,
    }
    return Attachment(
        kind="giveaway_result", filename="Giveaway results", metadata=metadata
    )


def _invoice_attachment(media: PostboxObject, fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaInvoice`` payload.

    Extended media (``extendedMedia``) is intentionally **not** walked
    into here — its nested ``TelegramExtendedMedia`` decoder is not
    registered, and decoding it would surface a raw ``dict``. The base
    text/metadata rendering is shipped first; full extended-media
    rendering is a follow-up.
    """
    title = _maybe_str(fields.get("title")) or ""
    description = _maybe_str(fields.get("description")) or ""
    currency = _maybe_str(fields.get("currency")) or ""
    raw_total = fields.get("total_amount")
    total_amount: Optional[int] = None
    if isinstance(raw_total, int) and not isinstance(raw_total, bool):
        total_amount = int(raw_total)
    photo = fields.get("photo")
    has_photo = isinstance(photo, PostboxObject)
    photo_attachment: Optional[Attachment] = None
    if has_photo and isinstance(photo, PostboxObject):
        nested = media_attachments(photo)
        if nested:
            photo_attachment = nested[0]
    raw_receipt_id = fields.get("receipt_message_id_id")
    receipt_message_id: Optional[int] = None
    if isinstance(raw_receipt_id, int) and not isinstance(raw_receipt_id, bool):
        receipt_message_id = int(raw_receipt_id)
    metadata: dict[str, Any] = {
        "title": title,
        "description": description,
        "currency": currency,
        "total_amount": total_amount,
        "has_photo": has_photo,
        "receipt_message_id": receipt_message_id,
    }
    return Attachment(
        kind="invoice",
        filename=title or None,
        metadata=metadata,
        preview_image=photo_attachment,
    )


def _live_stream_attachment(fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaLiveStream`` payload.

    The ``call`` field is a ``GroupCallReference`` (registered as a
    named decoder) carrying opaque id+accessHash that don't render
    meaningfully without the Telegram client. The ``kind`` is the
    ``Int32`` enum ``0``=rtmp, ``1``=rtc.
    """
    call_obj = fields.get("call")
    call_id: Optional[int] = None
    if isinstance(call_obj, PostboxObject):
        cid = call_obj.fields.get("id")
        if isinstance(cid, int) and not isinstance(cid, bool):
            call_id = int(cid)
    raw_kind = fields.get("kind")
    kind_int: Optional[int] = None
    if isinstance(raw_kind, int) and not isinstance(raw_kind, bool):
        kind_int = int(raw_kind)
    metadata: dict[str, Any] = {
        "call_id": call_id,
        "kind": kind_int,
        "kind_label": "video" if kind_int == 1 else "rtmp",
    }
    return Attachment(kind="live_stream", filename="Live stream", metadata=metadata)


def _map_attachment(fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaMap`` payload."""
    raw_lat = fields.get("latitude")
    latitude: Optional[float] = None
    if isinstance(raw_lat, (int, float)) and not isinstance(raw_lat, bool):
        latitude = float(raw_lat)
    raw_lng = fields.get("longitude")
    longitude: Optional[float] = None
    if isinstance(raw_lng, (int, float)) and not isinstance(raw_lng, bool):
        longitude = float(raw_lng)
    venue_obj = fields.get("venue")
    venue: Optional[dict[str, Any]] = None
    if isinstance(venue_obj, PostboxObject) and venue_obj.type_name == "MapVenue":
        v = venue_obj.fields
        venue = {
            "title": _maybe_str(v.get("title")),
            "address": _maybe_str(v.get("address")),
            "provider": _maybe_str(v.get("provider")),
            "id": _maybe_str(v.get("id")),
            "type": _maybe_str(v.get("type")),
        }
    address_obj = fields.get("address")
    address: Optional[dict[str, Any]] = None
    if (
        isinstance(address_obj, PostboxObject)
        and address_obj.type_name == "MapGeoAddress"
    ):
        a = address_obj.fields
        address = {
            "country": _maybe_str(a.get("country")),
            "state": _maybe_str(a.get("state")),
            "city": _maybe_str(a.get("city")),
            "street": _maybe_str(a.get("street")),
        }
    raw_live = fields.get("live_broadcasting_timeout")
    live_timeout: Optional[int] = None
    if isinstance(raw_live, int) and not isinstance(raw_live, bool):
        live_timeout = int(raw_live)
    metadata: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "venue": venue,
        "address": address,
        "live_timeout": live_timeout,
    }
    return Attachment(
        kind="map", filename=venue["title"] if venue else "Map pin", metadata=metadata
    )


def _paid_content_attachment(fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaPaidContent`` payload."""
    raw_amount = fields.get("amount")
    stars_amount: Optional[int] = None
    if isinstance(raw_amount, int) and not isinstance(raw_amount, bool):
        stars_amount = int(raw_amount)
    raw_media = fields.get("extended_media")
    extended_media_count = 0
    if isinstance(raw_media, list):
        extended_media_count = len(raw_media)
    metadata: dict[str, Any] = {
        "stars_amount": stars_amount,
        "extended_media_count": extended_media_count,
    }
    return Attachment(kind="paid_content", filename="Paid media", metadata=metadata)


def _story_attachment(fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaStory`` payload."""
    raw_peer_id = fields.get("peer_id")
    peer_id: Optional[int] = None
    if (
        isinstance(raw_peer_id, int)
        and not isinstance(raw_peer_id, bool)
        and raw_peer_id != 0
    ):
        peer_id = int(raw_peer_id)
    raw_story_id = fields.get("story_id")
    story_id: Optional[int] = None
    if isinstance(raw_story_id, int) and not isinstance(raw_story_id, bool):
        story_id = int(raw_story_id)
    raw_mention = fields.get("is_mention")
    is_mention = bool(raw_mention)
    metadata: dict[str, Any] = {
        "peer_id": peer_id,
        "story_id": story_id,
        "is_mention": is_mention,
    }
    return Attachment(
        kind="story",
        filename=f"Story {story_id}" if story_id is not None else "Story",
        metadata=metadata,
    )


def _todo_attachment(fields: dict[str, Any]) -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaTodo`` payload.

    ``Item`` and ``Completion`` are not registered as named decoders,
    so they come back as raw ``dict``s. The raw Postbox two-letter
    keys (``t``, ``i``, ``d``, ``p``) are used directly here — these
    items are throwaway structures (not reused across calls), so
    the raw-key access keeps the helper self-contained.
    """
    raw_flags = fields.get("flags")
    flags_int = (
        int(raw_flags)
        if isinstance(raw_flags, int) and not isinstance(raw_flags, bool)
        else 0
    )
    text = _maybe_str(fields.get("text")) or ""
    raw_items = fields.get("items")
    items: list[dict[str, Any]] = []
    if isinstance(raw_items, list):
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("i")
            item_id: Optional[int] = None
            if isinstance(raw_id, int) and not isinstance(raw_id, bool):
                item_id = int(raw_id)
            items.append(
                {
                    "id": item_id,
                    "text": _maybe_str(entry.get("t")) or "",
                }
            )
    raw_completions = fields.get("completions")
    completions: list[dict[str, Any]] = []
    if isinstance(raw_completions, list):
        for entry in raw_completions:
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("i")
            comp_id: Optional[int] = None
            if isinstance(raw_id, int) and not isinstance(raw_id, bool):
                comp_id = int(raw_id)
            raw_date = entry.get("d")
            date: Optional[int] = None
            if isinstance(raw_date, int) and not isinstance(raw_date, bool):
                date = int(raw_date)
            raw_by = entry.get("p")
            completed_by: Optional[int] = None
            if isinstance(raw_by, int) and not isinstance(raw_by, bool) and raw_by != 0:
                completed_by = int(raw_by)
            completions.append(
                {
                    "id": comp_id,
                    "date": date,
                    "completed_by": completed_by,
                }
            )
    metadata: dict[str, Any] = {
        "text": text,
        "flags": {
            "others_can_append": bool(flags_int & 1),
            "others_can_complete": bool(flags_int & 2),
            "raw": flags_int,
        },
        "items": items,
        "completions": completions,
    }
    return Attachment(
        kind="todo",
        filename=text or "Todo",
        metadata=metadata,
    )


def _unsupported_attachment() -> Attachment:
    """Build an :class:`Attachment` for a ``TelegramMediaUnsupported`` payload."""
    return Attachment(kind="unsupported", filename="Unsupported", metadata={})


def _extract_poll_metadata(fields: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Build a structured representation of a TelegramMediaPoll payload."""
    question = fields.get("text") or ""
    kind_payload = fields.get("kind")
    if not isinstance(kind_payload, PostboxObject):
        kind_payload = None
    kind_fields = kind_payload.fields if kind_payload is not None else {}
    variant = kind_fields.get("variant")
    multiple_answers = bool(kind_fields.get("multiple_answers"))
    if variant == 1:
        kind = "quiz"
    else:
        kind = "poll"

    publicity_value = fields.get("publicity", 0)
    publicity = "public" if publicity_value == 1 else "anonymous"

    is_closed = bool(fields.get("is_closed"))

    results_payload = fields.get("results")
    results_fields = (
        results_payload.fields if isinstance(results_payload, PostboxObject) else {}
    )
    total_voters = results_fields.get("total_voters")
    recent_voters_payload = results_fields.get("recent_voters") or []
    try:
        recent_voters = [int(peer_id) for peer_id in recent_voters_payload]
    except (TypeError, ValueError):
        recent_voters = []

    solution_payload = results_fields.get("solution_text")
    solution = str(solution_payload) if isinstance(solution_payload, str) else None

    voters_payload = results_fields.get("voters") or []
    voters_by_identifier: dict[bytes, dict[str, Any]] = {}
    for voter in voters_payload:
        if not isinstance(voter, PostboxObject):
            continue
        v_fields = voter.fields
        identifier = v_fields.get("opaque_identifier")
        if not isinstance(identifier, (bytes, bytearray)) or len(identifier) == 0:
            continue
        raw_recent = v_fields.get("recent_voters") or []
        try:
            option_recent = [int(peer_id) for peer_id in raw_recent]
        except (TypeError, ValueError):
            option_recent = []
        voters_by_identifier[bytes(identifier)] = {
            "selected": bool(v_fields.get("selected")),
            "count": v_fields.get("count"),
            "is_correct": bool(v_fields.get("is_correct")),
            "recent_voters": option_recent,
        }

    options_payload = fields.get("options") or []
    options: list[dict[str, Any]] = []
    correct_option_indices: list[int] = []
    correct_answers_payload = fields.get("correct_answers")
    correct_identifiers: set[bytes] = set()
    if isinstance(correct_answers_payload, list):
        for item in correct_answers_payload:
            if isinstance(item, (bytes, bytearray)) and len(item) > 0:
                correct_identifiers.add(bytes(item))

    for index, option in enumerate(options_payload):
        if not isinstance(option, PostboxObject):
            continue
        o_fields = option.fields
        identifier = o_fields.get("opaque_identifier")
        if isinstance(identifier, (bytes, bytearray)) and len(identifier) > 0:
            identifier_bytes = bytes(identifier)
        else:
            identifier_bytes = None
        voter_info = (
            voters_by_identifier.get(identifier_bytes) if identifier_bytes else None
        )
        if voter_info is None:
            voter_info = {
                "selected": False,
                "count": None,
                "is_correct": identifier_bytes in correct_identifiers
                if identifier_bytes is not None
                else False,
                "recent_voters": [],
            }
        elif identifier_bytes in correct_identifiers:
            voter_info = {**voter_info, "is_correct": True}
        if voter_info.get("is_correct"):
            correct_option_indices.append(index)
        options.append(
            {
                "text": o_fields.get("text", ""),
                "vote_count": voter_info.get("count"),
                "selected": voter_info.get("selected", False),
                "is_correct": voter_info.get("is_correct", False),
                "recent_voters": voter_info.get("recent_voters", []),
            }
        )

    return {
        "question": question,
        "kind": kind,
        "multiple_answers": multiple_answers,
        "publicity": publicity,
        "is_closed": is_closed,
        "total_voters": total_voters,
        "recent_voters": recent_voters,
        "options": options,
        "correct_option_indices": correct_option_indices,
        "solution": solution,
    }


class PostboxMediaResolver:
    """Resolve referenced media entries from t6 and t7."""

    def __init__(self, conn, media_table: str, message_table: str) -> None:
        self.conn = conn
        self.media_table = media_table
        self.message_table = message_table
        self.cache: dict[tuple[int, int], Optional[Any]] = {}

    def resolve(self, namespace: int, media_id: int) -> Optional[Any]:
        key = (namespace, media_id)
        if key in self.cache:
            return self.cache[key]
        raw_key = struct.pack(">iq", namespace, media_id)
        row = self.conn.execute(
            f"SELECT value FROM {self.media_table} WHERE key = ? LIMIT 1",
            (raw_key,),
        ).fetchone()
        if row is None:
            self.cache[key] = None
            return None
        entry = read_media_entry(row[0])
        if entry.entry_type == MediaEntryType.DIRECT:
            self.cache[key] = entry.media
            return entry.media
        if entry.message_index is None:
            self.cache[key] = None
            return None
        message_row = self.conn.execute(
            f"SELECT value FROM {self.message_table} WHERE key = ? LIMIT 1",
            (entry.message_index.as_bytes(),),
        ).fetchone()
        if message_row is None:
            self.cache[key] = None
            return None
        message = read_intermediate_message(message_row[0])
        if message:
            for media in message["embedded_media"]:
                if _media_id(media) == key:
                    self.cache[key] = media
                    return media
        self.cache[key] = None
        return None


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
    flags = (
        MessageForwardFlags(reader.read_int32())
        if FwdInfoFlags.FLAGS in info_flags
        else None
    )

    return {
        "author": author_id or None,
        "date": date_value,
        "src_id": source_id,
        "src_msg_peer": src_peer,
        "src_msg_ns": src_namespace,
        "src_msg_id": src_message_id,
        "signature": signature,
        "psa_type": psa_type,
        "flags": flags,
    }


def _decode_quote_payload(raw: Any) -> Optional[str]:
    """Return the ``t`` (snippet text) field from an ``EngineMessageReplyQuote`` blob.

    The ``qu`` key in both ``ReplyMessageAttribute`` and
    ``QuotedReplyMessageAttribute`` is JSON-encoded by the iOS client
    (see ``PostboxEncoder.encodeCodable`` in Coding.swift:424-428 and
    ``PostboxDecoder.decodeCodable`` in Coding.swift:1182-1188).
    ``raw`` is therefore ``bytes`` containing a JSON object with keys
    ``t`` (text), ``e`` (entities), ``m`` (media), ``o`` (offset).
    Returns ``None`` when the input is not bytes, fails to parse, or
    has no ``t`` field.
    """
    if not isinstance(raw, (bytes, bytearray)) or len(raw) == 0:
        return None
    try:
        decoded = json.loads(bytes(raw).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    text = decoded.get("t")
    if isinstance(text, str):
        return text
    return None


def _reply_info_from_attributes(
    attributes: list[Any],
    current_peer_id: int,
) -> Optional[ReplyInfo]:
    """Build a :class:`ReplyInfo` from a message's attribute list.

    Prefers ``ReplyMessageAttribute`` (intra-chat reply — always carries
    a real ``messageId``). Falls back to ``QuotedReplyMessageAttribute``
    (cross-chat quote — used when the original lives in a chat the
    client may not have full access to). Returns ``None`` when neither
    attribute is present.

    Payload layout per the iOS source
    (``submodules/TelegramCore/Sources/SyncCore/SyncCore_ReplyMessageAttribute.swift``):

    * ``ReplyMessageAttribute``: ``p`` (target peer id i64), ``i``
      (packed ``(namespace | (message_id << 32))`` i64), ``qu``
      (``EngineMessageReplyQuote`` JSON bytes), ``iq`` (isQuote bool).
    * ``QuotedReplyMessageAttribute``: ``p`` (target peer id i64,
      optional), ``a`` (author display name, optional), ``qu`` (same
      JSON blob), ``iq`` (isQuote bool, defaults to true).

    ``is_intra_chat`` is set to ``target_peer_id == current_peer_id``,
    which the renderer uses to pick an in-page ``#msg-{mid}`` anchor
    over an external ``t.me`` URL.
    """
    reply_attr: Optional[dict[str, Any]] = None
    quote_attr: Optional[dict[str, Any]] = None
    for attribute in attributes or []:
        if not isinstance(attribute, PostboxObject):
            continue
        if attribute.type_name == "ReplyMessageAttribute":
            reply_attr = attribute.payload
        elif attribute.type_name == "QuotedReplyMessageAttribute":
            quote_attr = attribute.payload

    target_peer_id: Optional[int] = None
    target_message_id: Optional[int] = None
    is_quote = False
    snippet: Optional[str] = None

    if reply_attr is not None:
        raw_peer = reply_attr.get("p")
        if isinstance(raw_peer, int) and not isinstance(raw_peer, bool):
            target_peer_id = int(raw_peer)
        raw_packed = reply_attr.get("i")
        if (
            isinstance(raw_packed, int)
            and not isinstance(raw_packed, bool)
            and target_peer_id is not None
        ):
            namespace = int(raw_packed) & 0xFFFFFFFF
            mid = (int(raw_packed) >> 32) & 0xFFFFFFFF
            if mid > 0:
                target_message_id = mid
                _ = namespace
        raw_is_quote = reply_attr.get("iq")
        if isinstance(raw_is_quote, bool):
            is_quote = raw_is_quote
        elif reply_attr.get("qu"):
            is_quote = True
        snippet = _decode_quote_payload(reply_attr.get("qu"))
    elif quote_attr is not None:
        raw_peer = quote_attr.get("p")
        if isinstance(raw_peer, int) and not isinstance(raw_peer, bool):
            target_peer_id = int(raw_peer)
        raw_is_quote = quote_attr.get("iq")
        if isinstance(raw_is_quote, bool):
            is_quote = raw_is_quote
        else:
            is_quote = True
        snippet = _decode_quote_payload(quote_attr.get("qu"))
        raw_target_id = quote_attr.get("i")
        if isinstance(raw_target_id, int) and not isinstance(raw_target_id, bool):
            mid = (int(raw_target_id) >> 32) & 0xFFFFFFFF
            if mid > 0:
                target_message_id = mid
        if target_message_id is None:
            return None

    if target_peer_id is None or target_message_id is None:
        return None

    return ReplyInfo(
        target_peer_id=target_peer_id,
        target_message_id=target_message_id,
        is_quote=is_quote,
        is_intra_chat=(target_peer_id == current_peer_id),
        target_snippet=snippet,
    )


def read_intermediate_message(payload: bytes) -> Optional[dict[str, Any]]:
    """Decode a Postbox message payload to a structured dict."""
    reader = ByteReader(io.BytesIO(payload))
    message_type = reader.read_int8()
    if message_type != 0:
        return None

    stable_id = reader.read_uint32()
    stable_version = reader.read_uint32()

    data_flags = MessageDataFlags(reader.read_uint8())
    globally_unique_id = None
    if MessageDataFlags.GLOBALLY_UNIQUE_ID in data_flags:
        globally_unique_id = reader.read_int64()

    global_tags = GlobalMessageTags(0)
    if MessageDataFlags.GLOBAL_TAGS in data_flags:
        global_tags = GlobalMessageTags(reader.read_uint32())

    grouping_key = None
    if MessageDataFlags.GROUPING_KEY in data_flags:
        grouping_key = reader.read_int64()

    group_info_stable_id = None
    if MessageDataFlags.GROUP_INFO in data_flags:
        group_info_stable_id = reader.read_uint32()

    local_tags = LocalMessageTags(0)
    if MessageDataFlags.LOCAL_TAGS in data_flags:
        local_tags = LocalMessageTags(reader.read_uint32())

    thread_id = None
    if MessageDataFlags.THREAD_ID in data_flags:
        thread_id = reader.read_int64()

    flags = MessageFlags(reader.read_uint32())
    tags = MessageTags(reader.read_uint32())

    fwd_info = read_intermediate_fwd_info(reader)

    author_id = None
    if reader.read_int8() == 1:
        author_id = reader.read_int64()

    text = reader.read_str()

    attributes_count = reader.read_int32()
    attributes = []
    for _ in range(attributes_count):
        attribute_data = reader.read_bytes()
        attributes.append(PostboxDecoder(attribute_data).decode_root_object())

    embedded_media_count = reader.read_int32()
    embedded_media = []
    for _ in range(embedded_media_count):
        media_data = reader.read_bytes()
        embedded_media.append(PostboxDecoder(media_data).decode_root_object())

    referenced_media_ids = []
    for _ in range(reader.read_int32()):
        namespace = reader.read_int32()
        message_id = reader.read_int64()
        referenced_media_ids.append((namespace, message_id))

    custom_tags = []
    if reader.buf.tell() < len(payload):
        for _ in range(reader.read_int32()):
            custom_tags.append(reader.read_bytes())

    return {
        "stable_id": stable_id,
        "stable_version": stable_version,
        "globally_unique_id": globally_unique_id,
        "global_tags": global_tags,
        "grouping_key": grouping_key,
        "group_info_stable_id": group_info_stable_id,
        "local_tags": local_tags,
        "thread_id": thread_id,
        "flags": flags,
        "tags": tags,
        "author_id": author_id,
        "fwd": fwd_info,
        "text": text,
        "attributes": attributes,
        "embedded_media": embedded_media,
        "referenced_media_ids": referenced_media_ids,
        "custom_tags": custom_tags,
    }


def iter_postbox_messages(
    rows: Iterable[tuple[bytes, bytes]],
    peer_id: Optional[int] = None,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    limit: Optional[int] = None,
    media_resolver: Optional[PostboxMediaResolver] = None,
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
        media = list(msg.get("embedded_media") or [])
        if media_resolver is not None:
            for namespace, media_id in msg.get("referenced_media_ids") or []:
                referenced_media = media_resolver.resolve(namespace, media_id)
                if referenced_media is not None:
                    media.append(referenced_media)
        attachments = tuple(
            attachment for item in media for attachment in media_attachments(item)
        )
        if not text and not attachments:
            continue

        incoming = MessageFlags.INCOMING in msg["flags"]
        timestamp = datetime.fromtimestamp(idx.timestamp) if idx.timestamp else None
        raw_forward_info = msg.get("fwd")
        forward_info = _build_forward_info(raw_forward_info)
        messages.append(
            Message(
                timestamp=timestamp,
                text=text,
                outgoing=None if incoming is None else not incoming,
                peer_id=idx.peer_id,
                author_id=msg.get("author_id"),
                attachments=attachments,
                forward_info=forward_info,
                message_id=idx.message_id,
                reply_info=_reply_info_from_attributes(
                    msg.get("attributes") or [], idx.peer_id
                ),
            )
        )
        if limit and len(messages) >= limit:
            break
    return messages


def _attachment_meta_string(attachment: Attachment) -> str:
    """Return a short human string for an attachment's media metadata.

    Used by :func:`resolve_reply_previews` to populate
    ``ReplyInfo.target_attachment_meta``. Concatenates size / duration /
    dimensions / sticker emoji / poll question, skipping empty parts.
    """
    parts: list[str] = []
    if isinstance(attachment.size, int) and attachment.size >= 0:
        size_str = _human_size(attachment.size)
        parts.append(size_str)
    if attachment.kind in ("video", "video_message", "voice", "audio"):
        raw_duration = (
            (attachment.metadata or {}).get("duration") if attachment.metadata else None
        )
        if raw_duration is None and attachment.kind in ("video", "video_message"):
            raw_duration = (attachment.metadata or {}).get("video_duration")
        if (
            isinstance(raw_duration, int)
            and not isinstance(raw_duration, bool)
            and raw_duration > 0
        ):
            parts.append(_format_duration(raw_duration))
    if (
        isinstance(attachment.width, int)
        and isinstance(attachment.height, int)
        and attachment.width > 0
        and attachment.height > 0
        and attachment.kind not in ("sticker",)
    ):
        parts.append(f"{attachment.width}×{attachment.height}")
    if attachment.kind == "sticker" and attachment.sticker_emoji:
        parts.append(attachment.sticker_emoji)
    if attachment.kind == "poll":
        question = (attachment.metadata or {}).get("question", "")
        if isinstance(question, str) and question:
            question = question.strip()
            if len(question) > 60:
                question = question[:57] + "…"
            parts.append(question)
    return " · ".join(parts)


def _build_forward_info(raw: Optional[dict[str, Any]]) -> Optional[ForwardInfo]:
    """Convert a raw ``read_intermediate_fwd_info`` dict to a ``ForwardInfo``.

    Returns ``None`` for the no-forward case. Centralised so the main
    message loop and ``resolve_reply_previews`` (which needs the target
    message's forward info) both decode the same shape.
    """
    if raw is None:
        return None
    forward_timestamp = raw.get("date")
    forward_flags = raw.get("flags")
    return ForwardInfo(
        author_id=raw.get("author"),
        source_id=raw.get("src_id"),
        source_message_peer_id=raw.get("src_msg_peer"),
        source_message_namespace=raw.get("src_msg_ns"),
        source_message_id=raw.get("src_msg_id"),
        date=(
            datetime.fromtimestamp(forward_timestamp, tz=timezone.utc)
            if forward_timestamp
            else None
        ),
        author_signature=raw.get("signature"),
        psa_type=raw.get("psa_type"),
        is_imported=bool(
            forward_flags and MessageForwardFlags.IS_IMPORTED in forward_flags
        ),
    )


def _attachment_kind_label(attachment: Attachment) -> str:
    """Return a stable short kind string for an attachment (used by ReplyInfo)."""
    if attachment.kind == "sticker":
        return "sticker"
    if attachment.kind == "video_message":
        return "video_message"
    if attachment.kind == "image":
        return "image"
    return attachment.kind


def _attachment_emoji(attachment: Attachment) -> Optional[str]:
    """Return the emoji glyph associated with a sticker attachment, if any."""
    if attachment.kind == "sticker" and attachment.sticker_emoji:
        return attachment.sticker_emoji
    return None


def _human_size(n: int) -> str:
    """Format ``n`` bytes as a human-readable string (B/KB/MB/GB/TB, 1024-based)."""

    units = ("B", "KB", "MB", "GB", "TB")
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{int(value)} TB"


def _format_duration(seconds: int) -> str:
    """Format a duration in seconds as ``M:SS`` or ``H:MM:SS``."""

    total = max(int(seconds), 0)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def resolve_reply_previews(
    messages: list[Message],
    conn: Any,
    message_table: str,
    media_resolver: Optional["PostboxMediaResolver"] = None,
) -> list[Message]:
    """Enrich every ``Message.reply_info`` with data looked up from ``t7``.

    Walks ``messages`` for unique ``(target_peer_id, target_message_id)``
    pairs in their ``reply_info`` field, scans the corresponding byte
    range in the Postbox message-history table once per unique target
    peer, and decodes the matching rows. Populates
    ``target_author_id`` / ``target_timestamp`` / ``target_text`` and the
    first attachment's kind / emoji / filename / meta on each
    ``ReplyInfo``. Pairs with no matching row get
    ``target_unavailable=True``. Messages without a ``reply_info`` are
    returned unchanged.

    Returns a new list of messages with their ``reply_info`` replaced
    via ``dataclasses.replace``; the input list is not mutated.
    """
    targets: dict[tuple[int, int], None] = {}
    for msg in messages:
        if msg.reply_info is None:
            continue
        targets[(msg.reply_info.target_peer_id, msg.reply_info.target_message_id)] = (
            None
        )
    if not targets:
        return list(messages)

    target_rows: dict[tuple[int, int], dict[str, Any]] = {}
    target_first_attachment: dict[tuple[int, int], Attachment] = {}
    target_author_ids: dict[tuple[int, int], Optional[int]] = {}

    for target_peer_id in sorted({peer for peer, _mid in targets}):
        rows = iter_postbox_message_rows_for_peer(conn, message_table, target_peer_id)
        for key, value in rows:
            if not isinstance(key, (bytes, bytearray)) or not isinstance(
                value, (bytes, bytearray)
            ):
                continue
            idx = MessageIndex.from_bytes(key)
            target_key = (idx.peer_id, idx.message_id)
            if target_key not in targets:
                continue
            decoded = read_intermediate_message(value)
            if decoded is None:
                continue
            text = decoded.get("text") or ""
            media = list(decoded.get("embedded_media") or [])
            if media_resolver is not None:
                for namespace, media_id in decoded.get("referenced_media_ids") or []:
                    referenced_media = media_resolver.resolve(namespace, media_id)
                    if referenced_media is not None:
                        media.append(referenced_media)
            attachments = tuple(
                att for item in media for att in media_attachments(item)
            )
            target_rows[target_key] = {
                "text": text,
                "timestamp": (
                    datetime.fromtimestamp(idx.timestamp) if idx.timestamp else None
                ),
                "author_id": decoded.get("author_id"),
                "attachments": attachments,
                "fwd": _build_forward_info(decoded.get("fwd")),
            }
            if attachments:
                target_first_attachment[target_key] = attachments[0]
            target_author_ids[target_key] = decoded.get("author_id")

    updated: list[Message] = []
    for msg in messages:
        if msg.reply_info is None:
            updated.append(msg)
            continue
        reply = msg.reply_info
        target_key = (reply.target_peer_id, reply.target_message_id)
        row = target_rows.get(target_key)
        if row is None:
            updated.append(
                replace(
                    msg,
                    reply_info=replace(reply, target_unavailable=True),
                )
            )
            continue
        first = target_first_attachment.get(target_key)
        updated.append(
            replace(
                msg,
                reply_info=replace(
                    reply,
                    target_author_id=target_author_ids.get(target_key),
                    target_timestamp=row["timestamp"],
                    target_text=row["text"] or None,
                    target_attachment_kind=(
                        _attachment_kind_label(first) if first is not None else None
                    ),
                    target_attachment_emoji=(
                        _attachment_emoji(first) if first is not None else None
                    ),
                    target_filename=first.filename if first is not None else None,
                    target_attachment_meta=(
                        _attachment_meta_string(first) if first is not None else None
                    ),
                    target_forward_info=row.get("fwd"),
                ),
            )
        )
    return updated


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


def _maybe_str(value: Any) -> Optional[str]:
    """Return ``value`` when it is a non-empty string, else ``None``."""
    if isinstance(value, str) and value:
        return value
    return None


def _peer_display_name(data: Any) -> Optional[str]:
    """Extract a display name from a decoded Postbox peer payload."""
    if not isinstance(data, dict):
        return None
    if "fn" in data or "ln" in data:
        return f"{data.get('fn', '')} {data.get('ln', '')}".strip()
    if "t" in data:
        return str(data.get("t"))
    if "un" in data:
        return f"@{data.get('un')}"
    return None


def _fallback_peer_name(peer_id: int) -> str:
    """Return a stable, human-readable name for a peer with no display fields."""
    return f"peer {peer_id}"


def _peer_kind_from_payload(peer_id: int, data: dict) -> PeerKind:
    """Infer :class:`PeerKind` from peer id and the peer's payload shape."""
    has_user_name = isinstance(data.get("fn"), str) or isinstance(data.get("ln"), str)
    has_title = isinstance(data.get("t"), str)
    if has_user_name and not has_title:
        return PeerKind.USER
    if has_title and not has_user_name:
        return PeerKind.CHANNEL if peer_id <= -1_000_000_000_000 else PeerKind.GROUP
    if peer_id > 0:
        return PeerKind.USER
    if peer_id <= -1_000_000_000_000:
        return PeerKind.CHANNEL
    return PeerKind.GROUP


def _peer_info_from_payload(peer_id: int, data: Any) -> Optional[PeerInfo]:
    """Build a :class:`PeerInfo` from a decoded Postbox peer payload."""
    if not isinstance(data, dict):
        return None
    kind = _peer_kind_from_payload(peer_id, data)
    name = _peer_display_name(data) or _fallback_peer_name(peer_id)
    if not name:
        return None
    raw_flags = data.get("fl") if isinstance(data.get("fl"), int) else 0
    fake_bit = 8 if kind == PeerKind.USER else 64
    raw_name_color = data.get("nclr")
    name_color = (
        raw_name_color
        if (
            isinstance(raw_name_color, int)
            and not isinstance(raw_name_color, bool)
            and raw_name_color >= 0
        )
        else None
    )
    return PeerInfo(
        name=name,
        kind=kind,
        username=_maybe_str(data.get("un")),
        phone=_maybe_str(data.get("p")),
        is_verified=bool(raw_flags & 0b1),
        is_scam=bool(raw_flags & 0b100),
        is_fake=bool(raw_flags & fake_bit),
        is_premium=bool(raw_flags & 0b10000),
        name_color=name_color,
        photo_cache_key=peer_photo_cache_key(data.get("ph")),
    )


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
        info = _peer_info_from_payload(peer_id, data)
        if info is None:
            continue
        display = info.name
        if not display:
            continue
        if term and term.lower() not in display.lower():
            continue
        results.append((PostboxTable.PEER.sqlite_name, peer_id, display))
    return results


def load_peer_map(rows: Iterable[tuple[bytes, bytes]]) -> dict[int, PeerInfo]:
    """Load peer mapping from Postbox t2 rows."""
    peer_map: dict[int, PeerInfo] = {}
    for key, value in rows:
        peer_id = parse_peer_key(key)
        if peer_id is None or not isinstance(value, (bytes, bytearray)):
            continue
        try:
            data = PostboxDecoder(value).decode_root_object()
        except (ValueError, TypeError):
            continue
        info = _peer_info_from_payload(peer_id, data)
        if info is not None:
            peer_map[peer_id] = info
    return peer_map


def load_account_peer_id(conn) -> Optional[int]:
    """Load the authorized account peer id from Postbox metadata table t0."""
    metadata_table = PostboxTable.METADATA.sqlite_name
    row = conn.execute(
        f"SELECT value FROM {metadata_table} WHERE key = ?",
        (PostboxMetadataKey.STATE,),
    ).fetchone()
    if row is None or not isinstance(row[0], (bytes, bytearray)):
        return None
    try:
        state = PostboxDecoder(row[0]).decode_root_object()
    except (ValueError, TypeError):
        return None
    if not isinstance(state, dict):
        return None
    peer_id = state.get("peerId")
    if isinstance(peer_id, int) and not isinstance(peer_id, bool) and peer_id > 0:
        return peer_id
    return None


def attachment_referenced_peer_ids(attachment: Attachment) -> list[int]:
    """Return peer ids referenced inside an :class:`Attachment`.

    A peer's row in the Postbox ``t2`` (PEER) table only gets loaded into
    the export's ``peer_map`` if the peer's id is collected up-front by
    the CLI. Peers that *only* appear inside an attachment — e.g. the
    author of a Story, the channel set of a Giveaway, the participants
    of a service action — are not picked up by the message-level scan
    in ``cli.py``. This helper closes that gap by walking each
    attachment's metadata and yielding every peer id it can find.

    Coverage:

    - ``contact`` / ``story`` — ``metadata.peer_id`` (single int)
    - ``giveaway`` / ``giveaway_results`` — ``metadata.channel_peer_ids``
      and ``metadata.winners_peer_ids`` (lists)
    - ``action`` — walks ``metadata.payload`` for ``peerIds`` (either a
      ``bytes`` blob, as used by ``addedMembers`` / ``removedMembers``,
      or a plain list, as used by ``inviteToGroupPhoneCall`` /
      ``requestedPeer``), plus the singular ``peerId`` / ``inviter`` /
      ``fromId`` / ``toId`` / ``channelId`` / ``groupId`` fields
    - everything else — empty list
    """
    metadata = attachment.metadata
    if not isinstance(metadata, dict):
        return []
    kind = attachment.kind
    ids: list[int] = []

    def _add_int(value: Any) -> None:
        if isinstance(value, int) and not isinstance(value, bool):
            ids.append(int(value))

    if kind in ("contact", "story"):
        _add_int(metadata.get("peer_id"))
    elif kind in ("giveaway", "giveaway_results"):
        for key in ("channel_peer_ids", "winners_peer_ids"):
            for value in metadata.get(key) or ():
                _add_int(value)
    elif kind == "action":
        payload = metadata.get("payload")
        if isinstance(payload, dict):
            peer_ids = payload.get("peerIds")
            if isinstance(peer_ids, (bytes, bytearray)):
                ids.extend(_decode_peer_ids_from_buffer(peer_ids))
            elif isinstance(peer_ids, list):
                for value in peer_ids:
                    _add_int(value)
            for key in (
                "peerId",
                "inviter",
                "fromId",
                "toId",
                "channelId",
                "groupId",
                "senderId",
                "toPeerId",
                "boostPeerId",
                "botId",
            ):
                _add_int(payload.get(key))
            pis = payload.get("pis")
            if isinstance(pis, list):
                for value in pis:
                    _add_int(value)
            part = payload.get("part")
            if isinstance(part, list):
                for value in part:
                    _add_int(value)
    return ids


def peer_url(
    peer: PeerInfo,
    peer_id: int,
    *,
    message_id: Optional[int] = None,
) -> Optional[str]:
    """Build a ``t.me`` URL for a peer, optionally pointing at a specific message."""
    if peer.username:
        base = f"https://t.me/{peer.username}"
    elif peer.kind == PeerKind.CHANNEL:
        channel_id = -peer_id - 1_000_000_000_000
        base = f"https://t.me/c/{channel_id}"
    else:
        return None
    if message_id is not None:
        return f"{base}/{message_id}"
    return base
