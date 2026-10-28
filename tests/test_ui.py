"""Browser smoke test. Skipped when Playwright is not installed."""
import os
from pathlib import Path

import pytest

from code_explainer.core import analyze_repo
from code_explainer.report import render_html

sync_api = pytest.importorskip("playwright.sync_api")
FIX = Path(__file__).parent / "fixtures"


def test_report_renders_and_navigates(tmp_path):
    out = tmp_path / "r.html"
    out.write_text(render_html(analyze_repo(FIX / "pyapp", "pyapp")), encoding="utf-8")
    with sync_api.sync_playwright() as p:
        try:
            exe = os.environ.get("CHROMIUM_PATH")
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()
        except Exception as e:  # browser binary missing
            pytest.skip(str(e))
        page = browser.new_page(viewport={"width": 1300, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(out.as_uri())
        page.wait_for_selector(".card.story")  # the tour is the landing page
        assert page.locator("text=The big picture").count()
        page.click("text=Next ▶")
        assert page.locator("text=The floors of the house").count()
        page.select_option("#level", "plain")
        assert page.locator("text=Folder layout").count()
        page.click("[data-tab=overview]")
        assert page.locator("text=Start here").count()
        page.click("[data-tab=flow]")
        page.wait_for_selector(".frow")
        page.click("[data-tab=words]")
        assert page.locator(".words .card").count() > 10
        page.click("[data-tab=treemap]")
        page.wait_for_selector("#tm .tile[data-path]")
        page.click("#tm .tile[data-path]")
        page.wait_for_selector("#detail:not([hidden])")
        page.click("[data-tab=graph]")
        page.wait_for_selector("#gr .node")
        page.fill("#search", "pricing")
        page.wait_for_selector("#results .item")
        page.keyboard.press("Enter")
        assert "pricing.py" in page.evaluate("location.hash")
        assert errors == []
        browser.close()


def test_changes_tab(repo, tmp_path):
    out = tmp_path / "c.html"
    out.write_text(render_html(analyze_repo(repo, "r", base="HEAD~1", head="HEAD")), encoding="utf-8")
    with sync_api.sync_playwright() as p:
        try:
            exe = os.environ.get("CHROMIUM_PATH")
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()
        except Exception as e:
            pytest.skip(str(e))
        page = browser.new_page(viewport={"width": 1300, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(out.as_uri())
        page.click("[data-tab=changes]")
        page.wait_for_selector(".card.chg")
        assert page.locator("text=Double-check these").count()
        page.click("text=Show the changes >> nth=0")
        assert page.locator(".patch .add").count() > 0
        page.click("[data-tab=treemap]")
        assert page.locator("select option[value=changed]").count() == 1
        assert errors == []
        browser.close()
