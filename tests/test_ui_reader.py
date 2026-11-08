"""The code reader: colours, clickable calls, margin markers, and who uses a function."""
import pytest

from code_explainer.core import analyze_repo
from conftest import git, write
from test_ui_ai_static import page_for  # noqa: F401  (shared fixture: opens a static report in a browser)

sync_api = pytest.importorskip("playwright.sync_api")

FILES = {
    "shop/__init__.py": "",
    "shop/pricing.py": '"""Price maths."""\n\n\ndef tax(x):\n    # a comment\n    return x * 0.2\n\n\ndef total(items):\n    label = "sum"\n    return sum(items) + tax(1)\n\n\ndef lonely(x):\n    if x:\n        return eval(x)\n    return 0\n',
    "shop/cart.py": "from shop import pricing\n\n\ndef checkout(items):\n    return pricing.total(items)\n\n\ndef again(items):\n    return pricing.total(items)\n",
    "tests/test_pricing.py": "from shop import pricing\n\n\ndef test_total():\n    assert pricing.total([1]) > 0\n",
}


@pytest.fixture
def proj(tmp_path):
    write(tmp_path, FILES)
    return tmp_path


def code_page(page_for, proj, path="shop/pricing.py", extra="", **kw):  # noqa: F811
    page, errors = page_for(analyze_repo(proj, "shop", **kw), f"#tab=overview&f={path}&pt=code{extra}")
    page.wait_for_selector("#detail [data-panel=code] .code .s")
    return page, errors


def test_source_text_is_unchanged_by_colouring(proj, page_for):  # noqa: F811
    page, errors = code_page(page_for, proj)
    text = page.evaluate("""() => { const c = document.querySelector("#code .s").cloneNode(true); c.querySelectorAll(".rd-uses-btn").forEach(b => b.remove()); return c.textContent; }""")
    assert "\n".join(l.rstrip() for l in text.split("\n")).rstrip("\n") == FILES["shop/pricing.py"].rstrip("\n")
    assert not errors


def test_keywords_strings_comments_and_numbers_get_their_own_colour(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj)
    assert page.locator("#code .s .tk-kw", has_text="def").count() >= 3
    assert page.locator("#code .s .tk-str", has_text='"sum"').count() == 1
    assert page.locator("#code .s .tk-cm", has_text="# a comment").count() == 1
    assert page.locator("#code .s .tk-num", has_text="0.2").count() == 1


def test_colour_is_not_the_only_signal_for_the_reader_it_is_just_extra(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj)
    colours = page.evaluate("""() => ["kw", "str", "cm", "num"].map(k => getComputedStyle(document.querySelector(".tk-" + k)).color)""")
    assert len(set(colours)) == 4


def test_a_call_to_a_function_in_the_repo_is_a_link_to_it(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj, "shop/cart.py")
    page.locator("#code .s a.cl-call", has_text="total").first.click()
    page.wait_for_selector("#detail [data-panel=code] .code .hl")
    assert "shop/pricing.py" in page.locator("#detail .dhead .t").inner_text()
    assert "line=9" in page.evaluate("location.hash")                      # the def line of total()
    page.go_back()
    page.wait_for_selector("#detail .dhead .t:has-text('shop/cart.py')")


def test_only_calls_are_links_not_other_mentions(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj, "shop/cart.py")
    links = page.locator("#code .s a.cl-call").all_inner_texts()
    assert links == ["total", "total"]                                      # not the import, not the def names


def test_each_definition_says_how_many_places_use_it(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj)
    texts = {t for t in page.locator("#code .rd-uses-btn").all_inner_texts()}
    assert "3 uses" in texts and "1 use" in texts           # total: two callers + a test; tax: total


def test_a_function_nothing_calls_says_so_in_the_margin(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj)
    assert page.locator("#code .rd-uses-btn.none").count() == 1               # lonely


def test_clicking_the_use_count_lists_the_calls_with_their_lines(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj)
    page.locator("#code .rd-uses-btn", has_text="3 uses").click()
    rows = page.locator("#detail .rd-use-list li")
    assert rows.count() == 3
    text = page.locator("#detail .rd-use-list").inner_text()
    assert "shop/cart.py:5" in text and "pricing.total(items)" in text and "test" in text
    rows.filter(has_text="shop/cart.py:5").locator("a").click()
    page.wait_for_selector("#detail .dhead .t:has-text('shop/cart.py')")
    assert "line=5" in page.evaluate("location.hash")


