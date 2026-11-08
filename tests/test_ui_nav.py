"""Navigation: a working Back button, tabs inside the file panel, and a bar that jumps to the same thing in other views."""
import pytest

from code_explainer.core import analyze_repo
from conftest import write
from test_ui_ai_static import page_for  # noqa: F401  (shared fixture: opens a static report in a browser)

sync_api = pytest.importorskip("playwright.sync_api")

FILES = {
    "shop/__init__.py": "",
    "shop/cli.py": 'from shop.api import routes\n\n\ndef main():\n    return routes.home()\n\n\nif __name__ == "__main__":\n    main()\n',
    "shop/api/__init__.py": "",
    "shop/api/routes.py": '"""Web handlers for the shop."""\nfrom flask import Flask\nfrom shop.core import pricing\nfrom shop.core import cart\n\napp = Flask(__name__)\n\n\ndef home():\n    return pricing.total(cart.items())\n',
    "shop/api/risky.py": "def run(s):\n    if s:\n        return eval(s)\n    return None\n",
    "shop/core/__init__.py": "",
    "shop/core/pricing.py": '"""Price maths."""\nfrom shop.db import store\n\n\ndef total(items):\n    return store.load() + len(items)\n',
    "shop/core/cart.py": "from shop.db import store\n\n\ndef items():\n    return store.load()\n",
    "shop/db/__init__.py": "",
    "shop/db/store.py": '"""Saves and loads things."""\nimport sqlite3\n\n\ndef load():\n    return sqlite3.connect("x").execute("select 1")\n',
    "tests/test_cart.py": "from shop.core import cart\n\n\ndef test_items():\n    assert cart.items() is not None\n",
}


@pytest.fixture
def proj(tmp_path):
    write(tmp_path, FILES)
    return tmp_path


def open_file(page_for, proj, path, extra="", tab="overview", **kw):  # noqa: F811
    page, errors = page_for(analyze_repo(proj, "shop", **kw), f"#tab={tab}&f={path}{extra}")
    page.wait_for_selector("#detail:not([hidden]) [role=tablist]")
    return page, errors


def tab(page, name):
    return page.locator(f'#detail [role=tab][data-ptab="{name}"]')


def panel(page, name):
    return page.locator(f'#detail [role=tabpanel][data-panel="{name}"]')


def lens(page, name):
    return page.locator(f'#detail .lens-btn[data-lens="{name}"]')


# ----------------------------------------------------------------- tabs inside the file panel
def test_the_file_panel_has_four_tabs_and_starts_on_the_summary(proj, page_for):
    page, errors = open_file(page_for, proj, "shop/api/routes.py")
    names = page.locator("#detail [role=tab]").evaluate_all("els => els.map(e => e.dataset.ptab)")
    assert names == ["summary", "code", "links", "quality"]
    assert tab(page, "summary").get_attribute("aria-selected") == "true"
    assert panel(page, "summary").is_visible() and not panel(page, "code").is_visible()
    assert errors == []


