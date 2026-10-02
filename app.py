"""
app.py -- Streamlit dashboard for all scanners.

Browsing only: settings changed here are session-only (NOT saved) and never
affect the scheduled alerts. Alerts are sent only by the scheduled GitHub
jobs (runner.py). This page makes NO network calls until you click a Run
button, and never posts to Discord. To permanently change a scanner, edit
files under data/ on GitHub (see README.md).
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from core.config import ConfigError, load_scanner_config, load_watchlists, resolve_tickers
from core.providers.alpaca import NOT_CONFIGURED_MSG, AlpacaProvider
from core.providers.fake import make_demo_provider, make_demo_universe_provider
from core.registry import BrokenScanner, discover_scanners
from core.scanner_base import ET, RunContext, ScanOutput
from core.secrets import has_secret
from core.ui import settings_form

st.set_page_config(page_title="Alex's Scanners", layout="wide")


def _show_results(scanner, out: ScanOutput, cfg: dict, fake: bool = False) -> None:
    if fake:
        st.warning("FAKE DATA -- this is a demo with made-up numbers, not real market data.", icon="🚨")
    if out.status == "not_configured":
        st.info(out.message)
        return
    if out.status == "error":
        st.error(out.message)
        return
    if out.message:
        if out.message.startswith("Swept") and "skipped" not in out.message and "IEX-only" not in out.message:
            st.info(out.message)
        else:
            st.warning(out.message)
    flagged_n = sum(1 for r in out.rows if r.get("flagged"))
    swept = (out.extra or {}).get("swept")
    if swept is not None:
        c1, c2, c3 = st.columns(3)
        c1.metric("Symbols swept", f"{swept:,}")
        c2.metric("Gappers checked for volume", len(out.rows))
        c3.metric("Flagged", flagged_n)
    else:
        c1, c2 = st.columns(2)
        c1.metric("Total Scanned", len(out.rows))
        c2.metric("Flagged", flagged_n)

    df = scanner.table(out)
    if len(df) == len(out.rows):
        flags = [bool(r.get("flagged")) for r in out.rows]
    else:
        flags = [False] * len(df)

    def highlight(row):
        return ["background-color: #1e5631; color: white" if flags[row.name] else "" for _ in row]

    fmt = {k: v for k, v in scanner.column_formats.items() if k in df.columns}
    styled = df.style.apply(highlight, axis=1).format(fmt, na_rep="N/A")
    st.dataframe(styled, width="stretch", hide_index=True)
    scanner.render_detail(out, cfg)


def _run(scanner, tickers, cfg, ctx) -> ScanOutput:
    whole = cfg.get("universe_mode") == "whole_market"
    with st.spinner("Sweeping the whole market..." if whole else f"Scanning {len(tickers)} ticker(s)..."):
        try:
            return scanner.run(tickers, cfg, ctx)
        except Exception as e:  # keep the page alive
            return ScanOutput("error", f"{type(e).__name__}: {e}")


def render_tab(scanner, base_cfg: dict, watchlists: dict) -> None:
    sid = scanner.id
    st.write(scanner.description)
    st.badge("Runs daily after the close" if scanner.lane == "daily" else "Intraday / pre-market", color="blue")

    with st.expander("Settings (this session only - not saved)"):
        cfg = settings_form(scanner, base_cfg, key_prefix=sid)
    whole = cfg.get("universe_mode") == "whole_market"
    if whole:
        st.caption("Scanning whole market (~12.7k symbols): gappers first, then a volume check on the survivors.")
        tickers = []
    else:
        default_tickers = resolve_tickers(base_cfg, watchlists)
        raw = st.text_input("Tickers (comma-separated, this session only)", value=", ".join(default_tickers), key=f"tickers_{sid}")
        tickers = list(dict.fromkeys(t.strip().upper() for t in raw.split(",") if t.strip()))

    intraday = scanner.lane == "intraday"
    demo = False
    auto = False
    if intraday:
        if not AlpacaProvider().is_configured():
            st.info(NOT_CONFIGURED_MSG)
            demo = st.checkbox("Show demo with FAKE data", key=f"demo_{sid}")
        auto = st.toggle(
            "Auto-refresh while this page is open", value=False, key=f"auto_{sid}",
            help="Off by default. In whole-market mode each refresh costs ~26 API calls (the free plan allows 200 per minute).",
        )

    def make_ctx():
        if demo:
            now = datetime.now(ET).replace(hour=8, minute=30, second=0, microsecond=0)
            prov = make_demo_universe_provider(now) if whole else make_demo_provider(tickers, now)
            return RunContext(intraday_provider=prov, now=now)
        return RunContext()

    def execute():
        if not whole and not tickers:
            st.error("Add at least one ticker before scanning.")
            return
        st.session_state[f"out_{sid}"] = _run(scanner, tickers, cfg, make_ctx())
        st.session_state[f"fake_{sid}"] = demo

    clicked = st.button("Run", type="primary", key=f"run_{sid}", width="stretch")
    if clicked:
        execute()

    def panel():
        out = st.session_state.get(f"out_{sid}")
        if out is None:
            st.info("Set your tickers and settings, then click **Run**.")
            return
        _show_results(scanner, out, cfg, fake=st.session_state.get(f"fake_{sid}", False))

    if intraday and auto:
        @st.fragment(run_every=int(cfg.get("auto_refresh_seconds", 60)))
        def live_panel():
            execute()
            panel()

        live_panel()
    else:
        panel()


def main() -> None:
    st.title("Alex's Scanners")

    with st.sidebar:
        st.header("Status")
        st.write("Discord webhook configured: " + ("✅ yes" if has_secret("DISCORD_WEBHOOK_URL") else "❌ no"))
        st.write(
            "Alpaca keys configured: "
            + ("✅ yes" if has_secret("ALPACA_API_KEY") and has_secret("ALPACA_API_SECRET") else "❌ no")
        )
        st.caption(
            "Alerts are sent **only** by the scheduled GitHub jobs -- never from this dashboard. "
            "Settings changed here are for browsing only and are not saved."
        )

    tabs_spec = []  # (label, scanner|None, cfg|None, error|None)
    try:
        watchlists = load_watchlists()
    except ConfigError as e:
        watchlists = {}
        tabs_spec.append(("⚠ watchlists", None, None, str(e)))

    for sid, obj in discover_scanners():
        if isinstance(obj, BrokenScanner):
            tabs_spec.append((f"⚠ {sid}", None, None, obj.error))
            continue
        try:
            cfg = load_scanner_config(obj)
            if not cfg.get("enabled", True):
                continue
            resolve_tickers(cfg, watchlists)
        except ConfigError as e:
            tabs_spec.append((f"⚠ {sid}", None, None, str(e)))
            continue
        tabs_spec.append((obj.name, obj, cfg, None))

    if not tabs_spec:
        st.info("No scanners are enabled.")
        return
    tabs = st.tabs([label for label, *_ in tabs_spec])
    for tab, (label, scanner, cfg, err) in zip(tabs, tabs_spec):
        with tab:
            if err:
                st.error(f"Could not load this scanner: {err}")
            else:
                render_tab(scanner, cfg, watchlists)


main()
