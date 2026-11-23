#!/usr/bin/env python3
"""Retired proof-of-concept preview helpers."""

import csv
import datetime
import json
import math
import re


REQUIRED = ("Date", "Vehicle", "Location", "State", "kWh", "Cost", "Category")
DATA_BLOCK = re.compile(
    r"(?P<prefix>    const data = )\[\n.*?\n    \];", re.DOTALL
)


def load_rows(path):
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or set(fields) not in (
            set(REQUIRED), set(REQUIRED) | {"$/kWh"}
        ):
            raise ValueError("CSV headers must match the documented columns")
        for line, row in enumerate(reader, 2):
            if None in row or any(row[key] is None for key in fields):
                raise ValueError(f"CSV row {line}: incorrect number of columns")
            values = {key: value.strip() for key, value in row.items()}
            if any(not values[key] for key in REQUIRED):
                raise ValueError(f"CSV row {line}: required value is blank")
            try:
                date = datetime.date.fromisoformat(values["Date"])
                if date.isoformat() != values["Date"]:
                    raise ValueError
            except ValueError:
                raise ValueError(f"CSV row {line}: date must be YYYY-MM-DD") from None
            for key in ("Vehicle", "Location", "State"):
                if any(c in values[key] for c in "<>") or any(
                    ord(c) < 32 or ord(c) == 127 for c in values[key]
                ):
                    raise ValueError(f"CSV row {line}: invalid characters in {key}")
            numbers = []
            for key in ("kWh", "Cost"):
                try:
                    number = float(values[key])
                except ValueError:
                    raise ValueError(f"CSV row {line}: {key} must be a number") from None
                if not math.isfinite(number) or number < 0:
                    raise ValueError(f"CSV row {line}: {key} must be finite and nonnegative")
                numbers.append(number)
            if values["Category"] not in ("WORK", "TRAVEL"):
                raise ValueError(f"CSV row {line}: Category must be WORK or TRAVEL")
            rows.append([
                values["Date"], values["Vehicle"], values["Location"], values["State"],
                *numbers, values["Category"],
            ])
    if not rows:
        raise ValueError("CSV must contain at least one session")
    return rows


def render_preview(template, rows):
    matches = list(DATA_BLOCK.finditer(template))
    if len(matches) != 1:
        raise ValueError("Dashboard data block not found exactly once; preview not created")
    match = matches[0]
    data = json.dumps(rows, ensure_ascii=True, allow_nan=False, indent=2)
    preview = template[:match.start()] + match.group("prefix") + data + ";" + template[match.end():]
    return preview.replace('href="./favicon.svg"', 'href="../favicon.svg"')


def main():
    raise SystemExit("The proof-of-concept preview is retired. Use dairy_charging.py for the local ledger.")


if __name__ == "__main__":
    main()
