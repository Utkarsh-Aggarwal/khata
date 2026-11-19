from khata.ledger import Ledger
from khata.sheets import SheetMirror


class FakeSheet:
    def __init__(self):
        self.rows: list[list] = [["entry_id"]]       # header in row 1
        self.fail_next = 0
        self.lose_response = False

    def append(self, row):
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("down")
        self.rows.append(list(row))
        if self.lose_response:                       # write landed, but the caller never hears back
            self.lose_response = False
            raise TimeoutError("lost response")
        return len(self.rows)

    def find(self, entry_id):
        for i, r in enumerate(self.rows, start=1):
            if str(r[0]) == str(entry_id):
                return i
        return None

    def set_status(self, row, status):
        self.rows[row - 1][6] = status


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_append_then_undo_marks_cancelled():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("o", "Ramesh", "credit", 50000)
    assert m.sync()["synced"] == 1
    assert sh.rows[1][2:5] == ["Ramesh", "credit", 500.0] and sh.rows[1][6] == "active"
    lg.undo_last("o")
    m.sync()
    assert len(sh.rows) == 2 and sh.rows[1][6] == "cancelled"
    assert m.sync() == {"synced": 0, "failed": 0, "pending": 0}       # idempotent


def test_outage_is_retried_with_backoff():
    lg, sh, clk = Ledger(), FakeSheet(), Clock()
    m = SheetMirror(lg, sh, "o", clk)
    lg.add_entry("o", "Sita", "payment", 100)
    sh.fail_next = 1
    assert m.sync() == {"synced": 0, "failed": 1, "pending": 1}
    assert m.sync()["failed"] == 0 and len(sh.rows) == 1             # still backing off
    clk.t += 10
    assert m.sync()["synced"] == 1 and len(sh.rows) == 2


def test_lost_response_does_not_duplicate():
    lg, sh, clk = Ledger(), FakeSheet(), Clock()
    m = SheetMirror(lg, sh, "o", clk)
    lg.add_entry("o", "Sita", "payment", 100)
    sh.lose_response = True
    m.sync()
    clk.t += 10
    m.sync()
    assert [r[0] for r in sh.rows[1:]] == [1]                        # exactly one row for entry 1


def test_only_mirrors_its_owner():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("someone-else", "X", "credit", 100)
    lg.add_entry("o", "Y", "credit", 100)
    m.sync()
    assert [r[2] for r in sh.rows[1:]] == ["Y"]


def test_google_client_request_shapes():
    from khata.sheets import GoogleSheetClient

    class Resp:
        def __init__(self, j): self.j = j
        def json(self): return self.j
        def raise_for_status(self): pass

    class Sess:
        calls = []
        def post(self, url, **kw):
            self.calls.append(("post", url, kw)); return Resp({"updates": {"updatedRange": "Khata!A12:H12"}})
        def get(self, url, **kw):
            self.calls.append(("get", url, kw)); return Resp({"values": [["entry_id"], ["7"], ["9"]]})
        def put(self, url, **kw):
            self.calls.append(("put", url, kw)); return Resp({})

    c = GoogleSheetClient.__new__(GoogleSheetClient)
    c.s, c.id, c.tab = Sess(), "SHEET", "Khata"
    assert c.append([1, "x"]) == 12
    assert c.find(9) == 3 and c.find(8) is None
    c.set_status(3, "cancelled")
    post, put = c.s.calls[0], c.s.calls[-1]
    assert post[1].endswith("/SHEET/values/Khata%21A%3AH:append")
    assert post[2]["params"]["valueInputOption"] == "RAW"
    assert put[1].endswith("Khata%21G3") and put[2]["json"] == {"values": [["cancelled"]]}
