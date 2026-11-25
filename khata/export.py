"""Excel export of one owner's khata, shared by `/api/export.xlsx` and Telegram `/export`.

Same two tables as the Google Sheet (khata/render.py): Transactions and Dues.
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
    tx = wb.active
    tx.title = render.TX_TAB
    tx.append(render.COLUMNS)
    for row in render.transaction_rows(ledger, owner):
        tx.append(row)
    dues = wb.create_sheet(render.DUES_TAB)
    for row in render.dues_rows(ledger, owner):
        dues.append(row)

    bold, head = Font(bold=True), PatternFill("solid", fgColor="F3E7C9")
    for ws, widths in ((tx, [7, 11, 22, 26, 11, 11, 11, 24, 12]), (dues, [22, 16, 18, 20, 16, 16])):
        for cell in ws[1]:
            cell.font, cell.fill = bold, head
        ws.freeze_panes = "A2"
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
    for r in range(2, dues.max_row + 1):
        if str(dues.cell(r, 1).value or "").startswith(("Total", "Net")):
            dues.cell(r, 1).font = bold
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
