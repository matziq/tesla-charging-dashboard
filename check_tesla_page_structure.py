#!/usr/bin/env python3
"""Report aggregate structure from two user-selected local response files."""

import json
import math
import sys
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("Invalid JSON constant")


def read_page(path):
    try:
        with Path(path).open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
    except (OSError, ValueError):
        raise ValueError("A selected file could not be read.") from None
    if len(raw) > MAX_BYTES:
        raise ValueError("A selected file exceeds the 10 MiB limit.")
    try:
        payload = json.loads(raw, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("A selected file is not valid unambiguous JSON.") from None
    if not isinstance(payload, dict):
        raise ValueError("A selected page has invalid structure.")
    rows, total = payload.get("data"), payload.get("totalResults")
    if (not isinstance(rows, list) or type(total) is not int or total < len(rows)
            or any(not isinstance(row, dict) for row in rows)):
        raise ValueError("A selected page has invalid structure.")
    for row in rows:
        fees = row.get("fees")
        if isinstance(fees, list) and any(not isinstance(fee, dict) for fee in fees):
            raise ValueError("A selected page has invalid fee entries.")
    return rows, total


def numeric(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def coverage(items, key, predicate):
    if not items:
        return "not applicable (no records)"
    matched = sum(predicate(item.get(key)) for item in items)
    return "all" if matched == len(items) else ("some" if matched else "none")


def report(first, second):
    rows = first[0] + second[0]
    equal = len(rows) == first[1] == second[1]
    print("Combined row count equals both reported totals:", "yes" if equal else "no")
    if first[0] and second[0] and all(type(row.get("sessionId")) is int for row in rows):
        first_ids = [row["sessionId"] for row in first[0]]
        second_ids = [row["sessionId"] for row in second[0]]
        repeated = len(set(first_ids)) != len(first_ids) or len(set(second_ids)) != len(second_ids)
        print("Repeated identifiers within a page:", "yes" if repeated else "no")
        print("Identifiers overlap across pages:", "yes" if set(first_ids) & set(second_ids) else "no")
    else:
        print("Identifier overlap check: not applicable (empty page or invalid identifiers)")
    print("Field results mean present with the expected type; they do not establish meaning or units.")
    for label, key, expected in (
        ("Session identifier (integer)", "sessionId", lambda v: type(v) is int),
        ("Vehicle identifier (nonempty text)", "vin", nonempty_text),
        ("Location (nonempty text)", "siteLocationName", nonempty_text),
        ("Start time (nonempty text)", "chargeStartDateTime", nonempty_text),
        ("Stop time (nonempty text)", "chargeStopDateTime", nonempty_text),
        ("Fees (array)", "fees", lambda v: isinstance(v, list)),
        ("Odometer candidate 'odometer' (number)", "odometer", numeric),
        ("Odometer candidate 'odometerMiles' (number)", "odometerMiles", numeric),
        ("Trip candidate 'tripMiles' (number)", "tripMiles", numeric),
        ("Energy candidate 'energy' (number)", "energy", numeric),
        ("Energy candidate 'energyKwh' (number)", "energyKwh", numeric),
    ):
        print(f"{label}: {coverage(rows, key, expected)}")
    fees = [fee for row in rows for fee in (row["fees"] if isinstance(row.get("fees"), list) else [])]
    print("Fee-field results apply only to entries in present fee arrays.")
    for label, key, expected in (
        ("Unit of measure (nonempty text)", "uom", nonempty_text),
        ("Currency (nonempty text)", "currencyCode", nonempty_text),
        ("Fee type (nonempty text)", "feeType", nonempty_text),
        ("Pricing type (nonempty text)", "pricingType", nonempty_text),
        ("Base rate (number)", "rateBase", numeric),
        ("Base usage (number)", "usageBase", numeric),
        ("Total due (number)", "totalDue", numeric),
        ("Net due (number)", "netDue", numeric),
        ("Paid flag (boolean)", "isPaid", lambda v: type(v) is bool),
    ):
        print(f"{label}: {coverage(fees, key, expected)}")
    print("Only named fields are checked. Matching totals do not prove complete or stable history.")
    print("No units, mileage, energy, payment, dairy-trip classification, or field meaning is inferred.")


def choose_pages():
    import tkinter as tk
    from tkinter import filedialog

    try:
        root = tk.Tk()
        root.withdraw()
        try:
            first = filedialog.askopenfilename(parent=root, title="Select first saved response")
            if not first:
                return None
            second = filedialog.askopenfilename(parent=root, title="Select second saved response")
            return (first, second) if second else None
        finally:
            root.destroy()
    except tk.TclError:
        raise ValueError("The file selection window could not be opened.") from None


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if args and len(args) != 2:
        print("Provide exactly two response paths, or no paths to select files.")
        return 1
    if not args:
        try:
            args = choose_pages()
        except (ImportError, OSError, ValueError):
            print("The file selection window could not be opened.")
            return 1
        if not args:
            print("Canceled; no report produced.")
            return 1
    try:
        first, second = (read_page(path) for path in args)
    except ValueError as error:
        print(f"Check failed: {error}")
        return 1
    report(first, second)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
