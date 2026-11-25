"""The shopkeeper only has the Google Sheet: edits there must reach the khata, safely."""
from khata.flow import Bot
from khata.ledger import Ledger
from khata.sheets import SheetMirror
from test_sheets import Clock, FakeSheet

O = "tg-shop"


def setup():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, O, Clock())
    return lg, sh, m


def row(sh, tab, ref):
    head = sh.tabs[tab][0]
    r = next(r for r in sh.tabs[tab][1:] if str(r[0]) == ref)
    return head, r


def set_cell(sh, tab, ref, col, value):
    head, r = row(sh, tab, ref)
    r[head.index(col)] = value


def test_editing_amount_name_type_date_and_note_updates_the_khata():
    lg, sh, m = setup()
    lg.add_entry(O, "Ramesh", "credit", 50000, entry_date="2026-11-20")
    m.sync()
    set_cell(sh, "Udhaar", "K-1", "Amount (₹)", "1,500")
    set_cell(sh, "Udhaar", "K-1", "Name", "Suresh")
    set_cell(sh, "Udhaar", "K-1", "Date", "25/11/2026")
    set_cell(sh, "Udhaar", "K-1", "Note", "2 bags rice")
    r = m.sync()
    assert r["imported"]["edited"] == 1
    e = lg.entries(O)[0]
    assert (e.customer, e.paise, e.entry_date, e.note) == ("Suresh", 150000, "2026-11-25", "2 bags rice")
    assert lg.balances(O)["Suresh"] == 150000
    set_cell(sh, "Udhaar", "K-1", "Type", "payment")
    m.sync()
    assert lg.balance(O, "Suresh") == -150000
    assert row(sh, "Udhaar", "K-1")[1][2] == "Payment received"     # redrawn in the standard words
    assert m.sync().get("imported") is None                        # stable: nothing more to import


def test_deleting_a_row_cancels_the_entry():
    lg, sh, m = setup()
    for n in ("A", "B", "C", "D"):
        lg.add_entry(O, n, "credit", 100)
    m.sync()
    sh.tabs["Udhaar"] = [r for r in sh.tabs["Udhaar"] if r[0] != "K-2"]
    m.sync()
    assert [e.customer for e in lg.entries(O)] == ["A", "C", "D"]
    assert lg.entries(O, include_cancelled=True)[1].status == "cancelled"   # kept, marked cancelled


def test_clearing_most_of_a_tab_is_treated_as_an_accident():
    lg, sh, m = setup()
    for n in "ABCDEFGH":
        lg.add_entry(O, n, "credit", 100)
    m.sync()
    sh.tabs["Udhaar"] = sh.tabs["Udhaar"][:2]          # 7 of 8 rows gone at once
    m.sync()
    assert len(lg.entries(O)) == 8 and len(sh.tabs["Udhaar"]) == 9      # rows put back


def test_a_new_row_typed_in_the_sheet_becomes_an_entry_once():
    lg, sh, m = setup()
    lg.add_entry(O, "A", "credit", 100)
    m.sync()
    sh.tabs["Udhaar"].append(["", "24/11/2026", "udhaar", "Kamla", "450", "", "", "dal", ""])
    sh.tabs["Entries"].append(["", "", "Expense (Rent)", "Landlord", "8000"])
    sh.tabs["Entries"].append(["", "", "sale", "", "2500"])
    m.sync()
    assert lg.balance(O, "Kamla") == 45000 and lg.entries(O)[-1].note == "dal"
    assert [(x.direction, x.category, x.paise, x.vendor) for x in lg.expenses(O)] == [
        ("out", "rent", 800000, "Landlord"), ("in", "sale", 250000, "")]
    refs = [r[0] for r in sh.tabs["Udhaar"][1:]]
    assert sorted(refs) == ["K-1", "K-2"]              # got a Ref; typed row replaced
    m.sync()
    assert len(lg.entries(O)) == 2 and len(lg.expenses(O)) == 2     # never imported twice


