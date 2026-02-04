"""Telegram Message Exporter package."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

__all__ = ["__version__"]


def _read_version_from_file() -> str:
    root = Path(__file__).resolve().parents[2]
    version_path = root / "VERSION"
    if version_path.exists():
        return version_path.read_text(encoding="utf-8").strip()
    return "0.0.0"


try:
    __version__ = metadata.version("telegram-message-exporter")
except metadata.PackageNotFoundError:  # pragma: no cover - not installed
    __version__ = _read_version_from_file()
