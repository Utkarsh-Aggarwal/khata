"""Three modes for Telegram shopkeepers: Bill (make a customer bill), Entry (money in/out),
Udhaar (credit given/taken and returned). The shopkeeper always picks the mode."""
import io

import pytest

from khata import billing
from khata.bills import SAMPLE_KACHA, intro
from khata.flow import Bot
from khata.ledger import Ledger

O = "tg-shop"


def bot():
    lg = Ledger()
    return lg, Bot(lg, mode_prefixes=("tg-",))


def pick(b, text, mode):
    r = b.handle(O, text)
    assert r["buttons"] == ["mode:bill", "mode:entry", "mode:udhaar"]
    return b.handle(O, f"mode:{mode}")


# ---- line parsing --------------------------------------------------------------------
@pytest.mark.parametrize("text,name,qty,unit,rate,amount", [
    ("atta 2 kg 45", "Atta", 2, "kg", 4500, 9000),
    ("sabun 2 x 35", "Sabun", 2, "", 3500, 7000),
    ("chai patti 250 g 70", "Chai Patti", 250, "g", None, 7000),
    ("tel 1 L 180", "Tel", 1, "L", 18000, 18000),
    ("maggi 12", "Maggi", 1, "", 1200, 1200),
    ("anda 6 5", "Anda", 6, "", 500, 3000),
    ("cheeni 3 kg", "Cheeni", 3, "kg", None, None),
    ("चावल 5 किलो 60", "चावल", 5, "kg", 6000, 30000),
])
def test_parse_line(text, name, qty, unit, rate, amount):
    l = billing.parse_line(text)
    assert (l.name, l.qty, l.unit, l.rate, l.amount) == (name, qty, unit, rate, amount)


def test_edits_rate_qty_remove_and_price_memory():
    d = billing.Draft()
    prices = {"cheeni": 4800}
    billing.apply(d, "atta 2 kg 45, tel 1 L 180, cheeni 3 kg", lambda n: prices.get(n.lower()))
    assert [l.amount for l in d.lines] == [9000, 18000, 14400]           # cheeni rate remembered
    billing.apply(d, "atta 50", lambda n: None)                          # new rate
    billing.apply(d, "atta 4 kg", lambda n: None)                        # new qty, rate kept
    billing.apply(d, "hatao tel", lambda n: None)
    assert [(l.name, l.qty, l.amount) for l in d.lines] == [("Atta", 4, 20000), ("Cheeni", 3, 14400)]
    assert d.total == 34400


def test_similar_short_names_are_not_merged():
    d = billing.Draft()
    billing.apply(d, "atta 2 kg 45, tel 1 L 180, aloo 1 kg 30", lambda n: None)
    assert [l.name for l in d.lines] == ["Atta", "Tel", "Aloo"]


def test_roman_for_english_pdf():
    assert billing.roman("चावल") == "Chaval" and billing.roman("रमेश") == "Ramesh"
    assert billing.roman("Atta") == "Atta"


def test_pdf_is_valid_and_english():
    d = billing.Draft()
    billing.apply(d, "चावल 5 kg 60, sabun 2 x 35", lambda n: None)
    pdf = billing.build_pdf(d, bill_no=7, customer="रमेश", payment="udhaar")
    assert pdf[:5] == b"%PDF-" and len(pdf) > 2000


# ---- modes ---------------------------------------------------------------------------
def test_every_new_message_asks_the_mode_and_saves_nothing():
    lg, b = bot()
    r = b.handle(O, "Ramesh ko 500 udhaar")
    assert r["buttons"] == ["mode:bill", "mode:entry", "mode:udhaar"] and "🧾" in r["reply"]
    assert lg.entries(O) == [] and lg.expenses(O) == []


def test_commands_skip_the_mode_question():
    lg, b = bot()
    lg.add_entry(O, "Ramesh", "credit", 50000)
    assert "Udhaar you gave" in b.handle(O, "/dues")["reply"]
    assert "Ramesh" in b.handle(O, "udhaar")["reply"]                    # one command: balances now


def test_udhaar_mode_shows_real_time_balance():
    lg, b = bot()
    r = pick(b, "Ramesh ko 500 udhaar", "udhaar")
    assert r["buttons"] == ["yes", "no"]
    assert "पर ₹500 बाकी" in b.handle(O, "haan")["reply"]
    pick(b, "Ramesh ne 200 diye", "udhaar")
    assert "पर ₹300 बाकी" in b.handle(O, "haan")["reply"]
    assert lg.balance(O, "Ramesh") == 30000