def test_bad_edits_are_refused_and_explained_not_lost():
    lg, sh, m = setup()
    lg.add_entry(O, "Ramesh", "credit", 50000)
    m.sync()
    set_cell(sh, "Udhaar", "K-1", "Amount (₹)", "five hundred")
    m.sync()
    assert lg.entries(O)[0].paise == 50000             # unchanged
    head, r = row(sh, "Udhaar", "K-1")
    assert r[head.index("Note")].startswith("⚠️ Not saved: amount 'five hundred' is not a number")
    set_cell(sh, "Udhaar", "K-1", "Amount (₹)", "700")    # fix it
    m.sync()
    head, r = row(sh, "Udhaar", "K-1")
    assert lg.entries(O)[0].paise == 70000 and r[head.index("Note")] == ""
    sh.tabs["Udhaar"].append(["", "yesterday-ish", "udhaar", "Kamla", "450"])
    m.sync()
    kept = sh.tabs["Udhaar"][-1]
    assert kept[3] == "Kamla" and kept[7].startswith("⚠️ Not saved: date") and len(lg.entries(O)) == 1


def test_bot_changes_are_not_undone_by_an_older_sheet():
    lg, sh, m = setup()
    lg.add_entry(O, "Ramesh", "credit", 50000)
    m.sync()
    lg.add_entry(O, "Sita", "credit", 20000)           # via the bot, sheet not redrawn yet
    lg.undo_last(O)                                    # and undone again
    lg.add_entry(O, "Mohan", "credit", 10000)
    m.sync()
    assert lg.balances(O) == {"Ramesh": 50000, "Sita": 0, "Mohan": 10000}
    assert {r[3] for r in sh.tabs["Udhaar"][1:]} == {"Ramesh", "Mohan"}


def test_rates_and_customers_tabs_are_editable_and_restored():
    lg, sh, m = setup()
    lg.set_price(O, "Atta", 4500, "kg")
    lg.add_entry(O, "Ramesh", "credit", 100)
    m.sync()
    set_cell(sh, "Rates", "Atta", "Rate (₹)", "48")
    sh.tabs["Rates"].append(["Cheeni", "46", "kg"])
    set_cell(sh, "Customers", "Ramesh", "WhatsApp number", "98765 43210")
    sh.tabs["Customers"].append(["Sita", "abc"])
    m.sync()
    assert lg.price(O, "atta") == 4800 and lg.price(O, "cheeni") == 4600
    assert lg.phones(O) == {"Ramesh": "919876543210"}
    assert sh.tabs["Customers"][-1][2].startswith("⚠️ Not saved: WhatsApp number")
    sh.tabs["Rates"] = [r for r in sh.tabs["Rates"] if r[0] != "Cheeni"]
    m.sync()
    assert lg.price(O, "cheeni") is None
    fresh = Ledger()                                   # disk wiped: rates and phones come back
    SheetMirror(fresh, sh, O, Clock()).boot()
    assert fresh.price(O, "atta") == 4800 and fresh.phones(O) == {"Ramesh": "919876543210"}


def test_bills_and_dues_are_calculated_and_redrawn():
    lg, sh, m = setup()
    lg.add_entry(O, "Ramesh", "credit", 50000)
    m.sync()
    sh.tabs["Dues"][1][1] = 99999
    m.sync()
    assert sh.tabs["Dues"][1] == ["Ramesh", 500, 0]


def test_linked_phones_survive_a_disk_wipe():
    lg, sh, m = setup()
    lg.add_entry(O, "A", "credit", 100)
    lg.link("tg-other", O)
    m.sync()
    fresh = Ledger()
    SheetMirror(fresh, sh, O, Clock()).boot()
    assert fresh.shop_of("tg-other") == O


def test_renamed_headers_change_nothing():
    lg, sh, m = setup()
    lg.add_entry(O, "A", "credit", 100)
    m.sync()
    sh.tabs["Udhaar"][0][4] = "Rupees"
    sh.tabs["Udhaar"] = sh.tabs["Udhaar"][:1]
    m.sync()
    assert len(lg.entries(O)) == 1 and sh.tabs["Udhaar"][0][4] == "Amount (₹)"


def test_entries_typed_by_bot_and_sheet_together():
    lg, sh, m = setup()
    b = Bot(lg)
    b.handle(O, "Ramesh ko 500 udhaar"); b.handle(O, "haan")
    m.sync()
    sh.tabs["Udhaar"].append(["", "", "payment", "Ramesh", "200"])
    m.sync()
    assert lg.balance(O, "Ramesh") == 30000
    assert "₹300" in b.handle(O, "Ramesh ka kitna baaki")["reply"]
