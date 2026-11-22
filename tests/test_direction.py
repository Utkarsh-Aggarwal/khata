"""Which way did the money go? Cases from the shopkeeper's own review, plus the doubt flow.

credit  = money or goods went from the shop to the person (they owe more / shop owes less)
payment = money came from the person to the shop (they owe less / shop owes more)
"""
import pytest

from khata.extract import extract
from khata.flow import M, Bot
from khata.ledger import Ledger

CASES = [
    # customer took credit
    ("Ramesh ne 500 udhaar liya", "add_credit"),
    ("Ramesh took 500 on credit", "add_credit"),
    ("रमेश ने 500 रुपये उधार लिए", "add_credit"),
    ("Ramesh ko 500 udhaar diya", "add_credit"),
    # customer paid me / paid their bill
    ("Ramesh ne 500 diye", "add_payment"),
    ("Ramesh paid me 500", "add_payment"),
    ("Ramesh ne bill ka 500 bhar diya", "add_payment"),
    # customer returned money
    ("Ramesh ne 500 wapas kiye", "add_payment"),
    ("Ramesh ne 500 lautaye", "add_payment"),
    ("रमेश ने 500 रुपये लौटाए", "add_payment"),
    ("Ramesh returned 500", "add_payment"),
    # I gave / I paid / I returned money to them
    ("maine Ramesh ko 500 diye", "add_credit"),
    ("I paid Ramesh 500", "add_credit"),
    ("maine Ramesh ko 500 wapas kiye", "add_credit"),
    ("I returned 500 to Ramesh", "add_credit"),
    ("मैंने रमेश को 500 रुपये लौटाए", "add_credit"),
    # I borrowed from them: money came to the shop
    ("मैंने रमेश से 500 रुपये उधार लिए", "add_payment"),
    # "I paid the bill": whose bill? ask instead of guessing
    ("maine Ramesh ka 500 ka bill bhara", "add_unknown"),
    ("maine bill 500 bhara", "add_unknown"),
]


@pytest.mark.parametrize("text,intent", CASES)
def test_direction(text, intent):
    e = extract(text)
    assert e.intent == intent and e.paise == 50000, (text, e)
    assert e.customer in ("Ramesh", "रमेश", None), (text, e.customer)


def test_confirm_says_direction_and_balance_after():
    lg = Ledger()
    b = Bot(lg)
    r = b.handle("o", "Ramesh took 500 on credit")
    assert "you gave Ramesh ₹500" in r["reply"] and "Ramesh owes you ₹500" in r["reply"]
    b.handle("o", "yes")
    r = b.handle("o", "I returned 800 to Ramesh")   # shop pays out more than Ramesh owed? no: shop -> Ramesh
    assert "Ramesh owes you ₹1,300" in r["reply"]
    b.handle("o", "no")
    r = b.handle("o", "Ramesh returned 700")
    assert "Ramesh gave you ₹700" in r["reply"] and "you owe Ramesh ₹200" in r["reply"]
    assert "you owe Ramesh ₹200" in b.handle("o", "yes")["reply"]


def test_doubt_explains_and_keeps_entry_waiting():
    lg = Ledger()
    b = Bot(lg)
    r = b.handle("o", "maine bill 500 bhara")
    assert r["reply"].startswith("यह ₹500 किसका बिल था") and r["buttons"] == ["expense", "?"]
    r = b.handle("o", "Ramesh")
    assert r["buttons"] == ["credit", "payment", "?"] and "₹500" in r["reply"]
    r = b.handle("o", "?")
    assert "📤" in r["reply"] and "📥" in r["reply"] and r["buttons"] == ["credit", "payment", "?"]
    r = b.handle("o", "kya matlab")                  # also a doubt; nothing discarded
    assert "discarded" not in r["reply"] and "हटा" not in r["reply"]
    r = b.handle("o", "credit")                      # I paid Ramesh's bill for him
    assert r["buttons"] == ["yes", "no"]
    b.handle("o", "yes")
    assert lg.balance("o", "Ramesh") == 50000


def test_doubt_at_confirm_step():
    lg = Ledger()
    b = Bot(lg)
    b.handle("o", "Ramesh ko 500 udhaar")
    r = b.handle("o", "?")
    assert "📤" in r["reply"] and r["buttons"] == ["yes", "no"]
    b.handle("o", "haan")
    assert lg.balance("o", "Ramesh") == 50000


def test_kind_answer_in_words():
    lg = Ledger()
    b = Bot(lg)
    b.handle("o", "maine bill 500 bhara")
    b.handle("o", "Sita")
    r = b.handle("o", "they gave me")["reply"]                 # Hinglish entry -> Hindi reply
    assert "📥 Sita ने आपको ₹500 दिए" in r and "आपको Sita को ₹500 देने हैं" in r