def test_the_summary_lists_the_functions_and_explains_the_file(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/routes.py")
    assert panel(page, "summary").locator(".symwrap", has_text="home").count() == 1
    assert "Web handlers" in panel(page, "summary").inner_text()
    assert panel(page, "summary").locator(".metrics").count() == 1


def test_the_code_is_one_click_away_and_visible_without_scrolling(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/routes.py")
    tab(page, "code").click()
    code = panel(page, "code").locator(".code")
    code.wait_for()
    box, detail = code.bounding_box(), page.locator("#detail").bounding_box()
    assert detail["y"] <= box["y"] and box["y"] + 40 <= detail["y"] + detail["height"]       # starts inside the visible panel
    assert "def home" in code.inner_text()


def test_clicking_a_function_in_the_summary_opens_the_code_at_that_line(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/routes.py")
    panel(page, "summary").locator(".symwrap", has_text="home").locator(".sym").click()
    assert tab(page, "code").get_attribute("aria-selected") == "true"
    assert "line=9" in page.evaluate("location.hash")
    assert page.locator("#detail .code .hl").count() == 1


def test_opening_a_line_goes_straight_to_the_code_tab(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/routes.py", "&line=9")
    assert tab(page, "code").get_attribute("aria-selected") == "true" and page.locator("#detail .code .hl").count() == 1


def test_the_chosen_tab_is_in_the_address_and_remembered_for_other_files(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/routes.py")
    tab(page, "links").click()
    assert "pt=links" in page.evaluate("location.hash")
    page.evaluate("location.hash = '#tab=overview&f=shop/core/cart.py'")
    page.wait_for_function("document.querySelector('#detail .t').textContent.includes('cart.py')")
    assert tab(page, "links").get_attribute("aria-selected") == "true"


def test_tabs_show_counts_where_there_is_something_to_count(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/risky.py")
    assert "(1)" in tab(page, "quality").inner_text()
    page2, _ = open_file(page_for, proj, "shop/api/routes.py")
    assert "(" in tab(page2, "links").inner_text()


def test_the_connections_tab_has_imports_importers_and_the_map_part(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/core/cart.py", "&pt=links")
    text = panel(page, "links").inner_text()
    assert "shop/db/store.py" in text and "shop/api/routes.py" in text and panel(page, "links").locator(".ar-partof").count() == 1


def test_the_tests_and_safety_tab_has_both(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/risky.py", "&pt=quality")
    assert panel(page, "quality").locator(".testsec").count() == 1 and panel(page, "quality").locator(".finding").count() == 1


def test_arrow_keys_move_between_the_tabs(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/routes.py")
    tab(page, "summary").focus()
    page.keyboard.press("ArrowRight")
    assert tab(page, "code").get_attribute("aria-selected") == "true"
    page.keyboard.press("End")
    assert tab(page, "quality").get_attribute("aria-selected") == "true"
    page.keyboard.press("Home")
    assert tab(page, "summary").get_attribute("aria-selected") == "true"


def test_a_file_that_is_not_code_still_gets_sensible_tabs(tmp_path, page_for):
    write(tmp_path, {"a.py": "def f():\n    return 1\n", "notes.md": "# hi\n\nsome text\n"})
    page, errors = open_file(page_for, tmp_path, "notes.md")
    tab(page, "code").click()
    assert "some text" in panel(page, "code").inner_text() and errors == []


# ----------------------------------------------------------------- the Back button
def test_back_returns_to_the_previous_file(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/routes.py")
    page.evaluate("document.querySelector('#detail [data-panel=summary]')")      # (the panel exists)
    tab(page, "links").click()
    page.locator('#detail [data-panel="links"] a.link', has_text="shop/core/cart.py").first.click()
    page.wait_for_function("document.querySelector('#detail .t').textContent.includes('cart.py')")
    page.go_back()
    page.wait_for_function("document.querySelector('#detail .t').textContent.includes('routes.py')")


def test_back_returns_to_the_previous_tab_of_the_page(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/routes.py")
    page.click('#tabs [data-tab="map"]')
    page.wait_for_selector("#arch svg .ar-comp")
    page.go_back()
    page.wait_for_selector(".grid")
    assert "tab=overview" in page.evaluate("location.hash")
    page.go_forward()
    page.wait_for_selector("#arch svg .ar-comp")


def test_back_closes_the_panel_when_it_was_opened_from_nothing(proj, page_for):
    page, _ = page_for(analyze_repo(proj, "shop"), "#tab=overview")
    page.wait_for_selector(".grid")
    page.locator("#view .list li").first.click()
    page.wait_for_selector("#detail:not([hidden])")
    page.go_back()
    page.wait_for_function("document.getElementById('detail').hidden")


# ----------------------------------------------------------------- the bar that jumps to the same thing elsewhere
def test_the_bar_shows_where_you_are_and_every_view_you_can_jump_to(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/core/cart.py")
    names = page.locator("#detail .lens-btn").evaluate_all("els => els.map(e => e.dataset.lens)")
    assert names == ["map", "flow", "graph", "treemap", "health"]
    assert "cart.py" in page.locator("#detail .lens").inner_text()


def test_map_opens_the_part_this_file_belongs_to(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/core/cart.py")
    lens(page, "map").click()
    page.wait_for_selector("#arch .ar-comp.sel")
    assert "core" in page.locator("#arch .ar-info").inner_text() and "tab=map" in page.evaluate("location.hash")


def test_the_map_button_is_unavailable_for_a_file_that_is_not_on_the_map(proj, page_for):
    page, _ = open_file(page_for, proj, "tests/test_cart.py")
    assert lens(page, "map").is_disabled() and "not on the map" in lens(page, "map").get_attribute("title")


def test_flow_can_start_from_any_function(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/core/pricing.py")
    panel(page, "summary").locator(".symwrap", has_text="total").locator(".sym").click()
    lens(page, "flow").click()
    page.wait_for_selector(".frow")
    assert page.locator('select[aria-label="Starting point"]').input_value() == "shop/core/pricing.py::total"
    assert "total" in page.locator(".frow").first.inner_text()


def test_flow_from_a_file_starts_from_its_most_important_function(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/routes.py")
    lens(page, "flow").click()
    page.wait_for_selector(".frow")
    assert page.locator('select[aria-label="Starting point"]').input_value() == "shop/api/routes.py::home"


def test_flow_is_available_for_code_and_unavailable_for_a_file_with_no_functions(proj, page_for):
    write(proj, {"data.txt": "just text\n"})
    page, _ = open_file(page_for, proj, "tests/test_cart.py")
    assert lens(page, "flow").is_enabled()
    page, _ = open_file(page_for, proj, "data.txt")
    assert lens(page, "flow").is_disabled() and len(lens(page, "flow").get_attribute("title")) > 10


def test_dependencies_shows_the_file_highlighted(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/core/cart.py")
    lens(page, "graph").click()
    page.wait_for_selector("#gr .node")
    assert page.locator("#gr .node.sel").count() >= 1 and "tab=graph" in page.evaluate("location.hash")


def test_a_file_nothing_imports_is_still_shown_on_the_dependency_graph(proj, page_for):
    write(proj, {"shop/solo.py": "def alone():\n    return 1\n"})
    page, _ = open_file(page_for, proj, "shop/solo.py")
    lens(page, "graph").click()
    page.wait_for_selector("#gr .node")
    assert page.locator('#gr .node.sel[data-path="shop/solo.py"]').count() == 1


def test_treemap_zooms_to_the_folder_and_marks_the_file(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/core/cart.py")
    lens(page, "treemap").click()
    page.wait_for_selector("#tm .tile")
    assert page.locator('#tm .tile.sel[data-path="shop/core/cart.py"]').count() == 1


def test_health_is_filtered_to_the_file_and_can_be_cleared(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/risky.py")
    lens(page, "health").click()
    page.wait_for_selector(".health")
    assert "shop/api/risky.py" in page.locator(".h-filter").inner_text()
    paths = page.locator(".finding .fhead .link").all_inner_texts()
    assert paths and all(p.startswith("shop/api/risky.py") for p in paths)
    page.click("#h-clear")
    assert page.locator(".h-filter").count() == 0


def test_the_health_filter_hides_everything_about_other_files(proj, page_for):
    write(proj, {"shop/api/other.py": "def other(s):\n    if s:\n        exec(s)\n    return 1\n"})
    page, _ = open_file(page_for, proj, "shop/api/risky.py")
    lens(page, "health").click()
    page.wait_for_selector(".health")
    assert all(p.startswith("shop/api/risky.py") for p in page.locator(".finding .fhead .link").all_inner_texts())
    assert page.locator("#h-hotspots li:not(.empty)").count() == 1 and "risky.py" in page.locator("#h-hotspots li").first.inner_text()
    assert all("risky.py" in t for t in page.locator("#h-files li").all_inner_texts())
    page.click("#h-clear")
    assert any("other.py" in p for p in page.locator(".finding .fhead .link").all_inner_texts())


def test_the_health_button_says_how_many_findings_there_are(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/risky.py")
    assert "1" in lens(page, "health").inner_text()


def test_focusing_a_function_adds_the_flowchart_to_the_bar(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/risky.py")
    assert lens(page, "chart").count() == 0
    panel(page, "summary").locator(".symwrap", has_text="run").locator(".sym").click()
    assert "run" in page.locator("#detail .lens-focus").inner_text() and "sym=run" in page.evaluate("location.hash")
    lens(page, "chart").click()
    page.wait_for_selector("#chart svg .fc-node")


def test_the_focus_survives_a_reload(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/api/risky.py", "&sym=run&line=1")
    assert "run" in page.locator("#detail .lens-focus").inner_text() and lens(page, "chart").count() == 1


def test_changes_appears_in_a_review_and_jumps_to_that_file(repo, page_for):
    page, _ = page_for(analyze_repo(repo, "demo", base="HEAD~1"), "#tab=overview&f=app/lib.py")
    page.wait_for_selector("#detail:not([hidden]) [role=tablist]")
    lens(page, "changes").click()
    page.wait_for_selector(".card.chg")
    assert page.locator(".card.chg.focus").count() == 1 and "app/lib.py" in page.locator(".card.chg.focus").inner_text()


def test_there_is_no_changes_button_outside_a_review(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/core/cart.py")
    assert lens(page, "changes").count() == 0


def test_every_lens_button_has_a_plain_tooltip(proj, page_for):
    page, _ = open_file(page_for, proj, "shop/core/cart.py")
    titles = page.locator("#detail .lens-btn").evaluate_all("els => els.map(e => e.title)")
    assert all(len(t) > 10 for t in titles)
