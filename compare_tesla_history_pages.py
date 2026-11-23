#!/usr/bin/env python3
"""Compare two privately saved Tesla history responses without network access."""

import argparse
import json
from pathlib import Path


MAX_BYTES = 10 * 1024 * 1024


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Page contains duplicate JSON keys")
        result[key] = value
    return result


def parse_page(data):
    if len(data) > MAX_BYTES:
        raise ValueError("Page exceeds the 10 MiB limit")
    try:
        page = json.loads(data, object_pairs_hook=_no_duplicate_keys)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Page is not valid unambiguous JSON") from None
    if not isinstance(page, dict):
        raise ValueError("Page has an unexpected structure")
    rows = page.get("data")
    total = page.get("totalResults")
    if (not isinstance(rows, list) or type(total) is not int or total < 0
            or total < len(rows)):
        raise ValueError("Page has invalid list or total metadata")
    identifiers = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("sessionId")) is not int:
            raise ValueError("Page has an invalid session identifier")
        identifiers.append(row["sessionId"])
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Page contains duplicate session identifiers")
    return total, identifiers


def read_page(path):
    try:
        with path.open("rb") as source:
            data = source.read(MAX_BYTES + 1)
    except OSError:
        raise ValueError("Page file could not be read") from None
    return parse_page(data)


def compare_pages(first, second):
    first_total, first_ids = first
    second_total, second_ids = second
    totals = "match" if first_total == second_total else "changed"
    if not first_ids or not second_ids:
        overlap = "at least one page is empty"
    elif first_ids == second_ids:
        overlap = "identical identifiers in the same order"
    elif set(first_ids) == set(second_ids):
        overlap = "identical identifiers in a different order"
    elif set(first_ids).isdisjoint(second_ids):
        overlap = "no identifiers overlap"
    else:
        overlap = "some identifiers overlap"
    return totals, overlap


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path, help="first private response file")
    parser.add_argument("second", type=Path, help="second private response file")
    args = parser.parse_args(argv)
    try:
        totals, overlap = compare_pages(read_page(args.first), read_page(args.second))
    except ValueError as error:
        raise SystemExit(f"Comparison failed: {error}") from None
    print(f"Reported totals: {totals}.")
    print(f"Page comparison: {overlap}.")
    print("This comparison alone does not establish pagination semantics or completeness.")


if __name__ == "__main__":
    main()
