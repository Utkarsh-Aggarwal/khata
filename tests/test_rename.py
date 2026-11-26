"""Fixing a customer's name after the entry was saved: 'uska naam Ramesh hai, Mujhko Ramesh nahi'."""
import pytest

from khata.flow import Bot
from khata.ledger import Ledger


def khata():
    lg, o = Ledger(), "tg-x"
    lg.add_entry(o, "मुझको रमेश", "credit", 50000)          # saved by the old extractor
    lg.add_entry(o, "Mahesh", "credit", 100)
    return Bot(lg, mode_prefixes=("tg-",)), lg, o


@pytest.mark.parametrize("text,new", [
    ("उसका नाम रमेश है, मुझको रमेश नहीं।", "रमेश"), ("मुझको रमेश नहीं, रमेश", "रमेश"),
    ("not Mujhko Ramesh, Ramesh", "Ramesh"), ("Mujhko Ramesh nahi, Ramesh hai", "Ramesh"),
    ("his name is Ramesh not Mujhko Ramesh", "Ramesh"),
])
def test_rename_asks_then_renames_everywhere(text, new):
    b, lg, o = khata()
    r = b.handle(o, text, source="voice")
    assert r["buttons"] == ["yes", "no"] and "मुझको रमेश" in r["reply"]
    assert "मुझको रमेश" in lg.balances(o)                   # nothing changed before the yes
    b.handle(o, "haan")
    assert lg.balances(o) == {new: 50000, "Mahesh": 100}
    assert lg.find_customer(o, "मुझको रमेश") == new         # the old spelling still finds them


def test_rename_into_an_existing_customer_merges():
    b, lg, o = khata()
    r = b.handle(o, "मुझको रमेश नहीं, Mahesh")
    assert "पहले से" in r["reply"]
    b.handle(o, "yes")
    assert lg.balances(o) == {"Mahesh": 50100}


def test_no_keeps_the_name():
    b, lg, o = khata()
    b.handle(o, "मुझको रमेश नहीं, रमेश")
    b.handle(o, "nahi")
    assert "मुझको रमेश" in lg.balances(o)


def test_name_right_after_saving():
    b, lg, o = khata()
    b.handle(o, "suresh ne 200 diye")
    b.handle(o, "mode:udhaar")
    b.handle(o, "yes")
    r = b.handle(o, "naam Sureshwar hai")
    assert '"Suresh"' in r["reply"] and r["buttons"] == ["yes", "no"]
    b.handle(o, "yes")
    assert "Sureshwar" in lg.balances(o) and "Suresh" not in lg.balances(o)


def test_explicit_rename_command():
    b, lg, o = khata()
    b.handle(o, "rename Mahesh to Mahesh Kumar")
    b.handle(o, "yes")
    assert "Mahesh Kumar" in lg.balances(o)


@pytest.mark.parametrize("text", ["Mahesh nahi aaya", "Mahesh nahi", "Mahesh ne nahi diye", "naam batao",
                                  "naam Ramesh hai", "Sita ne 500 diye"])
def test_ordinary_messages_are_not_renames(text):
    b, lg, o = khata()                                     # nothing saved this session, so no "last" name
    r = b.handle(o, text)
    assert o not in b.renames
    assert set(lg.balances(o)) == {"मुझको रमेश", "Mahesh"}


def test_another_message_drops_the_rename_question():
    b, lg, o = khata()
    b.handle(o, "मुझको रमेश नहीं, रमेश")
    b.handle(o, "dues")
    b.handle(o, "yes")
    assert "मुझको रमेश" in lg.balances(o)


def test_rename_never_touches_another_shop():
    b, lg, o = khata()
    lg.add_entry("tg-other", "मुझको रमेश", "credit", 700)
    b.handle(o, "मुझको रमेश नहीं, रमेश")
    b.handle(o, "yes")
    assert lg.balances("tg-other") == {"मुझको रमेश": 700}


def test_sheet_shows_the_new_name_and_does_not_undo_it():
    from khata.sheets import SheetMirror
    from test_sheets import Clock, FakeSheet
    b, lg, o = khata()
    sh = FakeSheet()
    m = SheetMirror(lg, sh, o, Clock())
    m.sync()
    b.handle(o, "मुझको रमेश नहीं, रमेश")
    b.handle(o, "yes")
    m.sync()
    m.sync()
    assert {r["Name"] for r in sh.khata()} == {"रमेश", "Mahesh"}
    assert lg.balances(o) == {"रमेश": 50000, "Mahesh": 100}
    assert [r[0] for r in sh.tabs["Customers"][1:]] == sorted(["रमेश", "Mahesh"]) or \
        {r[0] for r in sh.tabs["Customers"][1:]} == {"रमेश", "Mahesh"}
