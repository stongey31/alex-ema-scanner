"""Drop a new file in scanners/ and it appears everywhere, with no other edits."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

LABELS_SCRIPT = (
    "from streamlit.testing.v1 import AppTest;"
    "at=AppTest.from_file('app.py',default_timeout=60).run();"
    "print('EXC' if at.exception else 'NOEXC');"
    "print('LABELS|'+'|'.join(t.label for t in at.tabs))"
)


@pytest.fixture
def copy(tmp_path):
    dst = tmp_path / "proj"
    shutil.copytree(
        ROOT, dst,
        ignore=shutil.ignore_patterns(".venv", ".git", "worktrees", "__pycache__", ".pytest_cache"),
    )
    return dst


def sh(cwd, *args):
    return subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def test_template_copied_unchanged_appears_in_cli_and_dashboard(copy):
    src = (copy / "scanners" / "_template.py").read_text()
    assert 'name = "20-Day Breakout"' in src
    (copy / "scanners" / "dummy_breakout.py").write_text(src.replace('name = "20-Day Breakout"', 'name = "Dummy Breakout"'))

    listed = sh(copy, "runner.py", "--list")
    assert listed.returncode == 0
    assert "dummy_breakout\tdaily\tDummy Breakout" in listed.stdout
    assert "_template" not in listed.stdout

    app = sh(copy, "-c", LABELS_SCRIPT)
    assert "NOEXC" in app.stdout, app.stderr[-2000:]
    labels_line = next(l for l in app.stdout.splitlines() if l.startswith("LABELS|"))
    assert "Dummy Breakout" in labels_line and "Moving Average Bounce" in labels_line


def test_syntax_error_file_becomes_warning_tab(copy):
    (copy / "scanners" / "oops.py").write_text("def broken(:\n    pass\n")
    listed = sh(copy, "runner.py", "--list")
    assert listed.returncode == 0 and "oops\tBROKEN" in listed.stdout
    assert "ma_bounce\tdaily" in listed.stdout

    app = sh(copy, "-c", LABELS_SCRIPT)
    assert "NOEXC" in app.stdout, app.stderr[-2000:]
    labels_line = next(l for l in app.stdout.splitlines() if l.startswith("LABELS|"))
    assert "⚠ oops" in labels_line and "Moving Average Bounce" in labels_line


def test_bad_json_config_shows_warning_tab(copy):
    (copy / "data" / "config" / "ma_bounce.json").write_text('{"enabled": true,\n "watchlist": "mega_caps",\n}')
    app = sh(copy, "-c", LABELS_SCRIPT)
    assert "NOEXC" in app.stdout, app.stderr[-2000:]
    assert "⚠ ma_bounce" in app.stdout
