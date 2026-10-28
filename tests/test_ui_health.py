"""Browser tests for the Health tab: which tests reach the code, and the safety check."""
import pytest

from code_explainer.core import analyze_repo
from code_explainer.report import render_html
from conftest import write
from test_ui_ai_static import page_for  # noqa: F401  (shared fixture: opens a static report in a browser)

sync_api = pytest.importorskip("playwright.sync_api")

SECRET = "AKIAABCDEFGHIJKLMNOP"
LIB = '''import hashlib


def price(x):
    return x + 1


def lonely(x):
    if x:
        for i in range(x):
            if i % 2:
                return i
    return 0


def risky(s):
    return eval(s)


def sign(data):
    return hashlib.md5(data)


KEY = "@@SECRET@@"
'''.replace("@@SECRET@@", SECRET)
FILES = {
    "shop/__init__.py": "",
    "shop/lib.py": LIB,
    "shop/page.py": 'def show(s):\n    return eval("</script><b>x</b>")\n',
    "tests/test_lib.py": "from shop.lib import price\n\n\ndef test_price():\n    assert price(1) == 2\n\n\ndef test_eval():\n    eval('1')\n",
}


@pytest.fixture
def proj(tmp_path):
    write(tmp_path, FILES)
    return tmp_path


def health(page_for, proj, hash_="#tab=health"):  # noqa: F811
    page, errors = page_for(analyze_repo(proj, "shop"), hash_)
    page.wait_for_selector(".health" if "tab=health" in hash_ else "#detail:not([hidden])")
    return page, errors


def test_the_tab_summarises_tests_and_safety(proj, page_for):
    page, errors = health(page_for, proj)
    cards = page.locator(".health .grid .card").all_inner_texts()
    text = " | ".join(c.replace("\n", " ") for c in cards)
    assert "%" in text and "no test" in text.lower() and "worth a look" in text.lower()
    assert errors == []


def test_risky_untested_code_is_listed_and_opens_at_its_line(proj, page_for):
    page, _ = health(page_for, proj)
    row = page.locator("#h-hotspots li", has_text="lonely")
    assert row.count() == 1 and "shop/lib.py" in row.inner_text()
    row.click()
    page.wait_for_selector("#detail:not([hidden])")
    assert "f=shop%2Flib.py" in page.evaluate("location.hash") and "line=8" in page.evaluate("location.hash")


def test_findings_are_grouped_worst_first_with_a_plain_reason(proj, page_for):
    page, _ = health(page_for, proj)
    rows = page.locator(".finding:not(.is-test)")
    sevs = rows.evaluate_all("els => els.map(e => e.dataset.sev)")
    assert sevs and sevs == sorted(sevs, key=["high", "medium", "low", "info"].index)
    ev = page.locator('.finding[data-rule="py-eval"]', has_text="shop/lib.py")
    assert ev.count() == 1
    t = ev.inner_text()
    assert "shop/lib.py:17" in t and "eval" in t and "turns a piece of text into a command" in t and "How to fix" in t


def test_a_secret_is_never_shown_in_full_in_the_list(proj, page_for):
    page, _ = health(page_for, proj)
    row = page.locator('.finding[data-rule="secret-token"]')
    assert row.count() == 1
    assert SECRET not in row.inner_text() and "AKI…" in row.inner_text()
    assert SECRET not in page.locator(".health").inner_text()


def test_code_in_a_finding_is_shown_as_text_never_run(proj, page_for):
    page, errors = health(page_for, proj)
    row = page.locator('.finding[data-rule="py-eval"]', has_text="shop/page.py")
    assert "</script><b>x</b>" in row.inner_text() and row.locator(".fcode *").count() == 0 and errors == []


def test_findings_in_test_files_are_hidden_until_asked_for(proj, page_for):
    page, _ = health(page_for, proj)
    assert page.locator(".finding.is-test").count() == 0
    page.check("#h-tests")
    page.wait_for_selector(".finding.is-test")
    assert "tests/test_lib.py" in page.locator(".finding.is-test").first.inner_text()
    page.uncheck("#h-tests")
    assert page.locator(".finding.is-test").count() == 0


def test_filtering_by_severity(proj, page_for):
    page, _ = health(page_for, proj)
    total = page.locator(".finding").count()
    page.click('.sevchip[data-sev="low"]')
    shown = page.locator(".finding").evaluate_all("els => els.map(e => e.dataset.sev)")
    assert shown and set(shown) == {"low"} and len(shown) < total
    page.click('.sevchip[data-sev="low"]')
    assert page.locator(".finding").count() == total


def test_a_file_shows_which_tests_reach_it_and_what_they_miss(proj, page_for):
    page, _ = health(page_for, proj, "#tab=overview&f=shop/lib.py")
    page.wait_for_selector("#detail:not([hidden]) .dsec.testsec")
    sec = page.locator(".dsec.testsec").inner_text()
    assert "partly tested" in sec.lower() and "tests/test_lib.py" in sec and "lonely" in sec
    page.locator(".dsec.testsec a.link", has_text="tests/test_lib.py").click()
    page.wait_for_function("location.hash.includes('test_lib.py')")


def test_a_file_lists_its_own_findings_and_symbols_show_a_no_test_badge(proj, page_for):
    page, _ = health(page_for, proj, "#tab=overview&f=shop/lib.py")
    page.wait_for_selector("#detail:not([hidden]) .dsec.testsec")
    own = page.locator("#detail .finding")
    assert own.count() >= 3 and SECRET not in page.locator("#detail .dsec.safesec").inner_text()
    assert page.locator("#detail .symwrap", has_text="lonely").locator(".nt").count() == 1
    assert page.locator("#detail .symwrap", has_text="price").locator(".nt").count() == 0


def test_changes_flag_new_code_that_no_test_reaches(repo, page_for):
    data = analyze_repo(repo, "demo", base="HEAD~1")
    page, errors = page_for(data, "#tab=changes")
    page.wait_for_selector(".chg")
    cards = page.locator(".chg .untested")
    texts = cards.all_inner_texts()
    assert any("fresh" in t for t in texts) and any("sum_it" in t for t in texts)
    assert all("No test was seen reaching" in t for t in texts)
    assert "no test" in page.locator(".grid").inner_text().lower() and errors == []


def test_a_clean_project_says_so(tmp_path, page_for):
    write(tmp_path, {"a.py": "def f(x):\n    return x + 1\n", "tests/test_a.py": "from a import f\n\n\ndef test_f():\n    assert f(1) == 2\n"})
    page, errors = page_for(analyze_repo(tmp_path, "a"), "#tab=health")
    page.wait_for_selector(".health")
    text = page.locator(".health").inner_text().lower()
    assert "nothing suspicious" in text and "100%" in text and page.locator("#h-hotspots li:not(.empty)").count() == 0 and errors == []


def test_a_project_without_tests_says_so_plainly(tmp_path, page_for):
    write(tmp_path, {"a.py": "def f(x):\n    if x:\n        return 1\n    return 0\n"})
    page, errors = page_for(analyze_repo(tmp_path, "a"), "#tab=health")
    page.wait_for_selector(".health")
    assert "no tests" in page.locator(".health").inner_text().lower() and errors == []


def test_the_reading_level_changes_the_wording_not_the_facts(proj, page_for):
    page, errors = health(page_for, proj)
    page.select_option("#level", "kid")
    kid = page.locator(".health .intro").inner_text()
    page.select_option("#level", "tech")
    tech = page.locator(".health .intro").inner_text()
    assert kid != tech and page.locator(".finding").count() > 0 and errors == []
