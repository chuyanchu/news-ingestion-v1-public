from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        return records
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            records.append(
                {
                    "source": "parser",
                    "title": f"invalid json line {line_no}",
                    "url": "",
                    "crawled_at": "",
                    "status": "rejected",
                    "quality_score": 0,
                    "quality_flags": ["invalid_json"],
                    "raw_line": stripped,
                    "error": str(exc),
                }
            )
            continue
        records.append(record)
    return records


def write_jsonl(path: Path, records: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
