"""Shop expenses: the shop's own costs, kept apart from customer balances."""
import io
from datetime import date

import pytest
from openpyxl import load_workbook

from khata.bills import SAMPLE_BILL, to_extraction
from khata.export import build_xlsx
from khata.extract import extract
from khata.flow import Bot
from khata.ledger import Ledger
from khata import render
from khata.sheets import SheetMirror

from test_sheets import Clock, FakeSheet


@pytest.mark.parametrize("text,cat,paise", [
    ("maine bijli ka bill 500 bhara", "electricity", 50000),
    ("मैंने बिजली का बिल 900 भरा", "electricity", 90000),
    ("bijli ka bill 1200", "electricity", 120000),
    ("dukaan ka kiraya 8000 diya", "rent", 800000),
    ("ਕਿਰਾਇਆ 5000", "rent", 500000),
    ("Raju ko 3000 salary di", "salary", 300000),
    ("chai 50", "tea", 5000),
    ("kal petrol 400", "transport", 40000),
    ("maal 5000 ka liya", "stock", 500000),
    ("gas cylinder 1100", "gas", 110000),
    ("500 ka kharcha", "other", 50000),
])
def test_expense_phrases(text, cat, paise):
    e = extract(text)
    assert (e.intent, e.category, e.paise) == ("add_expense", cat, paise), (text, e)


@pytest.mark.parametrize("text,intent", [
    ("Ramesh ko 500 udhaar", "add_credit"),          # customer words still win
    ("Ramesh ko maal diya 500", "add_credit"),       # goods given to a person = credit
    ("Sita ne 200 diye", "add_payment"),
    ("maine Ramesh ka 500 ka bill bhara", "add_unknown"),
    ("kharcha", "expense_report"),
    ("is mahine ka kharcha kitna hua", "expense_report"),
])
def test_not_expenses(text, intent):
    assert extract(text).intent == intent


def test_expense_confirm_save_and_balances_untouched():
    lg = Ledger()
    b = Bot(lg)
    lg.add_entry("o", "Ramesh", "credit", 50000)
    r = b.handle("o", "bijli ka bill 900")
    assert r["buttons"] == ["yes", "no"] and "🧾" in r["reply"] and "₹900" in r["reply"]
    assert lg.expenses("o") == []                          # nothing before confirm
    r = b.handle("o", "haan")
    assert "₹900" in r["reply"]
    assert lg.expense_totals("o") == {"electricity": 90000}
    assert lg.balances("o") == {"Ramesh": 50000} and lg.total_due("o") == 50000
    assert "₹900" in b.handle("o", "kharcha")["reply"]


def test_salary_keeps_who_in_note():
    lg = Ledger()
    b = Bot(lg)
    b.handle("o", "Raju ko 3000 salary di")
    b.handle("o", "yes")
    x = lg.expenses("o")[0]
    assert (x.category, x.paise, x.vendor) == ("salary", 300000, "Raju")
    assert lg.balances("o") == {}                          # Raju is staff, not a customer


def test_whose_bill_shop_expense_or_person():
    lg = Ledger()
    b = Bot(lg)
    r = b.handle("o", "maine bill 500 bhara")
    assert r["buttons"] == ["expense", "?"]
    r = b.handle("o", "?")
    assert "🧾" in r["reply"] and r["buttons"] == ["expense", "?"]
    r = b.handle("o", "expense")
    assert r["buttons"] == ["yes", "no"] and "🧾" in r["reply"]
    b.handle("o", "yes")
    assert lg.expense_totals("o") == {"other": 50000}
    # same question, but it was a person's bill: then credit/payment as before
    b.handle("o", "maine bill 300 bhara")
    r = b.handle("o", "Sita")
    assert r["buttons"] == ["credit", "payment", "?"]


def test_bill_photo_can_be_shop_purchase():
    lg = Ledger()
    b = Bot(lg)
    r = b.start_from_extraction("o", to_extraction(SAMPLE_BILL), "bill")
    assert r["buttons"] == ["expense", "?"]
    b.handle("o", "🧾")
    b.handle("o", "yes")
    x = lg.expenses("o")[0]
    assert (x.category, x.paise, x.vendor) == ("purchase", 84650, "Sharma Kirana Store")


def test_undo_takes_back_the_newest_of_either():
    lg = Ledger()
    b = Bot(lg)
    b.handle("o", "Ramesh ko 500 udhaar"); b.handle("o", "yes")
    b.handle("o", "chai 50"); b.handle("o", "yes")
    assert "🧾" in b.handle("o", "undo")["reply"]
    assert lg.expenses("o") == [] and lg.balance("o", "Ramesh") == 50000
    assert "Ramesh" in b.handle("o", "undo")["reply"]
    assert lg.balance("o", "Ramesh") == 0


def test_expenses_are_per_owner_and_deleted_with_owner():
    lg = Ledger()
    lg.add_expense("a", "rent", 100)
    lg.add_expense("b", "rent", 999)
    assert lg.expense_totals("a") == {"rent": 100}
    lg.delete_owner_data("b")
    assert lg.expenses("b", include_cancelled=True) == [] and lg.expense_totals("a") == {"rent": 100}


def test_month_filter():
    lg = Ledger()
    lg.add_expense("o", "rent", 100, entry_date="2020-01-05")
    lg.add_expense("o", "rent", 200)
    assert lg.expense_totals("o", date.today().replace(day=1).isoformat()) == {"rent": 200}