def test_the_use_list_closes_again(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj)
    page.locator("#code .rd-uses-btn", has_text="3 uses").click()
    page.locator("#detail .rd-use-list").wait_for()
    page.locator("#detail .rd-uses-close").click()
    assert page.locator("#detail .rd-use-list").count() == 0


def test_risky_lines_are_marked_in_the_margin(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj)
    mark = page.locator("#code .gm-find")
    assert mark.count() == 1 and "eval" in (mark.get_attribute("title") or "").lower()


def test_untested_functions_are_marked_on_their_definition_line(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj)
    titles = page.locator("#code .gm-notest").evaluate_all("els => els.map(e => e.dataset.line)")
    assert titles == ["14"]                                                    # lonely() at line 14


def test_lines_changed_in_a_review_are_marked(tmp_path, page_for):  # noqa: F811
    write(tmp_path, FILES)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    write(tmp_path, {"shop/pricing.py": FILES["shop/pricing.py"].replace("x * 0.2", "x * 0.25")})
    page, _ = code_page(page_for, tmp_path, base="HEAD")
    assert page.locator("#code .gm-chg").evaluate_all("els => els.map(e => e.dataset.line)") == ["6"]


def test_a_very_large_file_is_still_shown_but_without_colour(tmp_path, page_for):  # noqa: F811
    big = "".join(f"def f{i}(): return {i}\n" for i in range(5000))
    write(tmp_path, {"big.py": big})
    page, _ = code_page(page_for, tmp_path, "big.py")
    assert page.locator("#code .s .tk-kw").count() == 0 and page.locator("#code .g").inner_text().count("\n") >= 4999


def test_javascript_is_coloured_too(tmp_path, page_for):  # noqa: F811
    write(tmp_path, {"a.js": "// hi\nexport function f(x) {\n  return `a${x}` + 'b' + 12;\n}\n"})
    page, _ = code_page(page_for, tmp_path, "a.js")
    assert page.locator("#code .s .tk-kw", has_text="function").count() == 1 and page.locator("#code .s .tk-cm").count() == 1
    assert page.locator("#code .s .tk-str").count() >= 2 and page.locator("#code .s .tk-num", has_text="12").count() == 1


def test_files_in_other_languages_stay_plain(tmp_path, page_for):  # noqa: F811
    write(tmp_path, {"a.py": "x = 1\n", "notes.md": "# Title\n\nSome def text\n"})
    page, _ = code_page(page_for, tmp_path, "notes.md")
    assert page.locator("#code .s [class^=tk-]").count() == 0


def test_a_hash_in_javascript_is_not_a_comment(tmp_path, page_for):  # noqa: F811
    write(tmp_path, {"a.js": "class A {\n  #x = 1;\n  get() { return this.#x; }\n}\n"})
    page, _ = code_page(page_for, tmp_path, "a.js")
    assert page.locator("#code .s .tk-cm").count() == 0


def test_the_name_in_a_definition_is_not_a_link_but_a_recursive_call_is(tmp_path, page_for):  # noqa: F811
    write(tmp_path, {"a.py": "def again(n): return again(n - 1)\n"})
    page, _ = code_page(page_for, tmp_path, "a.py")
    assert page.locator("#code .s a.cl-call").count() == 1


def test_test_files_get_no_use_counts(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj, "tests/test_pricing.py")
    assert page.locator("#code .rd-uses-btn").count() == 0


def test_special_methods_do_not_say_no_uses_but_other_unused_methods_do(tmp_path, page_for):  # noqa: F811
    write(tmp_path, {"a.py": "class A:\n    def __repr__(self):\n        return 'A'\n\n    def helper(self):\n        return 1\n"})
    page, _ = code_page(page_for, tmp_path, "a.py")
    texts = page.locator("#code .rd-uses-btn").all_inner_texts()
    assert len(texts) == 2                        # the class and helper(); __repr__ has none


