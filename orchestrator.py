#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI orchestrator: generate spreadsheets, auto-fill forms, track state.

This is the main entry point. It demonstrates:
- Subprocess IPC with a Node.js browser bridge
- ThreadPoolExecutor-based concurrent form submission
- Thread-safe state persistence with atomic JSON writes
- Idempotent resume-after-interrupt via submission tracking
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from schema import default_cells, FIELD_KEYS, FIELD_DEFS, CAT_FOLLOWUP
import form_builder as fb

# ── Paths ───────────────────────────────────────────────────────────

BASE     = Path(__file__).resolve().parent
BRIDGE   = BASE / "bridge" / "bridge.js"
EXPORTS  = BASE / "exports"
EXPORTS.mkdir(exist_ok=True)
SUBMIT_LOG = EXPORTS / "submitted.json"
TABLE_MAP  = EXPORTS / "table_links.json"

NODE      = os.environ.get("NODE_BIN", "node")
NODE_PATH = os.environ.get("NODE_PATH", "")

SUBMIT_LOCK = threading.Lock()

# ── API endpoint (placeholder) ──────────────────────────────────────

API_ENDPOINT = os.environ.get("API_ENDPOINT", "https://example.com/api/items")
API_UA = "form-autofill/1.0"


# ═══════════════════════════════════════════════════════════════════
#  Bridge IPC
# ═══════════════════════════════════════════════════════════════════

