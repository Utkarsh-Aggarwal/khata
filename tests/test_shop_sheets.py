"""Each shopkeeper connects their own Google Sheet (/sheet <link>) and can start fresh (/reset)."""
from khata.flow import Bot
from khata.ledger import Ledger
from khata.sheets import OFF, SheetMirror, SheetPool, SheetRegistry, sheet_id_of
from khata.telegram import owner_for_chat
from test_sheets import Clock, FakeSheet
from test_telegram import SALT, FakeAPI, make_adapter, text_update

MAIN, OTHER = 6296653009, 7111222333
SID_A = "1ErqXONt67wYJAPT10dV37smMw6uW01Hp7pvfTmQzKnY"
SID_B = "1BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


class Denied(Exception):
    class response:
        status_code = 403


class Google:
    """Sheets by id; ids in `denied` act like a sheet not shared with the service account."""
    def __init__(self):
        self.sheets: dict[str, FakeSheet] = {}
        self.denied: set[str] = set()

    def __call__(self, sid):
        if sid in self.denied:
            return _DeniedSheet()
        if sid.startswith("1RO"):
            return _ReadOnly()
        return self.sheets.setdefault(sid, FakeSheet())


class _ReadOnly(FakeSheet):
    def write_tab(self, tab, rows):
        raise Denied()


class _DeniedSheet(FakeSheet):
    def read_tab(self, tab):
        raise Denied()


def pool(lg=None, google=None, admin=None):
    lg, google = lg or Ledger(), google or Google()
    return SheetPool(lg, google, SheetRegistry(lg, admin), "khata@sa.iam", Clock()), lg, google


def callback(update_id, data, mid, chat_id=MAIN):
    return {"update_id": update_id, "callback_query": {
        "id": str(update_id), "data": data, "message": {"message_id": mid, "chat": {"id": chat_id, "type": "private"}}}}


def test_sheet_links_and_ids_are_read():
    assert sheet_id_of(f"https://docs.google.com/spreadsheets/d/{SID_A}/edit#gid=0") == SID_A
    assert sheet_id_of(SID_A) == SID_A
    assert sheet_id_of("hello") is None and sheet_id_of("https://example.com/x") is None


def test_two_shops_two_sheets_never_mix():
    p, lg, g = pool()
    p.connect("shop-a", SID_A)
    p.connect("shop-b", SID_B)
    lg.add_entry("shop-a", "Ramesh", "credit", 50000)
    lg.add_entry("shop-b", "Sita", "credit", 20000)
    p.run_once(full=True)
    assert [r["Name"] for r in g.sheets[SID_A].khata()] == ["Ramesh"]
    assert [r["Name"] for r in g.sheets[SID_B].khata()] == ["Sita"]


def test_boot_restores_every_shop_from_the_registry_after_the_disk_is_wiped():
    admin, g = FakeSheet(), Google()
    p, lg, _ = pool(google=g, admin=admin)
    p.connect("shop-a", SID_A)
    p.connect("shop-b", SID_B)
    lg.add_entry("shop-a", "Ramesh", "credit", 50000)
    lg.add_entry("shop-b", "Sita", "credit", 20000)
    p.run_once(full=True)
    fresh = Ledger()                                     # Render restarted
    p2 = SheetPool(fresh, g, SheetRegistry(fresh, admin), "", Clock())
    p2.boot()
    assert fresh.balances("shop-a") == {"Ramesh": 50000} and fresh.balances("shop-b") == {"Sita": 20000}


def test_env_default_shop_still_works_and_off_in_registry_wins():
    g = Google()
    p, lg, _ = pool(google=g)
    p.defaults = {"shop-a": SID_A}
    p.boot()
    assert p.get("shop-a")
    p.disconnect("shop-a")
    p2 = SheetPool(lg, g, SheetRegistry(lg), "", Clock())
    p2.defaults = {"shop-a": SID_A}
    p2.boot()
    assert p2.get("shop-a") is None and SheetRegistry(lg).load()["shop-a"] == OFF


def test_connect_refuses_to_mix_two_khatas():
    g = Google()
    other = Ledger()
    other.add_entry("x", "Mohan", "credit", 100)
    SheetMirror(other, g(SID_B), "x", Clock()).sync()       # SID_B already holds a khata
    p, lg, _ = pool(google=g)
    lg.add_entry("shop-a", "Ramesh", "credit", 50000)
    try:
        p.connect("shop-a", SID_B)
        raise AssertionError("should refuse")
    except Exception as e:
        assert str(e) == "both have data"
    assert lg.balances("shop-a") == {"Ramesh": 50000} and len(g.sheets[SID_B].khata()) == 1


def test_restore_into_a_busy_database_renumbers_without_duplicates():
    g = Google()
    other = Ledger()
    other.add_entry("x", "Mohan", "credit", 100)          # K-1 on another server
    SheetMirror(other, g(SID_B), "x", Clock()).sync()
    p, lg, _ = pool(google=g)
    lg.add_entry("demo-1", "Someone", "credit", 5)         # K-1 here belongs to someone else
    assert p.connect("shop-a", SID_B) == {"restored": 1, "skipped": 0}
    p.run_once(full=True)
    p.run_once(full=True)
    assert lg.balances("shop-a") == {"Mohan": 100} and lg.balances("demo-1") == {"Someone": 5}
    assert [r["Ref"] for r in g.sheets[SID_B].khata()] == ["K-2"]


