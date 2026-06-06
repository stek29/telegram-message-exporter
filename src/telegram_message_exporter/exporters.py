"""Export helpers for Markdown, CSV, and HTML."""

from __future__ import annotations

import csv
import html
import json
import re
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import Attachment, ForwardedSegment, Message, PeerInfo
from .postbox import peer_url
from .utils import linkify_html, linkify_markdown, to_local

HTML_BOOTSTRAP = (
    '<link rel="stylesheet" '
    'href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">'
)
HTML_FONTS = (
    '<link rel="stylesheet" '
    'href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&'
    'family=JetBrains+Mono:wght@400;600&display=swap">'
)

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
.bubble {
  padding: 12px 16px;
  border-radius: 14px;
  max-width: 72%;
  border: 1px solid rgba(148, 163, 184, 0.2);
  background: var(--bubble-in);
  box-shadow: 0 8px 20px var(--bubble-shadow);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.msg.out .bubble { background: var(--bubble-out); }
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
}
.video-message video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 50%;
}
.meta { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.forwarded {
  padding-left: 8px;
  border-left: 2px solid var(--accent);
  color: #bae6fd;
}
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
"""


@dataclass(frozen=True)
class HtmlStats:
    """Computed stats for HTML output."""

    message_count: int
    start_iso: Optional[str]
    end_iso: Optional[str]
    participants: str
    exported_at: str


@dataclass(frozen=True)
class RenderOptions:
    """Optional rendering preferences."""

    peer_map: Optional[dict[int, PeerInfo]] = None
    show_direction: bool = False
    tz: Optional[object] = None


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
            cache_keys = tuple(
                key
                for key in (
                    attachment.cache_key,
                    *attachment.alternate_cache_keys,
                )
                if key
            )
            direct_source = (
                Path(attachment.source_path).expanduser()
                if attachment.source_path
                else None
            )
            if not cache_keys and (
                direct_source is None or not direct_source.is_file()
            ):
                updated_attachments.append(attachment)
                continue
            selected_key = next(
                (key for key in cache_keys if (media_dir / key).is_file()),
                None,
            )
            if selected_key is not None:
                source = media_dir / selected_key
                target_name = selected_key
            elif direct_source is not None and direct_source.is_file():
                source = direct_source
                target_name = (
                    attachment.cache_key or direct_source.name or "local-attachment"
                )
            else:
                updated_attachments.append(attachment)
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / target_name
            if target_name not in copied_keys:
                shutil.copy2(source, target)
                copied_keys.add(target_name)
                copied_count += 1
            updated_attachments.append(
                replace(
                    attachment,
                    exported_path=(Path(target_dir.name) / target_name).as_posix(),
                )
            )
        updated_messages.append(
            replace(message, attachments=tuple(updated_attachments))
        )
    return updated_messages, copied_count


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


def _attachment_label(attachment: Attachment) -> str:
    return attachment.filename or attachment.kind.replace("_", " ").title()


def _attachment_description(attachment: Attachment) -> str:
    details = [attachment.kind]
    if attachment.filename:
        details.append(attachment.filename)
    if attachment.mime_type:
        details.append(attachment.mime_type)
    if attachment.cache_key and not attachment.exported_path:
        details.append(f"cache: {attachment.cache_key}")
    return " | ".join(details)


def _render_markdown_attachment(
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
) -> str:
    label = _attachment_label(attachment)
    if attachment.url:
        return f"[{label}]({attachment.url})"
    if attachment.exported_path:
        if attachment.kind == "image":
            return f"![{label}]({attachment.exported_path})"
        return f"[{label}]({attachment.exported_path})"
    if attachment.kind == "poll" and attachment.metadata:
        return _render_markdown_poll(attachment.metadata, peer_map)
    return f"`[{_attachment_description(attachment)}]`"


def _peer_names(
    peer_ids: list[int], peer_map: Optional[dict[int, PeerInfo]]
) -> list[str]:
    names: list[str] = []
    for peer_id in peer_ids:
        info = peer_map.get(peer_id) if peer_map else None
        names.append(info.name if info is not None else f"peer {peer_id}")
    return names


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
) -> HtmlStats:
    """Build summary stats for the HTML export."""
    timestamps = [msg.timestamp for msg in messages if msg.timestamp]
    start = min(timestamps) if timestamps else None
    end = max(timestamps) if timestamps else None

    participant_names: list[str] = []
    for msg in messages:
        speaker = resolve_speaker(msg, peer_map)
        if speaker != "Unknown" and speaker not in participant_names:
            participant_names.append(speaker)
    if title not in participant_names:
        participant_names.append(title)
    participants = " • ".join(participant_names)
    exported_at = datetime.now(tz=timezone.utc).isoformat()
    return HtmlStats(
        message_count=len(messages),
        start_iso=start.isoformat() if start else None,
        end_iso=end.isoformat() if end else None,
        participants=participants,
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
            forwarded_segments = build_forwarded_segments(msg, peer_map, tz)
            if forwarded_segments:
                rendered_forwarded = "".join(
                    _md_link(seg.text, seg.url) for seg in forwarded_segments
                )
                handle.write(f"*{rendered_forwarded}*\n\n")
            if msg.text:
                handle.write(f"{linkify_markdown(msg.text)}\n\n")
            for attachment in msg.attachments:
                handle.write(f"{_render_markdown_attachment(attachment, peer_map)}\n\n")
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
                "peer_id",
                "author_id",
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
                                "source_path": attachment.source_path,
                                "url": attachment.url,
                                "metadata": attachment.metadata,
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
                    msg.peer_id or "",
                    msg.author_id or "",
                ]
            )


def render_html(
    messages: list[Message],
    title: str,
    out_path: Path,
    peer_map: Optional[dict[int, PeerInfo]] = None,
) -> None:
    """Export messages to a styled HTML transcript."""
    stats = build_html_stats(messages, title, peer_map)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as handle:
        handle.write('<!doctype html><html><head><meta charset="utf-8">')
        handle.write(f"<title>{html.escape(title)}</title>")
        handle.write(HTML_BOOTSTRAP)
        handle.write(HTML_FONTS)
        handle.write(f"<style>{HTML_CSS}</style></head><body>")
        handle.write('<div class="background-blobs">')
        handle.write('<div class="blob blob-1"></div>')
        handle.write('<div class="blob blob-2"></div>')
        handle.write('<div class="blob blob-3"></div>')
        handle.write("</div>")
        handle.write('<div class="container">')
        _render_header(handle, title)
        _render_stats(handle, stats)
        _render_toolbar(handle)
        _render_messages(handle, messages, peer_map)
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
        f'<span class="value">{html.escape(stats.participants)}</span></div></div>'
    )
    handle.write(
        '<div class="stat-card glass">'
        '<div class="stat-info"><span class="label">Exported</span>'
        f'<span class="value mono">'
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


def _render_messages(
    handle,
    messages: list[Message],
    peer_map: Optional[dict[int, PeerInfo]],
) -> None:
    handle.write('<div class="chat-card glass" id="chat-card">')
    for msg in messages:
        iso = msg.timestamp.isoformat() if msg.timestamp else ""
        time_str = msg.timestamp.strftime("%H:%M:%S") if msg.timestamp else "??:??:??"
        speaker = resolve_speaker(msg, peer_map)
        speaker_url = _speaker_url(msg, peer_map)
        direction = "out" if msg.outgoing is True else "in"
        if iso:
            time_el = (
                f'<time class="local-time" datetime="{html.escape(iso)}">'
                f"{html.escape(time_str)}</time>"
            )
        else:
            time_el = "??:??:??"
        handle.write(f'<div class="msg {direction}" data-iso="{html.escape(iso)}">')
        handle.write('<div class="bubble">')
        handle.write(
            f'<div class="meta">[{time_el}] {_html_link(speaker, speaker_url)}</div>'
        )
        forwarded_segments = build_forwarded_segments(msg, peer_map)
        if forwarded_segments:
            handle.write('<div class="meta forwarded">')
            for seg in forwarded_segments:
                _render_forwarded_segment_html(handle, seg)
            handle.write("</div>")
        if msg.text:
            handle.write(linkify_html(msg.text))
        for attachment in msg.attachments:
            _render_html_attachment(handle, attachment, peer_map)
        _render_poll_question_note_html(handle, msg)
        handle.write("</div></div>")
    handle.write("</div>")

    handle.write('<button id="back-top" class="back-top">Back to top</button>')
    handle.write(_back_to_top_script())


def _render_html_attachment(
    handle,
    attachment: Attachment,
    peer_map: Optional[dict[int, PeerInfo]] = None,
) -> None:
    if attachment.kind == "poll" and attachment.metadata:
        _render_html_poll(handle, attachment.metadata, peer_map)
        return
    label = html.escape(_attachment_label(attachment))
    if attachment.url:
        url = html.escape(attachment.url, quote=True)
        handle.write(f'<div><a href="{url}">{label}</a></div>')
        return
    if attachment.exported_path:
        path = html.escape(attachment.exported_path, quote=True)
        mime_type = html.escape(attachment.mime_type or "", quote=True)
        if attachment.kind == "image" or (
            attachment.kind == "sticker"
            and attachment.mime_type in {"image/png", "image/webp"}
        ):
            handle.write(
                f'<div><img src="{path}" alt="{label}" loading="lazy" '
                'decoding="async"></div>'
            )
        elif attachment.kind == "video_message":
            handle.write(
                f'<div class="video-message"><video controls preload="none" playsinline>'
                f'<source src="{path}" type="{mime_type}"></video></div>'
            )
        elif attachment.kind == "video" or (
            attachment.kind == "sticker"
            and attachment.mime_type in {"video/mp4", "video/webm"}
        ):
            handle.write(
                f'<div><video controls preload="none"><source src="{path}" '
                f'type="{mime_type}"></video></div>'
            )
        elif attachment.kind in {"voice", "audio"}:
            handle.write(
                f'<div><audio controls preload="none"><source src="{path}" '
                f'type="{mime_type}"></audio></div>'
            )
        else:
            handle.write(f'<div><a download href="{path}">{label}</a></div>')
        return
    handle.write(
        f'<div class="meta">{html.escape(_attachment_description(attachment))}</div>'
    )


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
    </script>
    """
    return script


def _render_footer(handle) -> None:
    handle.write(
        '<footer style="margin-top:24px;color:var(--muted);font-size:12px;">'
        "Generated by Telegram Message Exporter"
        "</footer>"
    )
