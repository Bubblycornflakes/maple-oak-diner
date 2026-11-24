#!/usr/bin/env python3
"""Turn orders placed on the QR menu into a CSV the analysis reads.

Export the menu's "orders" and "history" documents as JSON files, one file per document, into
<folder>/orders/ and <folder>/history/. Then, from the repository root:

    python3 analysis/sync_qr_orders.py analysis/qr_export
    python3 analysis/analyze.py

It writes analysis/data/qr_order_lines.csv: one row per item per round, with table, open or closed
tab, price and time. analyze.py turns it into the dashboard's QR orders tab. Exports hold real
orders, so analysis/qr_export/ is git-ignored.
"""
import csv, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "qr_export")
rows = []
for coll, state in (("orders", "open"), ("history", "closed")):
    for path in sorted(glob.glob(os.path.join(src, coll, "*.json"))):
        doc = json.load(open(path))
        doc = doc.get("data", doc) if isinstance(doc, dict) else {}
        for r in doc.get("rounds") or []:
            for l in r.get("lines") or []:
                try:
                    rows.append(dict(round_id=str(r.get("id", "")), date=str(r.get("placedAt", ""))[:10], hour=int(str(r.get("placedAt", "T00"))[11:13] or 0),
                                     item_id=str(l.get("id", "")), qty=int(l.get("qty", 1)), price=round(float(l.get("price", 0)), 2),
                                     table=int(doc.get("table") or r.get("table") or 0), tab=state, source="qr-menu"))
                except (TypeError, ValueError):
                    print("skipped a malformed line in", path)
out = os.path.join(HERE, "data", "qr_order_lines.csv")
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["round_id", "date", "hour", "item_id", "qty", "price", "table", "tab", "source"]); w.writeheader(); w.writerows(rows)
print(f"{len(rows)} order lines from {len({r['round_id'] for r in rows})} rounds written to {out}")
