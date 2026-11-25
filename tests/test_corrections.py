"""Fixing a wrong name or direction at the confirm step, the way a shopkeeper would say it."""
import pytest

from khata.extract import extract
from khata.flow import Bot
from khata.ledger import Ledger


def confirm(first="mujhko suresh ne 500 diye", modes=True):
    lg = Ledger()
    b = Bot(lg, mode_prefixes=("tg-",) if modes else ())
    o = "tg-x" if modes else "demo-x"
    r = b.handle(o, first)
    if "mode:udhaar" in (r.get("buttons") or []):
        r = b.handle(o, "mode:udhaar")
    assert r["buttons"][:2] in (["yes", "no"], ["credit", "payment"])
    return b, lg, o


@pytest.mark.parametrize("text,name", [
    ("mujhko ramesh ne 500 diye", "Ramesh"), ("mujhe ramesh ne 500 diye", "Ramesh"),
    ("ramesh mujhko 500 diya", "Ramesh"), ("मुझको रमेश ने 500 दिए", "रमेश"),
    ("ਮੈਨੂੰ ਰਮੇਸ਼ ਨੇ 500 ਦਿੱਤੇ", "ਰਮੇਸ਼"), ("ramesh ne mere 500 lautaye", "Ramesh"),
])
def test_me_words_are_never_part_of_a_name(text, name):
    e = extract(text)
    assert e.customer == name and e.intent == "add_payment" and e.paise == 50000


@pytest.mark.parametrize("fix,name", [
    ("Ramesh", "Ramesh"), ("nahi Ramesh", "Ramesh"), ("naam Ramesh hai", "Ramesh"),
    ("name is Ramesh", "Ramesh"), ("galat naam, Ramesh", "Ramesh"), ("no its Ramesh", "Ramesh"),
    ("नहीं, रमेश", "रमेश"), ("Ramesh Kumar", "Ramesh Kumar"),
])
def test_a_name_correction_changes_the_name_and_asks_again(fix, name):
    b, lg, o = confirm()
    r = b.handle(o, fix)
    assert name in r["reply"] and r["buttons"] == ["yes", "no"]
    assert b.pending[o].ex.customer == name and lg.balances(o) == {}       # still not saved
    b.handle(o, "haan")
    assert lg.balances(o) == {name: -50000}


def test_a_correction_finds_an_existing_customer():
    b, lg, o = confirm()
    lg.add_entry(o, "Ramesh", "credit", 100)
    b.handle(o, "रमेश")
    assert b.pending[o].ex.customer == "Ramesh"


def test_direction_correction():
    b, lg, o = confirm()
    r = b.handle(o, "nahi, maine diye")
    assert "📤" in r["reply"]
    b.handle(o, "yes")
    assert lg.balances(o) == {"Suresh": 50000}


def test_name_and_direction_in_one_go():
    b, lg, o = confirm()
    b.handle(o, "Ramesh ne liye")
    b.handle(o, "yes")
    assert lg.balances(o) == {"Ramesh": 50000}


def test_correction_while_asked_which_way():
    b, lg, o = confirm("ramesh se 500 lene hain")
    r = b.handle(o, "Ramesh Kumar")
    assert r["buttons"][:2] == ["credit", "payment"]
    r = b.handle(o, "maine diye")
    assert r["buttons"] == ["yes", "no"]
    b.handle(o, "yes")
    assert lg.balances(o) == {"Ramesh Kumar": 50000}


def test_works_without_modes_too():
    b, lg, o = confirm(modes=False)
    b.handle(o, "nahi Ramesh")
    b.handle(o, "yes")
    assert lg.balances(o) == {"Ramesh": -50000}


@pytest.mark.parametrize("chat", ["acha", "wait", "hmm", "ruko", "thanks", "done", "bas", "kya hua"])
def test_chat_words_never_rename_and_keep_the_entry(chat):
    b, lg, o = confirm()
    r = b.handle(o, chat)
    assert b.pending[o].ex.customer == "Suresh" and r["buttons"] == ["yes", "no"] and lg.balances(o) == {}


@pytest.mark.parametrize("yes", ["theek hai", "haan ji", "ok ji", "sahi hai", "haan save karo", "yes please"])
def test_plain_yes_phrases_save(yes):
    b, lg, o = confirm()
    b.handle(o, yes)
    assert lg.balances(o) == {"Suresh": -50000}


def test_a_new_entry_is_not_a_correction():
    b, lg, o = confirm()
    r = b.handle(o, "Sita ne 200 diye")
    assert o not in b.pending and lg.balances(o) == {}
    assert "mode:udhaar" in r["buttons"]


