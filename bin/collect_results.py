#!/usr/bin/env python3
"""Merge per-slide PRISM2 JSON files into one wide TSV plus a combined JSON.

Stdlib only, so this runs in a bare python:3.12-slim container.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in-dir", required=True, type=Path)
    p.add_argument("--out-tsv", required=True, type=Path)
    p.add_argument("--out-json", required=True, type=Path)
    return p.parse_args()


def flatten(record: dict) -> dict:
    """One row per slide; one column per question id."""
    row = {
        "sample": record.get("sample", ""),
        "n_tiles_total": record.get("n_tiles_total", ""),
        "n_tiles_used": record.get("n_tiles_used", ""),
    }
    for qid, payload in (record.get("yes_no") or {}).items():
        row[f"yes_no__{qid}"] = payload.get("score", "")
    for block in ("open_ended", "multiple_choice"):
        for qid, payload in (record.get(block) or {}).items():
            row[f"{block}__{qid}"] = payload.get("answer", "")
    row["report"] = record.get("report", "")
    return row


def clean(value) -> str:
    """TSV-safe: no tabs, no newlines."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def main() -> None:
    args = parse_args()
    paths = sorted(args.in_dir.rglob("*.json"))
    records = [json.loads(p.read_text()) for p in paths]

    args.out_json.write_text(json.dumps(records, indent=2) + "\n")

    rows = [flatten(r) for r in records]
    # Stable column order: first-seen order across slides, report last
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    if "report" in columns:
        columns.append(columns.pop(columns.index("report")))

    with args.out_tsv.open("w") as fh:
        fh.write("\t".join(columns) + "\n")
        for row in rows:
            fh.write("\t".join(clean(row.get(c, "")) for c in columns) + "\n")

    print(f"Wrote {args.out_tsv} ({len(rows)} slides, {len(columns)} columns)")


if __name__ == "__main__":
    main()
