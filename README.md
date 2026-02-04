# 💬 Telegram Message Exporter (macOS Desktop)

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-macOS-111111.svg)](#)
[![Telegram](https://img.shields.io/badge/Telegram-Desktop-2CA5E0.svg)](https://desktop.telegram.org/)
[![Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://black.readthedocs.io/)
[![Ruff](https://img.shields.io/badge/lint-ruff-262626.svg)](https://docs.astral.sh/ruff/)
[![Pylint](https://img.shields.io/badge/lint-pylint-ffcd00.svg)](https://pylint.readthedocs.io/)

A professional, offline recovery and export tool for **Telegram Desktop (macOS)**. It decrypts the local `db_sqlite` using `.tempkeyEncrypted` and produces a clean, readable transcript in **HTML**, **Markdown**, or **CSV**.

## Table of Contents

- [Overview](#overview)
- [Motivation & Use Case](#motivation--use-case)
- [Key Features](#key-features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Export Formats](#export-formats)
- [Date Filtering](#date-filtering)
- [Safety & Privacy](#safety--privacy)
- [Quality Checks](#quality-checks)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)
- [Credits](#credits)

---

## Overview

Telegram Desktop stores messages locally in an encrypted SQLite database. This tool:

1. Decrypts `db_sqlite` using `.tempkeyEncrypted`
2. Parses the Postbox key/value format
3. Exports a clean transcript that a non‑technical user can read

It is designed for **offline recovery** on a Mac where the local cache still exists.

---

## Motivation & Use Case

Telegram does not provide server‑side recovery for deleted chats. In real‑world scenarios (accidental deletion, account changes, device loss, or audit requirements), the **only remaining source of truth** can be the local encrypted cache on a macOS device.

This project was created after a family conversation was removed with no way to restore it via Telegram’s servers. The local Mac still had the encrypted cache, so this tool was built to **recover what remained locally** and convert it into a clean, shareable export.

If you need a **defensible, offline transcript** from Telegram Desktop’s local database, this provides a reliable and repeatable path.

---

## Key Features

- **Offline decryption** using Telegram’s local key format (dbKey + dbSalt)
- **Human‑readable exports** with names, timestamps, and link handling
- **Modern HTML transcript** with date jump + back‑to‑top button
- **CSV export** for analysis or spreadsheets
- **Date filters** for targeted ranges
- **Best‑effort peer mapping** for clean names

---

## Prerequisites

- **macOS** with Telegram Desktop data present
- **Python 3.10+** (tested on 3.11–3.13)
- **Virtual environment recommended**

---

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Quick Start

### 1. Decrypt the database

```bash
python3 telegram_exporter.py decrypt \
  --key ~/Library/Group\ Containers/6N38VWS5BX.ru.keepcoder.Telegram/stable/.tempkeyEncrypted \
  --db  ~/Library/Group\ Containers/6N38VWS5BX.ru.keepcoder.Telegram/stable/account-*/postbox/db/db_sqlite \
  --out plaintext.db
```

If **Passcode Lock** is enabled in Telegram Desktop:

```bash
TG_LOCAL_PASSCODE="your-passcode" \
  python3 telegram_exporter.py decrypt --key <key> --db <db> --out plaintext.db
```

### 2. Find the peer ID

```bash
python3 telegram_exporter.py list-peers --db plaintext.db --search "Alice"
```

### 3. Export a readable transcript

```bash
python3 telegram_exporter.py export \
  --db plaintext.db \
  --peer-id 123456789 \
  --me-name "Me" \
  --format html \
  --out chat_export.html
```

---

## Export Formats

### HTML (recommended)
Clean, modern transcript with date jump and back‑to‑top.

```bash
python3 telegram_exporter.py export --db plaintext.db --peer-id 123456789 --format html --me-name "Me" --out chat_export.html
```

### Markdown
Readable, portable, easy to email.

```bash
python3 telegram_exporter.py export --db plaintext.db --peer-id 123456789 --format md --me-name "Me" --out chat_export.md
```

### CSV
For spreadsheets or analysis.

```bash
python3 telegram_exporter.py export --db plaintext.db --peer-id 123456789 --format csv --out chat_export.csv
```

---

## Date Filtering

Export only a range (inclusive):

```bash
python3 telegram_exporter.py export \
  --db plaintext.db \
  --peer-id 123456789 \
  --start-date 2024-01-01 \
  --end-date 2024-12-31 \
  --format html \
  --out chat_2024.html
```

Formats supported:
- `YYYY-MM-DD`
- `YYYY-MM-DDTHH:MM:SS`
- Unix timestamp (seconds)

---

## Safety & Privacy

- Keep the Mac **offline** during recovery to avoid sync deletions.
- Media files (if cached) live in:
  `~/Library/Group Containers/6N38VWS5BX.ru.keepcoder.Telegram/stable/account-*/files/`
- If decryption fails, retry with `--debug` to see which SQLCipher profile succeeds.

---

## Quality Checks

Run formatting and linting locally:

```bash
black *.py
ruff check *.py
pylint *.py
```

---

## Troubleshooting

**“file is not a database”**
- Key and DB are mismatched (wrong snapshot or wrong account path)
- Passcode Lock is enabled but not provided

**Passcode Lock on**
- Use `TG_LOCAL_PASSCODE` or `--passcode`

**mmh3 errors**
```bash
pip install mmh3==4.1.0
```

---

## Project Structure

```
telegram-message-exporter/
├── telegram_exporter.py               # CLI entry point
├── crypto.py                          # SQLCipher + tempkey handling
├── db.py                              # DB heuristics + message extraction
├── exporters.py                       # HTML / Markdown / CSV
├── postbox.py                         # Postbox parsing utilities
├── models.py                          # Message data model
├── utils.py                           # Date + link helpers
├── hashing.py                         # Murmur3 helper
├── requirements.txt                   # Python dependencies
└── README.md
```

---

## Credits

This project was informed by community research and reverse‑engineering work. In particular, the following reference was instrumental in understanding Telegram Desktop’s local key format and Postbox structure:

- https://gist.github.com/stek29/8a7ac0e673818917525ec4031d77a713

---

For enhancements or alternate export styles, open an issue with requirements and examples.
