import json

import pytest

import runner
from core import dedup
from core.dedup import is_duplicate
from core.scanner_base import RunContext, ScanOutput, Scanner, Signal


def _legacy_already_alerted(alerted, key, window_start):
    """Verbatim copy of the old runner._already_alerted (reference)."""
    previous_touch = alerted.get(key)
    if not previous_touch or not window_start:
        return False
    return previous_touch >= window_start


@pytest.mark.parametrize("prior", [None, "", "2026-01-01", "2026-01-05", "2026-01-10"])
@pytest.mark.parametrize("start", [None, "", "2026-01-05"])
def test_is_duplicate_truth_table(prior, start):
    log = {} if prior is None else {"K": prior}
    assert is_duplicate(log, "K", start) == _legacy_already_alerted(log, "K", start)
    assert is_duplicate({}, "K", start) is False


class FakeScanner(Scanner):
    id = "fake"
    name = "Fake"
    lane = "daily"
    default_config = {"tickers": ["AAA", "BBB"]}

    def __init__(self):
        self.signals = []

    def run(self, tickers, config, ctx):
        rows = [{"ticker": t, "flagged": True} for t in tickers]
        return ScanOutput("ok", "", rows, list(self.signals))


def sig(ticker, name, episode, start):
    return Signal(ticker, f"{ticker}:{name}", episode, start, {"v": 1})


@pytest.fixture
def env(monkeypatch, tmp_path):
    log_path = tmp_path / "alert_log.json"
    monkeypatch.setattr(dedup, "ALERT_LOG_PATH", log_path)
    scanner = FakeScanner()
    monkeypatch.setattr(runner, "discover_scanners", lambda: [("fake", scanner)])
    posted = []
    state = {"ok": True}

    def fake_post(payload, context=""):
        posted.append(payload)
        return state["ok"]

    monkeypatch.setattr(runner, "post_discord", fake_post)
    return scanner, posted, state, log_path


def read(p):
    return json.loads(p.read_text())


def test_lifecycle(env):
    scanner, posted, state, log_path = env
    scanner.signals = [sig("AAA", "x", "2026-01-05", "2026-01-01")]
    assert runner.main([]) == 0
    assert len(posted) == 1 and read(log_path) == {"fake": {"AAA:x": "2026-01-05"}}

    assert runner.main([]) == 0  # same episode next run: skipped
    assert len(posted) == 1

    scanner.signals = [sig("AAA", "x", "2026-02-10", "2026-02-01")]  # new episode
    runner.main([])
    assert len(posted) == 2 and read(log_path)["fake"]["AAA:x"] == "2026-02-10"


def test_failed_post_is_not_logged(env):
    scanner, posted, state, log_path = env
    state["ok"] = False
    scanner.signals = [sig("AAA", "x", "2026-01-05", "2026-01-01")]
    runner.main([])
    assert len(posted) == 1 and not log_path.exists()
    state["ok"] = True  # retried next run, now succeeds
    runner.main([])
    assert len(posted) == 2 and read(log_path)["fake"]["AAA:x"] == "2026-01-05"


def test_multi_signal_ticker_gets_one_message(env):
    scanner, posted, state, log_path = env
    scanner.signals = [
        sig("AAA", "x", "2026-01-05", "2026-01-01"),
        sig("AAA", "y", "2026-01-04", "2026-01-01"),
        sig("BBB", "x", "2026-01-05", "2026-01-01"),
    ]
    runner.main([])
    assert len(posted) == 2  # one per ticker
    assert [f["name"] for f in posted[0]["embeds"][0]["fields"]] == ["v", "v"]  # both AAA signals
    assert set(read(log_path)["fake"]) == {"AAA:x", "AAA:y", "BBB:x"}


def test_only_new_signals_are_sent_for_a_ticker(env):
    scanner, posted, state, log_path = env
    scanner.signals = [sig("AAA", "x", "2026-01-05", "2026-01-01")]
    runner.main([])
    scanner.signals = [sig("AAA", "x", "2026-01-05", "2026-01-01"), sig("AAA", "y", "2026-01-06", "2026-01-01")]
    runner.main([])
    assert len(posted) == 2 and len(posted[1]["embeds"][0]["fields"]) == 1


def test_log_untouched_when_nothing_sent(env):
    scanner, posted, state, log_path = env
    runner.main([])
    assert not posted and not log_path.exists()
    log_path.write_text('{"fake": {"OLD:x": "2025-01-01"}}\n')
    before = log_path.read_text()
    runner.main([])
    assert log_path.read_text() == before


def test_dry_run_writes_and_posts_nothing(env):
    scanner, posted, state, log_path = env
    scanner.signals = [sig("AAA", "x", "2026-01-05", "2026-01-01")]
    runner.main(["--dry-run"])
    assert not posted and not log_path.exists()


def test_only_and_lane_filters(env):
    scanner, posted, state, log_path = env
    scanner.signals = [sig("AAA", "x", "2026-01-05", "2026-01-01")]
    runner.main(["--lane", "intraday"])
    runner.main(["--only", "other"])
    assert not posted
    runner.main(["--only", "fake"])
    assert len(posted) == 1


def test_et_window_outside_exits_quietly(env, monkeypatch):
    scanner, posted, state, log_path = env
    scanner.signals = [sig("AAA", "x", "2026-01-05", "2026-01-01")]
    monkeypatch.setattr(runner, "_in_window", lambda now, w: False)
    assert runner.main(["--et-window", "07:45-09:29"]) == 0
    assert not posted


def test_in_window_helper():
    from datetime import datetime
    from core.scanner_base import ET

    assert runner._in_window(datetime(2026, 9, 30, 8, 0, tzinfo=ET), "07:45-09:29")
    assert not runner._in_window(datetime(2026, 9, 30, 7, 0, tzinfo=ET), "07:45-09:29")
    assert not runner._in_window(datetime(2026, 9, 30, 9, 30, tzinfo=ET), "07:45-09:29")


def test_scanner_crash_does_not_stop_others(env, monkeypatch):
    scanner, posted, state, log_path = env

    class Boom(FakeScanner):
        id = "boom"

        def run(self, *a):
            raise RuntimeError("x")

    good = FakeScanner()
    good.signals = [sig("AAA", "x", "2026-01-05", "2026-01-01")]
    monkeypatch.setattr(runner, "discover_scanners", lambda: [("boom", Boom()), ("fake", good)])
    assert runner.main([]) == 0
    assert len(posted) == 1
