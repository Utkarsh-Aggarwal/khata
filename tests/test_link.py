"""Several phones, one shop: /invite on the main phone, /join CODE on the other."""
from khata.flow import Bot
from khata.ledger import Ledger
from khata.sheets import SheetMirror
from khata.telegram import owner_for_chat
from test_sheets import Clock, FakeSheet
from test_telegram import SALT, FakeAPI, make_adapter, text_update

MAIN, OTHER = 6296653009, 7111222333


def setup():
    lg = Ledger()
    ad, _, api = make_adapter(bot=Bot(lg), api=FakeAPI())
    return lg, ad, api


def code_from(api):
    return next(w for w in api.sent()[-1][1].split() if w.isdigit() and len(w) == 6)


def test_join_moves_old_entries_and_new_ones_go_to_the_shop_sheet():
    lg, ad, api = setup()
    shop, other = owner_for_chat(MAIN, SALT), owner_for_chat(OTHER, SALT)
    lg.add_entry(shop, "Ramesh", "credit", 50000)
    lg.add_entry(other, "रमेश", "credit", 20000)          # same person, other phone, other script
    lg.add_expense(other, "electricity", 90000)
    ad.handle_update(text_update(1, "/invite", chat_id=MAIN))
    code = code_from(api)
    ad.handle_update(text_update(2, f"/join {code}", chat_id=OTHER))
    assert "2 earlier record(s) were moved" in api.sent()[-1][1]
    assert lg.balances(shop) == {"Ramesh": 70000} and lg.balances(other) == {}
    assert lg.expense_totals(shop) == {"electricity": 90000}
    ad.handle_update(text_update(3, "Sita ko 300 udhaar", chat_id=OTHER))
    ad.handle_update(text_update(4, "haan", chat_id=OTHER))
    assert lg.balance(shop, "Sita") == 30000              # the other phone writes to the shop
    sh = FakeSheet()
    SheetMirror(lg, sh, shop, Clock()).sync()
    assert {r["Name"] for r in sh.khata()} >= {"Ramesh", "Sita"}


def test_wrong_reused_or_self_codes_are_refused():
    lg, ad, api = setup()
    ad.handle_update(text_update(1, "/join 123456", chat_id=OTHER))
    assert "wrong or has expired" in api.sent()[-1][1]
    ad.handle_update(text_update(2, "/invite", chat_id=MAIN))
    code = code_from(api)
    ad.handle_update(text_update(3, f"/join {code}", chat_id=MAIN))
    assert "already the main phone" in api.sent()[-1][1]
    ad.handle_update(text_update(4, f"/join {code}", chat_id=OTHER))
    assert "wrong or has expired" in api.sent()[-1][1]   # one use only


def test_code_expires_after_ten_minutes():
    t = [0.0]
    lg = Ledger()
    ad, _, api = make_adapter(bot=Bot(lg), api=FakeAPI(), clock=lambda: t[0])
    ad.handle_update(text_update(1, "/invite", chat_id=MAIN))
    code = code_from(api)
    t[0] = 601
    ad.handle_update(text_update(2, f"/join {code}", chat_id=OTHER))
    assert "expired" in api.sent()[-1][1]


def test_leave_gives_the_phone_its_own_khata_back():
    lg, ad, api = setup()
    ad.handle_update(text_update(1, "/invite", chat_id=MAIN))
    ad.handle_update(text_update(2, f"/join {code_from(api)}", chat_id=OTHER))
    ad.handle_update(text_update(3, "/leave", chat_id=OTHER))
    assert "own khata again" in api.sent()[-1][1]
    assert lg.shop_of(owner_for_chat(OTHER, SALT)) == owner_for_chat(OTHER, SALT)
