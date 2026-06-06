"""Export helpers for Markdown, CSV, and HTML."""

from __future__ import annotations

import csv
import html
import json
import logging
import re
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import Attachment, ForwardedSegment, Message, PeerInfo, ReplyInfo
from .postbox import PostboxObject, _decode_peer_ids_from_buffer, peer_url
from .hashing import persistent_hash32
from .schema import ConferenceCallFlags, PhoneCallDiscardReason, TelegramMediaActionType
from .utils import linkify_html, linkify_markdown, to_local

logger = logging.getLogger(__name__)

HTML_BOOTSTRAP = (
    '<link rel="stylesheet" '
    'href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">'
)
HTML_FONTS = (
    '<link rel="stylesheet" '
    'href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&'
    'family=JetBrains+Mono:wght@400;600&display=swap">'
)

PEER_NAME_COLOR_HEX: dict[int, str] = {
    0: "#e17076",  # red
    1: "#e7c063",  # orange / yellow
    2: "#9b8ef1",  # violet
    3: "#7ec68f",  # green
    4: "#6ec1e4",  # cyan
    5: "#5fa5e4",  # blue
    6: "#d49bd9",  # pink
}

PEER_COLOR_SOFT_ALPHA = 0.18


def _hex_to_soft_rgba(hex_color: str, alpha: float = PEER_COLOR_SOFT_ALPHA) -> str:
    """Convert ``#rrggbb`` to ``rgba(r, g, b, alpha)``."""
    value = int(hex_color.lstrip("#"), 16)
    red = (value >> 16) & 0xFF
    green = (value >> 8) & 0xFF
    blue = value & 0xFF
    return f"rgba({red}, {green}, {blue}, {alpha:g})"


def peer_display_color(peer: PeerInfo, peer_id: int) -> tuple[str, str]:
    """Return ``(solid_hex, soft_rgba)`` for a peer's color coding.

    Uses ``peer.name_color`` when it indexes the ``PEER_NAME_COLOR_HEX``
    table, otherwise falls back to ``abs(peer_id) % 7`` mapped into the
    same table.
    """
    name_color = peer.name_color
    if (
        isinstance(name_color, int)
        and not isinstance(name_color, bool)
        and name_color in PEER_NAME_COLOR_HEX
    ):
        solid = PEER_NAME_COLOR_HEX[name_color]
    else:
        solid = PEER_NAME_COLOR_HEX[abs(peer_id) % 7]
    return solid, _hex_to_soft_rgba(solid)


def collect_peer_color_map(
    messages: list[Message], peer_map: Optional[dict[int, PeerInfo]]
) -> dict[int, tuple[str, str]]:
    """Build ``{peer_id: (solid, soft)}`` for every peer that appears in ``messages``."""
    colors: dict[int, tuple[str, str]] = {}
    for msg in messages:
        for candidate in (msg.peer_id, msg.author_id):
            if candidate is None or candidate in colors:
                continue
            info = peer_map.get(candidate) if peer_map else None
            if info is None:
                info = PeerInfo("peer", None)
            colors[candidate] = peer_display_color(info, candidate)
    return colors


