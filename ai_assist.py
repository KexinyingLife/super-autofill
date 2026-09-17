#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM-assisted draft generation with multi-layer validation.

Demonstrates:
- Structured prompt engineering for JSON-constrained output
- Field whitelist + option validation pipeline
- Dimension-aware evidence checking (keyword → field mapping)
- Score normalization from natural language sentiment
- Non-destructive Excel writeback via XML surgery
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from schema import FIELD_KEYS, FIELD_DEFS, LIB, FIELD_TYPES, DD_DEFAULT, CB_DEFAULT
import form_builder as fb

BASE = Path(__file__).resolve().parent
DRAFT_DIR = BASE / "exports" / "drafts"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "qwen3:8b")

# Only these keys may appear in LLM proposals
ALLOWED_KEYS = {"f1", "f2", "f3", "g1", "g2", "g3", "rec", "recNote",
                "flag1", "flag2", "sub1", "total", "w1", "w2", "w3"}

# Evidence keywords per field — prevents cross-dimensional scoring errors
EVIDENCE_KW: dict[str, tuple[str, ...]] = {
    "f1": ("功能", "完整", "缺少", "缺失", "feature", "missing"),
    "f2": ("易用", "方便", "麻烦", "复杂", "easy", "usabl", "hard"),
    "f3": ("性能", "流畅", "卡顿", "慢", "slow", "fast", "lag", "perf"),
    "g1": ("配色", "布局", "颜色", "排版", "color", "layout"),
    "g2": ("图标", "清晰", "模糊", "icon", "clear"),
    "g3": ("适配", "响应", "屏幕", "responsive", "screen"),
}

# Fields that must remain human-only
REVIEW_ONLY = {"cat", "sub1", "sub2"}


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    first, last = text.find("{"), text.rfind("}")
    if first < 0 or last < first:
        raise ValueError("Model did not return JSON")
    data = json.loads(text[first:last + 1])
    if not isinstance(data, dict):
        raise ValueError("Root is not an object")
    return data


