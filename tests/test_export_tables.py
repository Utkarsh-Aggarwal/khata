"""Export any table, or all of them, as PDF or Excel."""
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from khata.app import create_app
from khata.export import EXPORTABLE, export_file
from khata.flow import Bot
from khata.guard import PaidGuard
from khata.ledger import Ledger


def lg_with_data():
    lg = Ledger()
    lg.add_entry("o", "रमेश", "credit", 150000, note="चावल 5 kg")
    lg.add_expense("o", "rent", 800000, vendor="Landlord")
    lg.set_price("o", "Atta", 4500, "kg")
    return lg


@pytest.mark.parametrize("table", EXPORTABLE + ["all"])
@pytest.mark.parametrize("fmt", ["pdf", "xlsx"])
def test_every_table_in_both_formats(table, fmt):
    name, data, mime = export_file(lg_with_data(), "o", table, fmt)
    assert data[:4] == (b"%PDF" if fmt == "pdf" else b"PK\x03\x04") and name.endswith("." + fmt)
    if fmt == "xlsx":
        sheets = load_workbook(io.BytesIO(data)).sheetnames
        assert sheets == (EXPORTABLE if table == "all" else [table])


def test_unknown_table_is_refused():
    with pytest.raises(ValueError):
        export_file(Ledger(), "o", "Settings", "pdf")


def test_bot_menu_then_format():
    b = Bot(lg_with_data())
    r = b.handle("o", "export", source="telegram")
    assert r["buttons"] == [f"exp:{t}" for t in EXPORTABLE] + ["exp:all"]
    r = b.handle("o", "exp:Entries")
    assert r["buttons"] == ["fmt:pdf", "fmt:xlsx"]
    r = b.handle("o", "fmt:pdf")
    assert r["file"][:4] == b"%PDF" and r["file_name"] == "khata-entries.pdf"


@pytest.mark.parametrize("text,name", [("/export udhaar pdf", "khata-udhaar.pdf"),
                                       ("export bills excel", "khata-bills.xlsx"),
                                       ("export all xlsx", "khata.xlsx")])
def test_typed_export(text, name):
    r = Bot(lg_with_data()).handle("o", text, source="telegram")
    assert r["file_name"] == name


def test_typed_export_without_format_asks_for_it():
    b = Bot(lg_with_data())
    assert b.handle("o", "/export dues", source="telegram")["buttons"] == ["fmt:pdf", "fmt:xlsx"]
    assert b.handle("o", "fmt:xlsx")["file_name"] == "khata-dues.xlsx"


def test_web_endpoint():
    c = TestClient(create_app(":memory:", PaidGuard(key="", budget_calls=0, killed=False)))
    assert c.get("/api/export/Udhaar.pdf").status_code == 401
    c.post("/api/demo-login")
    r = c.get("/api/export/udhaar.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF" and "khata-udhaar.pdf" in r.headers["content-disposition"]
    assert c.get("/api/export/all.xlsx").content[:2] == b"PK"
    assert c.get("/api/export/Settings.pdf").status_code == 404
    assert c.get("/api/export/Udhaar.doc").status_code == 404