@pytest.mark.parametrize("answer", ["Me", "me", "mera", "khud", "मैं", "myself", "🧾"])
def test_me_means_shop_expense_not_a_customer_named_me(answer):
    """Real bug from the Google Sheet: 'Me' was saved as a customer with a payment."""
    lg = Ledger()
    b = Bot(lg)
    b.start_from_extraction("o", to_extraction(SAMPLE_BILL), "bill")
    r = b.handle("o", answer)
    assert "🧾" in r["reply"] and r["buttons"] == ["yes", "no"]
    b.handle("o", "yes")
    assert lg.balances("o") == {} and lg.expense_totals("o") == {"purchase": 84650}


def test_pronoun_is_never_a_customer_name():
    lg = Ledger()
    b = Bot(lg)
    b.handle("o", "500 udhaar")                          # asks "Who is the customer?"
    r = b.handle("o", "he")
    assert r["buttons"] == [] and lg.balances("o") == {}  # asked again, nothing created
    r = b.handle("o", "Ramesh")
    assert r["buttons"] == ["yes", "no"]


def test_dues_command_splits_given_and_taken():
    lg = Ledger()
    b = Bot(lg)
    lg.add_entry("o", "Ramesh", "credit", 150000)
    lg.add_entry("o", "Gupta", "credit", 250000)
    lg.add_entry("o", "Sita", "payment", 50000)          # Sita paid in advance: you owe her
    r = b.handle("o", "/dues")["reply"]
    assert "Udhaar you gave (they owe you): ₹4,000" in r and "• Gupta ₹2,500" in r
    assert "Udhaar you took (you owe them): ₹500" in r and "• Sita ₹500" in r
    assert "you will receive ₹3,500" in r
    assert "आपने उधार दिया" in b.handle("o", "हिसाब")["reply"]
    assert "Udhaar you gave" in b.handle("o", "list")["reply"]


def test_today_command_lists_todays_expenses_only():
    lg = Ledger()
    b = Bot(lg)
    assert "No shop expenses recorded today" in b.handle("o", "today")["reply"]
    lg.add_expense("o", "electricity", 90000)
    lg.add_expense("o", "purchase", 449000, vendor="GUJARAT FREIGHT TOOLS")
    lg.add_expense("o", "rent", 800000, entry_date="2020-01-01")
    r = b.handle("o", "aaj ka kharcha")["reply"]
    assert "₹5,390" in r and "(GUJARAT FREIGHT TOOLS)" in r and "₹8,000" not in r
    assert "₹5,390" in b.handle("o", "/today")["reply"]


def test_old_bill_note_moves_into_vendor_on_open(tmp_path):
    db = str(tmp_path / "k.db")
    lg = Ledger(db)
    lg.db.execute("INSERT INTO expenses(owner,category,paise,note,vendor,entry_date,created_at) "
                  "VALUES('o','purchase',449000,'bill: GUJARAT FREIGHT TOOLS','','2025-07-23','2025-07-23')")
    lg.db.commit()
    x = Ledger(db).expenses("o")[0]
    assert (x.vendor, x.note) == ("GUJARAT FREIGHT TOOLS", "bill photo")


def test_old_bill_date_is_stated_and_today_redates_it():
    """Real chat: a 2025-01-10 bill was confirmed as 'This month's expenses would be ₹38,026'."""
    from khata.bills import BillFields
    lg = Ledger()
    b = Bot(lg)
    b.start_from_extraction("o", to_extraction(BillFields("SUNRISE ENTERPRISE", 3802600, "2025-01-10")), "bill")
    r = b.handle("o", "expense")
    assert "2025-01-10" in r["reply"] and "January 2025" in r["reply"] and "this month's" not in r["reply"].lower()
    assert r["buttons"] == ["yes", "no", "today"]
    r = b.handle("o", "today")                         # paid today
    assert "This month's expenses would be ₹38,026" in r["reply"]
    b.handle("o", "yes")
    assert lg.expenses("o")[0].entry_date == date.today().isoformat()
    b.start_from_extraction("o", to_extraction(BillFields("SUNRISE ENTERPRISE", 100, "2025-01-10")), "bill")
    b.handle("o", "expense")
    assert "January 2025" in b.handle("o", "yes")["reply"]


def test_export_matches_sheet_layout_with_expenses_inline():
    lg = Ledger()
    lg.add_entry("o", "Ramesh", "credit", 50000)
    lg.add_expense("o", "electricity", 90050, note="meter 2", vendor="PSPCL")
    wb = load_workbook(io.BytesIO(build_xlsx(lg, "o")))
    assert wb.sheetnames == ["Transactions", "Dues"]
    rows = [dict(zip(render.COLUMNS, r)) for r in list(wb["Transactions"].iter_rows(values_only=True))[1:]]
    x = next(r for r in rows if r["Type"].startswith("Expense"))
    assert (x["Name"], x["Type"], x["Amount (₹)"], x["Note"]) == ("PSPCL", "Expense (Electricity)", 900.5, "meter 2")


def test_bill_vendor_reaches_the_sheet_name_column():
    lg, sh = Ledger(), FakeSheet()
    b = Bot(lg)
    b.start_from_extraction("o", to_extraction(SAMPLE_BILL), "bill")
    b.handle("o", "expense"); b.handle("o", "yes")
    SheetMirror(lg, sh, "o", Clock()).sync()
    row = sh.khata()[0]
    assert (row["Name"], row["Type"], row["Via"], row["Note"]) == ("Sharma Kirana Store", "Expense (Purchase)",
                                                                       "Bill photo", "")
