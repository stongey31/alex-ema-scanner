"""AlpacaProvider against canned HTTP responses (no network, fake keys)."""

from datetime import datetime

import pytest
import requests

from core.providers import alpaca as alpaca_mod
from core.providers.alpaca import AlpacaProvider
from core.providers.base import ProviderAuthError, ProviderError, ProviderPermissionError
from core.scanner_base import ET


class Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload or {}, text

    def json(self):
        return self._p


def _prov():
    return AlpacaProvider(key="test-key", secret="test-secret")


def test_is_configured():
    assert _prov().is_configured()
    assert not AlpacaProvider(key="", secret="").is_configured()


def test_auth_error_has_no_key_text(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp(401, text="bad test-key"))
    with pytest.raises(ProviderAuthError) as e:
        _prov().snapshots(["AAA"])
    assert "test-key" not in str(e.value) and "test-secret" not in str(e.value)


def test_permission_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp(403, text="subscription does not permit querying recent SIP data"))
    with pytest.raises(ProviderPermissionError):
        _prov().bars(["AAA"], datetime(2026, 9, 1, tzinfo=ET), datetime(2026, 9, 2, tzinfo=ET))


def test_retries_on_429_then_succeeds(monkeypatch):
    calls = []
    seq = [Resp(429), Resp(429), Resp(200, {"snapshots": {}})]
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1) or seq.pop(0))
    monkeypatch.setattr(alpaca_mod.time, "sleep", lambda s: None)
    assert _prov().snapshots(["AAA"]) == {}
    assert len(calls) == 3


def test_429_forever_raises(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp(429))
    monkeypatch.setattr(alpaca_mod.time, "sleep", lambda s: None)
    with pytest.raises(ProviderError):
        _prov().snapshots(["AAA"])


def test_snapshot_parsing_both_shapes(monkeypatch):
    snap = {"latestTrade": {"p": 12.5, "t": "2026-09-30T12:29:00Z"}}
    for payload in ({"snapshots": {"AAA": snap}}, {"AAA": snap}):
        monkeypatch.setattr(requests, "get", lambda *a, p=payload, **k: Resp(200, p))
        out = _prov().snapshots(["AAA"])
        assert out["AAA"]["last_price"] == 12.5 and out["AAA"]["last_trade_time"].year == 2026


def test_bars_pagination_and_timezone(monkeypatch):
    pages = [
        {"bars": {"AAA": [{"t": "2026-09-30T08:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 100}]}, "next_page_token": "tok"},
        {"bars": {"AAA": [{"t": "2026-09-30T08:05:00Z", "o": 2, "h": 3, "l": 2, "c": 3, "v": 200}]}, "next_page_token": None},
    ]
    seen = []
    monkeypatch.setattr(requests, "get", lambda url, params=None, **k: seen.append(dict(params)) or Resp(200, pages.pop(0)))
    out = _prov().bars(["AAA"], datetime(2026, 9, 30, tzinfo=ET), datetime(2026, 10, 1, tzinfo=ET))
    df = out["AAA"]
    assert list(df["volume"]) == [100.0, 200.0]
    assert str(df.index.tz) == "America/New_York" and df.index[0].hour == 4
    assert seen[1]["page_token"] == "tok" and seen[0]["feed"] == "sip"


def test_symbols_are_chunked(monkeypatch):
    seen = []
    monkeypatch.setattr(requests, "get", lambda url, params=None, **k: seen.append(params["symbols"]) or Resp(200, {"snapshots": {}}))
    _prov().snapshots([f"S{i}" for i in range(250)])
    assert [len(s.split(",")) for s in seen] == [100, 100, 50]
