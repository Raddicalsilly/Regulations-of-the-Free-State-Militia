#!/usr/bin/env python3
"""Light validator for the Plain Reading Dataset v1.

Checks:
  - File exists and is readable.
  - Every JSON record line parses as JSON.
  - IDs are unique.
  - Required fields present per record type.
  - TEST records carry an expected answer (gold star) and expected_value.
  - Record counts by type.

Usage: python3 validate_plain_reading_v1.py [path]
Default path: plain_reading_dataset_v1.txt next to this script.
"""

import json
import sys
from collections import Counter
from pathlib import Path

REQUIRED_COMMON = ("id", "type", "source", "source_verification")
REQUIRED_BY_TYPE = {
    "rule": ("text",),
    "lexicon": ("word", "text",),
    "text_example": ("text",),
    "not_plain_reading": ("text",),
    "method": ("text",),
    "test": ("question", "expected", "expected_value",),
}
VALID_TYPES = set(REQUIRED_BY_TYPE)


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("plain_reading_dataset_v1.txt")
    errors = []
    warnings = []

    if not path.exists():
        print(f"ERROR: file not found: {path}")
        return 1

    lines = path.read_text(encoding="utf-8").splitlines()

    records = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            records.append((lineno, json.loads(stripped)))
        except json.JSONDecodeError as exc:
            errors.append(f"line {lineno}: JSON parse error: {exc}")

    ids = Counter()
    types = Counter()
    for lineno, rec in records:
        if not isinstance(rec, dict):
            errors.append(f"line {lineno}: record is not a JSON object")
            continue
        rid = rec.get("id")
        rtype = rec.get("type")
        if rid is None:
            errors.append(f"line {lineno}: missing 'id'")
        else:
            ids[rid] += 1
        if rtype is None:
            errors.append(f"line {lineno}: missing 'type' (id={rid})")
            continue
        types[rtype] += 1
        if rtype not in VALID_TYPES:
            errors.append(f"line {lineno}: unknown type {rtype!r} (id={rid})")
            continue
        for field in REQUIRED_COMMON:
            if field not in rec:
                errors.append(f"line {lineno}: missing '{field}' (id={rid})")
        for field in REQUIRED_BY_TYPE[rtype]:
            if field not in rec:
                errors.append(f"line {lineno}: missing '{field}' for type {rtype!r} (id={rid})")
        if rtype == "test":
            if "expected" in rec and not str(rec.get("expected", "")).strip():
                errors.append(f"line {lineno}: empty 'expected' gold-star answer (id={rid})")
            if "expected_value" in rec and not str(rec.get("expected_value", "")).strip():
                errors.append(f"line {lineno}: empty 'expected_value' (id={rid})")

    for rid, count in ids.items():
        if count > 1:
            errors.append(f"duplicate id {rid!r} ({count} times)")

    # Header checks (light): the file should state its version and end marker.
    text = "\n".join(lines)
    if "PLAIN READING DATASET" not in text:
        warnings.append("header 'PLAIN READING DATASET' not found")
    if "END OF DATASET v1" not in text:
        warnings.append("end marker 'END OF DATASET v1' not found")

    print(f"Records: {len(records)}")
    print(f"By type: {dict(types)}")
    print(f"Unique IDs: {len(ids)}")
    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")
    for e in errors:
        print(f"  ERROR: {e}")
    for w in warnings:
        print(f"  WARNING: {w}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
