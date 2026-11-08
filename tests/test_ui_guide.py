"""Ease of use: a start card, a shortcuts sheet, plain-words tooltips, a first-visit tip, and a click budget."""
import pytest

from code_explainer.core import analyze_repo
from conftest import git, write
from test_ui_ai_static import page_for  # noqa: F401  (shared fixture: opens a static report in a browser)

sync_api = pytest.importorskip("playwright.sync_api")

FILES = {
    "shop/__init__.py": "",
    "shop/cli.py": "from shop import pricing\n\n\ndef main():\n    return pricing.total([1])\n\n\nif __name__ == '__main__':\n    main()\n",
    "shop/pricing.py": "def total(items):\n    if not items:\n        return 0\n    return sum(items)\n",
    "tests/test_pricing.py": "from shop import pricing\n\n\ndef test_total():\n    assert pricing.total([1]) == 1\n",
}


@pytest.fixture
def proj(tmp_path):
    write(tmp_path, FILES)
    return tmp_path


def overview(page_for, proj, **kw):  # noqa: F811
    page, errors = page_for(analyze_repo(proj, "shop", **kw), "#tab=overview")
    page.wait_for_selector(".start-card")
    return page, errors


# ----------------------------------------------------------------- the start card
def test_the_overview_opens_with_what_do_you_want_to_do(proj, page_for):  # noqa: F811
    page, errors = overview(page_for, proj)
    card = page.locator(".start-card")
    assert "What do you want to do?" in card.inner_text()
    assert card.locator("button.start-opt").count() == 5                     # no review, so no "review a change"
    assert not errors


@pytest.mark.parametrize("label,tab", [("See the big picture", "map"), ("Follow what happens when it runs", "flow"),
                                       ("Check what is risky or untested", "health"), ("Read the guided story", "story")])
def test_each_choice_goes_to_the_right_view(proj, page_for, label, tab):  # noqa: F811
    page, _ = overview(page_for, proj)
    page.locator("button.start-opt", has_text=label).click()
    assert f"tab={tab}" in page.evaluate("location.hash")
    assert page.locator(f'#tabs button.on[data-tab="{tab}"]').count() == 1


def test_find_something_puts_the_cursor_in_the_search_box(proj, page_for):  # noqa: F811
    page, _ = overview(page_for, proj)
    page.locator("button.start-opt", has_text="Find a file or function").click()
    assert page.evaluate("document.activeElement.id") == "search"


def test_reviewing_a_change_is_offered_only_when_there_is_one(tmp_path, page_for):  # noqa: F811
    write(tmp_path, FILES)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    write(tmp_path, {"shop/pricing.py": FILES["shop/pricing.py"] + "\n\ndef more():\n    return 1\n"})
    page, _ = overview(page_for, tmp_path, base="HEAD")
    assert page.locator("button.start-opt", has_text="Review what changed").count() == 1
    page.locator("button.start-opt", has_text="Review what changed").click()
    assert "tab=changes" in page.evaluate("location.hash")


# ----------------------------------------------------------------- the shortcuts sheet
def test_question_mark_opens_a_list_of_shortcuts_and_escape_closes_it(proj, page_for):  # noqa: F811
    page, _ = overview(page_for, proj)
    page.keyboard.press("?")
    dlg = page.locator("#help-sheet")
    dlg.wait_for()
    assert dlg.get_attribute("role") == "dialog" and "Search" in dlg.inner_text() and "?" in dlg.inner_text()
    page.keyboard.press("Escape")
    assert page.locator("#help-sheet").count() == 0


def test_the_header_has_a_help_button_for_people_who_do_not_know_the_key(proj, page_for):  # noqa: F811
    page, _ = overview(page_for, proj)
    page.locator("#help").click()
    assert page.locator("#help-sheet").count() == 1
    page.locator("#help-sheet .help-close").click()
    assert page.locator("#help-sheet").count() == 0
    assert page.evaluate("document.activeElement.id") == "help"              # focus goes back to where it was


def test_typing_a_question_mark_in_the_search_box_does_not_open_the_sheet(proj, page_for):  # noqa: F811
    page, _ = overview(page_for, proj)
    page.locator("#search").click()
    page.keyboard.type("what?")
    assert page.locator("#help-sheet").count() == 0 and page.locator("#search").input_value() == "what?"


# ----------------------------------------------------------------- the first-visit tip
def test_a_first_visit_tip_shows_once_and_can_be_dismissed_for_good(proj, page_for):  # noqa: F811
    page, _ = overview(page_for, proj)
    assert page.locator(".start-tip").is_visible()
    page.locator(".start-tip button").click()
    assert page.locator(".start-tip").count() == 0
    page.reload()
    page.wait_for_selector(".start-card")
    assert page.locator(".start-tip").count() == 0