def test_uses_from_tests_come_last_and_a_long_list_is_cut(tmp_path, page_for):  # noqa: F811
    calls = "".join(f"def c{i}():\n    return base()\n\n\n" for i in range(14))
    write(tmp_path, {"a.py": "def base():\n    return 1\n\n\n" + calls, "tests/test_a.py": "from a import base\n\n\ndef test_b():\n    assert base()\n"})
    page, _ = code_page(page_for, tmp_path, "a.py")
    page.locator("#code .rd-uses-btn", has_text="15 uses").click()
    assert page.locator("#detail .rd-use-list li").count() == 12 and "and 3 more" in page.locator("#detail .rd-uses").inner_text()
    page.locator("#code .rd-uses-btn", has_text="15 uses").click()          # a second click closes it
    assert page.locator("#detail .rd-use-list").count() == 0


def test_a_test_that_uses_it_is_listed_after_the_code_that_does(proj, page_for):  # noqa: F811
    page, _ = code_page(page_for, proj)
    page.locator("#code .rd-uses-btn", has_text="3 uses").click()
    assert "test" in page.locator("#detail .rd-use-list li").last.inner_text()
    assert "test" not in page.locator("#detail .rd-use-list li").first.inner_text().replace("pricing", "")


def test_changed_line_numbers_follow_the_patch_through_additions_and_deletions(tmp_path, page_for):  # noqa: F811
    write(tmp_path, {"a.py": "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\nf = 6\n"})
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    write(tmp_path, {"a.py": "a = 1\nc = 3\nd = 4\nnew1 = 7\nnew2 = 8\ne = 5\nf = 60"})
    page, _ = code_page(page_for, tmp_path, "a.py", base="HEAD")
    assert page.locator("#code .gm-chg").evaluate_all("els => els.map(e => e.dataset.line)") == ["4", "5", "7"]


def test_only_functions_are_marked_untested_not_classes(tmp_path, page_for):  # noqa: F811
    write(tmp_path, {"a.py": "class K:\n    def m(self, x):\n        return x\n"})
    page, _ = code_page(page_for, tmp_path, "a.py")
    assert page.locator("#code .gm-notest").evaluate_all("els => els.map(e => e.dataset.line)") == ["2"]


def test_two_findings_on_a_line_show_one_marker(tmp_path, page_for):  # noqa: F811
    write(tmp_path, {"a.py": "def f(x):\n    return eval(x) or exec(x)\n"})
    page, _ = code_page(page_for, tmp_path, "a.py")
    assert page.locator("#code .gm-find").count() == 1


def test_a_guessed_call_is_not_counted_as_a_use(tmp_path, page_for):  # noqa: F811
    write(tmp_path, {"a.py": "class K:\n    def m(self):\n        return 1\n\n\ndef u(o):\n    return o.m()\n"})
    page, _ = code_page(page_for, tmp_path, "a.py")
    assert page.locator("#code .rd-uses-btn").all_inner_texts()[1:2] == ["no uses"]


def test_test_files_are_listed_after_other_code_whatever_their_name(tmp_path, page_for):  # noqa: F811
    write(tmp_path, {"zz.py": "def base():\n    return 1\n\n\ndef user():\n    return base()\n",
                     "aa/test_x.py": "from zz import base\n\n\ndef test_x():\n    assert base()\n"})
    page, _ = code_page(page_for, tmp_path, "zz.py")
    page.locator("#code .rd-uses-btn", has_text="2 uses").click()
    items = page.locator("#detail .rd-use-list li")
    assert "aa/test_x.py" in items.last.inner_text() and "zz.py" in items.first.inner_text()


def test_a_missing_newline_at_the_end_of_the_old_file_does_not_shift_the_line_numbers(tmp_path, page_for):  # noqa: F811
    (tmp_path / "a.py").write_text("a = 1\nf = 6")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    (tmp_path / "a.py").write_text("a = 1\nf = 6\ng = 7\nh = 8\n")
    page, _ = code_page(page_for, tmp_path, "a.py", base="HEAD")
    assert page.locator("#code .gm-chg").evaluate_all("els => els.map(e => e.dataset.line)") == ["2", "3", "4"]
