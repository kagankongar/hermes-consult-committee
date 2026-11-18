#!/usr/bin/env python3
"""Monthly reconciliation: processor export vs. our payments table.

Usage: python3 scripts/reconcile.py --month YYYY-MM [--export-dir DIR]
"""
import argparse
import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from decimal import Decimal


def load_export(export_dir: str, month: str):
    path = os.path.join(export_dir, f"processor-{month}.csv")
    manifest_path = os.path.join(export_dir, f"processor-{month}.manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    raw = open(path, "rb").read()
    rows = list(csv.DictReader(raw.decode().splitlines()))
    digest = hashlib.sha256(raw).hexdigest()
    if len(rows) != manifest["rows"] or digest != manifest["sha256"]:
        sys.exit(
            f"reconcile: export integrity check failed for {month}: "
            f"manifest says {manifest['rows']} rows / sha256 {manifest['sha256'][:12]}…, "
            f"file has {len(rows)} rows / sha256 {digest[:12]}…. "
            "Refusing to reconcile against a partial export."
        )
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True)
    ap.add_argument("--export-dir", default=os.environ.get("PROCESSOR_EXPORT_DIR", "data/exports"))
    args = ap.parse_args()
    rows = load_export(args.export_dir, args.month)
    totals = defaultdict(Decimal)
    for r in rows:
        totals[r["currency"]] += Decimal(r["amount"])
    for cur, total in sorted(totals.items()):
        print(f"{cur}\t{total:.2f}")


if __name__ == "__main__":
    main()