def test_the_card_still_works_when_the_browser_blocks_storage(proj, page_for):  # noqa: F811
    page, errors = page_for(analyze_repo(proj, "shop"), "#tab=overview")
    page.evaluate("""() => { Object.defineProperty(window, "localStorage", { get() { throw new Error("blocked"); } }); }""")
    page.locator(".start-tip button").click()
    assert page.locator(".start-card").count() == 1 and not errors


# ----------------------------------------------------------------- plain words
def test_numbers_in_the_function_list_explain_themselves_on_hover(proj, page_for):  # noqa: F811
    page, _ = page_for(analyze_repo(proj, "shop"), "#tab=overview&f=shop/pricing.py")
    page.wait_for_selector("#detail .sym .ln")
    title = page.locator("#detail .sym .ln").first.get_attribute("title")
    assert "line" in title.lower() and "complexity" in title.lower()


def test_the_complexity_figure_in_the_summary_has_a_plain_explanation(proj, page_for):  # noqa: F811
    page, _ = page_for(analyze_repo(proj, "shop"), "#tab=overview&f=shop/pricing.py")
    page.wait_for_selector("#detail .metrics")
    assert "paths" in page.locator("#detail .metrics div", has_text="complexity").first.get_attribute("title").lower()


# ----------------------------------------------------------------- click budget
def test_a_flowchart_is_two_clicks_from_the_overview(proj, page_for):  # noqa: F811
    page, _ = overview(page_for, proj)
    page.locator(".cols li[data-path=\"shop/pricing.py\"]").first.click()   # 1: a file in "Start here"
    page.locator("#detail button.chartbtn").first.click()                       # 2: its flowchart
    assert page.locator("#chart .fc-node").count() >= 3


def test_the_code_is_on_screen_as_soon_as_its_tab_is_chosen(proj, page_for):  # noqa: F811
    page, _ = page_for(analyze_repo(proj, "shop"), "#tab=overview&f=shop/pricing.py&pt=code")
    page.wait_for_selector("#detail .code .s")
    box = page.locator("#detail .code").bounding_box()
    assert box["y"] + 60 < page.viewport_size["height"]


def test_who_calls_a_function_is_one_click_from_its_code(proj, page_for):  # noqa: F811
    page, _ = page_for(analyze_repo(proj, "shop"), "#tab=overview&f=shop/pricing.py&pt=code")
    page.wait_for_selector("#detail .code .s")
    page.locator("#code .rd-uses-btn").first.click()
    assert page.locator("#detail .rd-use-list li").count() >= 1


def test_a_shortcut_with_a_modifier_key_is_left_to_the_browser(proj, page_for):  # noqa: F811
    page, _ = overview(page_for, proj)
    page.keyboard.press("Control+?")
    assert page.locator("#help-sheet").count() == 0


def test_clicking_outside_the_sheet_closes_it(proj, page_for):  # noqa: F811
    page, _ = overview(page_for, proj)
    page.keyboard.press("?")
    page.locator("#help-sheet").click(position={"x": 5, "y": 5})
    assert page.locator("#help-sheet").count() == 0
    page.keyboard.press("?")
    page.locator("#help-sheet .help-box").click()                              # a click inside leaves it open
    assert page.locator("#help-sheet").count() == 1


def test_asking_twice_does_not_stack_two_sheets_and_focus_starts_inside(proj, page_for):  # noqa: F811
    page, _ = overview(page_for, proj)
    page.keyboard.press("?")
    page.keyboard.press("?")
    assert page.locator("#help-sheet").count() == 1
    assert page.evaluate("document.activeElement.className") == "help-close"


def test_the_overview_does_not_show_the_same_reading_list_twice(proj, page_for):  # noqa: F811
    page, _ = overview(page_for, proj)
    titles = page.locator(".cols h2").all_inner_texts()
    assert any(t.startswith("Start here") for t in titles) and not any(t.startswith("Next to learn") for t in titles)
    page.locator(".cols li[data-path=\"shop/pricing.py\"]").first.click()
    page.locator("#detail label.chk").click()
    page.locator("#tabs button[data-tab=overview]").click()
    page.wait_for_selector(".start-card")
    titles = page.locator(".cols h2").all_inner_texts()
    assert any(t.startswith("Next to learn") for t in titles) and any(t.startswith("Start here") for t in titles), titles
