import json

import pytest
import requests

import runner
from core import dedup
from core.scanner_base import RunContext
from scanners.premarket_momentum import PremarketMomentumScanner


@pytest.fixture(autouse=True)
def no_keys_no_network(monkeypatch):
    for k in ("ALPACA_API_KEY", "ALPACA_API_SECRET", "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)

    def boom(*a, **k):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "post", boom)


def test_run_returns_not_configured():
    s = PremarketMomentumScanner()
    out = s.run(["AAA"], s.default_config, RunContext())
    assert out.status == "not_configured"
    assert "ALPACA_API_KEY" in out.message and out.rows == [] and out.signals == []


def test_runner_intraday_exits_0_and_writes_nothing(monkeypatch, tmp_path):
    log_path = tmp_path / "alert_log.json"
    monkeypatch.setattr(dedup, "ALERT_LOG_PATH", log_path)
    assert runner.main(["--lane", "intraday"]) == 0
    assert not log_path.exists()
    log_path.write_text(json.dumps({"x": 1}))
    assert runner.main(["--lane", "intraday"]) == 0
    assert json.loads(log_path.read_text()) == {"x": 1}
