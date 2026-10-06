"""Write sample_import.csv and sample_import.xlsx next to this script, with expiry dates
relative to today (so the files always demonstrate every expiry band).

    python scripts/make_sample_imports.py
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

HERE = Path(__file__).resolve().parent
HEADER = ["product_name", "sku", "barcode", "category", "brand", "unit", "selling_price", "cost_price",
          "batch_number", "manufacturing_date", "expiry_date", "quantity"]


def rows() -> list[list]:
    t = date.today()
    d = lambda n: (t + timedelta(days=n)).isoformat()  # noqa: E731
    return [
        ["Fresh Milk 1L", "IMP-MLK-1L", "6009000000011", "Dairy", "Demo", "bottle", "1.50", "1.05", "IMP-M-01", d(-20), d(1), 48],
        ["Fresh Milk 1L", "IMP-MLK-1L", "6009000000011", "Dairy", "Demo", "bottle", "1.50", "1.05", "IMP-M-02", d(-5), d(45), 96],
        ["Greek Yoghurt", "IMP-YOG-1", "6009000000028", "Dairy", "Demo", "tub", "2.80", "1.90", "IMP-Y-01", d(-40), d(-2), 12],
        ["Wholegrain Bread", "IMP-BRD-1", "", "Bakery", "Demo", "loaf", "1.40", "0.80", "IMP-B-01", "", d(20), 30],
        ["Peanut Butter 400g", "IMP-PNB-400", "6009000000042", "Pantry", "Demo", "jar", "3.20", "2.10", "IMP-P-01", "", d(300), 60],
        # Deliberately invalid rows, to show error reporting:
        ["Broken Quantity", "IMP-BAD-1", "", "Pantry", "", "", "1.00", "0.50", "IMP-X-01", "", d(100), "ten"],
        ["Missing Identifier", "", "", "Pantry", "", "", "1.00", "0.50", "IMP-X-02", "", d(100), 5],
        ["Fresh Milk 1L", "IMP-MLK-1L", "6009000000011", "Dairy", "Demo", "bottle", "1.50", "1.05", "IMP-M-01", d(-20), d(1), 48],
    ]


def main() -> None:
    data = rows()
    with open(HERE / "sample_import.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        writer.writerows(data)
    wb = Workbook()
    ws = wb.active
    ws.title = "Stock"
    ws.append(HEADER)
    for r in data:
        out = list(r)
        for i in (9, 10):  # real Excel date cells
            out[i] = date.fromisoformat(out[i]) if out[i] else None
        ws.append(out)
    # Use different SKUs in the workbook so both files can be imported into the same company.
    for row in ws.iter_rows(min_row=2):
        if row[1].value:
            row[1].value = row[1].value.replace("IMP-", "XLS-")
        if row[2].value:
            row[2].value = "7" + str(row[2].value)[1:]
        row[8].value = str(row[8].value).replace("IMP-", "XLS-")
    wb.save(HERE / "sample_import.xlsx")
    print(f"Wrote {HERE / 'sample_import.csv'} and {HERE / 'sample_import.xlsx'}")


if __name__ == "__main__":
    main()
