"""Paid-API guard: one place that decides whether voice/vision may call a paid provider.

Off when no key is set, when KHATA_KILL=1, or when the call budget is spent. When off,
adapters fall back to demo mode (ask the user to type) instead of failing.
"""
from __future__ import annotations

import os
import threading


class PaidGuard:
    def __init__(self, key: str | None = None, budget_calls: int | None = None, killed: bool | None = None):
        self.key = key if key is not None else os.environ.get("KHATA_PAID_KEY", "")
        self.budget = budget_calls if budget_calls is not None else int(os.environ.get("KHATA_BUDGET_CALLS", "500"))
        self.killed = killed if killed is not None else os.environ.get("KHATA_KILL") == "1"
        self.used = 0
        self._lock = threading.Lock()

    @property
    def on(self) -> bool:
        return bool(self.key) and not self.killed and self.used < self.budget

    def take(self) -> bool:
        """Reserve one paid call. Returns False if paid APIs are off or the budget is spent."""
        with self._lock:
            if not self.on:
                return False
            self.used += 1
            return True

    def status(self) -> dict:
        return {"paid_apis": self.on, "budget_left": max(0, self.budget - self.used) if self.key else 0,
                "killed": self.killed}