@pytest.mark.parametrize("fix,kind", [
    ("usne liye", "add_credit"), ("unhone liya", "add_credit"), ("Ramesh ne liye", "add_credit"),
    ("maine diye", "add_credit"), ("Ramesh ko diye", "add_credit"), ("udhaar", "add_credit"),
    ("maine liye", "add_payment"), ("Ramesh se liye", "add_payment"), ("usne diye", "add_payment"),
    ("Ramesh ne diye", "add_payment"), ("Ramesh se mile", "add_payment"), ("jama", "add_payment"),
    ("diye", "add_unknown"), ("maine usne diye", "add_unknown"),     # unclear: ask, don't guess
])
def test_direction_corrections_go_the_right_way_or_ask(fix, kind):
    b, lg, o = confirm()
    r = b.handle(o, fix)
    assert b.pending[o].ex.intent == kind and lg.balances(o) == {}
    assert r["buttons"][:2] == (["credit", "payment"] if kind == "add_unknown" else ["yes", "no"])


@pytest.mark.parametrize("reply", ["1 do", "ok 1", "y do"])
def test_yes_with_a_number_never_saves(reply):
    b, lg, o = confirm()
    b.handle(o, reply)
    assert lg.balances(o) == {}


@pytest.mark.parametrize("word", ["kaun", "bye", "kab", "कौन"])
def test_question_and_signoff_words_are_not_names(word):
    b, lg, o = confirm()
    b.handle(o, word)
    assert o not in b.pending or b.pending[o].ex.customer == "Suresh"


@pytest.mark.parametrize("fix", ["nahi Ramesh ji", "Ramesh bhai"])
def test_honorifics_are_dropped_from_a_corrected_name(fix):
    b, lg, o = confirm()
    b.handle(o, fix)
    assert b.pending[o].ex.customer == "Ramesh"


@pytest.mark.parametrize("fix", ["maine nahi diye", "usne nahi diye", "Suresh ne nahi diye"])
def test_a_negated_direction_asks_instead_of_flipping(fix):
    b, lg, o = confirm()
    r = b.handle(o, fix)
    assert b.pending[o].ex.intent == "add_unknown" and r["buttons"][:2] == ["credit", "payment"]


@pytest.mark.parametrize("fix,name", [
    ("Suresh nahi, Ramesh", "Ramesh"), ("Ramesh nahi Suresh", "Suresh"), ("Suresh nahi Ramesh hai", "Ramesh"),
    ("not Suresh, Ramesh", "Ramesh"), ("no, Ramesh Kumar", "Ramesh Kumar"),
])
def test_not_x_but_y_keeps_only_the_right_name(fix, name):
    b, lg, o = confirm()
    b.handle(o, fix)
    assert b.pending[o].ex.customer == name


@pytest.mark.parametrize("fix", ["Suresh nahi", "not Suresh"])
def test_only_saying_the_name_is_wrong_asks_who(fix):
    b, lg, o = confirm()
    b.handle(o, fix)
    assert b.pending[o].awaiting == "customer" and b.pending[o].ex.customer is None
    b.handle(o, "Ramesh")
    assert b.pending[o].ex.customer == "Ramesh" and b.pending[o].awaiting == "confirm"


def test_two_names_without_a_not_is_not_a_correction():
    b, lg, o = confirm()
    b.handle(o, "ramesh, sita")
    assert o not in b.pending and lg.balances(o) == {}


@pytest.mark.parametrize("fix,kind", [
    ("Ramesh ko mile", "add_unknown"), ("रमेश को मिले", "add_unknown"), ("he received", "add_unknown"),
    ("he got", "add_unknown"), ("Ramesh got it", "add_unknown"), ("Ramesh se mile", "add_payment"),
    ("returned", "add_unknown"), ("Ramesh returned", "add_unknown"), ("I returned", "add_credit"),
    ("maine lautaye", "add_credit"), ("Ramesh ne wapas kiye", "add_payment"),
    ("usko diye", "add_credit"), ("usse liye", "add_payment"),
])
def test_received_and_returned_follow_who_did_it(fix, kind):
    b, lg, o = confirm("mujhko suresh ko 500 udhaar diye")
    b.pending["tg-x"].ex.intent = "add_credit"
    b.handle(o, fix)
    assert b.pending[o].ex.intent == kind


