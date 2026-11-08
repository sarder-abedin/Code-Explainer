"""Function contract cards in the browser: what a function takes, gives back, can fail with and touches."""
import pytest

from code_explainer.core import analyze_repo
from conftest import write
from test_ui_ai_static import page_for  # noqa: F401  (shared fixture: opens a static report in a browser)

sync_api = pytest.importorskip("playwright.sync_api")

FILES = {
    "shop/__init__.py": "",
    "shop/store.py": 'import sqlite3\n\n\ndef load(key, default=None):\n    """Read one value."""\n    if not key:\n        raise ValueError("empty key")\n    return sqlite3.connect("x").execute("select 1")\n',
    "shop/cart.py": "from shop import store\n\n\ndef add(items, name):\n    items.append(name)\n    print(name)\n    return store.load(name)\n",
    "tests/test_cart.py": "from shop import cart\n\n\ndef test_add():\n    assert cart.add([], 'a')\n",
}


@pytest.fixture
def proj(tmp_path):
    write(tmp_path, FILES)
    return tmp_path


def open_file(page_for, proj, path, level="kid"):  # noqa: F811
    page, errors = page_for(analyze_repo(proj, "shop"), f"#tab=overview&f={path}")
    page.evaluate(f"""() => {{ const s = document.getElementById("level"); s.value = "{level}"; s.onchange(); }}""")
    page.wait_for_selector("#detail:not([hidden]) .sym")
    return page, errors


def line(page, qual):
    return page.locator(f'#detail .symwrap:has(.nm:text-is("{qual}")) .ct > summary')


def test_each_function_gets_a_one_line_summary_in_the_list(proj, page_for):  # noqa: F811
    page, errors = open_file(page_for, proj, "shop/store.py")
    text = line(page, "load").inner_text()
    assert "key" in text and "default" in text and "gives back" in text and "ValueError" in text and "a database" in text
    assert not errors


def test_the_summary_opens_into_a_card_with_a_row_per_fact(proj, page_for):  # noqa: F811
    page, _ = open_file(page_for, proj, "shop/cart.py")
    line(page, "add").click()
    card = page.locator("#detail .symwrap:has(.nm:text-is('add')) .ct-card")
    assert card.is_visible()
    labels = card.locator(".ct-row b").all_inner_texts()
    assert labels[:2] == ["You give it", "You get back"] and "It changes" in labels and "It shows" in labels
    assert "the given items" in card.inner_text() and "prints text" in card.inner_text()
    assert "through" not in card.inner_text() or "a database (through load)" in card.inner_text()   # reached through the call to store.load


def test_the_wording_follows_the_reading_level(proj, page_for):  # noqa: F811
    page, _ = open_file(page_for, proj, "shop/store.py", level="tech")
    assert line(page, "load").inner_text().startswith("(key, default = None) →")
    page.locator("#level").select_option("plain")
    assert "Needs key" in line(page, "load").inner_text()


def test_a_function_that_changes_its_argument_says_so_in_plain_words(proj, page_for):  # noqa: F811
    page, _ = open_file(page_for, proj, "shop/cart.py")
    line(page, "add").click()
    row = page.locator("#detail .ct-row.warn").first
    assert "It changes" in row.inner_text() and "the given items" in row.inner_text()


def test_the_open_card_stays_open_when_the_panel_redraws(proj, page_for):  # noqa: F811
    page, _ = open_file(page_for, proj, "shop/cart.py")
    line(page, "add").click()
    page.locator("#level").select_option("plain")
    assert page.locator("#detail .symwrap:has(.nm:text-is('add')) details.ct").get_attribute("open") is not None


def test_the_flowchart_shows_the_card_before_a_box_is_picked(proj, page_for):  # noqa: F811
    page, _ = open_file(page_for, proj, "shop/store.py")
    page.locator("#detail .symwrap:has(.nm:text-is('load')) .fc-btn, #detail .symwrap:has(.nm:text-is('load')) button:has-text('flowchart')").first.click()
    page.wait_for_selector("#chart .fc-info .ct-card")
    assert "ValueError" in page.locator("#chart .fc-info .ct-card").inner_text()
    page.locator("#chart .fc-node").nth(1).click()
    assert page.locator("#chart .fc-info .ct-card").count() == 0                  # a picked box shows that box instead


def test_the_flow_step_card_has_the_summary_too(proj, page_for):  # noqa: F811
    page, _ = page_for(analyze_repo(proj, "shop"), "#tab=flow&root=shop/cart.py::add")
    page.get_by_role("button", name="Step through").click()
    page.wait_for_selector(".card.step")
    assert "gives back" in page.locator(".card.step .ct > summary").inner_text()


def test_classes_and_top_level_code_have_no_card(proj, page_for):  # noqa: F811
    write(proj, {"shop/k.py": "class K:\n    def m(self, x):\n        return x\n"})
    page, _ = open_file(page_for, proj, "shop/k.py")
    assert page.locator("#detail .symwrap:has(.nm:text-is('K')) .ct").count() == 0
    assert page.locator("#detail .symwrap:has(.nm:text-is('K.m')) .ct").count() == 1
    assert "self" not in line(page, "K.m").inner_text()
