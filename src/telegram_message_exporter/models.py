"""Data models for exports."""

from __future__ import annotations

import enum
from dataclasses import dataclass
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
    url: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


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

    def speaker_hint(self) -> str:
        """Return a concise label for direction when no names are available."""
        if self.outgoing is True:
            return "out"
        if self.outgoing is False:
            return "in"
        return "unknown"