def test_entry_mode_money_in_and_out_changes_no_balance():
    lg, b = bot()
    r = pick(b, "aaj 5000 ki bikri", "entry")
    assert "📥" in r["reply"] and r["buttons"] == ["yes", "no"]
    b.handle(O, "yes")
    pick(b, "bijli 900", "entry")
    b.handle(O, "yes")
    r = pick(b, "Ramesh 300", "entry")                  # direction unclear: ask
    assert r["buttons"] == ["dir:in", "dir:out"]
    b.handle(O, "dir:out"); b.handle(O, "yes")
    xs = lg.expenses(O)
    assert [(x.direction, x.category, x.paise) for x in xs] == [("in", "sale", 500000), ("out", "electricity", 90000),
                                                               ("out", "other", 30000)]
    assert lg.balances(O) == {}
    assert lg.expense_totals(O) == {"electricity": 90000, "other": 30000}   # sales are not expenses


def test_bill_cash_flow_makes_pdf_records_sale_and_remembers_rates():
    lg, b = bot()
    r = pick(b, "atta 2 kg 45, sabun 2 x 35, cheeni 3 kg", "bill")
    assert r["buttons"] == ["bill:done", "bill:cancel"] and "₹?" in r["reply"]
    r = b.handle(O, "bill:done")
    assert "Cheeni" in r["reply"] and r["buttons"] == ["bill:done", "bill:cancel"]   # rate missing
    b.handle(O, "cheeni 48")
    r = b.handle(O, "bill:done")
    assert r["buttons"] == ["pay:cash", "pay:udhaar", "bill:cancel"] and "₹304" in r["reply"]
    assert lg.expenses(O) == []                                           # nothing saved yet
    r = b.handle(O, "pay:cash")
    assert r["pdf"][:5] == b"%PDF-" and r["pdf_name"] == f"bill-{r['bill_no']}.pdf"
    x = lg.expenses(O)[0]
    assert (x.direction, x.category, x.paise) == ("in", "sale", 30400) and x.note.startswith("Bill ")
    assert lg.price(O, "cheeni") == 4800
    r = pick(b, "cheeni 2 kg", "bill")                                    # rate filled from memory
    assert "₹96" in r["reply"]
    assert "Cheeni: ₹48 / kg" in b.handle(O, "rates")["reply"]


def test_bill_udhaar_flow_adds_to_customer_balance():
    lg, b = bot()
    lg.add_entry(O, "Ramesh", "credit", 10000)
    pick(b, "tel 1 L 180", "bill")
    b.handle(O, "bill:done")
    assert "name" in b.handle(O, "pay:udhaar")["reply"]
    assert "Whose" in b.handle(O, "yes")["reply"]                         # not a name: ask again
    r = b.handle(O, "ramesh")
    assert "₹180" in r["reply"] and "Ramesh owes you ₹280" in r["reply"] and r["pdf"][:5] == b"%PDF-"
    assert lg.balance(O, "Ramesh") == 28000


def test_bill_cancel_and_commands_during_a_draft():
    lg, b = bot()
    lg.add_entry(O, "Sita", "credit", 100)
    pick(b, "atta 2 kg 45", "bill")
    assert "Sita" in b.handle(O, "/dues")["reply"]                        # answered, draft kept
    assert "Atta" in b.handle(O, "sabun 1 x 30")["reply"]
    assert "cancelled" in b.handle(O, "bill:cancel")["reply"]
    assert lg.expenses(O) == [] and O not in b.drafts


def test_bill_photo_offers_all_three_modes():
    lg, b = bot()
    r = b.start_from_bill(O, SAMPLE_KACHA, intro(SAMPLE_KACHA))
    assert "⚠️ Please check" in r["reply"] and r["buttons"] == ["mode:bill", "mode:entry", "mode:udhaar"]
    r = b.handle(O, "mode:bill")                                          # turn the kacha bill into a clean bill
    assert "चावल" in r["reply"] and r["buttons"] == ["bill:done", "bill:cancel"]
    b.start_from_bill(O, SAMPLE_KACHA, intro(SAMPLE_KACHA))
    r = b.handle(O, "mode:entry")                                         # or a supplier purchase
    assert "🧾" in r["reply"]
    b.handle(O, "yes")
    assert lg.expenses(O)[0].vendor == "Gupta Kiryana"


def test_web_and_other_owners_keep_the_old_flow():
    lg = Ledger()
    b = Bot(lg, mode_prefixes=("tg-",))
    assert b.handle("demo-x", "Ramesh ko 500 udhaar")["buttons"] == ["yes", "no"]


def test_sheet_shows_money_in_and_restores_it():
    from khata import render
    from khata.sheets import SheetMirror
    from test_sheets import Clock, FakeSheet
    lg, b = bot()
    pick(b, "aaj 5000 ki bikri", "entry"); b.handle(O, "yes")
    sh = FakeSheet()
    SheetMirror(lg, sh, O, Clock()).sync()
    assert sh.khata()[0]["Type"] == "Money in (Sale)"
    fresh = Ledger()
    assert SheetMirror(fresh, sh, O, Clock()).boot() == {"restored": 1, "skipped": 0}
    assert fresh.expenses(O)[0].direction == "in"