def call_bridge(action: str, payload: dict, timeout: int = 300) -> dict:
    """Spawn Node.js bridge subprocess, send JSON via stdin, read result."""
    env = dict(os.environ)
    if NODE_PATH:
        env["NODE_PATH"] = NODE_PATH
    payload["authPath"] = str(BASE / "bridge" / "auth.json")
    proc = subprocess.Popen(
        [NODE, str(BRIDGE), action],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env,
    )

    def pump_stderr():
        for line in proc.stderr:
            line = line.rstrip("\n")
            if line:
                print("[browser]", line, flush=True)

    threading.Thread(target=pump_stderr, daemon=True).start()
    try:
        stdout, _ = proc.communicate(
            json.dumps(payload, ensure_ascii=False), timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        return {"ok": False, "error": "timeout"}
    if not stdout.strip():
        return {"ok": False, "error": "no output"}
    try:
        return json.loads(stdout.strip())
    except json.JSONDecodeError:
        return {"ok": False, "error": "bad output: " + stdout[-500:]}


# ═══════════════════════════════════════════════════════════════════
#  Metadata fetching (placeholder — replace with real API)
# ═══════════════════════════════════════════════════════════════════

import urllib.request
import urllib.parse

def fetch_item_meta(item_id: str) -> dict:
    """Fetch item metadata from API (with retry and fallback)."""
    for attempt in range(3):
        try:
            url = f"{API_ENDPOINT}?id={urllib.parse.quote(item_id)}"
            req = urllib.request.Request(url, headers={"User-Agent": API_UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
            return {
                "name": data.get("name", ""),
                "cat": data.get("category", ""),
                "extra": data.get("extra", ""),
            }
        except Exception:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    return {"name": "", "cat": "", "extra": "", "error": "fetch failed"}


def enrich_items(items: list[tuple[str, str]]) -> list[dict]:
    """Concurrently fetch metadata for all items. items: [(id, name), ...]"""
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {
            ex.submit(fetch_item_meta, uid): (uid, name)
            for uid, name in items
        }
        for fut in as_completed(futures):
            uid, name = futures[fut]
            try:
                info = fut.result()
            except Exception as e:
                info = {"name": name, "cat": "", "error": str(e)[:200]}
            if not info.get("name"):
                info["name"] = name
            info["uid"] = uid
            results.append(info)
    order = {uid: i for i, (uid, _) in enumerate(items)}
    results.sort(key=lambda x: order.get(x["uid"], 999))
    return results


# ═══════════════════════════════════════════════════════════════════
#  Submission state
# ═══════════════════════════════════════════════════════════════════

def load_submitted() -> dict:
    try:
        return json.loads(SUBMIT_LOG.read_text("utf-8"))
    except Exception:
        return {}


def record_submitted(key: str, uid: str, note: str = "") -> None:
    with SUBMIT_LOCK:
        data = load_submitted()
        data.setdefault(key, {})[uid] = note or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        SUBMIT_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def load_table_links() -> dict:
    try:
        d = json.loads(TABLE_MAP.read_text("utf-8"))
        return d.get("tables", {}) if isinstance(d, dict) else {}
    except Exception:
        return {}


def record_table_link(path: Path, link: str) -> None:
    with SUBMIT_LOCK:
        try:
            d = json.loads(TABLE_MAP.read_text("utf-8"))
        except Exception:
            d = {}
        tables = d.setdefault("tables", {})
        tables[path.name] = {
            "link": link,
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        d["version"] = 1
        TABLE_MAP.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")


# ═══════════════════════════════════════════════════════════════════
#  Actions
# ═══════════════════════════════════════════════════════════════════

def action_fetch(link: str) -> str | None:
    """Open form URL, parse items, enrich metadata, generate spreadsheet."""
    print("Opening form and parsing items...")
    res = call_bridge("open", {"url": link})
    if not res.get("ok"):
        print("Failed to open:", res.get("error", "")[:300])
        return None
    items = res.get("items", [])
    if not items:
        print("No items found in form.")
        return None
    print(f"Found {len(items)} items. Fetching metadata...")
    enriched = enrich_items(items)
    link_key = link.strip()
    submitted = load_submitted().get(link_key, {})
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    rows = []
    for i, info in enumerate(enriched, 1):
        cat = info.get("cat", "")
        row = default_cells(i, info["uid"], info["name"], cat)
        done = submitted.get(info["uid"])
        row["status"] = "已提交" if done else "待提交"
        row["ts"] = done or ""
        rows.append(row)
    out = EXPORTS / f"form_{ts}.xlsx"
    fb.make_workbook(rows, out)
    record_table_link(out, link_key)
    print(f"\nSpreadsheet generated: {out}")
    return str(out)


def action_fill(path: str, link: str, dry_run: bool = False, parallel: int = 3) -> None:
    """Auto-fill forms from spreadsheet, submit concurrently."""
    print("Checking login state...")
    check = call_bridge("check_login", {"url": link})
    if not check.get("ok"):
        print("Login check failed:", check.get("error", "")[:300])
        return
    print("Login OK.")

    rows = fb.load_sheets(path)
    link_key = link.strip()
    submitted = load_submitted().get(link_key, {})

    pending = []
    for r in rows:
        uid = str(r.get("uid") or "").strip()
        if uid in submitted:
            continue
        if str(r.get("status")) == "已提交":
            continue
        pending.append(r)

    if not pending:
        print("Nothing to submit.")
        return

    print(f"Total: {len(rows)}, submitted: {len(rows)-len(pending)}, pending: {len(pending)}")
    for r in pending:
        print(f"  ▶ {r.get('uid')} {r.get('name')}")

    if not dry_run:
        ans = input(f"\nSubmit {len(pending)} items? Type yes: ").strip()
        if ans.lower() != "yes":
            print("Cancelled.")
            return

    def run_one(row: dict) -> tuple[str, str, bool, str]:
        uid = str(row.get("uid", "")).strip()
        name = str(row.get("name", ""))
        print(f"  ▶ Filling {name}...", flush=True)
        res = call_bridge("fill", {
            "url": link, "answers": row,
            "dryRun": dry_run, "mode": "vertical",
        }, timeout=120)
        ok = res.get("ok") and (res.get("submitted") or (dry_run and res.get("allOk")))
        if ok:
            status = "submitted" if res.get("submitted") else "dry-run OK"
            print(f"  ✔ {name} {status}", flush=True)
            if res.get("submitted"):
                record_submitted(link_key, uid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            return (uid, name, True, "")
        err = res.get("error", "unknown")
        print(f"  ✘ {name}: {err[:300]}", flush=True)
        return (uid, name, False, err[:300])

    parallel = max(1, min(parallel, 6))
    results: list[tuple] = []
    if parallel == 1:
        for row in pending:
            results.append(run_one(row))
    else:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futs = [ex.submit(run_one, row) for row in pending]
            for f in as_completed(futs):
                results.append(f.result())

    ok_count = sum(1 for _, _, ok, _ in results if ok)
    print(f"\nDone: {ok_count} succeeded, {len(pending)-ok_count} failed.")

    if not dry_run:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updates = {}
        for uid, _, ok, _ in results:
            updates[uid] = ("已提交" if ok else "失败", now if ok else "")
        if updates:
            updated = fb.update_statuses(path, updates)
            print(f"Status written back to {updated} sheets.")


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def ask_link(arg: str | None = None) -> str:
    if arg:
        return arg.strip()
    while True:
        link = input("Paste form URL: ").strip()
        if link:
            return link
        print("URL cannot be empty.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Form Auto-Fill Framework")
    ap.add_argument("--link", help="Form URL")
    ap.add_argument("--dry-run", action="store_true", help="Fill without submitting")
    ap.add_argument("--parallel", type=int, default=3, help="Parallel workers (1-6)")
    args = ap.parse_args()

    print("== Form Auto-Fill Framework ==")
    while True:
        print("\n1. Generate spreadsheet")
        print("2. Auto-fill & submit")
        print("3. Open form (debug)")
        print("4. AI draft (interactive)")
        print("q. Quit")
        choice = input("> ").strip().lower()
        if choice in ("q", "quit", "exit"):
            break
        if choice == "1":
            link = ask_link(args.link)
            action_fetch(link)
        elif choice == "2":
            files = sorted(EXPORTS.glob("form_*.xlsx"))
            if not files:
                print("No spreadsheets found. Run option 1 first.")
                continue
            for i, f in enumerate(files, 1):
                print(f"  {i}. {f.name}")
            sel = input("Select (number, or 'all'): ").strip()
            chosen = files if sel == "all" else [
                files[int(s.strip()) - 1]
                for s in sel.split(",") if s.strip().isdigit()
            ]
            for f in chosen:
                print(f"\n===== {f.name} =====")
                tl = load_table_links().get(f.name, {})
                link = tl.get("link") if isinstance(tl, dict) else ""
                if not link:
                    link = ask_link(args.link)
                    record_table_link(f, link)
                action_fill(str(f), link, dry_run=args.dry_run, parallel=args.parallel)
        elif choice == "3":
            link = ask_link(args.link)
            res = call_bridge("open", {"url": link})
            print("Result:", "OK" if res.get("ok") else "FAIL", res.get("error", ""))
        elif choice == "4":
            import ai_assist
            ai_assist.run_interactive()
        else:
            print("Invalid choice")


if __name__ == "__main__":
    main()
