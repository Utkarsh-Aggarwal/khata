"""Mirror ledger entries to a Google Sheet. SQLite stays the source of truth.

sync() diffs the ledger against what the sheet has seen (table sheet_sync), so failures
are retried on the next run with backoff. Appends are idempotent: before re-trying an
append, the entry id is looked up in column A so a lost response can't duplicate a row.
Undo flips the row's status cell to 'cancelled'; rows are never deleted.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Protocol

from .ledger import Ledger

HEADER = ["entry_id", "date", "customer", "kind", "rupees", "due", "status", "source"]
STATUS_COL = "G"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sheet_sync(
  entry_id INTEGER PRIMARY KEY, owner TEXT NOT NULL, row INTEGER, synced_status TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0, last_error TEXT);
"""


class SheetClient(Protocol):
    def append(self, row: list) -> int: ...              # returns 1-based row number
    def find(self, entry_id: int) -> int | None: ...
    def set_status(self, row: int, status: str) -> None: ...


class GoogleSheetClient:
    """Sheets API v4 via a service account (share the sheet with the service account email)."""
    API = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(self, spreadsheet_id: str, creds_file: str, tab: str = "Khata"):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            creds_file, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.s = AuthorizedSession(creds)
        self.id, self.tab = spreadsheet_id, tab

    def _url(self, rng: str, suffix: str = "") -> str:
        from urllib.parse import quote
        return f"{self.API}/{self.id}/values/{quote(f'{self.tab}!{rng}')}{suffix}"

    def append(self, row):
        r = self.s.post(self._url("A:H", ":append"), timeout=20,
                        params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                        json={"values": [row]})
        r.raise_for_status()
        rng = r.json()["updates"]["updatedRange"]           # e.g. Khata!A12:H12
        return int(rng.split("!")[1].split(":")[0].lstrip("ABCDEFGH"))

    def find(self, entry_id):
        r = self.s.get(self._url("A:A"), timeout=20)
        r.raise_for_status()
        for i, v in enumerate(r.json().get("values", []), start=1):
            if v and str(v[0]) == str(entry_id):
                return i
        return None

    def set_status(self, row, status):
        r = self.s.put(self._url(f"{STATUS_COL}{row}"), params={"valueInputOption": "RAW"},
                       json={"values": [[status]]}, timeout=20)
        r.raise_for_status()


class SheetMirror:
    def __init__(self, ledger: Ledger, client: SheetClient, owner: str, clock=time.time):
        self.ledger, self.client, self.owner, self.clock = ledger, client, owner, clock
        with ledger.lock:
            ledger.db.executescript(SCHEMA)

    def _state(self) -> dict[int, dict]:
        rows = self.ledger.db.execute("SELECT * FROM sheet_sync WHERE owner=?", (self.owner,))
        return {r["entry_id"]: dict(r) for r in rows}

    def pending(self) -> int:
        st = self._state()
        return sum(1 for e in self.ledger.entries(self.owner, include_cancelled=True)
                   if st.get(e.id, {}).get("synced_status") != e.status)

    def sync(self) -> dict:
        done = failed = 0
        now = self.clock()
        with self.ledger.lock:
            st = self._state()
            todo = [e for e in self.ledger.entries(self.owner, include_cancelled=True)
                    if st.get(e.id, {}).get("synced_status") != e.status
                    and st.get(e.id, {}).get("next_at", 0) <= now]
        for e in todo:
            s = st.get(e.id, {})
            try:
                row = s.get("row")
                if row is None and s.get("attempts", 0) > 0:
                    row = self.client.find(e.id)            # did a previous append land?
                if row is None:
                    row = self.client.append([e.id, e.entry_date, e.customer, e.kind, e.paise / 100,
                                              e.due_date or "", e.status, ""])
                elif e.status != s.get("synced_status"):
                    self.client.set_status(row, e.status)
                self._save(e.id, row, e.status, 0, 0, None)
                done += 1
            except Exception as ex:                          # retry later with backoff
                n = s.get("attempts", 0) + 1
                self._save(e.id, s.get("row"), s.get("synced_status"), n,
                           now + min(300, 2 ** n), type(ex).__name__)
                failed += 1
        return {"synced": done, "failed": failed, "pending": self.pending()}

    def _save(self, entry_id, row, status, attempts, next_at, err):
        with self.ledger.lock:
            self.ledger.db.execute(
                "INSERT INTO sheet_sync(entry_id,owner,row,synced_status,attempts,next_at,last_error) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET row=excluded.row, "
                "synced_status=excluded.synced_status, attempts=excluded.attempts, "
                "next_at=excluded.next_at, last_error=excluded.last_error",
                (entry_id, self.owner, row, status, attempts, next_at, err))
            self.ledger.db.commit()

    def start(self, every: float = 15.0) -> threading.Thread:
        def loop():
            while True:
                try:
                    self.sync()
                except Exception:
                    pass
                time.sleep(every)
        t = threading.Thread(target=loop, daemon=True, name="sheet-mirror")
        t.start()
        return t


def from_env(ledger: Ledger, owner: str | None) -> SheetMirror | None:
    sid, creds = os.environ.get("KHATA_SHEET_ID"), os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not (sid and creds and owner):
        return None
    return SheetMirror(ledger, GoogleSheetClient(sid, creds), owner)
