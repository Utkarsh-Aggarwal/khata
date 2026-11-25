"""Tests for `khata/export.py`: the xlsx workbook shared by `/api/export.xlsx` and Telegram `/export`."""
import io
from datetime import date

from hypothesis import given, settings, strategies as st
from openpyxl import load_workbook

from khata.export import HEADER, build_xlsx
from khata.flow import MAX_ENTRY_PAISE
from khata.ledger import Ledger

OWNER = "tg-0123456789abcdef01234567"
OTHER = "tg-fedcba9876543210fedcba98"

# Latin, Devanagari and Gurmukhi names, including Hinglish-style spellings.
NAMES = ["Ramesh", "Sunita Devi", "Mohan Lal", "रमेश", "सुनीता", "मोहन लाल",
         "ਗੁਰਪ੍ਰੀਤ", "ਹਰਜੀਤ ਸਿੰਘ", "ਸਿਮਰਨ"]

dates = st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31)).map(date.isoformat)

add_op = st.tuples(
    st.just("add"),
    st.sampled_from([OWNER, OTHER]),
    st.sampled_from(NAMES),
    st.sampled_from(["credit", "payment"]),
    st.integers(min_value=1, max_value=MAX_ENTRY_PAISE),
    dates,
    st.none() | dates,
)
undo_op = st.tuples(st.just("undo"), st.sampled_from([OWNER, OTHER]))


# Feature: telegram-channel, Property 11: Export round trip
# **Validates: Requirements 9.1, 9.5, 10.8**
@settings(max_examples=100, deadline=None)
@given(st.lists(st.one_of(add_op, undo_op), max_size=25))
def test_export_round_trip(ops):
    lg = Ledger()
    expected: dict[str, dict[int, dict]] = {OWNER: {}, OTHER: {}}
    for op in ops:
        if op[0] == "add":
            _, owner, name, kind, paise, entry_date, due = op
            eid = lg.add_entry(owner, name, kind, paise, entry_date=entry_date, due_date=due)
            expected[owner][eid] = {"customer": lg.find_customer(owner, name), "kind": kind,
                                    "paise": paise, "date": entry_date, "due": due,
                                    "status": "active"}
        else:
            owner = op[1]
            active = [i for i, e in expected[owner].items() if e["status"] == "active"]
            undone = lg.undo_last(owner)
            if active:
                assert undone is not None and undone.id == max(active)
                expected[owner][max(active)]["status"] = "cancelled"
            else:
                assert undone is None

    data = build_xlsx(lg, OWNER)
    assert data[:2] == b"PK"

    ws = load_workbook(io.BytesIO(data)).active
    assert ws.title == "Khata"
    rows = list(ws.iter_rows(values_only=True))
    assert list(rows[0]) == HEADER
    body = rows[1:]

    mine = expected[OWNER]
    C = {c: i for i, c in enumerate(HEADER)}
    ids = [int(r[C["Ref"]][2:]) for r in body]
    assert sorted(ids) == sorted(mine)                    # one row per entry of this owner only
    assert [r[C["Date"]] for r in body] == sorted(r[C["Date"]] for r in body)   # sorted by date
    other_ids = set(expected[OTHER])
    for r in body:
        eid = int(r[C["Ref"]][2:])
        assert r[C["Ref"]].startswith("K-") and (eid not in other_ids or eid in mine)
        e = mine[eid]
        assert r[C["Name"]] == e["customer"]
        assert r[C["Type"]] == ("Udhaar given" if e["kind"] == "credit" else "Payment received")
        amount = r[C["Out (₹)"]] if e["kind"] == "credit" else r[C["In (₹)"]]
        assert isinstance(e["paise"], int) and round(amount * 100) == e["paise"]
        assert r[C["Date"]] == e["date"]
        assert (r[C["Due"]] or None) == e["due"]
        assert r[C["Status"]] == e["status"]


def _rows(data: bytes) -> list[tuple]:
    ws = load_workbook(io.BytesIO(data)).active
    assert ws.title == "Khata"
    return list(ws.iter_rows(values_only=True))


def test_api_export_serves_build_xlsx_for_demo_login():
    """Requirement 9.2: `/api/export.xlsx` serves the Export_Builder workbook for the logged-in owner."""
    from fastapi.testclient import TestClient

    from khata.app import create_app
    from khata.guard import PaidGuard

    app = create_app(":memory:", PaidGuard(key="", budget_calls=0, killed=False))
    c = TestClient(app)
    assert c.get("/api/export.xlsx").status_code == 401           # no login, no export

    assert c.post("/api/demo-login").status_code == 200           # seeded demo khata
    sid = c.cookies.get("sid")
    lg = app.state.ledger
    entries = lg.entries(sid, include_cancelled=True)
    assert entries                                                 # the seed wrote something

    # A second demo owner whose rows must not leak into the first owner's export.
    other = TestClient(app)
    other.post("/api/demo-login?seed=false")
    lg.add_entry(other.cookies.get("sid"), "Stranger", "credit", 12345)

    r = c.get("/api/export.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"] == \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert r.headers["content-disposition"] == "attachment; filename=khata.xlsx"
    assert r.content[:2] == b"PK"

    # Same workbook as the builder (compared by content: xlsx bytes embed a timestamp).
    rows = _rows(r.content)
    assert rows == _rows(build_xlsx(lg, sid))
    assert list(rows[0]) == HEADER
    assert sorted(row[0] for row in rows[1:]) == sorted(f"K-{e.id}" for e in entries)
    assert "Stranger" not in {row[3] for row in rows[1:]}
    assert load_workbook(io.BytesIO(r.content)).sheetnames == ["Khata", "Summary"]
