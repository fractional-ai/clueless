"""Playwright smoke test for the Cher's Closet UI (app.py).

Drives the real Gradio app in a browser, but forces the "agent not provisioned"
branch so it makes NO calls to the managed agent — no API key, no network, fully
deterministic. It checks two things:
  1. the page renders (title, persona picker, the key buttons), and
  2. clicking "Meet my stylist" runs the start() handler through Gradio and shows
     the graceful not-provisioned notice.

One-time setup:
    pip install -r requirements.txt
    playwright install chromium

Run:
    pytest tests/test_ui_smoke.py
"""

import pytest

# Skip cleanly if the playwright package isn't installed.
pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

import app  # noqa: E402


@pytest.fixture(scope="module")
def server_url():
    """Launch the Gradio app on a free port for the duration of the module."""
    app.demo.launch(prevent_thread_lock=True, show_error=True)
    try:
        yield app.demo.local_url
    finally:
        app.demo.close()


@pytest.fixture
def page(server_url):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser binary not installed
            pytest.skip(f"Chromium unavailable — run `playwright install chromium` ({exc})")
        pg = browser.new_page()
        pg.goto(server_url)
        try:
            yield pg
        finally:
            browser.close()


def test_page_renders(page):
    expect(page.locator("h1")).to_contain_text("Cher's Closet")
    expect(page.get_by_text("Who am I dressing?")).to_be_visible()
    expect(page.get_by_role("button", name="Meet my stylist")).to_be_visible()
    expect(page.get_by_role("button", name="Send rating")).to_be_visible()


def test_start_without_agent_shows_notice(page, monkeypatch):
    # Force the not-provisioned branch so the handler never touches the API,
    # even on a machine where the agent IS set up.
    monkeypatch.setattr(app.clueless, "ids_present", lambda: False)

    page.get_by_role("button", name="Meet my stylist").click()

    expect(page.get_by_text("isn't provisioned")).to_be_visible(timeout=20_000)
