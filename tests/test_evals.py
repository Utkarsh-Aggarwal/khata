"""Regression guard: sets we have tuned on must stay at 100%. The blind set is reported, not gated."""
from evals.run import SETS, run


def test_golden_and_dev_sets_do_not_regress():
    for name in ("golden", "dev2"):
        ok, n, fails, _ = run(SETS[name])
        assert ok == n, f"{name}: {fails}"
