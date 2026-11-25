"""Excel export of one owner's khata, shared by `/api/export.xlsx` and Telegram `/export`.

Same layout as the Google Sheet (khata/render.py): a Khata tab with every entry and expense
sorted by date with running balances, and a Summary tab with the numbers the bot shows.
"""
from __future__ import annotations

import io

from . import render
from .ledger import Ledger

HEADER = render.COLUMNS


def build_xlsx(ledger: Ledger, owner: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Khata"
    ws.append(render.COLUMNS)
    for row in render.ledger_rows(ledger, owner):
        ws.append(row)
    sm = wb.create_sheet("Summary")
    for row in render.summary_rows(ledger, owner):
        sm.append(row)

    bold, head = Font(bold=True), PatternFill("solid", fgColor="F3E7C9")
    for cell in ws[1]:
        cell.font, cell.fill = bold, head
    ws.freeze_panes = "A2"
    grey = Font(color="777777", strike=True)
    status_col = render.COL["Status"] + 1
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, status_col).value == "cancelled":
            for c in range(1, len(render.COLUMNS) + 1):
                ws.cell(r, c).font = grey
    widths = [7, 11, 17, 24, 14, 10, 10, 18, 11, 10, 11, 24, 12]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    sm.column_dimensions["A"].width = 34
    sm.column_dimensions["B"].width = 16
    sm.column_dimensions["C"].width = 12
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
