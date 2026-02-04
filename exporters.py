"""Export helpers for Markdown, CSV, and HTML."""

from __future__ import annotations

import csv
import html
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import Message
from utils import linkify_html, linkify_markdown

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
.meta { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
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
"""


@dataclass(frozen=True)
class HtmlStats:
    """Computed stats for HTML output."""

    message_count: int
    date_range: str
    participants: str
    exported_at: str
    day_entries: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RenderOptions:
    """Optional rendering preferences."""

    peer_map: Optional[dict[int, str]] = None
    me_name: str = "Me"
    show_direction: bool = False


def resolve_speaker(
    msg: Message, peer_map: Optional[dict[int, str]], me_name: str
) -> str:
    """Resolve display name for a message."""
    if msg.outgoing is True:
        return me_name
    if peer_map:
        if msg.author_id and msg.author_id in peer_map:
            return peer_map[msg.author_id]
        if msg.peer_id and msg.peer_id in peer_map:
            return peer_map[msg.peer_id]
    return "Unknown"


def build_html_stats(messages: list[Message], title: str, me_name: str) -> HtmlStats:
    """Build summary stats for the HTML export."""
    timestamps = [msg.timestamp for msg in messages if msg.timestamp]
    start = min(timestamps) if timestamps else None
    end = max(timestamps) if timestamps else None
    if start and end:
        date_range = f"{start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}"
    else:
        date_range = "—"

    day_entries: list[tuple[str, str]] = []
    current_day = None
    for msg in messages:
        if msg.timestamp:
            day_key = msg.timestamp.strftime("%Y-%m-%d")
            day_label = msg.timestamp.strftime("%A, %B %d, %Y")
        else:
            day_key = "unknown"
            day_label = "Unknown Date"
        if day_key != current_day:
            current_day = day_key
            day_entries.append((day_key, day_label))

    participants = f"{me_name} • {title}"
    exported_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    return HtmlStats(
        message_count=len(messages),
        date_range=date_range,
        participants=participants,
        exported_at=exported_at,
        day_entries=tuple(day_entries),
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
    me_name = options.me_name
    show_direction = options.show_direction
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        handle.write(f"# Telegram Chat History: {title}\n\n")
        handle.write(
            f"**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )
        handle.write(f"**Total Messages:** {len(messages)}\n\n")
        handle.write("---\n")

        current_date = None
        for msg in messages:
            if msg.timestamp:
                msg_date = msg.timestamp.strftime("%Y-%m-%d")
            else:
                msg_date = "Unknown Date"

            if current_date != msg_date:
                current_date = msg_date
                header = (
                    msg.timestamp.strftime("%A, %B %d, %Y")
                    if msg.timestamp
                    else "Unknown"
                )
                handle.write(f"\n## {header}\n\n")

            time_str = (
                msg.timestamp.strftime("%H:%M:%S") if msg.timestamp else "??:??:??"
            )
            speaker = resolve_speaker(msg, peer_map, me_name)

            direction = ""
            if show_direction:
                direction = f" ({msg.speaker_hint()})"

            handle.write(f"**{time_str} — {speaker}{direction}**\n\n")
            handle.write(f"{linkify_markdown(msg.text)}\n\n")


def render_csv(
    messages: list[Message],
    out_path: Path,
    peer_map: Optional[dict[int, str]] = None,
    me_name: str = "Me",
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
                "peer_id",
                "author_id",
            ]
        )
        for msg in messages:
            ts = msg.timestamp
            date_str = ts.strftime("%Y-%m-%d") if ts else ""
            time_str = ts.strftime("%H:%M:%S") if ts else ""
            timestamp = int(ts.timestamp()) if ts else ""
            speaker = resolve_speaker(msg, peer_map, me_name)
            writer.writerow(
                [
                    date_str,
                    time_str,
                    timestamp,
                    msg.speaker_hint(),
                    speaker,
                    msg.text,
                    msg.peer_id or "",
                    msg.author_id or "",
                ]
            )


def render_html(
    messages: list[Message],
    title: str,
    out_path: Path,
    peer_map: Optional[dict[int, str]] = None,
    me_name: str = "Me",
) -> None:
    """Export messages to a styled HTML transcript."""
    stats = build_html_stats(messages, title, me_name)
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
        _render_toolbar(handle, stats.day_entries)
        _render_messages(handle, messages, peer_map, me_name)
        _render_footer(handle)
        handle.write("</div></body></html>")


def _render_header(handle, title: str) -> None:
    handle.write('<header class="glass header-panel">')
    handle.write('<div class="brand">')
    handle.write('<div class="logo">💬</div>')
    handle.write('<div class="title-area">')
    handle.write(f"<h1>{html.escape(title)}</h1>")
    handle.write(
        '<p class="subtitle">Local-only recovery export for Telegram Desktop</p>'
    )
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
        f'<span class="value mono">{html.escape(stats.date_range)}</span></div></div>'
    )
    handle.write(
        '<div class="stat-card glass">'
        '<div class="stat-info"><span class="label">Participants</span>'
        f'<span class="value">{html.escape(stats.participants)}</span></div></div>'
    )
    handle.write(
        '<div class="stat-card glass">'
        '<div class="stat-info"><span class="label">Exported</span>'
        f'<span class="value mono">{stats.exported_at}</span></div></div>'
    )
    handle.write("</section>")


def _render_toolbar(handle, day_entries: tuple[tuple[str, str], ...]) -> None:
    handle.write('<div class="toolbar glass">')
    handle.write('<label for="day-select">Jump to date</label>')
    handle.write('<select id="day-select">')
    handle.write('<option value="">Select a date...</option>')
    for day_key, day_label in day_entries:
        handle.write(
            f'<option value="day-{html.escape(day_key)}">'
            f"{html.escape(day_label)}</option>"
        )
    handle.write("</select>")
    handle.write("</div>")


def _render_messages(
    handle,
    messages: list[Message],
    peer_map: Optional[dict[int, str]],
    me_name: str,
) -> None:
    handle.write('<div class="chat-card glass">')
    current_date = None
    for msg in messages:
        msg_date, day_label, time_str = _message_date_parts(msg)
        if current_date != msg_date:
            current_date = msg_date
            handle.write(
                f'<div id="day-{html.escape(msg_date)}" class="day">'
                f"{html.escape(day_label)}</div>"
            )
        speaker = resolve_speaker(msg, peer_map, me_name)
        direction = "out" if msg.outgoing is True else "in"
        handle.write(f'<div class="msg {direction}">')
        handle.write('<div class="bubble">')
        handle.write(f'<div class="meta">[{time_str}] {html.escape(speaker)}</div>')
        handle.write(linkify_html(msg.text))
        handle.write("</div></div>")
    handle.write("</div>")

    handle.write('<button id="back-top" class="back-top">Back to top</button>')
    handle.write(_back_to_top_script())


def _message_date_parts(msg: Message) -> tuple[str, str, str]:
    if msg.timestamp:
        msg_date = msg.timestamp.strftime("%Y-%m-%d")
        day_label = msg.timestamp.strftime("%A, %B %d, %Y")
        time_str = msg.timestamp.strftime("%H:%M:%S")
    else:
        msg_date = "unknown"
        day_label = "Unknown Date"
        time_str = "??:??:??"
    return msg_date, day_label, time_str


def _back_to_top_script() -> str:
    script = """
    <script>
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