@pytest.mark.parametrize("answer,kind", [("returned", "add_credit"), ("I returned", "add_credit")])
def test_kind_question_answers_unchanged(answer, kind):
    b, lg, o = confirm("ramesh se 500 lene hain")
    b.handle(o, answer)
    assert b.pending[o].ex.intent == kind


@pytest.mark.parametrize("word", ["usko", "unko", "yaar", "nahi yaar", "pagal"])
def test_pronouns_and_interjections_never_become_names(word):
    b, lg, o = confirm()
    b.handle(o, word)
    assert o not in b.pending or b.pending[o].ex.customer == "Suresh"



@pytest.mark.parametrize("yes", ["haan kar do", "haan do", "ha do"])
def test_yes_do_it_saves(yes):
    b, lg, o = confirm()
    b.handle(o, yes)
    assert lg.balances(o) == {"Suresh": -50000}


@pytest.mark.parametrize("fix", ["not credit", "no, not credit", "it was not credit", "not udhaar", "credit nahi"])
def test_english_not_before_a_direction_asks(fix):
    b, lg, o = confirm()
    b.handle(o, fix)
    assert b.pending[o].ex.intent == "add_unknown"


@pytest.mark.parametrize("fix", ["not payment", "not jama"])
def test_not_payment_on_a_credit_asks(fix):
    b, lg, o = confirm()
    b.pending[o].ex.intent = "add_credit"
    b.handle(o, fix)
    assert b.pending[o].ex.intent == "add_unknown"


@pytest.mark.parametrize("fix", ["he gave him", "usne usko diye", "Ramesh ne usko diye", "Ramesh ne unko diye"])
def test_someone_gave_someone_asks(fix):
    b, lg, o = confirm()
    b.handle(o, fix)
    assert b.pending[o].ex.intent == "add_unknown"


@pytest.mark.parametrize("word", ["bilkul", "pakka", "mat karo"])
def test_affirmations_and_refusals_never_become_names(word):
    b, lg, o = confirm()
    b.handle(o, word)
    assert b.pending[o].ex.customer == "Suresh" and lg.balances(o) == {}


def test_bilkul_at_the_kind_question_is_not_a_name():
    b, lg, o = confirm("ramesh se 500 lene hain")
    b.handle(o, "bilkul")
    assert b.pending[o].ex.customer == "Ramesh"


@pytest.mark.parametrize("fix", ["maine nahi diye", "not credit", "not payment"])
def test_negation_at_the_kind_question_asks_again(fix):
    b, lg, o = confirm("ramesh se 500 lene hain")
    r = b.handle(o, fix)
    assert b.pending[o].ex.intent == "add_unknown" and r["buttons"][:2] == ["credit", "payment"]


def test_a_bare_marker_that_disagrees_asks():
    b, lg, o = confirm()                                    # a payment
    b.handle(o, "Ramesh ko")
    assert b.pending[o].ex.intent == "add_unknown" and b.pending[o].ex.customer == "Ramesh"



@pytest.mark.parametrize("credit", [False, True])
@pytest.mark.parametrize("fix", ["maine nhi diye", "I never gave", "maine nahin diye"])
def test_hinglish_and_english_negations_ask(fix, credit):
    b, lg, o = confirm()
    if credit:
        b.pending[o].ex.intent = "add_credit"
    b.handle(o, fix)
    assert b.pending[o].ex.intent == "add_unknown" and b.pending[o].ex.customer == "Suresh"


@pytest.mark.parametrize("no", ["nhi", "nhin"])
def test_nhi_cancels_like_nahi(no):
    b, lg, o = confirm()
    b.handle(o, no)
    assert o not in b.pending and lg.balances(o) == {}


def test_nhi_then_the_right_direction():
    b, lg, o = confirm()
    b.handle(o, "nhi, maine diye")
    assert b.pending[o].ex.intent == "add_credit" and b.pending[o].ex.customer == "Suresh"


@pytest.mark.parametrize("word", ["dont", "nahi chahiye"])
def test_refusal_words_are_not_names(word):
    b, lg, o = confirm()
    b.handle(o, word)
    assert o not in b.pending or b.pending[o].ex.customer == "Suresh"



@pytest.mark.parametrize("no", ["cancel karo", "nahi karna", "no no", "nahi save karo"])
def test_no_phrases_cancel(no):
    b, lg, o = confirm()
    r = b.handle(o, no)
    assert o not in b.pending and lg.balances(o) == {} and "Nothing saved" in b.say("cancelled", "en")
    assert r["reply"] == b.say("cancelled", "hi")
