# form-autofill

A Python + Node.js automation framework for filling structured forms via browser automation, with non-destructive spreadsheet editing at the OOXML level.

> **Note:** This repository contains a **desensitized architectural framework** extracted from a complete production project. Sensitive information (API endpoints, authentication credentials, domain names, internal field definitions, and business logic details) has been removed or replaced with placeholders for public sharing. The core technical architecture, design patterns, and implementation approach remain fully representative of the original system.

## Features

- **Non-destructive xlsx editing** — Operates directly on the OOXML zip archive to update cells while preserving native checkboxes, data-validation dropdowns, and formatting (which `openpyxl.save()` silently drops).
- **Browser form automation** — Playwright-driven Chrome interaction for complex form UIs (radio, checkbox, slider, rich-text editor, matrix ratings).
- **Python ↔ Node.js IPC** — Subprocess-based bridge with JSON-over-stdin/stdout, real-time stderr streaming, and graceful timeout handling.
- **Concurrent submission engine** — ThreadPoolExecutor with thread-safe state persistence and idempotent resume-after-interrupt.
- **Local LLM integration** — Ollama-backed draft generation with structured prompt engineering, field whitelist validation, and dimension-aware evidence checking.

## Architecture

```
form-autofill/
├── form_builder.py        # Spreadsheet generation + XML-level in-place editing
├── schema.py              # Form field definitions, option library, presets
├── orchestrator.py        # CLI entry point & submission orchestration
├── ai_assist.py           # LLM-powered draft generation with validation pipeline
├── bridge/
│   └── bridge.js          # Playwright browser automation (Python ↔ Node IPC)
├── tests/
│   └── test_form_builder.py
└── exports/               # Generated spreadsheets & logs
```

## Quick Start

```bash
# Generate a spreadsheet from form schema
uv run --with openpyxl --with xlsxwriter python orchestrator.py

# Auto-fill and submit (dry-run)
python orchestrator.py --dry-run --parallel 3

# AI-assisted draft from free-text input
uv run --with openpyxl --with xlsxwriter python ai_assist.py \
  --table exports/form_20260101.xlsx --id <ID> \
  --feedback "Works well but the UI is cluttered"
```

## Technical Deep-Dive

### Why XML Surgery?

Modern spreadsheet features (native checkboxes via `xlsxwriter.insert_checkbox`, data-validation dropdowns) are stored as OOXML extension elements. When `openpyxl` loads and re-saves a workbook, these extensions are stripped. The solution is to bypass openpyxl's save path entirely:

1. Parse `workbook.xml` + relationships to map sheet names → XML file paths
2. Parse `sharedStrings.xml` for text cell resolution
3. Locate target cells by `r` attribute (e.g. `B22`)
4. Replace cell content in-place (`<v>` for booleans, `<is>/<t>` for strings)
5. Rewrite only changed XML files back into the zip

All other zip entries pass through untouched — checkboxes, dropdowns, styles, and extensions are preserved byte-for-byte.

### Bridge Architecture

```
┌──────────────┐   stdin (JSON)    ┌──────────────────┐
│  Python CLI  │ ───────────────→  │  Node.js bridge  │
│              │ ←───────────────  │  (Playwright)    │
│              │   stdout (JSON)   │                  │
│              │ ←───────────────  │  stderr (logs)   │
└──────────────┘   real-time       └──────────────────┘
```

The Python orchestrator spawns Node.js as a subprocess, sends commands as JSON via stdin, reads results from stdout, and streams browser activity from stderr in real time.

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | Python 3.10+ (argparse, threading, concurrent.futures) |
| Browser automation | Playwright (Node.js) + system Chrome |
| Spreadsheet I/O | xlsxwriter · openpyxl · zipfile + xml.etree (XML surgery) |
| AI inference | Ollama (local) |
| Testing | unittest |
