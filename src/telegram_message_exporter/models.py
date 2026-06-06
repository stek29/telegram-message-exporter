"""Data models for exports."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class Attachment:
    """Normalized media attachment associated with a message."""

    kind: str
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    cache_key: Optional[str] = None
    alternate_cache_keys: tuple[str, ...] = ()
    selected_cache_key: Optional[str] = None
    source_path: Optional[str] = None
    exported_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    size: Optional[int] = None
    sticker_emoji: Optional[str] = None
    url: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    preview_image: Optional["Attachment"] = None
    preview_video: Optional["Attachment"] = None
    alternate_dimensions: dict[str, tuple[Optional[int], Optional[int]]] = field(
        default_factory=dict
    )
    is_preview_fallback: bool = False
    vcard_data: Optional[str] = None
    vcard_path: Optional[str] = None

    def is_image(self) -> bool:
        """True for image-kind attachments and any preview fallback to an image.

        On a preview fallback the original ``kind`` is no longer
        authoritative (a video whose only cached file is a JPEG
        preview should be rendered as an ``<img>``), so the mime
        type prefix is used instead.
        """
        if self.is_preview_fallback:
            return (self.mime_type or "").startswith("image/")
        if self.kind == "image":
            return True
        if self.kind == "sticker" and self.mime_type in {"image/png", "image/webp"}:
            return True
        return False

    def is_video(self) -> bool:
        """True for video-kind attachments and any preview fallback to a video."""
        if self.is_preview_fallback:
            return (self.mime_type or "").startswith("video/")
        if self.kind == "video":
            return True
        if self.kind == "sticker" and self.mime_type in {"video/mp4", "video/webm"}:
            return True
        return False

    def is_audio(self) -> bool:
        """True for audio-kind attachments and any preview fallback to audio."""
        if self.is_preview_fallback:
            return (self.mime_type or "").startswith("audio/")
        return self.kind in {"voice", "audio"}


@dataclass(frozen=True)
class ForwardInfo:
    """Original-message metadata attached to a forwarded message."""

    author_id: Optional[int]
    source_id: Optional[int]
    source_message_peer_id: Optional[int]
    source_message_namespace: Optional[int]
    source_message_id: Optional[int]
    date: Optional[datetime]
    author_signature: Optional[str]
    psa_type: Optional[str]
    is_imported: bool = False


@dataclass(frozen=True)
class ReplyInfo:
    """Pointer to a message that this message replies to (or quotes).

    The pointer is parsed from the message's
    ``ReplyMessageAttribute`` / ``QuotedReplyMessageAttribute`` payload
    (both already registered in ``POSTBOX_MESSAGE_ATTRIBUTE_TYPES``).
    The enrichment fields (``target_author_id`` / ``target_timestamp`` /
    ``target_text`` / ``target_snippet`` / ``target_attachment_*``) are
    filled by ``resolve_reply_previews`` after a follow-up scan of the
    Postbox ``t7`` (MESSAGE_HISTORY) table.

    ``target_author_name`` is *not* filled by the lookup pass — it is
    resolved at render time from ``peer_map[target_author_id]``, the
    same pattern used for ``ForwardInfo`` segments.

    ``is_intra_chat`` is set by the parser to
    ``target_peer_id == current_peer_id``. The renderer uses it to pick
    an in-page ``#msg-{mid}`` anchor over an external ``t.me`` URL.
    """

    target_peer_id: int
    target_message_id: int
    is_quote: bool = False
    is_intra_chat: bool = True

    target_author_id: Optional[int] = None
    target_author_name: Optional[str] = None
    target_timestamp: Optional[datetime] = None
    target_text: Optional[str] = None
    target_snippet: Optional[str] = None
    target_attachment_kind: Optional[str] = None
    target_attachment_emoji: Optional[str] = None
    target_filename: Optional[str] = None
    target_attachment_meta: Optional[str] = None
    target_unavailable: bool = False
    target_forward_info: Optional[ForwardInfo] = None
    target_via_bot_id: Optional[int] = None
    target_via_bot_title: Optional[str] = None


class PeerKind(enum.Enum):
    """High-level classification of a Telegram peer."""

    USER = "user"
    GROUP = "group"
    CHANNEL = "channel"
    SECRET_CHAT = "secret_chat"


@dataclass(frozen=True)
class PeerInfo:
    """Structured peer record derived from a Postbox peer payload."""

    name: str
    kind: PeerKind
    username: Optional[str] = None
    phone: Optional[str] = None
    is_verified: bool = False
    is_scam: bool = False
    is_fake: bool = False
    is_premium: bool = False
    name_color: Optional[int] = None
    photo_cache_key: Optional[str] = None


@dataclass(frozen=True)
class ForwardedSegment:
    """One run of text inside a 'Forwarded from ...' line.

    ``url`` is set when the segment should be rendered as a hyperlink.
    """

    text: str
    url: Optional[str] = None


@dataclass(frozen=True)
class Message:
    """Normalized message representation used by exporters."""

    timestamp: Optional[datetime]
    text: str
    outgoing: Optional[bool]
    peer_id: Optional[int]
    author_id: Optional[int]
    attachments: tuple[Attachment, ...] = ()
    forward_info: Optional[ForwardInfo] = None
    message_id: Optional[int] = None
    reply_info: Optional[ReplyInfo] = None
    via_bot_id: Optional[int] = None
    via_bot_title: Optional[str] = None

    def speaker_hint(self) -> str:
        """Return a concise label for direction when no names are available."""
        if self.outgoing is True:
            return "out"
        if self.outgoing is False:
            return "in"
        return "unknown"