def _normalize_bool(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("true", "是", "有"):
            return True
        if low in ("false", "否", "无"):
            return False
    return v


def _allowed_values(key: str) -> set:
    ft = FIELD_TYPES.get(key, "")
    if ft == "cb":
        return {True, False}
    if ft.startswith("dd_"):
        return set(LIB.get(ft[3:], []))
    return set()


def _has_relevant_evidence(key: str, evidence: str, feedback: str = "") -> bool:
    kws = EVIDENCE_KW.get(key)
    if not kws:
        return True
    ev = evidence.lower()
    if not any(w in ev for w in kws):
        return False
    if feedback:
        return any(w in feedback.lower() for w in kws)
    return True


def validate_draft(raw: dict, feedback: str = "") -> dict:
    """Filter proposals: whitelist, option validation, evidence check."""
    facts = raw.get("facts") if isinstance(raw.get("facts"), list) else []
    notes = raw.get("notes") if isinstance(raw.get("notes"), list) else []
    proposals_raw = raw.get("proposals") if isinstance(raw.get("proposals"), dict) else {}
    accepted: dict[str, dict] = {}
    rejected: list[dict] = []

    for key, item in proposals_raw.items():
        if key not in ALLOWED_KEYS:
            rejected.append({"key": key, "reason": "not in whitelist"})
            continue
        if not isinstance(item, dict):
            rejected.append({"key": key, "reason": "bad format"})
            continue
        evidence = str(item.get("evidence") or "").strip()
        if not evidence:
            rejected.append({"key": key, "reason": "no evidence"})
            continue
        value = _normalize_bool(item.get("value"))
        valid = _allowed_values(key)
        if valid and value not in valid:
            rejected.append({"key": key, "reason": f"invalid option: {value}"})
            continue
        if not _has_relevant_evidence(key, evidence, feedback):
            rejected.append({"key": key, "reason": "dimension mismatch"})
            continue
        conf = str(item.get("confidence") or "medium").lower()
        accepted[key] = {"value": value, "evidence": evidence, "confidence": conf}

    return {
        "facts": [x.strip() for x in facts if isinstance(x, str) and x.strip()],
        "proposals": accepted,
        "needs_review": [k for k in REVIEW_ONLY if k not in accepted],
        "notes": [str(x).strip() for x in notes if str(x).strip()],
        "rejected": rejected,
    }


def _score_from_text(text: str) -> str:
    """Map natural-language sentiment to a 1-5 score."""
    t = text.lower()
    if any(w in t for w in ("很差", "糟糕", "非常差")):
        return "1"
    if any(w in t for w in ("较差", "不太好")):
        return "2"
    if any(w in t for w in ("一般", "还可以")):
        return "3"
    if any(w in t for w in ("不错", "良好")):
        return "4"
    if any(w in t for w in ("很好", "优秀", "很棒")):
        return "5"
    return "3"


def resolve_full_answers(draft: dict, item: dict, feedback: str) -> None:
    """Merge defaults + LLM proposals into a complete answer set."""
    answers: dict[str, Any] = {}
    for key in FIELD_KEYS:
        v = item.get(key)
        if v not in (None, ""):
            answers[key] = v

    for k, v in CB_DEFAULT.items():
        answers.setdefault(k, v)
    for k, v in DD_DEFAULT.items():
        answers.setdefault(k, v)

    for key in ("f1", "f2", "f3"):
        answers.setdefault(key, "3")
    for key in ("g1", "g2", "g3"):
        answers.setdefault(key, "3:一般")

    answers["total"] = "5"
    answers["w1"] = True
    answers["w2"] = True

    for key, item_p in draft["proposals"].items():
        answers[key] = item_p["value"]

    answers["recNote"] = str(answers.get("recNote") or "").strip()
    if not answers["recNote"] and feedback.strip():
        answers["recNote"] = feedback.strip()[:200]

    draft["resolved_answers"] = {k: answers.get(k, "") for k in FIELD_KEYS}
    draft["needs_review"] = []


def build_prompt(feedback: str, item: dict) -> str:
    options = {k: sorted(str(v) for v in _allowed_values(k)) for k in sorted(ALLOWED_KEYS)}
    ctx = {"name": item.get("name", ""), "cat": item.get("cat", "")}
    return f"""You are a form-fill assistant. Output strict JSON only.

Context: {json.dumps(ctx, ensure_ascii=False)}
User feedback: {feedback}

Rules:
1. proposals keys must be from allowed fields only.
2. Each proposal needs value, evidence, confidence (high/medium/low).
3. evidence must reference the user's actual words.
4. Do not fill REVIEW_ONLY fields — put them in unknown_fields.
5. Scores: use user sentiment. "不错"=4, "很差"=1, neutral=3.
6. BUG flag: only for blocking crashes/data loss, not minor UI issues.

Allowed fields: {json.dumps(options, ensure_ascii=False)}

Output:
{{"facts":["..."],"proposals":{{"field":{{"value":"...","evidence":"...","confidence":"high"}}}},"unknown_fields":["field"],"notes":["..."]}}"""


def call_ollama(prompt: str, model: str = "", url: str = "") -> dict:
    model = model or DEFAULT_MODEL
    url = url or OLLAMA_URL
    payload = {
        "model": model, "stream": False, "format": "json",
        "think": False,
        "options": {"temperature": 0.3, "num_predict": 600},
        "messages": [
            {"role": "system", "content": "Output only valid JSON."},
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot connect to Ollama: {e.reason}") from e
    if data.get("error"):
        raise RuntimeError(f"Ollama error: {data['error']}")
    content = ((data.get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("Empty model response")
    return _parse_json(content)


def mock_response() -> dict:
    return {
        "facts": ["UI is cluttered", "loading is slow", "core feature works"],
        "proposals": {
            "f3": {"value": "2", "evidence": "loading is slow", "confidence": "high"},
            "g2": {"value": "2:欠佳", "evidence": "icons are unclear", "confidence": "medium"},
            "rec": {"value": "不愿意", "evidence": "would not recommend", "confidence": "high"},
            "recNote": {"value": "Core features work but UI is cluttered and loading is slow.",
                        "evidence": "UI cluttered, slow loading", "confidence": "high"},
        },
        "unknown_fields": ["cat", "sub1"],
        "notes": ["Mock output for testing."],
    }


def save_draft(draft: dict, table: str, item: dict, feedback: str) -> Path:
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(item.get("uid", "")))[:32]
    path = DRAFT_DIR / f"draft_{ts}_{safe_id}.json"
    payload = {
        "version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "table": Path(table).name,
        "item": {k: item.get(k, "") for k in ("uid", "name", "cat")},
        "feedback": feedback,
        "draft": draft,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return path


def create_draft(table: str, item: dict, feedback: str, *,
                 model: str = "", ollama_url: str = "",
                 mock: bool = False, write: bool = True) -> tuple[dict, Path]:
    if not feedback.strip():
        raise ValueError("Feedback cannot be empty")
    raw = mock_response() if mock else call_ollama(
        build_prompt(feedback, item), model or DEFAULT_MODEL, ollama_url or OLLAMA_URL,
    )
    draft = validate_draft(raw, feedback)
    resolve_full_answers(draft, item, feedback)
    if write and not mock:
        changed = fb.update_answers(table, item["uid"], draft["resolved_answers"])
        if changed != 1:
            raise RuntimeError("Could not locate target sheet")
        draft["writeback"] = {"written": True, "uid": str(item["uid"])}
    path = save_draft(draft, table, item, feedback)
    return draft, path


def render_report(draft: dict) -> str:
    lines = ["\n== AI Draft Result =="]
    if draft["facts"]:
        lines.append("Extracted facts:")
        lines.extend(f"  - {f}" for f in draft["facts"])
    lines.append("\nProposals:")
    for key, item in draft.get("proposals", {}).items():
        label = next((h for k, h, *_ in FIELD_DEFS if k == key), key)
        lines.append(f"  - {label}: {item['value']} [{item['confidence']}]")
        lines.append(f"    Evidence: {item['evidence']}")
    if draft.get("needs_review"):
        lines.append("\nNeeds manual review:")
        lines.extend(f"  - {k}" for k in draft["needs_review"])
    if draft.get("rejected"):
        lines.append("\nRejected:")
        lines.extend(f"  - {r['key']}: {r['reason']}" for r in draft["rejected"])
    return "\n".join(lines)


def run_interactive() -> None:
    from orchestrator import EXPORTS
    files = sorted(EXPORTS.glob("form_*.xlsx"))
    if not files:
        print("No spreadsheets found.")
        return
    for i, f in enumerate(files, 1):
        print(f"  {i}. {f.name}")
    choice = input("Select spreadsheet: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(files)):
        return
    table = files[int(choice) - 1]
    items = fb.load_sheets(table)
    if not items:
        print("No items in spreadsheet.")
        return
    for i, it in enumerate(items, 1):
        print(f"  {i}. {it.get('name', '')} ({it.get('uid', '')})")
    sel = input("Select item: ").strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(items)):
        return
    item = items[int(sel) - 1]
    feedback = input("Describe your experience (one line): ").strip()
    if not feedback:
        return
    try:
        draft, path = create_draft(str(table), item, feedback)
    except Exception as e:
        print(f"Error: {e}")
        return
    print(render_report(draft))
    print(f"\nDraft saved: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Draft Generator")
    parser.add_argument("--table", help="Spreadsheet path")
    parser.add_argument("--id", help="Item ID")
    parser.add_argument("--feedback", help="Free-text feedback")
    parser.add_argument("--model", default="")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    if args.table and args.id and args.feedback:
        items = fb.load_sheets(args.table)
        item = next((i for i in items if str(i.get("uid")) == args.id), None)
        if not item:
            sys.exit(f"ID not found: {args.id}")
        draft, path = create_draft(args.table, item, args.feedback,
                                   model=args.model, mock=args.mock,
                                   write=not args.mock)
        print(render_report(draft))
        print(f"Saved: {path}")
    else:
        parser.print_help()
