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


# ---- export one table (or all) as PDF or Excel ----------------------------------------
EXPORTABLE = [render.UDHAAR_TAB, render.BILLS_TAB, render.ENTRIES_TAB, render.DUES_TAB, render.RATES_TAB,
              render.CUSTOMERS_TAB]
TABLE_ALIASES = {"udhaar": render.UDHAAR_TAB, "udhar": render.UDHAAR_TAB, "उधार": render.UDHAAR_TAB,
                 "bills": render.BILLS_TAB, "bill": render.BILLS_TAB, "बिल": render.BILLS_TAB,
                 "entries": render.ENTRIES_TAB, "entry": render.ENTRIES_TAB, "एंट्री": render.ENTRIES_TAB,
                 "dues": render.DUES_TAB, "hisaab": render.DUES_TAB, "हिसाब": render.DUES_TAB,
                 "rates": render.RATES_TAB, "rate": render.RATES_TAB, "customers": render.CUSTOMERS_TAB,
                 "all": "all", "sab": "all", "सब": "all"}
INDIC = __import__("re").compile(r"[\u0900-\u097F\u0A00-\u0A7F]")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MIME = "application/pdf"


def _tables(ledger: Ledger, owner: str, table: str) -> dict[str, list[list]]:
    tabs = render.all_tabs(ledger, owner)
    names = EXPORTABLE if table == "all" else [table]
    return {n: tabs[n] for n in names}


def build_table_xlsx(ledger: Ledger, owner: str, table: str = "all") -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    bold, head = Font(bold=True), PatternFill("solid", fgColor="F3E7C9")
    for name, rows in _tables(ledger, owner, table).items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
        for cell in ws[1]:
            cell.font, cell.fill = bold, head
        ws.freeze_panes = "A2"
        for i, w in enumerate(WIDTHS[name], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_table_pdf(ledger: Ledger, owner: str, table: str = "all") -> bytes:
    """English PDF of one table or all of them (Hindi/Punjabi names romanised)."""
    from datetime import date
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    from .billing import FONTS, roman

    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(True, margin=12)
    pdf.add_font("Noto", "", str(FONTS / "NotoSans-Regular.ttf"))
    pdf.add_font("Noto", "B", str(FONTS / "NotoSans-Bold.ttf"))
    for name, rows in _tables(ledger, owner, table).items():
        pdf.add_page()
        pdf.set_font("Noto", "B", 14)
        pdf.cell(0, 9, f"Khata: {name}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Noto", "", 8)
        pdf.cell(0, 5, f"As of {date.today().isoformat()}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        body = [r for r in rows[1:] if any(str(c).strip() for c in r)]
        if not body:
            pdf.set_font("Noto", "", 10)
            pdf.cell(0, 8, "(nothing yet)")
            continue
        width = len(rows[0])
        widths = [w for w in WIDTHS[name][:width]] + [12] * (width - len(WIDTHS[name]))
        pdf.set_font("Noto", "", 8)
        with pdf.table(col_widths=widths, text_align="LEFT", line_height=5,
                       headings_style=FontFace(emphasis="BOLD", fill_color=(243, 231, 201))) as t:
            for r in [rows[0]] + body:
                cells = list(r) + [""] * (width - len(r))
                row = t.row()
                for c in cells[:width]:
                    v = c if not isinstance(c, (int, float)) or isinstance(c, bool) else (
                        f"{c:,}" if isinstance(c, int) else f"{c:,.2f}")
                    text = str(v)
                    row.cell(roman(text) if INDIC.search(text) else text)
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def export_file(ledger: Ledger, owner: str, table: str, fmt: str) -> tuple[str, bytes, str]:
    """(filename, bytes, mime) for table in EXPORTABLE or 'all', fmt 'pdf' or 'xlsx'."""
    if table != "all" and table not in EXPORTABLE:
        raise ValueError(table)
    stem = "khata" if table == "all" else f"khata-{table.lower()}"
    if fmt == "pdf":
        return f"{stem}.pdf", build_table_pdf(ledger, owner, table), PDF_MIME
    return f"{stem}.xlsx", build_table_xlsx(ledger, owner, table), XLSX_MIME