def test_moving_to_a_new_sheet_leaves_the_old_one_alone():
    p, lg, g = pool()
    p.connect("shop-a", SID_A)
    lg.add_entry("shop-a", "Ramesh", "credit", 50000)
    p.run_once(full=True)
    p.connect("shop-a", SID_B)
    lg.add_entry("shop-a", "Sita", "credit", 100)
    p.run_once(full=True)
    assert [r["Name"] for r in g.sheets[SID_A].khata()] == ["Ramesh"]
    assert {r["Name"] for r in g.sheets[SID_B].khata()} == {"Ramesh", "Sita"}


# ---- Telegram -----------------------------------------------------------------------
def tg(p, lg):
    ad, _, api = make_adapter(bot=Bot(lg), api=FakeAPI(), sheets=p)
    return ad, api


def test_sheet_command_explains_connects_and_disconnects():
    p, lg, g = pool()
    ad, api = tg(p, lg)
    shop = owner_for_chat(MAIN, SALT)
    ad.handle_update(text_update(1, "/sheet", chat_id=MAIN))
    assert "khata@sa.iam" in api.sent()[-1][1]
    g.denied.add(SID_B)
    ad.handle_update(text_update(2, f"/sheet https://docs.google.com/spreadsheets/d/{SID_B}/edit", chat_id=MAIN))
    assert "Share it with khata@sa.iam" in api.sent()[-1][1] and p.get(shop) is None
    ad.handle_update(text_update(3, f"/sheet https://docs.google.com/spreadsheets/d/{SID_A}/edit", chat_id=MAIN))
    assert "Sheet connected" in api.sent()[-1][1] and p.sheet_ids[shop] == SID_A
    ad.handle_update(text_update(4, "/sheet", chat_id=MAIN))
    assert SID_A in api.sent()[-1][1]
    ad.handle_update(text_update(5, "/sheet off", chat_id=MAIN))
    assert "disconnected" in api.sent()[-1][1] and p.get(shop) is None


def test_only_the_main_phone_changes_the_sheet_or_resets():
    p, lg, _ = pool()
    ad, api = tg(p, lg)
    ad.handle_update(text_update(1, "/invite", chat_id=MAIN))
    code = next(w for w in api.sent()[-1][1].split() if w.isdigit() and len(w) == 6)
    ad.handle_update(text_update(2, f"/join {code}", chat_id=OTHER))
    ad.handle_update(text_update(3, f"/sheet {SID_A}", chat_id=OTHER))
    assert "main phone" in api.sent()[-1][1] and not p.mirrors
    ad.handle_update(text_update(4, "/reset", chat_id=OTHER))
    assert "main phone" in api.sent()[-1][1] and not api.sent()[-1][2]


def test_reset_needs_a_tap_clears_the_khata_and_keeps_the_old_sheet():
    p, lg, g = pool()
    ad, api = tg(p, lg)
    shop = owner_for_chat(MAIN, SALT)
    ad.handle_update(text_update(1, f"/sheet {SID_A}", chat_id=MAIN))
    lg.add_entry(shop, "Ramesh", "credit", 50000)
    lg.add_entry("someone-else", "Sita", "credit", 100)
    p.run_once(full=True)
    ad.handle_update(text_update(2, "/reset", chat_id=MAIN))
    assert api.sent()[-1][2] == ["reset:yes", "reset:no"]
    assert lg.balances(shop) == {"Ramesh": 50000}          # nothing deleted before the tap
    mid = ad.keyboards[MAIN]
    ad.handle_update(callback(3, "reset:yes", mid))
    assert "Khata cleared" in api.sent()[-1][1]
    assert lg.balances(shop) == {} and lg.balances("someone-else") == {"Sita": 100}
    p.run_once(full=True)
    assert [r["Name"] for r in g.sheets[SID_A].khata()] == ["Ramesh"]   # old sheet kept as a backup
    assert p.get(shop) is None
    ad.handle_update(text_update(4, f"/sheet {SID_B}", chat_id=MAIN))   # start again on a new sheet
    assert p.sheet_ids[shop] == SID_B


def test_reset_no_keeps_everything():
    p, lg, _ = pool()
    ad, api = tg(p, lg)
    shop = owner_for_chat(MAIN, SALT)
    lg.add_entry(shop, "Ramesh", "credit", 50000)
    ad.handle_update(text_update(1, "/reset", chat_id=MAIN))
    ad.handle_update(callback(2, "reset:no", ad.keyboards[MAIN]))
    assert "nothing was deleted" in api.sent()[-1][1] and lg.balances(shop) == {"Ramesh": 50000}


def test_stale_reset_button_does_nothing():
    p, lg, _ = pool()
    ad, api = tg(p, lg)
    shop = owner_for_chat(MAIN, SALT)
    lg.add_entry(shop, "Ramesh", "credit", 50000)
    ad.handle_update(text_update(1, "/reset", chat_id=MAIN))
    old = ad.keyboards[MAIN]
    ad.handle_update(text_update(2, "/dues", chat_id=MAIN))
    ad.handle_update(text_update(3, "Sita ko 100 udhaar", chat_id=MAIN))   # a newer keyboard
    ad.handle_update(callback(4, "reset:yes", old))
    assert lg.balances(shop) == {"Ramesh": 50000}


def test_a_sheet_we_can_only_read_is_refused():
    p, lg, _ = pool()
    lg.add_entry("shop-a", "Ramesh", "credit", 50000)
    try:
        p.connect("shop-a", "1RO" + "x" * 40)
        raise AssertionError("should refuse")
    except Exception as e:
        assert str(e) == "read only"
    assert p.get("shop-a") is None
