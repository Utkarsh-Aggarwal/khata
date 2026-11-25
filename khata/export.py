"""Excel export of one owner's khata, shared by `/api/export.xlsx` and Telegram `/export`.

Same four tables as the Google Sheet (khata/render.py): Udhaar, Bills, Entries and Dues.
"""
from __future__ import annotations

import io

from . import render
from .ledger import Ledger

HEADER = render.UDHAAR_COLUMNS
WIDTHS = {render.UDHAAR_TAB: [7, 11, 18, 24, 11, 11, 11, 30, 12], render.BILLS_TAB: [8, 11, 22, 60, 11, 10],
          render.ENTRIES_TAB: [7, 11, 24, 26, 11, 11, 30, 12], render.DUES_TAB: [24, 16, 20],
          render.RATES_TAB: [24, 10, 8, 30], render.CUSTOMERS_TAB: [24, 18, 30], render.SETTINGS_TAB: [16, 32, 30]}


def build_xlsx(ledger: Ledger, owner: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    bold, head = Font(bold=True), PatternFill("solid", fgColor="F3E7C9")
    for name, rows in render.all_tabs(ledger, owner).items():
        if name == render.SETTINGS_TAB:            # internal (linked phone ids): not for the Excel file
            continue
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
        for cell in ws[1]:
            cell.font, cell.fill = bold, head
        ws.freeze_panes = "A2"
        for i, w in enumerate(WIDTHS[name], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        if name == render.DUES_TAB:
            for r in range(2, ws.max_row + 1):
                if str(ws.cell(r, 1).value or "") == "Total":
                    ws.cell(r, 1).font = bold
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
