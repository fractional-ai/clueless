"""Tests for the custom-tool wiring in clueless.py.

Fully offline by default: build_catalog_argv() and run_custom_tool() are pure
enough to test without a real dataset or a real display module. The only test
that actually shells out to scripts/clueless-data is skipped when
data/clueless.db doesn't exist (this worktree has no data/ — see README).
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import clueless  # noqa: E402


DB_PATH = REPO_ROOT / "data" / "clueless.db"
requires_db = pytest.mark.skipif(
    not DB_PATH.exists(), reason="data/clueless.db not present in this worktree"
)


# ------------------------------------------------------------- build_catalog_argv

def test_argv_search():
    argv = clueless.build_catalog_argv({"command": "search", "query": "denim jacket"})
    assert argv[0] == sys.executable
    assert argv[1:] == [str(clueless.SCRIPTS_DIR / "clueless-data"), "search", "denim jacket"]


def test_argv_search_with_category_and_limit():
    argv = clueless.build_catalog_argv(
        {"command": "search", "query": "boots", "category": "shoes", "limit": 5}
    )
    assert argv[2:] == ["search", "boots", "--category", "shoes", "--limit", "5"]


def test_argv_item():
    argv = clueless.build_catalog_argv({"command": "item", "item_id": "201813350"})
    assert argv[2:] == ["item", "201813350"]


def test_argv_outfit():
    argv = clueless.build_catalog_argv({"command": "outfit", "set_id": "210750761"})
    assert argv[2:] == ["outfit", "210750761"]


def test_argv_pairs_with():
    argv = clueless.build_catalog_argv({"command": "pairs-with", "item_id": "201813350"})
    assert argv[2:] == ["pairs-with", "201813350"]


def test_argv_pairs_with_category_and_limit():
    argv = clueless.build_catalog_argv(
        {"command": "pairs-with", "item_id": "1", "category": "bags", "limit": 3}
    )
    assert argv[2:] == ["pairs-with", "1", "--category", "bags", "--limit", "3"]


def test_argv_random_no_args():
    argv = clueless.build_catalog_argv({"command": "random"})
    assert argv[2:] == ["random"]


def test_argv_random_with_category():
    argv = clueless.build_catalog_argv({"command": "random", "category": "tops"})
    assert argv[2:] == ["random", "--category", "tops"]


def test_argv_stats():
    argv = clueless.build_catalog_argv({"command": "stats"})
    assert argv[2:] == ["stats"]


def test_argv_schema():
    argv = clueless.build_catalog_argv({"command": "schema"})
    assert argv[2:] == ["schema"]


def test_argv_sql():
    argv = clueless.build_catalog_argv({"command": "sql", "query": "SELECT 1"})
    assert argv[2:] == ["sql", "SELECT 1"]


def test_argv_ignores_limit_zero_is_still_passed():
    # limit=0 is a legitimate (if odd) value — must not be dropped like a falsy string.
    argv = clueless.build_catalog_argv({"command": "random", "limit": 0})
    assert argv[2:] == ["random", "--limit", "0"]


# ------------------------------------------------------------- missing required fields

def test_missing_command_raises():
    with pytest.raises(ValueError, match="command"):
        clueless.build_catalog_argv({})


@pytest.mark.parametrize(
    "tool_input,missing_field",
    [
        ({"command": "search"}, "query"),
        ({"command": "item"}, "item_id"),
        ({"command": "outfit"}, "set_id"),
        ({"command": "pairs-with"}, "item_id"),
        ({"command": "sql"}, "query"),
    ],
)
def test_missing_required_field_raises(tool_input, missing_field):
    with pytest.raises(ValueError, match=missing_field):
        clueless.build_catalog_argv(tool_input)


def test_run_custom_tool_missing_field_is_reported_as_error():
    text, is_error = clueless.run_custom_tool("query_catalog", {"command": "item"}, True)
    assert is_error is True
    assert "item_id" in text
    assert "item" in text


# ------------------------------------------------------------- run_custom_tool: query_catalog

def test_run_custom_tool_query_catalog_success(monkeypatch):
    def fake_run(argv, capture_output, text, timeout):
        assert argv[2] == "stats"
        return subprocess.CompletedProcess(argv, 0, stdout='{"items": 1}\n', stderr="")

    monkeypatch.setattr(clueless.subprocess, "run", fake_run)
    text, is_error = clueless.run_custom_tool("query_catalog", {"command": "stats"}, True)
    assert is_error is False
    assert text == '{"items": 1}\n'


def test_run_custom_tool_query_catalog_nonzero_exit_uses_stderr(monkeypatch):
    def fake_run(argv, capture_output, text, timeout):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom\n")

    monkeypatch.setattr(clueless.subprocess, "run", fake_run)
    text, is_error = clueless.run_custom_tool("query_catalog", {"command": "stats"}, True)
    assert is_error is True
    assert text == "boom"


def test_run_custom_tool_query_catalog_nonzero_exit_no_stderr_falls_back(monkeypatch):
    def fake_run(argv, capture_output, text, timeout):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

    monkeypatch.setattr(clueless.subprocess, "run", fake_run)
    text, is_error = clueless.run_custom_tool("query_catalog", {"command": "stats"}, True)
    assert is_error is True
    assert text == "query failed"


def test_run_custom_tool_query_catalog_timeout(monkeypatch):
    def fake_run(argv, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr(clueless.subprocess, "run", fake_run)
    text, is_error = clueless.run_custom_tool("query_catalog", {"command": "stats"}, True)
    assert is_error is True
    assert "timed out" in text


def test_run_custom_tool_truncates_long_stdout(monkeypatch):
    huge = "x" * 40000

    def fake_run(argv, capture_output, text, timeout):
        return subprocess.CompletedProcess(argv, 0, stdout=huge, stderr="")

    monkeypatch.setattr(clueless.subprocess, "run", fake_run)
    text, is_error = clueless.run_custom_tool("query_catalog", {"command": "stats"}, True)
    assert is_error is False
    assert len(text) == clueless.CATALOG_TRUNCATE_AT + len(clueless.CATALOG_TRUNCATE_SUFFIX)
    assert text.endswith(clueless.CATALOG_TRUNCATE_SUFFIX)


def test_run_custom_tool_short_stdout_not_truncated(monkeypatch):
    def fake_run(argv, capture_output, text, timeout):
        return subprocess.CompletedProcess(argv, 0, stdout="short", stderr="")

    monkeypatch.setattr(clueless.subprocess, "run", fake_run)
    text, is_error = clueless.run_custom_tool("query_catalog", {"command": "stats"}, True)
    assert text == "short"
    assert is_error is False


# ------------------------------------------------------------- run_custom_tool: present_picks

def test_run_custom_tool_present_picks_no_display_module(monkeypatch):
    monkeypatch.setattr(clueless, "write_picks", None)
    text, is_error = clueless.run_custom_tool(
        "present_picks", {"headline": "h", "sections": []}, True
    )
    assert is_error is False
    assert text == clueless.NO_DISPLAY_MESSAGE


def test_run_custom_tool_present_picks_success(monkeypatch):
    monkeypatch.setattr(
        clueless, "write_picks", lambda picks, enabled: "wrote app/src/picks.json"
    )
    text, is_error = clueless.run_custom_tool(
        "present_picks", {"headline": "h", "sections": []}, True
    )
    assert text == "wrote app/src/picks.json"
    assert is_error is False


def test_run_custom_tool_present_picks_passes_enabled_flag(monkeypatch):
    seen = {}

    def fake_write_picks(picks, enabled):
        seen["enabled"] = enabled
        return "ok"

    monkeypatch.setattr(clueless, "write_picks", fake_write_picks)
    clueless.run_custom_tool("present_picks", {"headline": "h", "sections": []}, False)
    assert seen["enabled"] is False


def test_run_custom_tool_present_picks_value_error_is_reported():
    def boom(picks, enabled):
        raise ValueError("bad shape: sections missing heading")

    import clueless as clueless_mod

    original = clueless_mod.write_picks
    clueless_mod.write_picks = boom
    try:
        text, is_error = clueless.run_custom_tool(
            "present_picks", {"headline": "h", "sections": []}, True
        )
    finally:
        clueless_mod.write_picks = original

    assert is_error is True
    assert text == "bad shape: sections missing heading"


# ------------------------------------------------------------- unknown tool

def test_run_custom_tool_unknown_tool():
    text, is_error = clueless.run_custom_tool("some_other_tool", {}, True)
    assert is_error is True
    assert "some_other_tool" in text


# ------------------------------------------------------------- live DB (skipped if absent)

@requires_db
def test_query_catalog_actually_runs_against_real_db():
    text, is_error = clueless.run_custom_tool("query_catalog", {"command": "stats"}, True)
    assert is_error is False
    assert "items" in text