def _escape_css_selector(value: str) -> str:
    """Escape a string for safe inclusion inside a CSS attribute selector."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_peer_color_css(color_map: dict[int, tuple[str, str]]) -> str:
    """Emit CSS rules that set ``--peer-color``/``--peer-soft`` per peer id."""
    if not color_map:
        return ""
    rules: list[str] = []
    for peer_id, (solid, soft) in sorted(color_map.items()):
        selector = f'.msg[data-peer-id="{_escape_css_selector(str(peer_id))}"]'
        rules.append(f"{selector} {{ --peer-color: {solid}; --peer-soft: {soft}; }}")
        rules.append(f"{selector} .bubble {{ border-left-color: var(--peer-color); }}")
        rules.append(
            f"{selector} .meta .speaker, "
            f"{selector} .meta .speaker a "
            f"{{ color: var(--peer-color); font-weight: 600; }}"
        )
    return "/* per-peer colors */\n" + "\n".join(rules) + "\n"


HTML_CSS = """
:root {
  --bg-0: #0b1120;
  --bg-1: #0f172a;
  --bg-2: #111827;
  --ink: #e2e8f0;
  --muted: #94a3b8;
  --accent: #38bdf8;
  --glass: rgba(15, 23, 42, 0.55);
  --glass-strong: rgba(15, 23, 42, 0.75);
  --glass-border: rgba(148, 163, 184, 0.2);
  --bubble-in: rgba(30, 41, 59, 0.75);
  --bubble-out: rgba(14, 116, 144, 0.55);
  --bubble-shadow: rgba(15, 23, 42, 0.45);
}
body {
  background:
    radial-gradient(circle at 10% 10%, rgba(56, 189, 248, 0.16), transparent 35%),
    radial-gradient(circle at 90% 20%, rgba(99, 102, 241, 0.14), transparent 40%),
    radial-gradient(circle at 30% 90%, rgba(34, 197, 94, 0.12), transparent 35%),
    linear-gradient(135deg, var(--bg-0) 0%, var(--bg-1) 45%, var(--bg-2) 100%);
  font-family: "Outfit", system-ui, -apple-system, sans-serif;
  color: var(--ink);
  min-height: 100vh;
}
.background-blobs .blob {
  position: fixed;
  width: 420px;
  height: 420px;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.35;
  z-index: 0;
}
.blob-1 { background: #38bdf8; top: -120px; left: -120px; }
.blob-2 { background: #6366f1; top: 20px; right: -140px; }
.blob-3 { background: #22c55e; bottom: -160px; left: 20%; }

.container { position: relative; z-index: 1; padding: 22px 16px 44px; max-width: 1120px; }
.glass {
  background: var(--glass);
  border: 1px solid var(--glass-border);
  box-shadow: 0 18px 40px rgba(2, 6, 23, 0.55);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
}
header.header-panel {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-radius: 18px;
}
.brand { display: flex; align-items: center; gap: 16px; }
.logo {
  width: 40px;
  height: 40px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  background: rgba(56, 189, 248, 0.2);
  border: 1px solid rgba(56, 189, 248, 0.4);
  font-size: 18px;
}
.title-area h1 { margin: 0; font-weight: 700; font-size: 24px; }
.subtitle { margin: 0; color: var(--muted); font-size: 12px; }
.badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12px;
  color: var(--ink);
  background: var(--glass-strong);
  border: 1px solid var(--glass-border);
}
.badge .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
  box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.15);
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin: 8px 0 16px;
}
.stat-card {
  padding: 12px 14px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  position: relative;
  overflow: hidden;
  min-height: 60px;
}
.stat-card::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: linear-gradient(180deg, #38bdf8, #6366f1);
  opacity: 0.7;
}
.stat-info { display: flex; flex-direction: column; gap: 4px; padding-left: 10px; }
.stat-info .label {
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.stat-info .value {
  font-size: 17px;
  font-weight: 500;
  letter-spacing: 0.01em;
  color: #f1f5f9;
}
.stat-info .value.mono {
  font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 15px;
  letter-spacing: 0.02em;
  color: #e2e8f0;
}
.chat-card { border-radius: 18px; padding: 22px; }
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 14px;
  margin: 6px 0 18px;
  flex-wrap: wrap;
}
.toolbar label {
  font-size: 12px;
  color: var(--muted);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.toolbar select {
  background: rgba(15, 23, 42, 0.65);
  color: var(--ink);
  border: 1px solid var(--glass-border);
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
}
.day {
  margin: 22px 0 8px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  font-size: 12px;
  letter-spacing: 0.12em;
  text-align: center;
}
.msg { display: flex; margin: 12px 0; gap: 10px; }
.msg.out { justify-content: flex-end; }
.msg-avatar,
.msg-avatar-initial {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 600;
  font-size: 14px;
  text-transform: uppercase;
  box-shadow: 0 4px 10px rgba(2, 6, 23, 0.4);
  margin-top: 2px;
  overflow: hidden;
}
.msg-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.bubble {
  padding: 12px 16px;
  border-radius: 14px;
  max-width: 72%;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-left: 3px solid transparent;
  background: var(--bubble-in);
  box-shadow: 0 8px 20px var(--bubble-shadow);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.msg.out .bubble { background: var(--bubble-out); }
.meta .speaker { font-weight: 600; }
.participant-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  align-items: center;
}
.participant {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  background: rgba(15, 23, 42, 0.45);
  border: 1px solid var(--glass-border);
  color: #f1f5f9;
  max-width: 100%;
}
.participant .swatch {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: 0 0 auto;
  box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.6);
}
.participant-avatar,
.participant-avatar-initial {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  overflow: hidden;
}
.participant-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.bubble img, .bubble video { max-width: 100%; height: auto; border-radius: 10px; }
.bubble audio { width: min(420px, 100%); }
.video-message {
  position: relative;
  width: min(280px, 100%);
  aspect-ratio: 1 / 1;
  border-radius: 50%;
  overflow: hidden;
  background: #000;
  box-shadow: 0 8px 22px rgba(2, 6, 23, 0.5);
  cursor: pointer;
}
.video-message video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 50%;
}
.video-message img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.video-message-preview { cursor: default; }
.video-message .play-overlay {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background: rgba(0, 0, 0, 0.25);
  border: 0;
  padding: 0;
  cursor: pointer;
  transition: opacity 0.2s ease;
}
.video-message .play-overlay svg {
  width: 28%;
  height: 28%;
  fill: rgba(255, 255, 255, 0.95);
  filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.4));
}
.video-message.playing .play-overlay {
  opacity: 0;
  pointer-events: none;
}
.meta { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.file-meta { font-size: 0.85em; opacity: 0.7; margin-top: 4px; }
.service-msg {
  font-size: 12px;
  color: var(--muted);
  font-style: italic;
  text-align: center;
  padding: 6px 0;
  border-top: 1px dashed rgba(148, 163, 184, 0.15);
  border-bottom: 1px dashed rgba(148, 163, 184, 0.15);
  margin: 4px 0;
}
.service-msg a { color: var(--ink); font-style: normal; text-decoration: underline; }
.service-msg .service-emoji { opacity: 0.8; margin-right: 4px; }
.forwarded {
  padding-left: 8px;
  border-left: 2px solid var(--accent);
  color: #bae6fd;
}
.via-bot {
  font-size: 11px;
  color: var(--muted);
  font-style: italic;
  padding-left: 8px;
  margin-top: 2px;
}
.via-bot a { color: var(--accent); font-style: normal; }
.reply-quote .reply-via { color: var(--muted); font-weight: 400; }
.reply-quote {
  display: block;
  padding: 6px 10px;
  margin: 0 0 8px;
  border-left: 2px solid var(--accent);
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.35);
  font-size: 12px;
  color: var(--muted);
  text-decoration: none;
  max-width: 100%;
}
.reply-quote a, .reply-quote a:hover {
  color: inherit;
  text-decoration: none;
  display: block;
}
.reply-quote:hover { background: rgba(15, 23, 42, 0.55); }
.reply-quote .reply-author { font-weight: 600; color: var(--ink); }
.reply-quote .reply-forwarded { color: var(--muted); font-weight: 400; }
.reply-quote .reply-snippet { display: block; margin-top: 2px; }
.reply-quote .reply-emoji { margin-right: 4px; }
.reply-quote.unavailable { opacity: 0.6; font-style: italic; }
.time-anchor { color: inherit; text-decoration: none; }
.time-anchor:hover { text-decoration: underline; }
.mono { font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace; }
a { color: var(--accent); text-decoration: none; word-break: break-word; overflow-wrap: anywhere; }
a:hover { text-decoration: underline; }
.back-top {
  position: fixed;
  right: 18px;
  bottom: 18px;
  z-index: 5;
  padding: 10px 14px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--glass-border);
  background: var(--glass-strong);
  color: var(--ink);
  cursor: pointer;
  box-shadow: 0 12px 28px rgba(2, 6, 23, 0.45);
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.back-top.show { opacity: 1; transform: translateY(0); }
.poll {
  margin: 6px 0 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid var(--glass-border);
}
.poll-question { font-weight: 600; margin-bottom: 6px; }
.poll-meta { font-size: 11px; color: var(--muted); margin-bottom: 8px; }
.poll-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  margin: 3px 0;
  border-radius: 6px;
  background: rgba(56, 189, 248, 0.08);
  font-size: 13px;
  flex-wrap: wrap;
}
.poll-option.correct { background: rgba(34, 197, 94, 0.18); }
.poll-option.selected { border: 1px solid var(--accent); }
.poll-option .text { flex: 1 1 auto; min-width: 0; }
.poll-option .count {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
}
.poll-option .recent {
  font-size: 10px;
  color: var(--muted);
  font-style: italic;
  flex-basis: 100%;
}
.poll-solution {
  margin-top: 6px;
  padding: 6px 8px;
  border-left: 2px solid #22c55e;
  font-size: 12px;
  color: #bbf7d0;
}
.link-preview {
  margin: 6px 0 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid var(--glass-border);
  max-width: 520px;
}
.link-preview-site {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin-bottom: 4px;
}
.link-preview-title {
  display: block;
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 4px;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.link-preview-meta {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}
.link-preview-desc {
  font-size: 13px;
  color: var(--ink);
  margin-top: 4px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.link-preview img, .link-preview video { max-width: 100%; height: auto; border-radius: 6px; margin-top: 6px; display: block; }
.contact-card {
  margin: 6px 0 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid var(--glass-border);
  max-width: 520px;
}
.contact-card .contact-name { font-weight: 600; }
.contact-card .contact-meta { font-size: 12px; color: var(--muted); margin-top: 4px; }
.game-card {
  margin: 6px 0 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid var(--glass-border);
  max-width: 520px;
}
.game-card .game-title { font-weight: 600; font-size: 14px; }
.game-card .game-desc { font-size: 13px; color: var(--ink); margin-top: 4px; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }
.game-card img { max-width: 100%; height: auto; border-radius: 6px; margin-top: 6px; display: block; }
.invoice-card {
  margin: 6px 0 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid var(--glass-border);
  max-width: 520px;
}
.invoice-card .invoice-title { font-weight: 600; font-size: 14px; }
.invoice-card .invoice-amount { font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 13px; margin-top: 4px; }
.invoice-card .invoice-desc { font-size: 13px; color: var(--ink); margin-top: 4px; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }
.invoice-card img { max-width: 100%; height: auto; border-radius: 6px; margin-top: 6px; display: block; }
.giveaway-card {
  margin: 6px 0 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid var(--glass-border);
  max-width: 520px;
}
.giveaway-card .giveaway-title { font-weight: 600; }
.giveaway-card .giveaway-meta { font-size: 12px; color: var(--muted); margin-top: 4px; }
.map-pin {
  margin: 6px 0 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid var(--glass-border);
  max-width: 520px;
}
.map-pin .map-venue { font-weight: 600; }
.map-pin .map-address { font-size: 13px; color: var(--ink); margin-top: 4px; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }
.map-pin .map-meta { font-size: 12px; color: var(--muted); margin-top: 4px; }
.todo-card {
  margin: 6px 0 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid var(--glass-border);
  max-width: 520px;
}
.todo-card .todo-header { font-weight: 600; }
.todo-card .todo-flags { font-size: 11px; color: var(--muted); font-style: italic; margin-top: 2px; }
.todo-card .todo-progress { font-size: 12px; color: var(--muted); margin-top: 4px; }
.todo-card ul.todo-items { list-style: none; padding-left: 0; margin: 6px 0 0; }
.todo-card ul.todo-items li { padding: 3px 0; display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; }
.todo-card ul.todo-items li .completion { font-size: 11px; color: var(--muted); }
.todo-card input[type="checkbox"][disabled] { margin: 0; }
"""


@dataclass(frozen=True)
class ParticipantEntry:
    """One participant shown in the HTML header legend."""

    peer_id: Optional[int]
    name: str
    color: str
    photo_path: Optional[str] = None


@dataclass(frozen=True)
class HtmlStats:
    """Computed stats for HTML output."""

    message_count: int
    start_iso: Optional[str]
    end_iso: Optional[str]
    participants: tuple[ParticipantEntry, ...]
    exported_at: str


@dataclass(frozen=True)
class RenderOptions:
    """Optional rendering preferences."""

    peer_map: Optional[dict[int, PeerInfo]] = None
    show_direction: bool = False
    tz: Optional[object] = None
    peer_photo_paths: Optional[dict[int, str]] = None


def _copy_single_attachment(
    attachment: Attachment,
    media_dir: Path,
    target_dir: Path,
    copied_keys: set[str],
) -> tuple[Attachment, int]:
    """Copy a single attachment's file into ``target_dir`` and return a replaced copy.

    The returned attachment has ``selected_cache_key`` and ``exported_path``
    set. ``copied_count`` is incremented when a new file is written.

    The resolver tries the main file first (``cache_key`` /
    ``alternate_cache_keys`` / ``source_path``). If none of those are
    present on disk, the attachment's :attr:`Attachment.preview_image`
    or :attr:`Attachment.preview_video` is promoted to be the rendered
    file. The returned attachment then carries the preview's mime type
    and dimensions so the HTML/Markdown renderers don't try to load a
    JPEG as a video, and :attr:`Attachment.is_preview_fallback` is set
    so the description can add a "preview" suffix.

    Webpages are an exception: their ``preview_image`` and
    ``preview_video`` are sub-attachments rendered inside the
    link-preview card, not fallbacks for a missing main file. Promoting
    them here would clear the preview fields and leave the card empty,
    so the recursion in :func:`copy_message_media` is left to copy the
    sub-attachments directly.

    Contacts with ``vcard_data`` get a synthetic ``.vcf`` file written
    into ``target_dir`` and the resulting relative path exposed on
    ``attachment.vcard_path``. The raw ``vcard_data`` is cleared so it
    doesn't leak into the CSV/HTML output.
    """
    main_attachment, main_count = _copy_to_target(
        attachment, media_dir, target_dir, copied_keys
    )
    if main_count > 0 or main_attachment.exported_path:
        logger.debug(
            "attachment kind=%s: main file copied (path=%s)",
            attachment.kind,
            main_attachment.exported_path,
        )
        return main_attachment, main_count
    if attachment.kind == "webpage":
        logger.debug("attachment kind=webpage: skipping preview promotion")
        return attachment, 0
    if attachment.preview_image is not None:
        logger.debug(
            "attachment kind=%s: trying preview_image (key=%s)",
            attachment.kind,
            attachment.preview_image.cache_key,
        )
        promoted, added = _promote_preview(
            attachment,
            attachment.preview_image,
            media_dir,
            target_dir,
            copied_keys,
        )
        if added > 0 or promoted.exported_path:
            return promoted, added
    if attachment.preview_video is not None:
        logger.debug(
            "attachment kind=%s: trying preview_video (key=%s)",
            attachment.kind,
            attachment.preview_video.cache_key,
        )
        promoted, added = _promote_preview(
            attachment,
            attachment.preview_video,
            media_dir,
            target_dir,
            copied_keys,
        )
        if added > 0 or promoted.exported_path:
            return promoted, added
    logger.debug(
        "attachment kind=%s: no file copied (main key=%s, "
        "preview_image=%s, preview_video=%s)",
        attachment.kind,
        attachment.cache_key,
        attachment.preview_image.cache_key if attachment.preview_image else None,
        attachment.preview_video.cache_key if attachment.preview_video else None,
    )
    if attachment.vcard_data is not None:
        written, vcard_count = _write_vcard(attachment, target_dir, copied_keys)
        if vcard_count > 0:
            return written, vcard_count
    return attachment, 0


def _write_vcard(
    attachment: Attachment,
    target_dir: Path,
    copied_keys: set[str],
) -> tuple[Attachment, int]:
    """Write a contact's ``vcard_data`` to a ``.vcf`` file in ``target_dir``.

    Returns the updated attachment (with ``vcard_path`` set and
    ``vcard_data`` cleared) and the number of files written. The
    filename is derived from a stable hash of the vCard contents so
    the same contact across multiple messages shares one file.
    """
    data = attachment.vcard_data
    if not isinstance(data, str) or not data:
        return attachment, 0
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"vcard-{persistent_hash32(data)}.vcf"
    target = target_dir / name
    copied = 0
    if name not in copied_keys:
        target.write_text(data, encoding="utf-8")
        copied_keys.add(name)
        copied = 1
    rel_path = (Path(target_dir.name) / name).as_posix()
    return (
        replace(attachment, vcard_path=rel_path, vcard_data=None),
        copied,
    )


def _copy_to_target(
    attachment: Attachment,
    media_dir: Path,
    target_dir: Path,
    copied_keys: set[str],
) -> tuple[Attachment, int]:
    """Try to copy the attachment's own main file. Returns (attachment, added)."""
    cache_keys = tuple(
        key
        for key in (
            attachment.cache_key,
            *attachment.alternate_cache_keys,
        )
        if key
    )
    direct_source = (
        Path(attachment.source_path).expanduser() if attachment.source_path else None
    )
    if not cache_keys and (direct_source is None or not direct_source.is_file()):
        return attachment, 0
    on_disk = {key: (media_dir / key).is_file() for key in cache_keys}
    logger.debug(
        "_copy_to_target kind=%s mime=%s cache_keys=%s on_disk=%s direct_source=%s",
        attachment.kind,
        attachment.mime_type,
        cache_keys,
        on_disk,
        direct_source,
    )
    selected_key = next(
        (key for key in cache_keys if (media_dir / key).is_file()),
        None,
    )
    if selected_key is not None:
        source = media_dir / selected_key
        target_name = selected_key
    elif direct_source is not None and direct_source.is_file():
        source = direct_source
        target_name = attachment.cache_key or direct_source.name or "local-attachment"
    else:
        return attachment, 0
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / target_name
    copied_count = 0
    if target_name not in copied_keys:
        shutil.copy2(source, target)
        copied_keys.add(target_name)
        copied_count = 1
    new_width = attachment.width
    new_height = attachment.height
    if selected_key is not None and selected_key != attachment.cache_key:
        dims = attachment.alternate_dimensions.get(selected_key)
        if dims is not None:
            alt_width, alt_height = dims
            if isinstance(alt_width, int):
                new_width = alt_width
            if isinstance(alt_height, int):
                new_height = alt_height
    return (
        replace(
            attachment,
            selected_cache_key=(
                selected_key
                if selected_key is not None
                else attachment.selected_cache_key
            ),
            exported_path=(Path(target_dir.name) / target_name).as_posix(),
            width=new_width,
            height=new_height,
        ),
        copied_count,
    )


def _promote_preview(
    parent: Attachment,
    preview: Attachment,
    media_dir: Path,
    target_dir: Path,
    copied_keys: set[str],
) -> tuple[Attachment, int]:
    """Use a ``preview_image`` / ``preview_video`` as the rendered file.

    Returns a new top-level :class:`Attachment` that keeps the parent's
    description context (kind, filename, size, sticker_emoji, url) but
    carries the preview's mime, dimensions, cache key, and
    ``exported_path``. ``is_preview_fallback`` is set so the renderer
    and description pick up the "preview" suffix.
    """
    copied_preview, added = _copy_to_target(preview, media_dir, target_dir, copied_keys)
    if not copied_preview.exported_path:
        logger.debug(
            "_promote_preview: preview key=%s not on disk, no fallback",
            preview.cache_key,
        )
        return parent, 0
    logger.debug(
        "_promote_preview: promoted parent kind=%s to preview key=%s mime=%s",
        parent.kind,
        copied_preview.cache_key,
        copied_preview.mime_type,
    )
    promoted = replace(
        parent,
        cache_key=copied_preview.cache_key,
        alternate_cache_keys=(),
        selected_cache_key=copied_preview.selected_cache_key
        or copied_preview.cache_key,
        mime_type=copied_preview.mime_type or parent.mime_type,
        width=copied_preview.width
        if copied_preview.width is not None
        else parent.width,
        height=copied_preview.height
        if copied_preview.height is not None
        else parent.height,
        preview_image=None,
        preview_video=None,
        is_preview_fallback=True,
    )
    return replace(promoted, exported_path=copied_preview.exported_path), added


def copy_message_media(
    messages: list[Message],
    media_dir: Path,
    out_path: Path,
) -> tuple[list[Message], int]:
    """Copy referenced cached media and attach relative export paths."""
    target_dir = out_path.parent / f"{out_path.stem}_media"
    copied_keys: set[str] = set()
    copied_count = 0
    updated_messages: list[Message] = []
    for message in messages:
        updated_attachments: list[Attachment] = []
        for attachment in message.attachments:
            copied_attachment, added = _copy_single_attachment(
                attachment, media_dir, target_dir, copied_keys
            )
            copied_count += added
            if (
                copied_attachment is attachment
                and attachment.preview_image is None
                and attachment.preview_video is None
            ):
                updated_attachments.append(attachment)
                continue
            nested_replaced: Optional[Attachment] = copied_attachment
            if nested_replaced.preview_image is not None:
                new_image, img_added = _copy_single_attachment(
                    nested_replaced.preview_image,
                    media_dir,
                    target_dir,
                    copied_keys,
                )
                copied_count += img_added
                if new_image is not nested_replaced.preview_image:
                    nested_replaced = replace(nested_replaced, preview_image=new_image)
            if nested_replaced.preview_video is not None:
                new_video, vid_added = _copy_single_attachment(
                    nested_replaced.preview_video,
                    media_dir,
                    target_dir,
                    copied_keys,
                )
                copied_count += vid_added
                if new_video is not nested_replaced.preview_video:
                    nested_replaced = replace(nested_replaced, preview_video=new_video)
            updated_attachments.append(nested_replaced)
        updated_messages.append(
            replace(message, attachments=tuple(updated_attachments))
        )
    return updated_messages, copied_count


def copy_peer_photos(
    peer_map: Optional[dict[int, PeerInfo]],
    media_dir: Path,
    out_path: Path,
) -> dict[int, str]:
    """Copy peer profile photos into the export's ``_media`` directory.

    Returns a ``{peer_id: relative_path}`` map for peers whose photo file
    exists in ``media_dir`` and was successfully copied. Peers with no
    ``photo_cache_key`` or with a key that doesn't resolve to a local file
    are silently skipped (the caller will render an initial-letter
    placeholder for them).
    """
    if not peer_map:
        return {}
    target_dir = out_path.parent / f"{out_path.stem}_media"
    copied_keys: set[str] = set()
    paths: dict[int, str] = {}
    for peer_id, info in peer_map.items():
        cache_key = info.photo_cache_key
        if not cache_key:
            continue
        source = media_dir / cache_key
        if not source.is_file():
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / cache_key
        if cache_key not in copied_keys:
            shutil.copy2(source, target)
            copied_keys.add(cache_key)
        paths[peer_id] = (Path(target_dir.name) / cache_key).as_posix()
    return paths


def _peer_initial(name: str) -> str:
    """Return a single uppercase letter for avatar placeholders."""
    for ch in name:
        if not ch.isspace():
            return ch.upper()
    return "?"


def _render_avatar_html(
    handle,
    photo_path: Optional[str],
    initial: str,
    color: str,
    css_class: str = "avatar",
) -> None:
    """Write an avatar cell: ``<img>`` if a photo is available, else initial."""
    safe_class = html.escape(css_class, quote=True)
    if photo_path:
        safe_src = html.escape(photo_path, quote=True)
        handle.write(
            f'<div class="{safe_class}">'
            f'<img loading="lazy" decoding="async" src="{safe_src}" alt="">'
            f"</div>"
        )
    else:
        safe_color = html.escape(color, quote=True)
        safe_initial = html.escape(initial, quote=True)
        handle.write(
            f'<div class="{safe_class} {safe_class}-initial" '
            f'style="background:{safe_color}">{safe_initial}</div>'
        )


def resolve_speaker(msg: Message, peer_map: Optional[dict[int, PeerInfo]]) -> str:
    """Resolve display name for a message."""
    if peer_map:
        if msg.author_id and msg.author_id in peer_map:
            return peer_map[msg.author_id].name
        if msg.outgoing is not True and msg.peer_id and msg.peer_id in peer_map:
            return peer_map[msg.peer_id].name
    return "Unknown"


def _speaker_url(
    msg: Message, peer_map: Optional[dict[int, PeerInfo]]
) -> Optional[str]:
    """Return a t.me URL for a message's speaker, if the peer is linkable."""
    if not peer_map:
        return None
    if msg.author_id is not None and msg.author_id in peer_map:
        return peer_url(peer_map[msg.author_id], msg.author_id)
    if msg.outgoing is not True and msg.peer_id and msg.peer_id in peer_map:
        return peer_url(peer_map[msg.peer_id], msg.peer_id)
    return None


def _resolve_peer_name(
    peer_id: Optional[int],
    peer_map: Optional[dict[int, PeerInfo]],
) -> Optional[str]:
    if peer_id is None or not peer_map:
        return None
    info = peer_map.get(peer_id)
    return info.name if info is not None else None


def _html_link(text: str, url: Optional[str]) -> str:
    """Render ``text`` as an ``<a>`` tag when ``url`` is set, else HTML-escaped text."""
    if url is None:
        return html.escape(text)
    safe_text = html.escape(text)
    safe_url = html.escape(url, quote=True)
    return f'<a href="{safe_url}" target="_blank" rel="noopener">{safe_text}</a>'


def _md_link(text: str, url: Optional[str]) -> str:
    """Render ``text`` as a Markdown link when ``url`` is set, else plain text."""
    if url is None:
        return text
    return f"[{text}]({url})"


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _render_forwarded_segment_html(handle, seg: ForwardedSegment) -> None:
    """Write one forwarded-line segment to ``handle``, preserving JS-side date localization.

    Date segments are wrapped in a ``<time>`` element so the page's JS can
    localize the text to the viewer's browser timezone. The ``<a>`` wraps
    the ``<time>`` (not the other way around) so the JS-side ``textContent``
    replacement on the ``<time>`` does not destroy the link.
    """
    if _ISO_DATE_RE.match(seg.text):
        safe_iso = html.escape(seg.text)
        time_el = (
            f'<time class="local-datetime" datetime="{safe_iso}">{safe_iso}</time>'
        )
        if seg.url is None:
            handle.write(time_el)
        else:
            safe_url = html.escape(seg.url, quote=True)
            handle.write(
                f'<a href="{safe_url}" target="_blank" rel="noopener">{time_el}</a>'
            )
    else:
        handle.write(_html_link(seg.text, seg.url))


def build_forwarded_segments(
    msg: Message,
    peer_map: Optional[dict[int, PeerInfo]],
    tz: Optional[object] = None,
) -> list[ForwardedSegment]:
    """Return structured segments for the 'Forwarded from ...' line."""
    info = msg.forward_info
    if info is None:
        return []

    source = (
        peer_map.get(info.source_id)
        if info.source_id is not None and peer_map
        else None
    )
    author = (
        peer_map.get(info.author_id)
        if info.author_id is not None and peer_map
        else None
    )

    if source is not None:
        display_name = source.name
        display_peer = source
        display_peer_id = info.source_id
    elif author is not None:
        display_name = author.name
        display_peer = author
        display_peer_id = info.author_id
    elif info.author_signature:
        display_name = info.author_signature
        display_peer = None
        display_peer_id = None
    else:
        fallback_id = info.source_id or info.author_id
        if fallback_id is None:
            return []
        display_name = f"peer {fallback_id}"
        display_peer = None
        display_peer_id = None

    name_url = (
        peer_url(display_peer, display_peer_id)
        if display_peer is not None and display_peer_id is not None
        else None
    )

    date_url = None
    if (
        source is not None
        and info.source_id is not None
        and info.source_message_id is not None
    ):
        date_url = peer_url(source, info.source_id, message_id=info.source_message_id)

    segments: list[ForwardedSegment] = [
        ForwardedSegment("Forwarded from "),
        ForwardedSegment(display_name, name_url),
    ]
    if info.date is not None:
        local_date = to_local(info.date, tz) if tz else info.date
        segments.append(ForwardedSegment(" | "))
        segments.append(ForwardedSegment(local_date.isoformat(), date_url))
    if info.psa_type:
        segments.append(ForwardedSegment(f" | PSA: {info.psa_type}"))
    if info.is_imported:
        segments.append(ForwardedSegment(" | imported"))
    return segments


def _via_bot_segments(
    msg: Message,
    peer_map: Optional[dict[int, PeerInfo]],
) -> list[ForwardedSegment]:
    """Return structured segments for the 'via @Bot' line.

    Populated from :attr:`Message.via_bot_id` /
    :attr:`Message.via_bot_title`, both of which are derived from the
    ``InlineBotMessageAttribute`` / ``InlineBusinessBotMessageAttribute``
    attribute on the source message.

    Returns an empty list when the message has no inline-bot attribute.
    When the bot peer id is resolvable through ``peer_map`` the bot's
    display name is taken from there and a ``t.me/<username>`` link is
    used when the bot has a public username; otherwise the raw
    ``via_bot_title`` from the attribute is rendered as plain text
    (covers the secret-chat case where only the name is stored).

    The shape matches :func:`build_forwarded_segments` so the caller
    can use the same renderer for the line.
    """
    bot_id = msg.via_bot_id
    bot_title = msg.via_bot_title
    if bot_id is None and not bot_title:
        return []

    display_peer: Optional[PeerInfo] = None
    display_peer_id: Optional[int] = None
    if bot_id is not None and peer_map:
        display_peer = peer_map.get(bot_id)
        display_peer_id = bot_id
    if display_peer is not None:
        display_name = display_peer.name
    elif bot_title:
        display_name = bot_title
    else:
        display_name = f"bot {bot_id}"

    name_url = (
        peer_url(display_peer, display_peer_id)
        if display_peer is not None and display_peer_id is not None
        else None
    )

    segments: list[ForwardedSegment] = [
        ForwardedSegment("via "),
        ForwardedSegment(display_name, name_url),
    ]
    return segments


def _attachment_label(attachment: Attachment) -> str:
    return attachment.filename or attachment.kind.replace("_", " ").title()


def _attachment_description(attachment: Attachment) -> str:
    details = [attachment.kind]
    if attachment.filename:
        details.append(attachment.filename)
    if attachment.mime_type:
        details.append(attachment.mime_type)
    if (
        isinstance(attachment.width, int)
        and isinstance(attachment.height, int)
        and attachment.width > 0
        and attachment.height > 0
    ):
        details.append(f"{attachment.width}×{attachment.height}")
    if isinstance(attachment.size, int) and attachment.size >= 0:
        details.append(_human_size(attachment.size))
    if attachment.kind == "sticker" and attachment.sticker_emoji:
        details.append(attachment.sticker_emoji)
    cache_key = attachment.selected_cache_key or attachment.cache_key
    if cache_key:
        details.append(f"cache: {cache_key}")
    if attachment.is_preview_fallback:
        details.append("preview")
    return " | ".join(details)


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
    """Format a duration in seconds as ``M:SS`` or ``H:MM:SS`` (no leading-zero hours)."""
    total = max(int(seconds), 0)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


_REPLY_KIND_EMOJI: dict[str, str] = {
    "image": "📷",
    "video": "🎬",
    "video_message": "🎥",
    "voice": "🎤",
    "audio": "🎵",
    "sticker": "💬",
    "file": "📎",
    "webpage": "🔗",
    "poll": "📊",
    "action": "ℹ️",
}


def _reply_emoji_for_kind(kind: Optional[str]) -> Optional[str]:
    """Return the emoji glyph used in the reply preview for a given media kind."""
    if not kind:
        return None
    return _REPLY_KIND_EMOJI.get(kind)


def _reply_kind_label(kind: Optional[str]) -> str:
    """Return the Title-Cased kind word used in the reply preview label."""
    if not kind:
        return "Message"
    overrides = {
        "video_message": "Video message",
        "image": "Photo",
        "voice": "Voice",
        "audio": "Audio",
        "sticker": "Sticker",
        "file": "File",
        "webpage": "Link",
        "poll": "Poll",
        "action": "Service",
    }
    return overrides.get(kind, kind.replace("_", " ").title())


def _truncate_reply_text(text: str, limit: int = 200) -> str:
    """Trim a long reply snippet/full-text to ``limit`` chars with a trailing ellipsis."""
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _format_reply_meta(reply: ReplyInfo) -> str:
    """Compose the ``· filename · size · duration`` suffix for a reply preview.

    Skips empty parts and joins the remaining ones with ``" · "``.
    For polls the question replaces the suffix; for stickers the
    ``sticker_emoji`` is appended.
    """
    parts: list[str] = []
    kind = reply.target_attachment_kind
    if kind == "poll":
        if reply.target_attachment_meta:
            parts.append(reply.target_attachment_meta)
    else:
        if reply.target_filename:
            parts.append(reply.target_filename)
        if reply.target_attachment_meta:
            parts.append(reply.target_attachment_meta)
        if kind == "sticker" and reply.target_attachment_emoji:
            parts.append(reply.target_attachment_emoji)
    return " · ".join(parts)


def _format_phone_call_label(reason: Optional[int]) -> str:
    """Map a ``PhoneCallDiscardReason`` raw value to a human label."""
    if reason is None:
        return "ended"
    try:
        enum_value = PhoneCallDiscardReason(int(reason))
    except (TypeError, ValueError):
        return "ended"
    return {
        PhoneCallDiscardReason.MISSED: "missed",
        PhoneCallDiscardReason.DISCONNECT: "disconnected",
        PhoneCallDiscardReason.HANGUP: "ended",
        PhoneCallDiscardReason.BUSY: "busy",
    }.get(enum_value, "ended")


def _format_autoremove_period(seconds: int) -> str:
    """Render an auto-delete period as ``off`` / ``1 day`` / ``2 hours`` / ``30 minutes`` / ``45s``."""
    total = int(seconds)
    if total <= 0:
        return "off"
    if total % 86400 == 0:
        days = total // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    if total % 3600 == 0:
        hours = total // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    if total % 60 == 0:
        minutes = total // 60
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    return f"{total}s"


def _format_conference_flags(flags: int) -> dict[str, bool]:
    """Decompose the ``ConferenceCallFlags`` OptionSet into a dict of named flags."""
    raw = int(flags) if isinstance(flags, int) and not isinstance(flags, bool) else 0
    return {
        "is_video": bool(raw & ConferenceCallFlags.IS_VIDEO),
        "is_active": bool(raw & ConferenceCallFlags.IS_ACTIVE),
        "is_missed": bool(raw & ConferenceCallFlags.IS_MISSED),
    }


def _peer_label(peer_id: int, peer_map: Optional[dict[int, PeerInfo]]) -> str:
    """Return the display label for ``peer_id`` (falling back to ``peer <id>``)."""
    if peer_map is not None:
        info = peer_map.get(peer_id)
        if info is not None and info.name:
            return info.name
    return f"peer {peer_id}"


def _peer_url_for(
    peer_id: int, peer_map: Optional[dict[int, PeerInfo]]
) -> Optional[str]:
    """Return a ``t.me`` URL for ``peer_id`` when the peer is linkable."""
    if peer_map is None:
        return None
    info = peer_map.get(peer_id)
    if info is None:
        return None
    return peer_url(info, peer_id)


def _join_peer_names_html(
    peer_ids: list[int], peer_map: Optional[dict[int, PeerInfo]]
) -> str:
    """Render a peer list as ``Alice, Bob, Carol`` with each name linkified.

    Empty list returns an empty string. Renders a single comma for the
    natural-language join, applying the same comma-only style used by
    the poll recent-voter renderer.
    """
    if not peer_ids:
        return ""
    rendered: list[str] = []
    for peer_id in peer_ids:
        label = _peer_label(peer_id, peer_map)
        url = _peer_url_for(peer_id, peer_map)
        rendered.append(_html_link(label, url))
    return ", ".join(rendered)


def _join_peer_names_md(
    peer_ids: list[int], peer_map: Optional[dict[int, PeerInfo]]
) -> str:
    """Same as :func:`_join_peer_names_html` but produces Markdown links."""
    if not peer_ids:
        return ""
    rendered: list[str] = []
    for peer_id in peer_ids:
        label = _peer_label(peer_id, peer_map)
        url = _peer_url_for(peer_id, peer_map)
        rendered.append(_md_link(label, url))
    return ", ".join(rendered)


def _decode_payload_peer_ids(payload: dict) -> list[int]:
    """Return the list of peer ids referenced by a service-action payload.

    Handles both formats Telegram uses: the ``bytes``-encoded PeerId
    buffer (used by ``addedMembers`` / ``removedMembers``) and the plain
    ``INT64_ARRAY`` (used by ``inviteToGroupPhoneCall``).
    """
    raw_bytes = payload.get("peerIds")
    if isinstance(raw_bytes, (bytes, bytearray)):
        decoded = _decode_peer_ids_from_buffer(raw_bytes)
        if decoded:
            return decoded
    raw_array = payload.get("peerIds")
    if isinstance(raw_array, list):
        return [
            int(x) for x in raw_array if isinstance(x, int) and not isinstance(x, bool)
        ]
    single = payload.get("peerId")
    if isinstance(single, int) and not isinstance(single, bool) and single != 0:
        return [single]
    return []


def _build_action_summary(
    metadata: dict,
    peer_map: Optional[dict[int, PeerInfo]] = None,
    author_id: Optional[int] = None,
) -> str:
    """Build a human-readable summary string for a service-action attachment.

    ``metadata`` is the dict stored on the :class:`Attachment` by
    :func:`telegram_message_exporter.postbox._action_attachment`.
    ``author_id`` is the message author's peer id, used to resolve the
    joiner name for ``peerJoined`` / ``joinedByRequest`` / ``joinedByLink``.
    """
    raw_type = metadata.get("raw_type")
    payload = metadata.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    if raw_type == TelegramMediaActionType.GROUP_CREATED:
        return _summary_group_created(payload)
    if raw_type == TelegramMediaActionType.ADDED_MEMBERS:
        return _summary_added_members(payload, peer_map)
    if raw_type == TelegramMediaActionType.REMOVED_MEMBERS:
        return _summary_removed_members(payload, peer_map)
    if raw_type == TelegramMediaActionType.TITLE_UPDATED:
        return _summary_title_updated(payload)
    if raw_type == TelegramMediaActionType.PINNED_MESSAGE_UPDATED:
        return "Pinned a message"
    if raw_type == TelegramMediaActionType.JOINED_BY_LINK:
        return _summary_joined_by_link(payload, peer_map, author_id)
    if raw_type == TelegramMediaActionType.MESSAGE_AUTOREMOVE_TIMEOUT_UPDATED:
        return _summary_autoremove(payload, peer_map)
    if raw_type == TelegramMediaActionType.PHONE_CALL:
        return _summary_phone_call(payload)
    if raw_type == TelegramMediaActionType.CUSTOM_TEXT:
        return _summary_custom_text(payload)
    if raw_type == TelegramMediaActionType.PEER_JOINED:
        return _summary_peer_joined(peer_map, author_id)
    if raw_type == TelegramMediaActionType.GROUP_PHONE_CALL:
        return _summary_group_phone_call(payload)
    if raw_type == TelegramMediaActionType.INVITE_TO_GROUP_PHONE_CALL:
        return _summary_invite_to_group_call(payload, peer_map)
    if raw_type == TelegramMediaActionType.SET_CHAT_THEME:
        return _summary_set_chat_theme(metadata, payload)
    if raw_type == TelegramMediaActionType.JOINED_BY_REQUEST:
        return _summary_joined_by_request(peer_map, author_id)
    if raw_type == TelegramMediaActionType.CONFERENCE_CALL:
        return _summary_conference_call(payload, peer_map)
    if raw_type == TelegramMediaActionType.PHOTO_UPDATED:
        return _summary_photo_updated(payload)
    if raw_type == TelegramMediaActionType.CHANNEL_MIGRATED_FROM_GROUP:
        return _summary_channel_migrated_from_group(payload)
    if raw_type == TelegramMediaActionType.GROUP_MIGRATED_TO_CHANNEL:
        return _summary_group_migrated_to_channel(payload)
    if raw_type == TelegramMediaActionType.HISTORY_CLEARED:
        return "Chat history was cleared"
    if raw_type == TelegramMediaActionType.HISTORY_SCREENSHOT:
        return "Took a screenshot of the chat history"
    if raw_type == TelegramMediaActionType.GAME_SCORE:
        return _summary_game_score(payload)
    if raw_type == TelegramMediaActionType.PAYMENT_SENT:
        return _summary_payment_sent(payload)
    if raw_type == TelegramMediaActionType.BOT_DOMAIN_ACCESS_GRANTED:
        return _summary_bot_domain_access(payload)
    if raw_type == TelegramMediaActionType.BOT_SENT_SECURE_VALUES:
        return _summary_bot_sent_secure_values(payload)
    if raw_type == TelegramMediaActionType.PHONE_NUMBER_REQUEST:
        return "Requested the user's phone number"
    if raw_type == TelegramMediaActionType.GEO_PROXIMITY_REACHED:
        return _summary_geo_proximity(payload, peer_map, author_id)
    if raw_type == TelegramMediaActionType.WEB_VIEW_DATA:
        return "Sent data from a web view"
    if raw_type == TelegramMediaActionType.GIFT_PREMIUM:
        return _summary_gift_premium(payload)
    if raw_type == TelegramMediaActionType.TOPIC_CREATED:
        return _summary_topic_created(payload)
    if raw_type == TelegramMediaActionType.TOPIC_EDITED:
        return _summary_topic_edited(payload)
    if raw_type == TelegramMediaActionType.SUGGESTED_PROFILE_PHOTO:
        return "Suggested a profile photo"
    if raw_type == TelegramMediaActionType.ATTACH_MENU_BOT_ALLOWED:
        return "Allowed a bot to attach to the menu"
    if raw_type == TelegramMediaActionType.REQUESTED_PEER:
        return _summary_requested_peer(payload, peer_map)
    if raw_type == TelegramMediaActionType.SET_CHAT_WALLPAPER:
        # TODO(Step 7b): decode the wallpaper PostboxObject (TelegramWallpaper
        # enum: .builtin/.color/.gradient/.image/.file/.emoticon) and surface
        # the color / emoticon / image kind. The Postbox field is a JSON-
        # encoded TelegramWallpaperNativeCodable wrapper; decoding it here
        # is non-trivial so it's deferred.
        return "Set a chat wallpaper"
    if raw_type == TelegramMediaActionType.SET_SAME_CHAT_WALLPAPER:
        # TODO(Step 7b): same as SET_CHAT_WALLPAPER above.
        return "Set a chat wallpaper for both sides"
    if raw_type == TelegramMediaActionType.BOT_APP_ACCESS_GRANTED:
        return _summary_bot_app_access(payload)
    if raw_type == TelegramMediaActionType.GIFT_CODE:
        return _summary_gift_code(payload)
    if raw_type == TelegramMediaActionType.GIVEAWAY_LAUNCHED:
        return _summary_giveaway_launched(payload)
    if raw_type == TelegramMediaActionType.JOINED_CHANNEL:
        return "Joined the channel"
    if raw_type == TelegramMediaActionType.GIVEAWAY_RESULTS:
        return _summary_giveaway_results(payload)
    if raw_type == TelegramMediaActionType.BOOSTS_APPLIED:
        return _summary_boosts_applied(payload)
    if raw_type == TelegramMediaActionType.PAYMENT_REFUNDED:
        return _summary_payment_refunded(payload, peer_map)
    if raw_type == TelegramMediaActionType.GIFT_STARS:
        return _summary_gift_stars(payload)
    if raw_type == TelegramMediaActionType.PRIZE_STARS:
        return _summary_prize_stars(payload)
    if raw_type == TelegramMediaActionType.STAR_GIFT:
        return _summary_star_gift(payload, peer_map)
    if raw_type == TelegramMediaActionType.STAR_GIFT_UNIQUE:
        return _summary_star_gift_unique(payload, peer_map)
    if raw_type == TelegramMediaActionType.PAID_MESSAGES_REFUNDED:
        return _summary_paid_messages_refunded(payload)
    if raw_type == TelegramMediaActionType.PAID_MESSAGES_PRICE_EDITED:
        return _summary_paid_messages_price_edited(payload)
    if raw_type == TelegramMediaActionType.TODO_COMPLETIONS:
        return _summary_todo_completions(payload)
    if raw_type == TelegramMediaActionType.TODO_APPEND_TASKS:
        return _summary_todo_append_tasks(payload)
    if raw_type == TelegramMediaActionType.SUGGESTED_POST_APPROVAL_STATUS:
        return _summary_suggested_post_approval(payload)
    if raw_type == TelegramMediaActionType.GIFT_TON:
        return _summary_gift_ton(payload)
    if raw_type == TelegramMediaActionType.SUGGESTED_POST_SUCCESS:
        return _summary_suggested_post_success(payload)
    if raw_type == TelegramMediaActionType.SUGGESTED_POST_REFUND:
        return _summary_suggested_post_refund(payload)
    if raw_type == TelegramMediaActionType.SUGGESTED_BIRTHDAY:
        return _summary_suggested_birthday(payload)
    if raw_type == TelegramMediaActionType.STAR_GIFT_PURCHASE_OFFER:
        return _summary_star_gift_purchase_offer(payload)
    if raw_type == TelegramMediaActionType.STAR_GIFT_PURCHASE_OFFER_DECLINED:
        return _summary_star_gift_purchase_offer_declined(payload)
    if raw_type == TelegramMediaActionType.GROUP_CREATOR_CHANGE:
        return _summary_group_creator_change(payload, peer_map)
    if raw_type == TelegramMediaActionType.COPY_PROTECTION_TOGGLE:
        return _summary_copy_protection_toggle(payload)
    if raw_type == TelegramMediaActionType.COPY_PROTECTION_REQUEST:
        return _summary_copy_protection_request(payload)
    if raw_type == TelegramMediaActionType.MANAGED_BOT_CREATED:
        return _summary_managed_bot_created(payload, peer_map)
    if raw_type == TelegramMediaActionType.POLL_OPTION_APPENDED:
        return _summary_poll_option_appended(payload)
    if raw_type == TelegramMediaActionType.POLL_OPTION_DELETED:
        return _summary_poll_option_deleted(payload)
    fallback = metadata.get("summary")
    if isinstance(fallback, str):
        return fallback
    return ""


def _summary_group_created(payload: dict) -> str:
    title = payload.get("title")
    if isinstance(title, str) and title:
        return f"Group {title} was created"
    return "Group was created"


def _summary_added_members(
    payload: dict, peer_map: Optional[dict[int, PeerInfo]]
) -> str:
    names = _join_peer_names_md(_decode_payload_peer_ids(payload), peer_map)
    if names:
        return f"Added {names}"
    return "Added members"


def _summary_removed_members(
    payload: dict, peer_map: Optional[dict[int, PeerInfo]]
) -> str:
    names = _join_peer_names_md(_decode_payload_peer_ids(payload), peer_map)
    if names:
        return f"Removed {names}"
    return "Removed members"


def _summary_title_updated(payload: dict) -> str:
    title = payload.get("title")
    if isinstance(title, str) and title:
        return f"Title changed to {title}"
    return "Title changed"


def _summary_joined_by_link(
    payload: dict,
    peer_map: Optional[dict[int, PeerInfo]],
    author_id: Optional[int],
) -> str:
    joiner = _join_label(peer_map, author_id)
    inviter_raw = payload.get("inviter")
    inviter_id = (
        int(inviter_raw)
        if isinstance(inviter_raw, int)
        and not isinstance(inviter_raw, bool)
        and inviter_raw != 0
        else None
    )
    base = f"{joiner} joined via invite link"
    if (
        inviter_id is not None
        and peer_map is not None
        and inviter_id != author_id
        and inviter_id in peer_map
    ):
        inviter_label = _peer_label(inviter_id, peer_map)
        return f"{base} from {inviter_label}"
    return base


def _summary_joined_by_request(
    peer_map: Optional[dict[int, PeerInfo]], author_id: Optional[int]
) -> str:
    return f"{_join_label(peer_map, author_id)} joined via request"


def _summary_peer_joined(
    peer_map: Optional[dict[int, PeerInfo]], author_id: Optional[int]
) -> str:
    return f"{_join_label(peer_map, author_id)} joined"


def _join_label(
    peer_map: Optional[dict[int, PeerInfo]], author_id: Optional[int]
) -> str:
    if author_id is None:
        return "Member"
    return _peer_label(author_id, peer_map)


def _summary_autoremove(
    payload: dict, peer_map: Optional[dict[int, PeerInfo]] = None
) -> str:
    period = payload.get("t")
    src_raw = payload.get("src")
    src_id = (
        int(src_raw)
        if isinstance(src_raw, int) and not isinstance(src_raw, bool) and src_raw != 0
        else None
    )
    base = "Auto-delete updated"
    if isinstance(period, int) and not isinstance(period, bool):
        base = f"Auto-delete: {_format_autoremove_period(period)}"
    if src_id is not None:
        base += f" (set by {_peer_label(src_id, peer_map)})"
    return base


def _summary_phone_call(payload: dict) -> str:
    is_video = bool(payload.get("vc"))
    kind = "Video call" if is_video else "Voice call"
    duration = payload.get("d")
    reason_raw = payload.get("dr")
    reason_int = (
        int(reason_raw)
        if isinstance(reason_raw, int) and not isinstance(reason_raw, bool)
        else None
    )
    reason_label = _format_phone_call_label(reason_int)
    parts: list[str] = [f"📞 {kind}"]
    if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
        parts.append(_format_duration(duration))
    parts.append(reason_label)
    return " · ".join(parts)


def _summary_custom_text(payload: dict) -> str:
    text = payload.get("text")
    if isinstance(text, str) and text:
        return text
    return "Custom text"


def _summary_set_chat_theme(metadata: dict, payload: dict) -> str:
    emoticon = metadata.get("emoticon")
    if isinstance(emoticon, str) and emoticon:
        return f"Theme: {emoticon}"
    return "Theme updated"


def _summary_group_phone_call(payload: dict) -> str:
    duration = payload.get("duration")
    schedule = payload.get("scheduleDate")
    if isinstance(schedule, int) and not isinstance(schedule, bool) and schedule > 0:
        scheduled = datetime.fromtimestamp(schedule, tz=timezone.utc)
        return f"🎙️ Voice chat scheduled for {scheduled.isoformat()}"
    if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
        return f"🎙️ Voice chat ended · {_format_duration(duration)}"
    return "🎙️ Voice chat started"


def _summary_invite_to_group_call(
    payload: dict, peer_map: Optional[dict[int, PeerInfo]]
) -> str:
    names = _join_peer_names_md(_decode_payload_peer_ids(payload), peer_map)
    if names:
        return f"Invited {names} to a voice chat"
    return "Invited members to a voice chat"


def _summary_conference_call(
    payload: dict, peer_map: Optional[dict[int, PeerInfo]]
) -> str:
    flags = _format_conference_flags(payload.get("flags", 0))
    duration = payload.get("dur")
    label = (
        "Video conference call" if flags.get("is_video") else "Voice conference call"
    )
    parts: list[str] = [f"📹 {label}"]
    if flags.get("is_missed"):
        return f"📹 Missed {label.lower()}"
    if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
        parts.append(_format_duration(duration))
    if flags.get("is_active"):
        parts.append("active")
    other = _decode_payload_peer_ids({"peerIds": payload.get("part")})
    if other:
        parts.append(f"with {_join_peer_names_md(other, peer_map)}")
    return " · ".join(parts)


def _summary_photo_updated(payload: dict) -> str:
    has_image = "image" in payload and payload.get("image") is not None
    if has_image:
        return "Updated the chat photo"
    return "Removed the chat photo"


def _summary_channel_migrated_from_group(payload: dict) -> str:
    title = payload.get("title")
    if isinstance(title, str) and title:
        return f"Group was upgraded to a supergroup: {title}"
    return "Group was upgraded to a supergroup"


def _summary_group_migrated_to_channel(payload: dict) -> str:
    return "Group was upgraded to a channel"


def _summary_game_score(payload: dict) -> str:
    score = payload.get("s")
    if isinstance(score, int) and not isinstance(score, bool):
        return f"Scored {score} points in a game"
    return "Posted a game score"


def _summary_payment_sent(payload: dict) -> str:
    amount = payload.get("ta")
    currency = payload.get("currency")
    is_recurring_init = bool(payload.get("isRecurringInit", False))
    is_recurring_used = bool(payload.get("isRecurringUsed", False))
    base = "Sent a payment"
    if (
        isinstance(amount, int)
        and not isinstance(amount, bool)
        and isinstance(currency, str)
        and currency
    ):
        if amount >= 1_000_000_000:
            base = f"Sent a Stars payment of {amount // 1_000_000_000}✦"
        else:
            base = f"Sent a payment of {amount} {currency}"
    if is_recurring_init:
        return f"{base} (recurring init)"
    if is_recurring_used:
        return f"{base} (recurring)"
    return base


def _summary_bot_domain_access(payload: dict) -> str:
    domain = payload.get("do")
    if isinstance(domain, str) and domain:
        return f"Granted a bot access to {domain}"
    return "Granted a bot access to a website"


def _summary_bot_sent_secure_values(payload: dict) -> str:
    types = payload.get("ty")
    if isinstance(types, list) and types:
        labels: list[str] = []
        for raw in types:
            label = _format_secure_value_type(raw)
            if label:
                labels.append(label)
        if labels:
            return f"Submitted Telegram Passport data: {', '.join(labels)}"
    return "Submitted Telegram Passport data"


def _format_secure_value_type(raw: object) -> Optional[str]:
    if not isinstance(raw, int) or isinstance(raw, bool):
        return None
    from .schema import SentSecureValueType

    try:
        kind = SentSecureValueType(int(raw))
    except ValueError:
        return None
    return {
        SentSecureValueType.PERSONAL_DETAILS: "personal details",
        SentSecureValueType.PASSPORT: "passport",
        SentSecureValueType.DRIVERS_LICENSE: "driver's license",
        SentSecureValueType.ID_CARD: "ID card",
        SentSecureValueType.ADDRESS: "address",
        SentSecureValueType.BANK_STATEMENT: "bank statement",
        SentSecureValueType.UTILITY_BILL: "utility bill",
        SentSecureValueType.RENTAL_AGREEMENT: "rental agreement",
        SentSecureValueType.PHONE: "phone",
        SentSecureValueType.EMAIL: "email",
        SentSecureValueType.INTERNAL_PASSPORT: "internal passport",
        SentSecureValueType.PASSPORT_REGISTRATION: "passport registration",
        SentSecureValueType.TEMPORARY_REGISTRATION: "temporary registration",
    }.get(kind)


def _summary_geo_proximity(
    payload: dict,
    peer_map: Optional[dict[int, PeerInfo]],
    author_id: Optional[int],
) -> str:
    distance = payload.get("dst")
    dist_label = (
        f"{int(distance)} m"
        if isinstance(distance, int) and not isinstance(distance, bool) and distance > 0
        else None
    )
    from_label = _join_label(peer_map, author_id)
    to_id_raw = payload.get("toId")
    to_id = (
        int(to_id_raw)
        if isinstance(to_id_raw, int) and not isinstance(to_id_raw, bool)
        else None
    )
    if to_id is not None and to_id != author_id:
        to_label = _peer_label(to_id, peer_map)
        base = f"{from_label} is nearby {to_label}"
    else:
        base = f"{from_label} is nearby"
    if dist_label:
        return f"{base} · {dist_label}"
    return base


def _summary_gift_premium(payload: dict) -> str:
    months_raw = payload.get("days")
    months = (
        int(months_raw) // 30
        if isinstance(months_raw, int) and not isinstance(months_raw, bool)
        else 0
    )
    if months > 0:
        return f"Gifted Telegram Premium ({months} months)"
    return "Gifted Telegram Premium"


def _summary_topic_created(payload: dict) -> str:
    title = payload.get("title")
    icon_color_raw = payload.get("iconColor")
    icon_file_id = payload.get("iconFileId")
    suffix_parts: list[str] = []
    if (
        isinstance(icon_color_raw, int)
        and not isinstance(icon_color_raw, bool)
        and icon_color_raw >= 0
    ):
        suffix_parts.append(f"color {int(icon_color_raw)}")
    if (
        isinstance(icon_file_id, int)
        and not isinstance(icon_file_id, bool)
        and icon_file_id != 0
    ):
        suffix_parts.append(f"icon {int(icon_file_id)}")
    suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
    if isinstance(title, str) and title:
        return f"Created forum topic: {title}{suffix}"
    return f"Created a forum topic{suffix}"


def _summary_topic_edited(payload: dict) -> str:
    components = payload.get("components")
    if isinstance(components, list) and components:
        labels: list[str] = []
        for comp in components:
            label = _format_topic_component(comp)
            if label:
                labels.append(label)
        if labels:
            return f"Edited forum topic: {', '.join(labels)}"
    return "Edited a forum topic"


def _format_topic_component(comp: object) -> Optional[str]:
    if not isinstance(comp, PostboxObject):
        return None
    fields = comp.fields
    type_raw = fields.get("_t")
    if not isinstance(type_raw, int) or isinstance(type_raw, bool):
        return None
    from .schema import ForumTopicEditComponentType

    try:
        kind = ForumTopicEditComponentType(int(type_raw))
    except ValueError:
        return None
    if kind is ForumTopicEditComponentType.TITLE:
        new_title = fields.get("title")
        if isinstance(new_title, str) and new_title:
            return f"renamed to {new_title}"
        return "renamed"
    if kind is ForumTopicEditComponentType.ICON_FILE_ID:
        return "icon changed"
    if kind is ForumTopicEditComponentType.IS_CLOSED:
        opened = not bool(fields.get("isClosed", False))
        return "reopened" if opened else "closed"
    if kind is ForumTopicEditComponentType.IS_HIDDEN:
        shown = not bool(fields.get("isHidden", False))
        return "shown" if shown else "hidden"
    return None


def _summary_requested_peer(
    payload: dict, peer_map: Optional[dict[int, PeerInfo]]
) -> str:
    button_id_raw = payload.get("b")
    button_suffix = ""
    if (
        isinstance(button_id_raw, int)
        and not isinstance(button_id_raw, bool)
        and button_id_raw > 0
    ):
        button_suffix = f" (button {int(button_id_raw)})"
    pis = payload.get("pis")
    if isinstance(pis, list) and pis:
        names = _join_peer_names_md(
            [int(x) for x in pis if isinstance(x, int) and not isinstance(x, bool)],
            peer_map,
        )
        if names:
            return f"Shared {names}{button_suffix}"
        return f"Shared {len(pis)} peer(s){button_suffix}"
    single = payload.get("pi")
    if isinstance(single, int) and not isinstance(single, bool) and single != 0:
        return f"Shared {_peer_label(int(single), peer_map)}{button_suffix}"
    return f"Shared a peer{button_suffix}"


def _summary_bot_app_access(payload: dict) -> str:
    app = payload.get("app")
    type_raw = payload.get("atp")
    type_label = _format_bot_app_access_type(type_raw)
    if isinstance(app, str) and app:
        if type_label:
            return f"Granted {app} {type_label} access"
        return f"Granted {app} access"
    if type_label:
        return f"Granted a bot {type_label} access"
    return "Granted a bot app access"


def _format_bot_app_access_type(raw: object) -> Optional[str]:
    if not isinstance(raw, int) or isinstance(raw, bool):
        return None
    from .schema import BotSendMessageAccessGrantedType

    try:
        kind = BotSendMessageAccessGrantedType(int(raw))
    except ValueError:
        return None
    return {
        BotSendMessageAccessGrantedType.ATTACH_MENU: "attach-menu",
        BotSendMessageAccessGrantedType.REQUEST: "request",
    }.get(kind)


def _summary_gift_code(payload: dict) -> str:
    months = payload.get("months")
    if isinstance(months, int) and not isinstance(months, bool) and months > 0:
        unclaimed = bool(payload.get("unclaimed", False))
        if unclaimed:
            return f"Posted an unclaimed Premium gift code ({months} months)"
        return f"Gifted Telegram Premium ({months} months)"
    return "Posted a gift code"


def _summary_giveaway_launched(payload: dict) -> str:
    stars = payload.get("stars")
    if isinstance(stars, int) and not isinstance(stars, bool) and stars > 0:
        return f"Launched a giveaway ({stars} Stars)"
    return "Launched a giveaway"


def _summary_giveaway_results(payload: dict) -> str:
    winners = payload.get("winners")
    unclaimed = payload.get("unclaimed")
    is_stars = bool(payload.get("stars", False))
    prize = "Stars" if is_stars else "Premium subscriptions"
    parts: list[str] = []
    if isinstance(winners, int) and not isinstance(winners, bool) and winners > 0:
        parts.append(f"{winners} winner(s)")
    if isinstance(unclaimed, int) and not isinstance(unclaimed, bool) and unclaimed > 0:
        parts.append(f"{unclaimed} unclaimed")
    suffix = f" ({', '.join(parts)})" if parts else ""
    return f"Giveaway ended: {prize}{suffix}"


def _summary_boosts_applied(payload: dict) -> str:
    boosts = payload.get("boosts")
    if isinstance(boosts, int) and not isinstance(boosts, bool) and boosts > 0:
        return f"Applied {boosts} boost(s)"
    return "Applied boosts"


def _summary_payment_refunded(
    payload: dict, peer_map: Optional[dict[int, PeerInfo]] = None
) -> str:
    amount = payload.get("amount")
    currency = payload.get("currency")
    peer_id_raw = payload.get("pi")
    peer_id = (
        int(peer_id_raw)
        if isinstance(peer_id_raw, int) and not isinstance(peer_id_raw, bool)
        else None
    )
    if (
        isinstance(amount, int)
        and not isinstance(amount, bool)
        and isinstance(currency, str)
        and currency
    ):
        base = f"Refunded a payment of {amount} {currency}"
        if peer_id is not None:
            base += f" to {_peer_label(peer_id, peer_map)}"
        return base
    if peer_id is not None:
        return f"Refunded a payment to {_peer_label(peer_id, peer_map)}"
    return "Refunded a payment"


def _summary_gift_stars(payload: dict) -> str:
    count = payload.get("count")
    amount = payload.get("amount")
    currency = payload.get("currency")
    if (
        isinstance(count, int)
        and not isinstance(count, bool)
        and count > 0
        and isinstance(amount, int)
        and not isinstance(amount, bool)
        and isinstance(currency, str)
        and currency
        and currency != "XTR"
    ):
        if amount >= 1_000_000_000:
            return f"Gifted {count} stars for {amount // 1_000_000_000}✦"
        return f"Gifted {count} stars for {amount} {currency}"
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        return f"Gifted {count} Stars"
    return "Gifted Stars"


def _summary_prize_stars(payload: dict) -> str:
    amount = payload.get("amount")
    unclaimed = bool(payload.get("unclaimed", False))
    if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0:
        if unclaimed:
            return f"Won a Stars prize of {amount} (unclaimed)"
        return f"Won a Stars prize of {amount}"
    if unclaimed:
        return "Won an unclaimed Stars prize"
    return "Won a Stars prize"


def _summary_star_gift(payload: dict, peer_map: Optional[dict[int, PeerInfo]]) -> str:
    title = _star_gift_title(payload.get("gift"))
    sender = _peer_label_for_id(payload.get("senderId"), peer_map)
    recipient = _peer_label_for_id(payload.get("peerId"), peer_map)
    if title and sender and recipient:
        return f"{sender} sent a star gift to {recipient}"
    if title:
        return "Sent a star gift"
    return "Sent a star gift"


def _summary_star_gift_unique(
    payload: dict, peer_map: Optional[dict[int, PeerInfo]]
) -> str:
    title = _star_gift_title(payload.get("gift"))
    if title:
        return f"Sent a unique star gift: {title}"
    return "Sent a unique star gift"


def _summary_paid_messages_refunded(payload: dict) -> str:
    count = payload.get("count")
    stars = payload.get("stars")
    if (
        isinstance(count, int)
        and not isinstance(count, bool)
        and isinstance(stars, int)
        and not isinstance(stars, bool)
    ):
        return f"Refunded {stars} Stars for {count} paid message(s)"
    return "Refunded paid messages"


def _summary_paid_messages_price_edited(payload: dict) -> str:
    stars = payload.get("stars")
    broadcast = bool(payload.get("brmsg", False))
    if isinstance(stars, int) and not isinstance(stars, bool):
        if broadcast:
            return f"Edited the paid message price to {stars} Stars (broadcast allowed)"
        return f"Edited the paid message price to {stars} Stars"
    return "Edited the paid message price"


def _summary_todo_completions(payload: dict) -> str:
    completed = payload.get("completed") or []
    incompleted = payload.get("incompleted") or []
    parts: list[str] = []
    if isinstance(completed, list) and completed:
        parts.append(f"completed {len(completed)}")
    if isinstance(incompleted, list) and incompleted:
        parts.append(f"uncompleted {len(incompleted)}")
    if parts:
        return f"Updated the todo list ({', '.join(parts)})"
    return "Updated the todo list"


def _summary_todo_append_tasks(payload: dict) -> str:
    """Render a ``TODO_APPEND_TASKS`` action with the appended task texts.

    Each task is a ``TelegramMediaTodo.Item`` PostboxObject; the ``text``
    field carries the human-readable task label. When at least one task
    has a non-empty text we surface them as ``"Added todo items: a, b, c"``.
    """
    tasks = payload.get("tasks")
    labels: list[str] = []
    if isinstance(tasks, list):
        for task in tasks:
            if not isinstance(task, PostboxObject):
                continue
            text = task.fields.get("text")
            if isinstance(text, str) and text:
                labels.append(text)
    if labels:
        return f"Added todo items: {', '.join(labels)}"
    count = len(tasks) if isinstance(tasks, list) else 0
    if count:
        return f"Added {count} todo item(s)"
    return "Updated the todo list"


def _summary_suggested_post_approval(payload: dict) -> str:
    status = payload.get("st")
    if not isinstance(status, PostboxObject):
        return "Updated the suggested post status"
    fields = status.fields
    type_raw = fields.get("_t")
    if not isinstance(type_raw, int) or isinstance(type_raw, bool):
        return "Updated the suggested post status"
    from .schema import SuggestedPostApprovalStatusType

    try:
        kind = SuggestedPostApprovalStatusType(int(type_raw))
    except ValueError:
        return "Updated the suggested post status"
    if kind is SuggestedPostApprovalStatusType.APPROVED:
        return "Approved the suggested post"
    return "Rejected the suggested post"


def _summary_gift_ton(payload: dict) -> str:
    amount = payload.get("amount")
    currency = payload.get("currency")
    if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0:
        if isinstance(currency, str) and currency == "XTR":
            return f"Gifted {amount} Stars"
        if isinstance(currency, str) and currency and currency != "TON":
            return f"Gifted {amount} {currency}"
        return f"Gifted {amount} TON"
    return "Gifted TON"


def _summary_suggested_post_success(payload: dict) -> str:
    amount = payload.get("amt")
    if isinstance(amount, PostboxObject):
        amount_label = _format_currency_amount(amount)
        if amount_label:
            return f"Suggested post was published (earned {amount_label})"
    return "Suggested post was published"


def _summary_suggested_post_refund(payload: dict) -> str:
    status = payload.get("s")
    if isinstance(status, PostboxObject):
        user_initiated = bool(status.fields.get("iui", False))
        if user_initiated:
            return "Refunded the suggested post (user-initiated)"
    return "Refunded the suggested post"


def _summary_suggested_birthday(payload: dict) -> str:
    birthday = payload.get("birthday")
    if isinstance(birthday, PostboxObject):
        fields = birthday.fields
        day = fields.get("day")
        month = fields.get("month")
        year = fields.get("year")
        if (
            isinstance(day, int)
            and not isinstance(day, bool)
            and isinstance(month, int)
            and not isinstance(month, bool)
        ):
            year_str = ""
            if (
                isinstance(year, int)
                and not isinstance(year, bool)
                and 1900 <= int(year) <= 2100
            ):
                year_str = f" {int(year)}"
            return (
                f"Set a suggested birthday ({_format_birthday(day, month)}{year_str})"
            )
    return "Set a suggested birthday"


def _format_birthday(day: int, month: int) -> str:
    months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    if 1 <= month <= 12:
        return f"{months[month - 1]} {day}"
    return f"{month}/{day}"


def _summary_star_gift_purchase_offer(payload: dict) -> str:
    title = _star_gift_title(payload.get("gift"))
    is_accepted = bool(payload.get("isAccepted", False))
    is_declined = bool(payload.get("isDeclined", False))
    if is_accepted:
        verb = "accepted"
    elif is_declined:
        verb = "declined"
    else:
        verb = "received"
    amount = payload.get("amount")
    amount_label = ""
    if isinstance(amount, PostboxObject):
        amount_label = _format_currency_amount(amount)
    suffix = f" for {amount_label}" if amount_label else ""
    if title:
        return f"Star-gift {verb}: {title}{suffix}"
    return f"Star-gift {verb}{suffix}"


def _format_currency_amount(amount_obj: PostboxObject) -> str:
    """Render a ``CurrencyAmount`` PostboxObject as ``"<N> <label>"``.

    ``CurrencyAmount`` encodes ``amount`` as ``a`` and ``currency`` as ``c``
    (Telegram-side Postbox key codes). For Stars (currency 0) the value is
    divided by ``1_000_000_000`` and rendered as ``N✦``; for fiat/TON the
    raw integer and a short label are used. Returns ``""`` for empty/zero
    amounts so callers can skip the suffix.
    """
    fields = amount_obj.fields
    raw = fields.get("a")
    currency = fields.get("c")
    if not isinstance(raw, int) or isinstance(raw, bool):
        return ""
    if not isinstance(currency, int) or isinstance(currency, bool):
        return str(int(raw))
    if raw <= 0:
        return ""
    if currency == 0:
        return f"{int(raw) // 1_000_000_000}✦"
    return f"{int(raw)} crn{int(currency)}"


def _summary_star_gift_purchase_offer_declined(payload: dict) -> str:
    has_expired = bool(payload.get("hasExpired", False))
    title = _star_gift_title(payload.get("gift"))
    amount = payload.get("amount")
    amount_label = ""
    if isinstance(amount, PostboxObject):
        amount_label = _format_currency_amount(amount)
    subject = title or "a star-gift"
    if has_expired and amount_label:
        return f"Declined an expired offer for {subject} ({amount_label})"
    if has_expired:
        return f"Declined an expired offer for {subject}"
    if amount_label:
        return f"Declined the offer for {subject} ({amount_label})"
    return f"Declined the offer for {subject}"


def _summary_group_creator_change(
    payload: dict, peer_map: Optional[dict[int, PeerInfo]]
) -> str:
    change = payload.get("d")
    if isinstance(change, PostboxObject):
        fields = change.fields
        kind_raw = fields.get("k")
        target_raw = fields.get("t")
        if isinstance(target_raw, int) and not isinstance(target_raw, bool):
            target = _peer_label(int(target_raw), peer_map)
        else:
            target = None
        from .schema import GroupCreatorChangeKind

        try:
            kind_enum = GroupCreatorChangeKind(int(kind_raw))
        except (TypeError, ValueError):
            kind_enum = None
        if kind_enum is GroupCreatorChangeKind.PENDING:
            return f"Pending: ownership transfer to {target or 'another member'}"
        if kind_enum is GroupCreatorChangeKind.APPLIED:
            return f"Transferred ownership to {target or 'another member'}"
    return "Group creator changed"


def _summary_copy_protection_toggle(payload: dict) -> str:
    new_value = bool(payload.get("newValue", False))
    if new_value:
        return "Enabled copy protection for media"
    return "Disabled copy protection for media"


def _summary_copy_protection_request(payload: dict) -> str:
    has_expired = bool(payload.get("hasExpired", False))
    if has_expired:
        return "Copy-protection request expired"
    new_value = bool(payload.get("newValue", False))
    if new_value:
        return "Enabled copy protection for media"
    return "Disabled copy protection for media"


def _summary_managed_bot_created(
    payload: dict, peer_map: Optional[dict[int, PeerInfo]]
) -> str:
    bot_id = payload.get("botId")
    if isinstance(bot_id, int) and not isinstance(bot_id, bool):
        return f"Created the managed bot {_peer_label(int(bot_id), peer_map)}"
    return "Created a managed bot"


def _summary_poll_option_appended(payload: dict) -> str:
    option = payload.get("option")
    if isinstance(option, PostboxObject):
        text = option.fields.get("text")
        if isinstance(text, str) and text:
            return f"Added a poll option: {text}"
    return "Added a poll option"


def _summary_poll_option_deleted(payload: dict) -> str:
    option = payload.get("option")
    if isinstance(option, PostboxObject):
        text = option.fields.get("text")
        if isinstance(text, str) and text:
            return f"Removed a poll option: {text}"
    return "Removed a poll option"


def _peer_label_for_id(
    value: object, peer_map: Optional[dict[int, PeerInfo]]
) -> Optional[str]:
    if isinstance(value, int) and not isinstance(value, bool) and value != 0:
        return _peer_label(int(value), peer_map)
    return None


def _star_gift_title(gift_obj: object) -> Optional[str]:
    if not isinstance(gift_obj, PostboxObject):
        return None
    value = gift_obj.fields.get("value")
    if isinstance(value, PostboxObject):
        title = value.fields.get("title")
        if isinstance(title, str) and title:
            return title
    return None


def _render_html_service_message(
    handle,
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
    author_id: Optional[int] = None,
) -> None:
    """Render a service-action attachment as an HTML ``<div class="service-msg">``."""
    metadata = attachment.metadata or {}
    raw_type = metadata.get("raw_type")
    payload = metadata.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    if raw_type == TelegramMediaActionType.CUSTOM_TEXT:
        text = payload.get("text")
        if isinstance(text, str) and text:
            _render_html_service_div(handle, linkify_html(text))
        else:
            _render_html_service_div(handle, "")
        return

    _SUMMARY_TYPES = frozenset(
        (
            TelegramMediaActionType.GROUP_CREATED,
            TelegramMediaActionType.TITLE_UPDATED,
            TelegramMediaActionType.PINNED_MESSAGE_UPDATED,
            TelegramMediaActionType.MESSAGE_AUTOREMOVE_TIMEOUT_UPDATED,
            TelegramMediaActionType.PHONE_CALL,
            TelegramMediaActionType.GROUP_PHONE_CALL,
            TelegramMediaActionType.SET_CHAT_THEME,
            TelegramMediaActionType.CONFERENCE_CALL,
        )
    )
    if raw_type in _SUMMARY_TYPES:
        _render_html_service_summary(
            handle, _build_action_summary(metadata, peer_map, author_id)
        )
        return

    if raw_type == TelegramMediaActionType.ADDED_MEMBERS:
        html_body = _render_added_removed_html(
            "Added", _decode_payload_peer_ids(payload), peer_map, "Added members"
        )
    elif raw_type == TelegramMediaActionType.REMOVED_MEMBERS:
        html_body = _render_added_removed_html(
            "Removed", _decode_payload_peer_ids(payload), peer_map, "Removed members"
        )
    elif raw_type == TelegramMediaActionType.JOINED_BY_LINK:
        html_body = _render_joined_by_link_html(payload, peer_map, author_id)
    elif raw_type == TelegramMediaActionType.PEER_JOINED:
        html_body = _render_simple_join_html(" joined", peer_map, author_id)
    elif raw_type == TelegramMediaActionType.INVITE_TO_GROUP_PHONE_CALL:
        html_body = _render_invite_to_call_html(
            payload, peer_map, "Invited members to a voice chat"
        )
    elif raw_type == TelegramMediaActionType.JOINED_BY_REQUEST:
        html_body = _render_simple_join_html(" joined via request", peer_map, author_id)
    else:
        summary = _build_action_summary(metadata, peer_map, author_id)
        if summary:
            _render_html_service_summary(handle, summary)
        else:
            handle.write('<div class="service-msg"></div>')
        return

    _render_html_service_div(handle, html_body)


def _render_added_removed_html(
    verb: str,
    peer_ids: list[int],
    peer_map: Optional[dict[int, PeerInfo]],
    fallback: str,
) -> str:
    names = _join_peer_names_html(peer_ids, peer_map)
    if names:
        return f"{verb} {names}"
    return fallback


def _render_simple_join_html(
    suffix: str,
    peer_map: Optional[dict[int, PeerInfo]],
    author_id: Optional[int],
) -> str:
    label = _join_label(peer_map, author_id)
    if author_id is None:
        return f"Member{suffix}"
    url = _peer_url_for(author_id, peer_map)
    return f"{_html_link(label, url)}{suffix}"


def _render_joined_by_link_html(
    payload: dict,
    peer_map: Optional[dict[int, PeerInfo]],
    author_id: Optional[int],
) -> str:
    joiner_url = (
        _peer_url_for(author_id, peer_map)
        if author_id is not None and peer_map is not None
        else None
    )
    joiner_html = _html_link(_join_label(peer_map, author_id), joiner_url)
    base = f"{joiner_html} joined via invite link"
    inviter_raw = payload.get("inviter")
    inviter_id = (
        int(inviter_raw)
        if isinstance(inviter_raw, int)
        and not isinstance(inviter_raw, bool)
        and inviter_raw != 0
        else None
    )
    if (
        inviter_id is not None
        and inviter_id != author_id
        and peer_map is not None
        and inviter_id in peer_map
    ):
        inviter_label = _peer_label(inviter_id, peer_map)
        inviter_url = _peer_url_for(inviter_id, peer_map)
        return f"{base} from {_html_link(inviter_label, inviter_url)}"
    return base


def _render_invite_to_call_html(
    payload: dict,
    peer_map: Optional[dict[int, PeerInfo]],
    fallback: str,
) -> str:
    names = _join_peer_names_html(_decode_payload_peer_ids(payload), peer_map)
    if names:
        return f"Invited {names} to a voice chat"
    return fallback


def _render_html_service_summary(handle, summary: str) -> None:
    for emoji in ("📞", "🎙️", "📹"):
        if emoji in summary:
            head, _, tail = summary.partition(emoji)
            inner = (
                f"{html.escape(head)}"
                f'<span class="service-emoji">{emoji}</span>'
                f"{html.escape(tail)}"
            )
            _render_html_service_div(handle, inner)
            return
    _render_html_service_div(handle, html.escape(summary))


def _render_html_service_div(handle, inner_html: str) -> None:
    handle.write(f'<div class="service-msg">{inner_html}</div>')


def _render_markdown_service_message(
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
    author_id: Optional[int] = None,
) -> str:
    """Render a service-action attachment as a single italic Markdown line."""
    metadata = attachment.metadata or {}
    raw_type = metadata.get("raw_type")
    payload = metadata.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    if raw_type == TelegramMediaActionType.ADDED_MEMBERS:
        return _md_join_line(
            "Added", _decode_payload_peer_ids(payload), peer_map, "Added members"
        )
    if raw_type == TelegramMediaActionType.REMOVED_MEMBERS:
        return _md_join_line(
            "Removed", _decode_payload_peer_ids(payload), peer_map, "Removed members"
        )
    if raw_type == TelegramMediaActionType.JOINED_BY_LINK:
        return _md_joined_by_link(payload, peer_map, author_id)
    if raw_type == TelegramMediaActionType.PEER_JOINED:
        return _md_simple_join(" joined", peer_map, author_id)
    if raw_type == TelegramMediaActionType.INVITE_TO_GROUP_PHONE_CALL:
        names = _join_peer_names_md(_decode_payload_peer_ids(payload), peer_map)
        if names:
            return f"Invited {names} to a voice chat"
        return "Invited members to a voice chat"
    if raw_type == TelegramMediaActionType.JOINED_BY_REQUEST:
        return _md_simple_join(" joined via request", peer_map, author_id)
    if raw_type == TelegramMediaActionType.CUSTOM_TEXT:
        text = payload.get("text")
        if isinstance(text, str) and text:
            return linkify_markdown(text)
        return ""
    summary = _build_action_summary(metadata, peer_map, author_id)
    return summary


def _md_join_line(
    verb: str,
    peer_ids: list[int],
    peer_map: Optional[dict[int, PeerInfo]],
    fallback: str,
) -> str:
    names = _join_peer_names_md(peer_ids, peer_map)
    if names:
        return f"{verb} {names}"
    return fallback


def _md_simple_join(
    suffix: str,
    peer_map: Optional[dict[int, PeerInfo]],
    author_id: Optional[int],
) -> str:
    label = _join_label(peer_map, author_id)
    if author_id is None:
        return f"Member{suffix}"
    url = _peer_url_for(author_id, peer_map)
    return f"{_md_link(label, url)}{suffix}"


def _md_joined_by_link(
    payload: dict,
    peer_map: Optional[dict[int, PeerInfo]],
    author_id: Optional[int],
) -> str:
    joiner_url = (
        _peer_url_for(author_id, peer_map)
        if author_id is not None and peer_map is not None
        else None
    )
    joiner_md = _md_link(_join_label(peer_map, author_id), joiner_url)
    base = f"{joiner_md} joined via invite link"
    inviter_raw = payload.get("inviter")
    inviter_id = (
        int(inviter_raw)
        if isinstance(inviter_raw, int)
        and not isinstance(inviter_raw, bool)
        and inviter_raw != 0
        else None
    )
    if (
        inviter_id is not None
        and inviter_id != author_id
        and peer_map is not None
        and inviter_id in peer_map
    ):
        inviter_label = _peer_label(inviter_id, peer_map)
        inviter_url = _peer_url_for(inviter_id, peer_map)
        return f"{base} from {_md_link(inviter_label, inviter_url)}"
    return base


def _render_markdown_reply_line(
    msg: Message,
    peer_map: Optional[dict[int, PeerInfo]],
    out_path: Optional[Path] = None,
) -> Optional[str]:
    """Return a Markdown blockquote line summarising a reply target.

    Returns ``None`` when ``msg.reply_info`` is absent. Returns a line of
    the form:

    * ``> In reply to (message unavailable)`` when the t7 lookup missed.
    * ``> In reply to **Author**: <snippet or emoji caption>`` for
      intra-chat replies. The link is a ``#msg-{mid}`` in-page anchor.
    * ``> In reply to **Author**: <...>`` for cross-chat replies. The
      link is a ``t.me/.../{mid}`` URL.
    * When the target was itself a forward, the author line is extended
      with ``· Forwarded from **Original**`` between the author and the
      snippet. The "Forwarded from" fragment is plain text (no link).
    * When the target was sent via an inline bot, an extra
      ``· via @BotName`` fragment is appended (after the "Forwarded
      from" fragment when both apply). The "via" fragment is a link to
      the bot's ``t.me/<username>`` when the bot peer is resolvable and
      has a username, otherwise plain text.

    The ``out_path`` is used to build a same-file in-page anchor for
    intra-chat cases (``{basename}.html#msg-{mid}``). When not given,
    the line emits a bare ``#msg-{mid}`` anchor; Markdown viewers don't
    follow it as a link, but the text is still informative.
    """
    reply = msg.reply_info
    if reply is None:
        return None
    if reply.target_unavailable:
        return "> In reply to (message unavailable)"

    author_name = "Unknown"
    if reply.target_author_id is not None and peer_map is not None:
        author_name = _peer_label(reply.target_author_id, peer_map)
    if not author_name:
        author_name = "Unknown"

    forwarded_from_name = _reply_forwarded_from_name(reply, peer_map)
    via_bot_label, via_bot_url = _reply_via_bot_link(reply, peer_map)

    href: Optional[str] = None
    if reply.is_intra_chat:
        if out_path is not None:
            href = f"{out_path.name}#msg-{reply.target_message_id}"
        else:
            href = f"#msg-{reply.target_message_id}"
    else:
        target_peer = peer_map.get(reply.target_peer_id) if peer_map else None
        if target_peer is not None:
            href = peer_url(
                target_peer,
                reply.target_peer_id,
                message_id=reply.target_message_id,
            )

    text = _reply_preview_text(reply)
    body_label = f"In reply to **{author_name}**"
    if forwarded_from_name:
        body_label = f"{body_label} · Forwarded from **{forwarded_from_name}**"
    if via_bot_label:
        via_fragment = (
            _md_link(via_bot_label, via_bot_url)
            if via_bot_url
            else f"**{via_bot_label}**"
        )
        body_label = f"{body_label} · via {via_fragment}"
    if text:
        body_label = f"{body_label}: {text}"

    if href:
        return f"> {_md_link(body_label, href)}"
    return f"> {body_label}"


def _render_markdown_attachment(
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
    author_id: Optional[int] = None,
) -> str:
    if attachment.kind == "webpage" and attachment.metadata:
        return _render_markdown_webpage_preview(attachment)
    if attachment.kind == "action":
        return _render_markdown_service_message(attachment, peer_map, author_id)
    if attachment.kind == "poll" and attachment.metadata:
        return _render_markdown_poll(attachment.metadata, peer_map)
    if attachment.kind == "contact":
        return _render_markdown_contact(attachment, peer_map)
    if attachment.kind == "dice":
        return _render_markdown_dice(attachment)
    if attachment.kind == "expired_content":
        return _render_markdown_expired(attachment)
    if attachment.kind == "game":
        return _render_markdown_game(attachment, peer_map)
    if attachment.kind == "invoice":
        return _render_markdown_invoice(attachment)
    if attachment.kind == "giveaway":
        return _render_markdown_giveaway(attachment)
    if attachment.kind == "giveaway_result":
        return _render_markdown_giveaway_result(attachment)
    if attachment.kind == "live_stream":
        return _render_markdown_live_stream(attachment)
    if attachment.kind == "map":
        return _render_markdown_map(attachment, peer_map)
    if attachment.kind == "paid_content":
        return _render_markdown_paid_content(attachment)
    if attachment.kind == "story":
        return _render_markdown_story(attachment, peer_map)
    if attachment.kind == "todo":
        return _render_markdown_todo(attachment, peer_map)
    if attachment.kind == "unsupported":
        return _render_markdown_unsupported(attachment)
    label = _attachment_label(attachment)
    if attachment.url:
        return f"[{label}]({attachment.url})"
    if attachment.exported_path:
        if attachment.is_preview_fallback:
            primary = _render_markdown_preview_fallback(attachment, label)
        elif attachment.kind == "image":
            primary = f"![{label}]({attachment.exported_path})"
        else:
            primary = f"[{label}]({attachment.exported_path})"
        return f"{primary}\n`[{_attachment_description(attachment)}]`"
    return _render_markdown_unknown_kind(attachment)


def _render_markdown_preview_fallback(attachment: Attachment, label: str) -> str:
    """Render the preview that was promoted to be the main file as Markdown.

    Image previews get inline ``![]()`` so the image is visible in
    Markdown viewers; video previews get a link with a play marker;
    everything else falls through to a plain download link. The
    surrounding ``file-meta`` description line still shows the
    original ``kind`` and the "preview" suffix.
    """
    mime_type = attachment.mime_type or ""
    if mime_type.startswith("image/"):
        return f"![{label}]({attachment.exported_path})"
    if mime_type.startswith("video/"):
        return f"[▶ {label}]({attachment.exported_path})"
    if mime_type.startswith("audio/"):
        return f"[▶ {label}]({attachment.exported_path})"
    return f"[{label}]({attachment.exported_path})"


def _render_markdown_webpage_preview(attachment: Attachment) -> str:
    """Render a loaded-content webpage as a Markdown link-preview block."""
    metadata = attachment.metadata or {}
    url = attachment.url or ""
    title = metadata.get("title") or metadata.get("display_url") or url

    parts: list[str] = []
    site_name = metadata.get("site_name")
    if site_name:
        parts.append(f"**{site_name} — {title}** ([link]({url}))")
    else:
        parts.append(f"**{title}** ([link]({url}))")

    author = metadata.get("author")
    duration = metadata.get("duration")
    if author or isinstance(duration, int):
        meta_bits: list[str] = []
        if author:
            meta_bits.append(str(author))
        if isinstance(duration, int):
            meta_bits.append(_format_duration(duration))
        parts.append(f"*{' · '.join(meta_bits)}*")

    description = metadata.get("description")
    if description:
        parts.append(f"> {description}")

    preview_image = attachment.preview_image
    if preview_image is not None and preview_image.exported_path:
        parts.append(f"![{title}]({preview_image.exported_path})")
    preview_video = attachment.preview_video
    if preview_video is not None and preview_video.exported_path:
        video_label = preview_video.filename or "Video"
        parts.append(f"[▶ {video_label}]({preview_video.exported_path})")

    return "\n".join(parts)


def _render_markdown_contact(
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
) -> str:
    """Render a contact as a single-line Markdown summary + optional vCard link."""
    metadata = attachment.metadata or {}
    first_name = metadata.get("first_name") or ""
    last_name = metadata.get("last_name") or ""
    phone = metadata.get("phone_number") or ""
    name = " ".join(part for part in (first_name, last_name) if part).strip()
    head_bits: list[str] = []
    if name:
        head_bits.append(f"👤 **{name}**")
    if phone:
        head_bits.append(phone)
    parts: list[str] = []
    if head_bits:
        parts.append(" · ".join(head_bits))
    if attachment.vcard_path:
        parts.append(f"[vCard]({attachment.vcard_path})")
    if not parts:
        return f"`[{_attachment_description(attachment)}]`"
    return "\n".join(parts)


def _render_markdown_dice(attachment: Attachment) -> str:
    """Render a dice as an italic service-style Markdown line."""
    metadata = attachment.metadata or {}
    emoji = metadata.get("emoji") or "🎲"
    value = metadata.get("value")
    ton_amount = metadata.get("ton_amount")
    bits: list[str] = []
    if value is not None:
        bits.append(f"*{emoji} {value}*")
    else:
        bits.append(f"*{emoji}*")
    if ton_amount:
        bits.append(f"{ton_amount} TON")
    return " · ".join(bits)


def _render_markdown_expired(attachment: Attachment) -> str:
    """Render an expired-content placeholder as italic Markdown."""
    metadata = attachment.metadata or {}
    label = metadata.get("label") or "media"
    return f"*(expired {label})*"


def _render_markdown_game(
    attachment: Attachment, peer_map: Optional[dict[int, PeerInfo]] = None
) -> str:
    """Render a game as Markdown title + description + optional thumbnail."""
    metadata = attachment.metadata or {}
    title = (metadata.get("title") or "").strip() or "Game"
    description = (metadata.get("description") or "").strip()
    parts: list[str] = [f"**Game: {title}**"]
    if description:
        parts.append("")
        parts.append(description)
    image_path = (
        attachment.preview_image.exported_path
        if attachment.preview_image and attachment.preview_image.exported_path
        else None
    )
    if image_path:
        parts.append("")
        parts.append(f"![{title}]({image_path})")
    return "\n".join(parts)


def _render_markdown_invoice(attachment: Attachment) -> str:
    """Render an invoice as Markdown title + amount + description."""
    metadata = attachment.metadata or {}
    title = (metadata.get("title") or "").strip() or "Invoice"
    amount_str = _format_invoice_amount(
        metadata.get("total_amount"), metadata.get("currency") or ""
    )
    description = (metadata.get("description") or "").strip()
    parts: list[str] = [f"**Invoice: {title}** — {amount_str}"]
    if description:
        parts.append("")
        parts.append(description)
    photo = attachment.preview_image
    if photo and photo.exported_path:
        parts.append("")
        parts.append(f"![{title}]({photo.exported_path})")
    return "\n".join(parts)


def _format_invoice_amount(total_amount: Any, currency: str) -> str:
    """Render an invoice total as ``9.99 USD`` or ``100.00 XTR`` (stars)."""
    if not isinstance(total_amount, int) or isinstance(total_amount, bool):
        return currency or "0"
    if currency == "XTR":
        return f"{total_amount}.00 XTR"
    if not currency:
        return f"{total_amount / 100:.2f}"
    if total_amount % 100 == 0:
        return f"{total_amount // 100}.00 {currency}"
    return f"{total_amount / 100:.2f} {currency}"


def _render_markdown_giveaway(attachment: Attachment) -> str:
    """Render a giveaway as a single Markdown line."""
    return _giveaway_text(attachment)


def _render_markdown_giveaway_result(attachment: Attachment) -> str:
    """Render a giveaway result as a single Markdown line."""
    metadata = attachment.metadata or {}
    bits: list[str] = ["🎁 Giveaway ended"]
    winners = metadata.get("winners_count")
    unclaimed = metadata.get("unclaimed_count")
    if isinstance(winners, int):
        bits.append(f"{winners} winners")
    if isinstance(unclaimed, int):
        bits.append(f"{unclaimed} unclaimed")
    months = metadata.get("premium_months")
    stars = metadata.get("stars_amount")
    if isinstance(months, int) and months > 0:
        bits.append(f"{months} months Premium")
    elif isinstance(stars, int) and stars > 0:
        bits.append(f"{stars} stars")
    return " · ".join(bits)


def _giveaway_text(attachment: Attachment) -> str:
    """Build the canonical human-readable line for a giveaway."""
    metadata = attachment.metadata or {}
    months = metadata.get("premium_months")
    stars = metadata.get("stars_amount")
    channels = metadata.get("channel_peer_ids") or []
    quantity = metadata.get("quantity")
    until_date = metadata.get("until_date")
    prize_description = metadata.get("prize_description")
    bits: list[str] = ["🎁 Giveaway"]
    if isinstance(quantity, int) and quantity > 1:
        bits.append(f"{quantity} ×")
    if isinstance(months, int) and months > 0:
        bits.append(f"{months} months Premium")
    elif isinstance(stars, int) and stars > 0:
        bits.append(f"{stars} stars")
    if channels:
        bits.append("in " + ", ".join(f"peer {cid}" for cid in channels))
    if isinstance(until_date, int) and until_date > 0:
        until_iso = (
            datetime.fromtimestamp(until_date, tz=timezone.utc).date().isoformat()
        )
        bits.append(f"ends {until_iso}")
    if isinstance(prize_description, str) and prize_description:
        bits.append(f"({prize_description})")
    return " · ".join(bits)


def _render_markdown_live_stream(attachment: Attachment) -> str:
    """Render a live stream placeholder as italic Markdown."""
    return "*📹 Live stream*"


def _render_markdown_map(
    attachment: Attachment, peer_map: Optional[dict[int, PeerInfo]] = None
) -> str:
    """Render a map pin as a Markdown line with OpenStreetMap link."""
    metadata = attachment.metadata or {}
    latitude = metadata.get("latitude")
    longitude = metadata.get("longitude")
    venue = metadata.get("venue") or {}
    address = metadata.get("address") or {}
    live_timeout = metadata.get("live_timeout")
    title = venue.get("title") or "Location"
    address_parts: list[str] = []
    if isinstance(address, dict):
        street = address.get("street")
        city = address.get("city")
        state = address.get("state")
        country = address.get("country")
        if street:
            address_parts.append(str(street))
        city_state_country = ", ".join(part for part in (city, state, country) if part)
        if city_state_country:
            address_parts.append(city_state_country)
    bits: list[str] = [f"📍 **{title}**"]
    if address_parts:
        bits.append(" — ".join(address_parts))
    if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
        bits.append(
            f"[{latitude:.5f}, {longitude:.5f}]({_osm_url(latitude, longitude)})"
        )
    if isinstance(live_timeout, int) and live_timeout > 0:
        bits.append(_live_until_label(live_timeout))
    return " · ".join(bits)


def _osm_url(latitude: float, longitude: float) -> str:
    """Build an OpenStreetMap permalink for ``(lat, lng)``."""
    return (
        f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}"
        f"#map=17/{latitude}/{longitude}"
    )


def _live_until_label(timeout: int) -> str:
    """Render a liveBroadcastingTimeout (seconds-from-now) as an absolute ``HH:MM``."""
    until_dt = datetime.fromtimestamp(timeout, tz=timezone.utc)
    return f"live until {until_dt.strftime('%H:%M')}"


def _render_markdown_paid_content(attachment: Attachment) -> str:
    """Render a paid-content placeholder as Markdown."""
    metadata = attachment.metadata or {}
    stars = metadata.get("stars_amount")
    bits: list[str] = ["Paid media"]
    if isinstance(stars, int) and stars > 0:
        bits.append(f"{stars} ★")
    return f"💎 **{' · '.join(bits)}**"


def _render_markdown_story(
    attachment: Attachment, peer_map: Optional[dict[int, PeerInfo]] = None
) -> str:
    """Render a story preview as Markdown with a t.me link."""
    metadata = attachment.metadata or {}
    peer_id = metadata.get("peer_id")
    story_id = metadata.get("story_id")
    is_mention = bool(metadata.get("is_mention"))
    name: Optional[str] = None
    username: Optional[str] = None
    if peer_map and isinstance(peer_id, int) and peer_id in peer_map:
        info = peer_map[peer_id]
        name = info.name
        username = info.username
    if not isinstance(story_id, int):
        return "*📖 Story*"
    if isinstance(peer_id, int) and username:
        url = f"https://t.me/{username}/s/{story_id}"
    else:
        url = None
    label = name or f"peer {peer_id}" if isinstance(peer_id, int) else "Story"
    if is_mention:
        return f"📖 **{label}** mentioned you in a story" + (
            f" ([open]({url}))" if url else ""
        )
    if url:
        return f"📖 Story from [{label}]({url})"
    return f"📖 Story from **{label}**"


def _render_markdown_todo(
    attachment: Attachment, peer_map: Optional[dict[int, PeerInfo]] = None
) -> str:
    """Render a todo list as Markdown header + GFM task list."""
    metadata = attachment.metadata or {}
    text = (metadata.get("text") or "").strip()
    items = metadata.get("items") or []
    completions = metadata.get("completions") or []
    flags = metadata.get("flags") or {}
    completed_ids = {
        completion.get("id")
        for completion in completions
        if completion.get("id") is not None
    }
    completed_count = sum(1 for item in items if item.get("id") in completed_ids)
    total = len(items)
    parts: list[str] = []
    if text:
        parts.append(f"**{text}**")
    parts.append(f"*{completed_count} of {total} completed*")
    if flags.get("others_can_append"):
        parts.append("*(others can append)*")
    if flags.get("others_can_complete"):
        parts.append("*(others can complete)*")
    parts.append("")
    for item in items:
        item_id = item.get("id")
        is_done = item_id in completed_ids
        marker = "x" if is_done else " "
        parts.append(f"- [{marker}] {item.get('text', '')}")
    return "\n".join(parts)


def _render_markdown_unsupported(attachment: Attachment) -> str:
    """Render an unsupported-media placeholder as italic Markdown."""
    return "*❓ (unsupported media)*"


def _render_markdown_unknown_kind(attachment: Attachment) -> str:
    """Markdown fallback for any kind that has no dedicated renderer."""
    return f"`[{_attachment_description(attachment)}]`"


def _peer_names(
    peer_ids: list[int], peer_map: Optional[dict[int, PeerInfo]]
) -> list[str]:
    names: list[str] = []
    for peer_id in peer_ids:
        info = peer_map.get(peer_id) if peer_map else None
        names.append(info.name if info is not None else f"peer {peer_id}")
    return names


def _csv_name_color(
    peer_id: Optional[int],
    peer_map: Optional[dict[int, PeerInfo]],
) -> object:
    """Return the author's name color for CSV export (empty when unknown)."""
    if peer_id is None or peer_map is None:
        return ""
    info = peer_map.get(peer_id)
    if info is None or info.name_color is None:
        return ""
    return info.name_color


def _csv_safe_metadata(value: object) -> object:
    """Recursively drop non-JSON-serializable values from CSV metadata.

    Action attachments carry the raw Postbox payload (nested
    ``PostboxObject`` instances, ``INT64_ARRAY`` / ``BYTES`` blobs) under
    ``metadata["payload"]``. That data is useful for in-process rendering
    but should not appear in the CSV column — it isn't tabular and isn't
    JSON-serializable. This walker returns a copy with:

    * ``PostboxObject`` instances replaced with a ``{"@type": name}``
      marker (just enough to identify them, none of the heavy fields).
    * ``bytes`` / ``bytearray`` replaced with their hex string.
    * any other type left as-is if JSON-serializable, otherwise
      ``repr(obj)`` so we never silently lose the value.
    """
    if isinstance(value, dict):
        return {key: _csv_safe_metadata(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_csv_safe_metadata(item) for item in value]
    from telegram_message_exporter.postbox import PostboxObject

    if isinstance(value, PostboxObject):
        return {"@type": value.type_name}
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return value


def _csv_reply_info(
    msg: Message,
    peer_map: Optional[dict[int, PeerInfo]],
    tz: Optional[object],
) -> object:
    """Build a ``reply_info`` JSON dict for the CSV column, or ``""``."""
    reply = msg.reply_info
    if reply is None:
        return ""
    target_author: Optional[str] = None
    if reply.target_author_id is not None and peer_map is not None:
        target_author = _resolve_peer_name(reply.target_author_id, peer_map)
    target_text: Optional[str] = reply.target_text
    if target_text is not None and len(target_text) > 280:
        target_text = target_text[:279] + "…"
    target_timestamp: Optional[str] = None
    if reply.target_timestamp is not None:
        if tz is not None:
            target_timestamp = to_local(reply.target_timestamp, tz).isoformat()
        else:
            target_timestamp = reply.target_timestamp.isoformat()
    target_forwarded_from_id: Optional[int] = None
    target_forwarded_from: Optional[str] = None
    if reply.target_forward_info is not None:
        fwd = reply.target_forward_info
        target_forwarded_from_id = fwd.source_id or fwd.author_id
        target_forwarded_from = _reply_forwarded_from_name(reply, peer_map)
    target_via_bot_id = reply.target_via_bot_id
    target_via_bot_title = reply.target_via_bot_title
    target_via_bot_name: Optional[str] = None
    target_via_bot_username: Optional[str] = None
    if target_via_bot_id is not None and peer_map:
        target_bot_info = peer_map.get(target_via_bot_id)
        if target_bot_info is not None:
            target_via_bot_name = target_bot_info.name
            target_via_bot_username = target_bot_info.username
    if target_via_bot_name is None and target_via_bot_title:
        target_via_bot_name = target_via_bot_title
    return json.dumps(
        {
            "target_peer_id": reply.target_peer_id,
            "target_message_id": reply.target_message_id,
            "is_quote": reply.is_quote,
            "is_intra_chat": reply.is_intra_chat,
            "target_author_id": reply.target_author_id,
            "target_author": target_author,
            "target_timestamp": target_timestamp,
            "target_text": target_text,
            "target_snippet": reply.target_snippet,
            "target_attachment_kind": reply.target_attachment_kind,
            "target_filename": reply.target_filename,
            "target_attachment_meta": reply.target_attachment_meta,
            "unavailable": reply.target_unavailable,
            "target_forwarded_from_id": target_forwarded_from_id,
            "target_forwarded_from": target_forwarded_from,
            "target_via_bot_id": target_via_bot_id,
            "target_via_bot_username": target_via_bot_username,
            "target_via_bot_name": target_via_bot_name,
        },
        ensure_ascii=False,
    )


def _csv_via_bot(msg: Message, peer_map: Optional[dict[int, PeerInfo]]) -> str:
    """Build the ``via_bot`` CSV cell for ``msg``.

    Returns a JSON object carrying the bot's peer id, display name
    (resolved from ``peer_map`` when possible), and username; the raw
    attribute ``title`` is preserved as a fallback. Empty string when
    the message was not sent via an inline / business bot.
    """
    bot_id = msg.via_bot_id
    bot_title = msg.via_bot_title
    if bot_id is None and not bot_title:
        return ""
    bot_name: Optional[str] = None
    bot_username: Optional[str] = None
    if bot_id is not None and peer_map:
        bot_info = peer_map.get(bot_id)
        if bot_info is not None:
            bot_name = bot_info.name
            bot_username = bot_info.username
    if bot_name is None and bot_title:
        bot_name = bot_title
    return json.dumps(
        {
            "bot_id": bot_id,
            "bot_username": bot_username,
            "bot_name": bot_name,
            "bot_title": bot_title,
        },
        ensure_ascii=False,
    )


def _poll_percentages(metadata: dict) -> list[Optional[float]]:
    options = metadata.get("options") or []
    counts: list[Optional[int]] = [opt.get("vote_count") for opt in options]
    valid = [c for c in counts if isinstance(c, int)]
    total = sum(valid) if valid else None
    result: list[Optional[float]] = []
    for count in counts:
        if count is None or total in (None, 0):
            result.append(None)
        else:
            result.append(round(count * 100.0 / total, 1))
    return result


def _render_markdown_poll(
    metadata: dict,
    peer_map: Optional[dict[int, PeerInfo]] = None,
) -> str:
    question = (metadata.get("question") or "").strip() or "(no question)"
    kind = metadata.get("kind", "poll")
    multiple_answers = bool(metadata.get("multiple_answers"))
    publicity = metadata.get("publicity", "anonymous")
    is_closed = bool(metadata.get("is_closed"))
    total_voters = metadata.get("total_voters")
    options = metadata.get("options") or []
    solution = metadata.get("solution")
    percentages = _poll_percentages(metadata)
    show_correct = kind == "quiz" and is_closed

    tags: list[str] = []
    tags.append("Quiz" if kind == "quiz" else "Poll")
    if multiple_answers:
        tags.append("multiple answers")
    tags.append("public" if publicity == "public" else "anonymous")
    if is_closed:
        tags.append("closed")
    if isinstance(total_voters, int):
        tags.append(f"{total_voters} votes")
    header = f"**Poll:** {question}\n\n*({' • '.join(tags)})*\n"

    lines: list[str] = []
    for index, option in enumerate(options):
        text = (option.get("text") or "").strip() or "(empty)"
        vote_count = option.get("vote_count")
        recent = option.get("recent_voters") or []
        percent = percentages[index]
        markers: list[str] = []
        if option.get("is_correct") and show_correct:
            markers.append("✓ correct")
        if option.get("selected"):
            markers.append("you voted")
        marker = f" *({' / '.join(markers)})*" if markers else ""
        if isinstance(vote_count, int) and percent is not None:
            count_part = f"{vote_count} ({percent}%)"
        elif isinstance(vote_count, int):
            count_part = f"{vote_count}"
        else:
            count_part = "—"
        line = f"- {text} — {count_part}{marker}"
        if recent:
            line += f"  \n  *Recent voters: {', '.join(_peer_names(recent, peer_map))}*"
        lines.append(line)

    body = "\n".join(lines)
    result = f"{header}\n{body}"
    if solution and is_closed:
        result += f"\n\n**Solution:** {solution}"
    return result


def _render_html_poll(
    handle,
    metadata: dict,
    peer_map: Optional[dict[int, PeerInfo]],
) -> None:
    question = (metadata.get("question") or "").strip() or "(no question)"
    kind = metadata.get("kind", "poll")
    multiple_answers = bool(metadata.get("multiple_answers"))
    publicity = metadata.get("publicity", "anonymous")
    is_closed = bool(metadata.get("is_closed"))
    total_voters = metadata.get("total_voters")
    options = metadata.get("options") or []
    solution = metadata.get("solution")
    percentages = _poll_percentages(metadata)
    show_correct = kind == "quiz" and is_closed

    tags: list[str] = []
    tags.append("Quiz" if kind == "quiz" else "Poll")
    if multiple_answers:
        tags.append("multiple answers")
    tags.append("public" if publicity == "public" else "anonymous")
    if is_closed:
        tags.append("closed")
    if isinstance(total_voters, int):
        tags.append(f"{total_voters} votes")

    handle.write('<div class="poll">')
    handle.write(f'<div class="poll-question">{html.escape(question)}</div>')
    handle.write(f'<div class="poll-meta">{html.escape(" • ".join(tags))}</div>')
    for index, option in enumerate(options):
        text = (option.get("text") or "").strip() or "(empty)"
        vote_count = option.get("vote_count")
        recent = option.get("recent_voters") or []
        percent = percentages[index]
        classes = ["poll-option"]
        if option.get("is_correct") and show_correct:
            classes.append("correct")
        if option.get("selected"):
            classes.append("selected")
        class_attr = html.escape(" ".join(classes), quote=True)
        handle.write(f'<div class="{class_attr}">')
        handle.write(f'<span class="text">{html.escape(text)}</span>')
        if isinstance(vote_count, int) and percent is not None:
            count_text = f"{vote_count} ({percent}%)"
        elif isinstance(vote_count, int):
            count_text = f"{vote_count}"
        else:
            count_text = "—"
        handle.write(f'<span class="count">{html.escape(count_text)}</span>')
        markers: list[str] = []
        if option.get("is_correct") and show_correct:
            markers.append("✓ correct")
        if option.get("selected"):
            markers.append("you voted")
        if markers:
            handle.write(
                f'<span class="count">{html.escape(" • ".join(markers))}</span>'
            )
        if recent:
            recent_names = ", ".join(_peer_names(recent, peer_map))
            handle.write(
                f'<span class="recent">Recent voters: '
                f"{html.escape(recent_names)}</span>"
            )
        handle.write("</div>")
    if solution and is_closed:
        handle.write(
            f'<div class="poll-solution"><strong>Solution:</strong> '
            f"{html.escape(solution)}</div>"
        )
    handle.write("</div>")


def build_html_stats(
    messages: list[Message],
    title: str,
    peer_map: Optional[dict[int, PeerInfo]],
    peer_photo_paths: Optional[dict[int, str]] = None,
) -> HtmlStats:
    """Build summary stats for the HTML export."""
    timestamps = [msg.timestamp for msg in messages if msg.timestamp]
    start = min(timestamps) if timestamps else None
    end = max(timestamps) if timestamps else None

    color_map = collect_peer_color_map(messages, peer_map)
    photo_paths = peer_photo_paths or {}

    seen: set[tuple[Optional[int], str]] = set()
    entries: list[ParticipantEntry] = []
    for msg in messages:
        speaker = resolve_speaker(msg, peer_map)
        if speaker == "Unknown":
            continue
        peer_id = msg.author_id if msg.author_id is not None else msg.peer_id
        key = (peer_id, speaker)
        if key in seen:
            continue
        seen.add(key)
        solid, _ = color_map.get(peer_id) if peer_id is not None else (None, None)
        if solid is None:
            solid = peer_display_color(
                peer_map.get(peer_id)
                if peer_map and peer_id is not None
                else PeerInfo("peer", None),
                peer_id if peer_id is not None else 0,
            )[0]
        photo_path = photo_paths.get(peer_id) if peer_id is not None else None
        entries.append(ParticipantEntry(peer_id, speaker, solid, photo_path))
    title_entry = next((entry for entry in entries if entry.name == title), None)
    if title_entry is None:
        title_color, _ = peer_display_color(PeerInfo("peer", None), 0)
        title_entry = ParticipantEntry(None, title, title_color)
        entries.append(title_entry)
    elif title_entry.peer_id is None:
        entries.remove(title_entry)
        entries.append(title_entry)
    exported_at = datetime.now(tz=timezone.utc).isoformat()
    return HtmlStats(
        message_count=len(messages),
        start_iso=start.isoformat() if start else None,
        end_iso=end.isoformat() if end else None,
        participants=tuple(entries),
        exported_at=exported_at,
    )


def render_markdown(
    messages: list[Message],
    title: str,
    out_path: Path,
    options: Optional[RenderOptions] = None,
) -> None:
    """Export messages to Markdown."""
    options = options or RenderOptions()
    peer_map = options.peer_map
    show_direction = options.show_direction
    tz = options.tz
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        handle.write(f"# Telegram Chat History: {title}\n\n")
        handle.write(f"**Exported:** {datetime.now(tz=timezone.utc).isoformat()}\n\n")
        handle.write(f"**Total Messages:** {len(messages)}\n\n")
        handle.write("---\n")

        current_date = None
        for msg in messages:
            local_ts = to_local(msg.timestamp, tz) if tz else msg.timestamp
            if local_ts:
                msg_date = local_ts.strftime("%Y-%m-%d")
            else:
                msg_date = "Unknown Date"

            if current_date != msg_date:
                current_date = msg_date
                header = local_ts.strftime("%A, %B %d, %Y") if local_ts else "Unknown"
                handle.write(f"\n## {header}\n\n")

            time_str = local_ts.isoformat() if local_ts else "??:??:??"
            speaker = resolve_speaker(msg, peer_map)
            speaker_url = _speaker_url(msg, peer_map)

            direction = ""
            if show_direction:
                direction = f" ({msg.speaker_hint()})"

            handle.write(
                f"**{time_str} — {_md_link(speaker, speaker_url)}{direction}**\n\n"
            )
            via_segments = _via_bot_segments(msg, peer_map)
            if via_segments:
                rendered_via = "".join(
                    _md_link(seg.text, seg.url) for seg in via_segments
                )
                handle.write(f"*{rendered_via}*\n\n")
            reply_line = _render_markdown_reply_line(msg, peer_map, out_path)
            if reply_line:
                handle.write(f"{reply_line}\n\n")
            forwarded_segments = build_forwarded_segments(msg, peer_map, tz)
            if forwarded_segments:
                rendered_forwarded = "".join(
                    _md_link(seg.text, seg.url) for seg in forwarded_segments
                )
                handle.write(f"*{rendered_forwarded}*\n\n")
            if msg.text:
                handle.write(f"{linkify_markdown(msg.text)}\n\n")
            for attachment in msg.attachments:
                handle.write(
                    f"{_render_markdown_attachment(attachment, peer_map, msg.author_id)}\n\n"
                )
            poll_note = _render_poll_question_note_markdown(msg)
            if poll_note:
                handle.write(f"{poll_note}\n\n")


def render_csv(
    messages: list[Message],
    out_path: Path,
    peer_map: Optional[dict[int, PeerInfo]] = None,
    tz: Optional[object] = None,
) -> None:
    """Export messages to CSV."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "date",
                "time",
                "timestamp",
                "direction",
                "speaker",
                "text",
                "attachments",
                "forward_info",
                "reply_info",
                "peer_id",
                "author_id",
                "name_color",
                "via_bot",
            ]
        )
        for msg in messages:
            ts = to_local(msg.timestamp, tz) if tz else msg.timestamp
            date_str = ts.date().isoformat() if ts else ""
            time_str = ts.time().isoformat() if ts else ""
            timestamp = int(ts.timestamp()) if ts else ""
            speaker = resolve_speaker(msg, peer_map)
            writer.writerow(
                [
                    date_str,
                    time_str,
                    timestamp,
                    msg.speaker_hint(),
                    speaker,
                    msg.text,
                    json.dumps(
                        [
                            {
                                "kind": attachment.kind,
                                "filename": attachment.filename,
                                "mime_type": attachment.mime_type,
                                "path": attachment.exported_path,
                                "cache_key": attachment.cache_key,
                                "alternate_cache_keys": (
                                    attachment.alternate_cache_keys
                                ),
                                "selected_cache_key": attachment.selected_cache_key,
                                "source_path": attachment.source_path,
                                "url": attachment.url,
                                "metadata": _csv_safe_metadata(attachment.metadata),
                                **(
                                    {
                                        "action_summary": _build_action_summary(
                                            attachment.metadata or {},
                                            peer_map,
                                            msg.author_id,
                                        )
                                    }
                                    if attachment.kind == "action"
                                    and attachment.metadata
                                    else {}
                                ),
                            }
                            for attachment in msg.attachments
                        ],
                        ensure_ascii=False,
                    ),
                    (
                        json.dumps(
                            {
                                "author_id": msg.forward_info.author_id,
                                "author": _resolve_peer_name(
                                    msg.forward_info.author_id, peer_map
                                ),
                                "source_id": msg.forward_info.source_id,
                                "source": _resolve_peer_name(
                                    msg.forward_info.source_id, peer_map
                                ),
                                "source_message_peer_id": (
                                    msg.forward_info.source_message_peer_id
                                ),
                                "source_message_namespace": (
                                    msg.forward_info.source_message_namespace
                                ),
                                "source_message_id": (
                                    msg.forward_info.source_message_id
                                ),
                                "date": (
                                    to_local(msg.forward_info.date, tz).isoformat()
                                    if msg.forward_info.date and tz
                                    else (
                                        msg.forward_info.date.isoformat()
                                        if msg.forward_info.date
                                        else None
                                    )
                                ),
                                "author_signature": (msg.forward_info.author_signature),
                                "psa_type": msg.forward_info.psa_type,
                                "is_imported": msg.forward_info.is_imported,
                            },
                            ensure_ascii=False,
                        )
                        if msg.forward_info
                        else ""
                    ),
                    _csv_reply_info(msg, peer_map, tz),
                    msg.peer_id or "",
                    msg.author_id or "",
                    _csv_name_color(msg.author_id, peer_map),
                    _csv_via_bot(msg, peer_map),
                ]
            )


def render_html(
    messages: list[Message],
    title: str,
    out_path: Path,
    peer_map: Optional[dict[int, PeerInfo]] = None,
    peer_photo_paths: Optional[dict[int, str]] = None,
) -> None:
    """Export messages to a styled HTML transcript."""
    stats = build_html_stats(messages, title, peer_map, peer_photo_paths)
    color_map = collect_peer_color_map(messages, peer_map)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as handle:
        handle.write('<!doctype html><html><head><meta charset="utf-8">')
        handle.write(f"<title>{html.escape(title)}</title>")
        handle.write(HTML_BOOTSTRAP)
        handle.write(HTML_FONTS)
        handle.write(f"<style>{HTML_CSS}{render_peer_color_css(color_map)}</style>")
        handle.write("</head><body>")
        handle.write('<div class="background-blobs">')
        handle.write('<div class="blob blob-1"></div>')
        handle.write('<div class="blob blob-2"></div>')
        handle.write('<div class="blob blob-3"></div>')
        handle.write("</div>")
        handle.write('<div class="container">')
        _render_header(handle, title)
        _render_stats(handle, stats)
        _render_toolbar(handle)
        _render_messages(handle, messages, peer_map, peer_photo_paths)
        _render_footer(handle)
        handle.write("</div></body></html>")


def _primary_poll_question(msg: Message) -> Optional[str]:
    """Return the question text of the first poll attachment, if any."""
    for attachment in msg.attachments:
        if attachment.kind == "poll" and attachment.metadata:
            question = attachment.metadata.get("question")
            if isinstance(question, str):
                return question.strip()
    return None


def _render_poll_question_note_html(handle, msg: Message) -> None:
    """Render the message text as a note when it differs from the poll question."""
    poll_question = _primary_poll_question(msg)
    if poll_question is None or not msg.text or poll_question == msg.text.strip():
        return
    handle.write(f'<div class="meta">{html.escape(msg.text)}</div>')


def _render_poll_question_note_markdown(msg: Message) -> Optional[str]:
    """Return the message text as a Markdown note when it differs from the poll question."""
    poll_question = _primary_poll_question(msg)
    if poll_question is None or not msg.text or poll_question == msg.text.strip():
        return None
    return f"_{msg.text}_"


def _render_header(handle, title: str) -> None:
    handle.write('<header class="glass header-panel">')
    handle.write('<div class="brand">')
    handle.write('<div class="logo">💬</div>')
    handle.write('<div class="title-area">')
    handle.write(f"<h1>{html.escape(title)}</h1>")
    handle.write('<p class="subtitle">Recovery export for Telegram Desktop (macOS)</p>')
    handle.write("</div></div>")
    handle.write(
        '<div class="badge glass"><span class="dot"></span>'
        '<span class="text">Ready</span></div>'
    )
    handle.write("</header>")


def _render_stats(handle, stats: HtmlStats) -> None:
    handle.write('<section class="stats-grid">')
    handle.write(
        '<div class="stat-card glass">'
        '<div class="stat-info"><span class="label">Messages</span>'
        f'<span class="value">{stats.message_count}</span></div></div>'
    )
    handle.write(
        '<div class="stat-card glass">'
        '<div class="stat-info"><span class="label">Date Range</span>'
        '<span class="value mono">'
        f'<time class="local-date" datetime="{html.escape(stats.start_iso)}">'
        f"{html.escape(stats.start_iso or '')}</time>"
        " → "
        f'<time class="local-date" datetime="{html.escape(stats.end_iso)}">'
        f"{html.escape(stats.end_iso or '')}</time>"
        "</span></div></div>"
    )
    handle.write(
        '<div class="stat-card glass">'
        '<div class="stat-info"><span class="label">Participants</span>'
        '<span class="value"><div class="participant-list">'
    )
    for entry in stats.participants:
        safe_name = html.escape(entry.name)
        safe_color = html.escape(entry.color)
        handle.write(f'<span class="participant" style="color:{safe_color}">')
        _render_avatar_html(
            handle,
            entry.photo_path,
            _peer_initial(entry.name),
            entry.color,
            css_class="participant-avatar",
        )
        handle.write(
            f'<span class="swatch" style="background:{safe_color}"></span>'
            f"{safe_name}</span>"
        )
    handle.write("</div></span></div></div>")
    handle.write(
        '<div class="stat-card glass">'
        '<div class="stat-info"><span class="label">Exported</span>'
        '<span class="value mono">'
        f'<time class="local-datetime" datetime="{html.escape(stats.exported_at)}">'
        f"{html.escape(stats.exported_at)}</time></span></div></div>"
    )
    handle.write("</section>")


def _render_toolbar(handle) -> None:
    handle.write('<div class="toolbar glass">')
    handle.write('<label for="day-select">Jump to date</label>')
    handle.write('<select id="day-select" disabled>')
    handle.write('<option value="">Loading...</option>')
    handle.write("</select>")
    handle.write("</div>")


def _render_reply_preview_html(
    handle,
    msg: Message,
    reply: ReplyInfo,
    peer_map: Optional[dict[int, PeerInfo]],
) -> None:
    """Render the ``.reply-quote`` block at the top of the bubble.

    The href is an in-page ``#msg-{mid}`` anchor when the reply points
    to a message in the current chat, or a ``t.me/.../{mid}`` URL when
    it points to a message in a different chat. The block falls back to
    a plain (no-link) "Message unavailable" cell when the t7 lookup
    could not find the original.
    """
    if reply.target_unavailable:
        handle.write('<div class="reply-quote unavailable">Message unavailable</div>')
        return

    href: Optional[str] = None
    target_peer = peer_map.get(reply.target_peer_id) if peer_map else None
    if reply.is_intra_chat:
        href = f"#msg-{reply.target_message_id}"
    elif target_peer is not None:
        href = peer_url(
            target_peer, reply.target_peer_id, message_id=reply.target_message_id
        )

    author_name = _peer_label_for_reply(reply, peer_map)
    if reply.target_author_id is not None and peer_map is not None:
        author_name = _peer_label(reply.target_author_id, peer_map)
    if not author_name:
        author_name = "Unknown"

    forwarded_from_name = _reply_forwarded_from_name(reply, peer_map)
    via_bot_label, via_bot_url = _reply_via_bot_link(reply, peer_map)

    text = _reply_preview_text(reply)
    emoji = _reply_emoji_for_kind(reply.target_attachment_kind)

    via_html = ""
    if via_bot_label:
        label = html.escape(via_bot_label)
        if via_bot_url is not None:
            safe_url = html.escape(via_bot_url, quote=True)
            via_html = (
                f' <span class="reply-via">· via '
                f'<a href="{safe_url}" target="_blank" rel="noopener">'
                f"{label}</a></span>"
            )
        else:
            via_html = f' <span class="reply-via">· via {label}</span>'

    parts: list[str] = []
    if forwarded_from_name:
        parts.append(
            f'<div class="reply-author">'
            f"{html.escape(author_name)} "
            f'<span class="reply-forwarded">'
            f"· Forwarded from {html.escape(forwarded_from_name)}"
            f"</span>"
            f"{via_html}"
            f"</div>"
        )
    else:
        if via_html:
            parts.append(
                f'<div class="reply-author">{html.escape(author_name)}{via_html}</div>'
            )
        else:
            parts.append(f'<div class="reply-author">{html.escape(author_name)}</div>')
    snippet_html = ""
    if emoji:
        snippet_html += f'<span class="reply-emoji">{html.escape(emoji)}</span>'
    if text:
        snippet_html += f'<span class="reply-text">{html.escape(text)}</span>'
    if snippet_html:
        parts.append(f'<div class="reply-snippet">{snippet_html}</div>')
    elif not emoji:
        parts.append(
            '<div class="reply-snippet"><span class="reply-text">Message</span></div>'
        )

    inner_html = "".join(parts)
    if href is None:
        handle.write(f'<div class="reply-quote">{inner_html}</div>')
    else:
        safe_href = html.escape(href, quote=True)
        handle.write(
            f'<div class="reply-quote">'
            f'<a class="reply-link" href="{safe_href}">{inner_html}</a>'
            f"</div>"
        )


def _peer_label_for_reply(
    reply: ReplyInfo, peer_map: Optional[dict[int, PeerInfo]]
) -> str:
    """Best-effort display name for the original's author."""
    if reply.target_author_id is not None and peer_map is not None:
        return _peer_label(reply.target_author_id, peer_map)
    return "Unknown"


def _reply_forwarded_from_name(
    reply: ReplyInfo, peer_map: Optional[dict[int, PeerInfo]]
) -> Optional[str]:
    """Display name for the *original* author of a forwarded reply target.

    Returns ``None`` when the target is not a forward, or when no
    resolvable name is available. Used to inline a "Forwarded from X"
    fragment in the reply preview.
    """
    info = reply.target_forward_info
    if info is None:
        return None
    source = (
        peer_map.get(info.source_id)
        if info.source_id is not None and peer_map
        else None
    )
    if source is not None:
        return source.name
    author = (
        peer_map.get(info.author_id)
        if info.author_id is not None and peer_map
        else None
    )
    if author is not None:
        return author.name
    if info.author_signature:
        return info.author_signature
    fallback_id = info.source_id or info.author_id
    if fallback_id is not None:
        return f"peer {fallback_id}"
    return None


def _reply_via_bot_link(
    reply: ReplyInfo, peer_map: Optional[dict[int, PeerInfo]]
) -> tuple[Optional[str], Optional[str]]:
    """Display name and ``t.me`` link for the *inline bot* a reply target was sent via.

    Returns ``(None, None)`` when the target carries no inline-bot
    attribute. When ``target_via_bot_id`` is resolvable through
    ``peer_map`` the bot's display name is taken from there and the
    link is a ``t.me/<username>`` URL (when the bot has one);
    otherwise the raw ``target_via_bot_title`` from the attribute is
    used as the label with no link (covers the secret-chat case where
    only the name is stored).
    """
    bot_id = reply.target_via_bot_id
    bot_title = reply.target_via_bot_title
    if bot_id is None and not bot_title:
        return None, None

    display_peer: Optional[PeerInfo] = None
    display_peer_id: Optional[int] = None
    if bot_id is not None and peer_map:
        display_peer = peer_map.get(bot_id)
        display_peer_id = bot_id
    if display_peer is not None:
        label = display_peer.name
    elif bot_title:
        label = bot_title
    else:
        label = f"bot {bot_id}"

    url = (
        peer_url(display_peer, display_peer_id)
        if display_peer is not None and display_peer_id is not None
        else None
    )
    return label, url


def _reply_preview_text(reply: ReplyInfo) -> str:
    """Return the snippet / full text / media-caption for a reply preview.

    Preference order:

    1. The user-selected snippet (``reply.target_snippet``), when set.
    2. The original's full text (``reply.target_text``), truncated to
       200 chars.
    3. The composed media caption (``{kind word}{ · meta}``) when the
       original had a first attachment and no text.
    4. Empty string (caller decides whether to render anything).
    """
    if reply.target_snippet:
        return _truncate_reply_text(reply.target_snippet)
    if reply.target_text:
        return _truncate_reply_text(reply.target_text)
    if reply.target_attachment_kind:
        label = _reply_kind_label(reply.target_attachment_kind)
        meta = _format_reply_meta(reply)
        if meta:
            return f"{label} · {meta}"
        return label
    return ""


def _render_messages(
    handle,
    messages: list[Message],
    peer_map: Optional[dict[int, PeerInfo]],
    peer_photo_paths: Optional[dict[int, str]] = None,
) -> None:
    handle.write('<div class="chat-card glass" id="chat-card">')
    photo_paths = peer_photo_paths or {}
    color_map = collect_peer_color_map(messages, peer_map)
    for msg in messages:
        iso = msg.timestamp.isoformat() if msg.timestamp else ""
        time_str = msg.timestamp.strftime("%H:%M:%S") if msg.timestamp else "??:??:??"
        speaker = resolve_speaker(msg, peer_map)
        speaker_url = _speaker_url(msg, peer_map)
        direction = "out" if msg.outgoing is True else "in"
        peer_id = msg.author_id if msg.author_id is not None else msg.peer_id
        peer_id_attr = (
            f' data-peer-id="{html.escape(str(peer_id))}"'
            if peer_id is not None
            else ""
        )
        msg_id_attr = (
            f' id="msg-{html.escape(str(msg.message_id), quote=True)}"'
            if msg.message_id is not None
            else ""
        )
        if iso:
            time_inner = (
                f'<time class="local-time" datetime="{html.escape(iso)}">'
                f"{html.escape(time_str)}</time>"
            )
            if msg.message_id is not None:
                safe_anchor = html.escape(f"#msg-{msg.message_id}", quote=True)
                time_el = (
                    f'<a class="time-anchor" href="{safe_anchor}">{time_inner}</a>'
                )
            else:
                time_el = time_inner
        else:
            time_el = "??:??:??"
        handle.write(
            f'<div class="msg {direction}" data-iso="{html.escape(iso)}"'
            f"{peer_id_attr}{msg_id_attr}>"
        )
        if peer_id is not None:
            peer_info = peer_map.get(peer_id) if peer_map else None
            if peer_info is None:
                peer_info = PeerInfo("peer", None)
            solid, _ = color_map.get(peer_id) or peer_display_color(peer_info, peer_id)
            _render_avatar_html(
                handle,
                photo_paths.get(peer_id),
                _peer_initial(peer_info.name),
                solid,
                css_class="msg-avatar",
            )
        handle.write('<div class="bubble">')
        handle.write(
            f'<div class="meta">[{time_el}] '
            f'<span class="speaker">{_html_link(speaker, speaker_url)}</span></div>'
        )
        via_segments = _via_bot_segments(msg, peer_map)
        if via_segments:
            handle.write('<div class="meta via-bot">')
            for seg in via_segments:
                _render_forwarded_segment_html(handle, seg)
            handle.write("</div>")
        if msg.reply_info is not None:
            _render_reply_preview_html(handle, msg, msg.reply_info, peer_map)
        forwarded_segments = build_forwarded_segments(msg, peer_map)
        if forwarded_segments:
            handle.write('<div class="meta forwarded">')
            for seg in forwarded_segments:
                _render_forwarded_segment_html(handle, seg)
            handle.write("</div>")
        if msg.text:
            handle.write(linkify_html(msg.text))
        for attachment in msg.attachments:
            _render_html_attachment(handle, attachment, peer_map, msg.author_id)
        _render_poll_question_note_html(handle, msg)
        handle.write("</div></div>")
    handle.write("</div>")

    handle.write('<button id="back-top" class="back-top">Back to top</button>')
    handle.write(_back_to_top_script())


def _render_html_attachment(
    handle,
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
    author_id: Optional[int] = None,
) -> None:
    if attachment.kind == "webpage" and attachment.metadata:
        _render_html_webpage_preview(handle, attachment)
        return
    if attachment.kind == "poll" and attachment.metadata:
        _render_html_poll(handle, attachment.metadata, peer_map)
        return
    if attachment.kind == "action":
        _render_html_service_message(handle, attachment, peer_map, author_id)
        return
    if attachment.kind == "contact":
        _render_html_contact_card(handle, attachment, peer_map)
        return
    if attachment.kind == "dice":
        _render_html_dice(handle, attachment)
        return
    if attachment.kind == "expired_content":
        _render_html_expired_content(handle, attachment)
        return
    if attachment.kind == "game":
        _render_html_game_card(handle, attachment)
        return
    if attachment.kind == "invoice":
        _render_html_invoice_card(handle, attachment)
        return
    if attachment.kind == "giveaway":
        _render_html_giveaway_card(handle, attachment, peer_map)
        return
    if attachment.kind == "giveaway_result":
        _render_html_giveaway_result_card(handle, attachment)
        return
    if attachment.kind == "live_stream":
        _render_html_live_stream(handle, attachment)
        return
    if attachment.kind == "map":
        _render_html_map_pin(handle, attachment, peer_map)
        return
    if attachment.kind == "paid_content":
        _render_html_paid_content(handle, attachment)
        return
    if attachment.kind == "story":
        _render_html_story(handle, attachment, peer_map)
        return
    if attachment.kind == "todo":
        _render_html_todo_card(handle, attachment, peer_map)
        return
    if attachment.kind == "unsupported":
        _render_html_unsupported(handle, attachment)
        return
    label = html.escape(_attachment_label(attachment))
    if attachment.url:
        url = html.escape(attachment.url, quote=True)
        handle.write(f'<div><a href="{url}">{label}</a></div>')
        return
    description = _attachment_description(attachment)
    if attachment.exported_path:
        path = html.escape(attachment.exported_path, quote=True)
        mime_type = html.escape(attachment.mime_type or "", quote=True)
        dim_attr = ""
        if (
            isinstance(attachment.width, int)
            and isinstance(attachment.height, int)
            and attachment.width > 0
            and attachment.height > 0
        ):
            dim_attr = f' width="{attachment.width}" height="{attachment.height}"'
        poster_attr = _html_video_poster_attr(attachment)
        if attachment.kind == "video_message" and not attachment.is_preview_fallback:
            handle.write(
                f'<div class="video-message" data-video-message>'
                f'<video preload="metadata" playsinline loop'
                f"{poster_attr}{dim_attr}>"
                f'<source src="{path}" type="{mime_type}"></video>'
                f'<button class="play-overlay" type="button" '
                f'aria-label="Play video message">'
                f'<svg viewBox="0 0 24 24" aria-hidden="true">'
                f'<path d="M8 5v14l11-7z"/></svg>'
                f"</button></div>"
            )
        elif attachment.kind == "video_message":
            handle.write(
                f'<div class="video-message video-message-preview">'
                f'<img src="{path}" alt="{label}" loading="lazy" '
                f'decoding="async"{dim_attr}></div>'
            )
        elif attachment.is_image():
            handle.write(
                f'<div><img src="{path}" alt="{label}" loading="lazy" '
                f'decoding="async"{dim_attr}></div>'
            )
        elif attachment.is_video():
            handle.write(
                f'<div><video controls preload="none"{poster_attr}{dim_attr}>'
                f'<source src="{path}" '
                f'type="{mime_type}"></video></div>'
            )
        elif attachment.is_audio():
            handle.write(
                f'<div><audio controls preload="none"><source src="{path}" '
                f'type="{mime_type}"></audio></div>'
            )
        else:
            handle.write(f'<div><a download href="{path}">{label}</a></div>')
        handle.write(f'<div class="meta file-meta">{html.escape(description)}</div>')
        return
    handle.write(f'<div class="meta">{html.escape(description)}</div>')


def _html_video_poster_attr(attachment: Attachment) -> str:
    """Return a poster attribute for a copied image preview, if available."""
    preview = attachment.preview_image
    if preview is None or not preview.exported_path or not preview.is_image():
        return ""
    return f' poster="{html.escape(preview.exported_path, quote=True)}"'


def _render_html_webpage_preview(handle, attachment: Attachment) -> None:
    """Render a loaded-content webpage as an HTML link-preview card."""
    metadata = attachment.metadata or {}
    url = html.escape(attachment.url or "", quote=True)
    raw_title = (
        metadata.get("title") or metadata.get("display_url") or attachment.url or ""
    )
    title = html.escape(str(raw_title))

    handle.write('<div class="link-preview">')

    site_name = metadata.get("site_name")
    if isinstance(site_name, str) and site_name:
        handle.write(f'<div class="link-preview-site">{html.escape(site_name)}</div>')

    handle.write(f'<a class="link-preview-title" href="{url}">{title}</a>')

    author = metadata.get("author")
    duration = metadata.get("duration")
    if isinstance(author, str) and author:
        author_html = html.escape(author)
    else:
        author_html = None
    duration_html = (
        html.escape(_format_duration(int(duration)))
        if isinstance(duration, int) and not isinstance(duration, bool)
        else None
    )
    if author_html is not None or duration_html is not None:
        sep = " · " if author_html is not None and duration_html is not None else ""
        meta_text = (author_html or "") + sep + (duration_html or "")
        handle.write(f'<div class="link-preview-meta">{meta_text}</div>')

    description = metadata.get("description")
    if isinstance(description, str) and description:
        handle.write(f'<div class="link-preview-desc">{html.escape(description)}</div>')

    preview_image = attachment.preview_image
    if preview_image is not None and preview_image.exported_path:
        img_path = html.escape(preview_image.exported_path, quote=True)
        img_label = html.escape(
            preview_image.filename or str(raw_title) or "preview image"
        )
        dim_attr = ""
        if (
            isinstance(preview_image.width, int)
            and isinstance(preview_image.height, int)
            and preview_image.width > 0
            and preview_image.height > 0
        ):
            dim_attr = f' width="{preview_image.width}" height="{preview_image.height}"'
        handle.write(
            f'<img src="{img_path}" alt="{img_label}" loading="lazy" '
            f'decoding="async"{dim_attr}>'
        )
        handle.write(
            f'<div class="meta file-meta">'
            f"{html.escape(_attachment_description(preview_image))}</div>"
        )

    preview_video = attachment.preview_video
    if preview_video is not None and preview_video.exported_path:
        vid_path = html.escape(preview_video.exported_path, quote=True)
        vid_mime = html.escape(preview_video.mime_type or "", quote=True)
        dim_attr = ""
        if (
            isinstance(preview_video.width, int)
            and isinstance(preview_video.height, int)
            and preview_video.width > 0
            and preview_video.height > 0
        ):
            dim_attr = f' width="{preview_video.width}" height="{preview_video.height}"'
        if preview_video.kind == "video_message":
            handle.write(
                f'<video preload="metadata" playsinline loop{dim_attr}>'
                f'<source src="{vid_path}" type="{vid_mime}"></video>'
            )
        else:
            handle.write(
                f'<video controls preload="none"{dim_attr}>'
                f'<source src="{vid_path}" type="{vid_mime}"></video>'
            )
        handle.write(
            f'<div class="meta file-meta">'
            f"{html.escape(_attachment_description(preview_video))}</div>"
        )

    handle.write("</div>")


def _render_html_contact_card(
    handle,
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
) -> None:
    """Render a contact as a ``.contact-card`` div with optional vCard link."""
    metadata = attachment.metadata or {}
    first_name = (metadata.get("first_name") or "").strip()
    last_name = (metadata.get("last_name") or "").strip()
    phone = (metadata.get("phone_number") or "").strip()
    name = " ".join(part for part in (first_name, last_name) if part).strip()
    raw_peer_id = metadata.get("peer_id")
    peer_url = (
        _peer_url_for(int(raw_peer_id), peer_map)
        if isinstance(raw_peer_id, int)
        and not isinstance(raw_peer_id, bool)
        and raw_peer_id != 0
        else None
    )
    handle.write('<div class="contact-card">')
    name_html = html.escape(name) if name else html.escape(phone) or "Contact"
    bits: list[str] = ["👤 "]
    if peer_url:
        safe_url = html.escape(peer_url, quote=True)
        bits.append(
            f'<a href="{safe_url}" target="_blank" rel="noopener">'
            f"<strong>{name_html}</strong></a>"
        )
    else:
        bits.append(f"<strong>{name_html}</strong>")
    if phone:
        bits.append(f" · {html.escape(phone)}")
    if peer_url:
        bits.append(
            f' · <a href="{html.escape(peer_url, quote=True)}" '
            f'target="_blank" rel="noopener">Open chat</a>'
        )
    if attachment.vcard_path:
        vcard_url = html.escape(attachment.vcard_path, quote=True)
        bits.append(f' · <a href="{vcard_url}" download>vCard (.vcf)</a>')
    handle.write("".join(bits))
    handle.write("</div>")


def _render_html_dice(handle, attachment: Attachment) -> None:
    """Render a dice as a service-style ``.service-msg`` line."""
    metadata = attachment.metadata or {}
    emoji = metadata.get("emoji") or "🎲"
    value = metadata.get("value")
    ton_amount = metadata.get("ton_amount")
    inner_bits: list[str] = []
    if value is not None:
        inner_bits.append(f"{html.escape(emoji)} <em>{html.escape(str(value))}</em>")
    else:
        inner_bits.append(html.escape(emoji))
    if ton_amount:
        inner_bits.append(f" · {html.escape(str(ton_amount))} TON")
    handle.write(f'<div class="service-msg">{"".join(inner_bits)}</div>')


def _render_html_expired_content(handle, attachment: Attachment) -> None:
    """Render an expired-content placeholder as a service-style line."""
    metadata = attachment.metadata or {}
    label = metadata.get("label") or "media"
    handle.write(
        f'<div class="service-msg">(<em>expired {html.escape(str(label))}</em>)</div>'
    )


def _render_html_game_card(handle, attachment: Attachment) -> None:
    """Render a game as a ``.game-card`` div with optional thumbnail."""
    metadata = attachment.metadata or {}
    title = (metadata.get("title") or "").strip() or "Game"
    description = (metadata.get("description") or "").strip()
    handle.write('<div class="game-card">')
    handle.write(f'<div class="game-title">{html.escape(title)}</div>')
    if description:
        handle.write(f'<div class="game-desc">{html.escape(description)}</div>')
    image = attachment.preview_image
    if image and image.exported_path:
        img_path = html.escape(image.exported_path, quote=True)
        dim_attr = ""
        if (
            isinstance(image.width, int)
            and isinstance(image.height, int)
            and image.width > 0
            and image.height > 0
        ):
            dim_attr = f' width="{image.width}" height="{image.height}"'
        handle.write(
            f'<img src="{img_path}" alt="{html.escape(title)}" '
            f'loading="lazy" decoding="async"{dim_attr}>'
        )
        handle.write(
            f'<div class="meta file-meta">'
            f"{html.escape(_attachment_description(image))}</div>"
        )
    handle.write("</div>")


def _render_html_invoice_card(handle, attachment: Attachment) -> None:
    """Render an invoice as a ``.invoice-card`` div with title, amount, photo."""
    metadata = attachment.metadata or {}
    title = (metadata.get("title") or "").strip() or "Invoice"
    amount_str = _format_invoice_amount(
        metadata.get("total_amount"), metadata.get("currency") or ""
    )
    description = (metadata.get("description") or "").strip()
    photo = attachment.preview_image
    handle.write('<div class="invoice-card">')
    if photo and photo.exported_path:
        img_path = html.escape(photo.exported_path, quote=True)
        dim_attr = ""
        if (
            isinstance(photo.width, int)
            and isinstance(photo.height, int)
            and photo.width > 0
            and photo.height > 0
        ):
            dim_attr = f' width="{photo.width}" height="{photo.height}"'
        handle.write(
            f'<img src="{img_path}" alt="{html.escape(title)}" '
            f'loading="lazy" decoding="async"{dim_attr}>'
        )
    handle.write(f'<div class="invoice-title">{html.escape(title)}</div>')
    handle.write(f'<div class="invoice-amount">{html.escape(amount_str)}</div>')
    if description:
        handle.write(f'<div class="invoice-desc">{html.escape(description)}</div>')
    handle.write("</div>")


def _render_html_giveaway_card(
    handle,
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
) -> None:
    """Render a giveaway as a ``.giveaway-card`` div with the canonical line."""
    text = _giveaway_text(attachment)
    handle.write('<div class="giveaway-card">')
    handle.write(f'<div class="giveaway-title">{html.escape(text)}</div>')
    channels = (attachment.metadata or {}).get("channel_peer_ids") or []
    if peer_map and channels:
        names = _join_peer_names_html(list(channels), peer_map)
        if names:
            handle.write(f'<div class="giveaway-meta">Channels: {names}</div>')
    handle.write("</div>")


def _render_html_giveaway_result_card(handle, attachment: Attachment) -> None:
    """Render a giveaway result as a ``.giveaway-card`` div."""
    text = _render_markdown_giveaway_result(attachment)
    handle.write('<div class="giveaway-card">')
    handle.write(f'<div class="giveaway-title">{html.escape(text)}</div>')
    handle.write("</div>")


def _render_html_live_stream(handle, attachment: Attachment) -> None:
    """Render a live stream placeholder as a service-style line."""
    handle.write('<div class="service-msg">📹 <em>Live stream</em></div>')


def _render_html_map_pin(
    handle,
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
) -> None:
    """Render a map pin as a ``.map-pin`` div with OpenStreetMap link."""
    metadata = attachment.metadata or {}
    latitude = metadata.get("latitude")
    longitude = metadata.get("longitude")
    venue = metadata.get("venue") or {}
    address = metadata.get("address") or {}
    live_timeout = metadata.get("live_timeout")
    title = (venue.get("title") or "").strip() or "Location"
    handle.write('<div class="map-pin">')
    handle.write(
        f'<div class="map-venue">📍 <strong>{html.escape(title)}</strong></div>'
    )
    if isinstance(address, dict):
        street = address.get("street")
        city = address.get("city")
        state = address.get("state")
        country = address.get("country")
        if street:
            handle.write(f'<div class="map-address">{html.escape(str(street))}</div>')
        city_state_country = ", ".join(part for part in (city, state, country) if part)
        if city_state_country:
            handle.write(
                f'<div class="map-address">{html.escape(city_state_country)}</div>'
            )
    if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
        map_url = html.escape(_osm_url(latitude, longitude), quote=True)
        handle.write(
            f'<div class="map-meta">{latitude:.5f}, {longitude:.5f} · '
            f'<a href="{map_url}" target="_blank" rel="noopener">'
            f"Open in OpenStreetMap</a>"
        )
        if isinstance(live_timeout, int) and live_timeout > 0:
            handle.write(f" · {html.escape(_live_until_label(live_timeout))}")
        handle.write("</div>")
    elif isinstance(live_timeout, int) and live_timeout > 0:
        handle.write(
            f'<div class="map-meta">'
            f"{html.escape(_live_until_label(live_timeout))}</div>"
        )
    handle.write("</div>")


def _render_html_paid_content(handle, attachment: Attachment) -> None:
    """Render a paid-content placeholder as a service-style line."""
    metadata = attachment.metadata or {}
    stars = metadata.get("stars_amount")
    bits: list[str] = ["💎 Paid media"]
    if isinstance(stars, int) and stars > 0:
        bits.append(f" · {stars} ★")
    handle.write(f'<div class="service-msg">{html.escape("".join(bits))}</div>')


def _render_html_story(
    handle,
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
) -> None:
    """Render a story as a ``.story`` div with a t.me link."""
    metadata = attachment.metadata or {}
    peer_id = metadata.get("peer_id")
    story_id = metadata.get("story_id")
    is_mention = bool(metadata.get("is_mention"))
    info = (
        peer_map.get(int(peer_id))
        if peer_map and isinstance(peer_id, int) and peer_id in peer_map
        else None
    )
    name = (
        info.name
        if info is not None
        else (f"peer {peer_id}" if isinstance(peer_id, int) else "Story")
    )
    username = info.username if info is not None else None
    if isinstance(story_id, int) and username:
        url = f"https://t.me/{username}/s/{story_id}"
    else:
        url = None
    handle.write('<div class="story">')
    if is_mention:
        handle.write(f"<strong>{html.escape(name)}</strong> mentioned you in a story ")
        if url:
            safe_url = html.escape(url, quote=True)
            handle.write(
                f'(<a href="{safe_url}" target="_blank" rel="noopener">open</a>)'
            )
    else:
        handle.write("📖 Story from ")
        if url:
            safe_url = html.escape(url, quote=True)
            handle.write(
                f'<a href="{safe_url}" target="_blank" rel="noopener">'
                f"{html.escape(name)}</a>"
            )
        else:
            handle.write(html.escape(name))
    handle.write("</div>")


def _render_html_todo_card(
    handle,
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
) -> None:
    """Render a todo as a ``.todo-card`` div with checkboxes + completions."""
    metadata = attachment.metadata or {}
    text = (metadata.get("text") or "").strip()
    items = metadata.get("items") or []
    completions = metadata.get("completions") or []
    flags = metadata.get("flags") or {}
    completed_ids = {
        completion.get("id")
        for completion in completions
        if completion.get("id") is not None
    }
    completion_by_id = {
        completion.get("id"): completion
        for completion in completions
        if completion.get("id") is not None
    }
    completed_count = sum(1 for item in items if item.get("id") in completed_ids)
    total = len(items)
    handle.write('<div class="todo-card">')
    if text:
        handle.write(f'<div class="todo-header">{html.escape(text)}</div>')
    flag_notes: list[str] = []
    if flags.get("others_can_append"):
        flag_notes.append("others can append")
    if flags.get("others_can_complete"):
        flag_notes.append("others can complete")
    if flag_notes:
        handle.write(
            f'<div class="todo-flags">{html.escape(" · ".join(flag_notes))}</div>'
        )
    handle.write(
        f'<div class="todo-progress">'
        f"{html.escape(f'{completed_count} of {total} completed')}</div>"
    )
    handle.write('<ul class="todo-items">')
    for item in items:
        item_id = item.get("id")
        is_done = item_id in completed_ids
        item_text = item.get("text", "")
        checked = " checked" if is_done else ""
        handle.write(
            f'<li><input type="checkbox" disabled{checked}> {html.escape(item_text)}'
        )
        if is_done:
            completion = completion_by_id.get(item_id) or {}
            completer_id = completion.get("completed_by")
            completer_name = (
                _peer_label(int(completer_id), peer_map)
                if isinstance(completer_id, int) and completer_id != 0
                else None
            )
            raw_date = completion.get("date")
            date_label = ""
            if isinstance(raw_date, int) and raw_date > 0:
                date_label = datetime.fromtimestamp(raw_date, tz=timezone.utc).strftime(
                    "%Y-%m-%d"
                )
            note_bits: list[str] = []
            if completer_name:
                note_bits.append(html.escape(completer_name))
            if date_label:
                note_bits.append(html.escape(date_label))
            if note_bits:
                handle.write(
                    f' <span class="completion">— {", ".join(note_bits)}</span>'
                )
        handle.write("</li>")
    handle.write("</ul>")
    handle.write("</div>")


def _render_html_unsupported(handle, attachment: Attachment) -> None:
    """Render an unsupported-media placeholder as a service-style line."""
    handle.write('<div class="service-msg">❓ (<em>unsupported media</em>)</div>')


def _back_to_top_script() -> str:
    script = """
    <script>
    (function() {
      const formatTime = (iso) => {
        const d = new Date(iso);
        if (isNaN(d)) return iso;
        return d.toLocaleTimeString(undefined, {
          hour: '2-digit', minute: '2-digit', second: '2-digit',
          hour12: false
        });
      };
      const formatDateTime = (iso) => {
        const d = new Date(iso);
        if (isNaN(d)) return iso;
        return d.toLocaleString(undefined, {
          year: 'numeric', month: 'short', day: 'numeric',
          hour: '2-digit', minute: '2-digit', second: '2-digit',
          hour12: false
        });
      };
      const formatDayLabel = (d) => d.toLocaleDateString(undefined, {
        weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
      });
      const formatDateOnly = (d) => d.toLocaleDateString(undefined, {
        year: 'numeric', month: '2-digit', day: '2-digit'
      });
      const dayKey = (d) => {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return `day-${y}-${m}-${day}`;
      };

      document.querySelectorAll('time.local-time').forEach(el => {
        const iso = el.getAttribute('datetime');
        if (iso) el.textContent = formatTime(iso);
      });
      document.querySelectorAll('time.local-datetime').forEach(el => {
        const iso = el.getAttribute('datetime');
        if (iso) el.textContent = formatDateTime(iso);
      });
      document.querySelectorAll('time.local-date').forEach(el => {
        const iso = el.getAttribute('datetime');
        if (iso) {
          const d = new Date(iso);
          if (!isNaN(d)) el.textContent = formatDateOnly(d);
        }
      });

      const card = document.getElementById('chat-card');
      const sel = document.getElementById('day-select');
      if (card) {
        const messages = Array.from(card.querySelectorAll('.msg[data-iso]'));
        const dayEntries = [];
        let lastKey = null;
        messages.forEach(msg => {
          const iso = msg.getAttribute('data-iso');
          if (!iso) return;
          const d = new Date(iso);
          if (isNaN(d)) return;
          const key = dayKey(d);
          if (key !== lastKey) {
            lastKey = key;
            const label = formatDayLabel(d);
            const header = document.createElement('div');
            header.className = 'day day-header';
            header.id = key;
            header.setAttribute('data-day-key', key);
            header.textContent = label;
            card.insertBefore(header, msg);
            dayEntries.push({ id: key, label: label, date: d });
          }
        });

        if (sel) {
          sel.innerHTML = '';
          const placeholder = document.createElement('option');
          placeholder.value = '';
          placeholder.textContent = 'Select a date...';
          sel.appendChild(placeholder);
          dayEntries
            .sort((a, b) => a.date - b.date)
            .forEach(entry => {
              const opt = document.createElement('option');
              opt.value = entry.id;
              opt.textContent = entry.label;
              sel.appendChild(opt);
            });
          sel.disabled = false;
        }
      }
    })();
    const sel = document.getElementById('day-select');
    if (sel) {
      sel.addEventListener('change', () => {
        const id = sel.value;
        if (!id) return;
        const el = document.getElementById(id);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      });
    }
    const back = document.getElementById('back-top');
    const toggleBack = () => {
      if (window.scrollY > 400) {
        back.classList.add('show');
      } else {
        back.classList.remove('show');
      }
    };
    window.addEventListener('scroll', toggleBack);
    toggleBack();
    if (back) {
      back.addEventListener('click', () => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
      });
    }
    document.querySelectorAll('[data-video-message]').forEach(container => {
      const video = container.querySelector('video');
      const toggle = () => {
        if (video.paused || video.ended) {
          video.play();
        } else {
          video.pause();
        }
      };
      container.addEventListener('click', toggle);
      video.addEventListener('play', () => container.classList.add('playing'));
      video.addEventListener('pause', () => container.classList.remove('playing'));
      video.addEventListener('ended', () => container.classList.remove('playing'));
    });
    </script>
    """
    return script


def _render_footer(handle) -> None:
    handle.write(
        '<footer style="margin-top:24px;color:var(--muted);font-size:12px;">'
        "Generated by Telegram Message Exporter"
        "</footer>"
    )
