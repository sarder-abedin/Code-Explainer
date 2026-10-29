"""Browser tests for the function flowchart: opening it, reading it, diving into calls, exporting it."""
import pytest

from code_explainer.analysis import javascript_ts
from code_explainer.core import analyze_repo
from conftest import write
from test_ui_ai_static import page_for  # noqa: F401  (shared fixture: opens a static report in a browser)

sync_api = pytest.importorskip("playwright.sync_api")

CODE = '''def clean(items):
    out = []
    for item in items:
        if item is None:
            continue
        if check(item):
            out.append(item)
    return out


def check(item):
    if item > 0:
        return True
    return False


def straight(a):
    b = a + 1
    return b


def tricky(x):
    if x == "</script><b>x</b>":
        return 1
    return 2


def main(argv):
    if not argv:
        return 1
    return len(clean(argv))
'''


@pytest.fixture
def proj(tmp_path):
    write(tmp_path, {"a.py": CODE})
    return tmp_path


def sym_flow(proj, qual):
    data = analyze_repo(proj, "x")
    f = next(f for f in data["files"] if f["path"] == "a.py")
    return next(s for s in f["symbols"] if s["qual"] == qual)["flow"]


def show_file(page_for, proj, hash_="#tab=overview&f=a.py"):  # noqa: F811
    page, errors = page_for(analyze_repo(proj, "x"), hash_)
    page.wait_for_selector("#detail:not([hidden]) .symwrap")
    return page, errors


def open_chart(page, name):
    page.locator("#detail .symwrap", has=page.locator(f'.nm:text-is("{name}")')).locator(".chartbtn").click()
    page.wait_for_selector("#chart svg .fc-node")


# ----------------------------------------------------------------- opening and closing
def test_only_functions_with_choices_get_a_flowchart_button(proj, page_for):
    page, errors = show_file(page_for, proj)
    names = page.locator("#detail .symwrap", has=page.locator(".chartbtn")).locator(".nm").all_inner_texts()
    assert sorted(names) == ["check", "clean", "main", "tricky"] and errors == []


def test_the_chart_opens_as_a_dialog_and_draws_every_box_and_arrow(proj, page_for):
    page, errors = show_file(page_for, proj)
    open_chart(page, "clean")
    dlg = page.locator("#chart")
    assert dlg.get_attribute("role") == "dialog" and dlg.get_attribute("aria-modal") == "true"
    assert "clean" in page.locator("#chart-title").inner_text()
    flow = sym_flow(proj, "clean")
    assert page.locator("#chart .fc-node").count() == len(flow["nodes"])
    assert page.locator("#chart .fc-edge").count() == len(flow["edges"])
    assert page.locator("#chart .fc-back").count() == sum(e["back"] for e in flow["edges"])
    assert page.locator("#chart .fc-elabel").count() == sum(bool(e["label"]) for e in flow["edges"])
    assert errors == []


def test_escape_and_the_close_button_close_it_and_focus_goes_back(proj, page_for):
    page, _ = show_file(page_for, proj)
    open_chart(page, "clean")
    page.keyboard.press("Escape")
    assert page.locator("#chart").count() == 0
    assert page.evaluate("document.activeElement.className") == "chartbtn"
    open_chart(page, "check")
    page.click("#chart-close")
    assert page.locator("#chart").count() == 0 and "chart=" not in page.evaluate("location.hash")


def test_changing_the_address_switches_the_open_chart(proj, page_for):
    page, errors = show_file(page_for, proj)
    open_chart(page, "check")
    page.evaluate("location.hash = '#tab=overview&f=a.py&chart=a.py%3A%3Aclean'")
    page.wait_for_function("document.getElementById('chart-title').textContent.includes('clean')")
    page.evaluate("location.hash = '#tab=overview&f=a.py'")
    page.wait_for_function("!document.getElementById('chart')")
    assert errors == []


def test_the_address_remembers_the_open_chart(proj, page_for):
    page, errors = show_file(page_for, proj)
    open_chart(page, "check")
    assert "chart=a.py%3A%3Acheck" in page.evaluate("location.hash")
    page.reload()
    page.wait_for_selector("#chart svg .fc-node")
    assert "check" in page.locator("#chart-title").inner_text()
    page.goto(page.url.split("#")[0] + "#tab=overview&chart=nope")
    page.wait_for_selector("#view")
    assert page.locator("#chart").count() == 0 and errors == []


# ----------------------------------------------------------------- reading it
def box_text(page):
    """The words in every box, line by line (SVG text has no innerText, so read the pieces)."""
    return " ".join(page.locator("#chart .fc-node text").evaluate_all(
        "els => els.map(e => Array.from(e.querySelectorAll('tspan')).map(t => t.textContent).join(' '))"))


def node_text(node):
    return node.locator("text").evaluate("e => Array.from(e.querySelectorAll('tspan')).map(t => t.textContent).join(' ')")


@pytest.mark.parametrize("level,needle", [("kid", "is more than 0"), ("plain", "is more than 0"), ("tech", "if item > 0:")])
def test_the_boxes_follow_the_reading_level(proj, page_for, level, needle):
    page, _ = show_file(page_for, proj)
    page.select_option("#level", level)
    open_chart(page, "check")
    assert needle in box_text(page)


def test_beginner_wording_differs_from_plain_for_steps(proj, page_for):
    page, _ = show_file(page_for, proj)
    page.select_option("#level", "kid")
    open_chart(page, "clean")
    kid = box_text(page)
    page.keyboard.press("Escape")
    page.select_option("#level", "plain")
    open_chart(page, "clean")
    assert kid != box_text(page) and "one by one" in kid


def test_long_text_is_cut_with_the_full_text_in_the_tooltip(tmp_path, page_for):
    cond = " and ".join(f"cond_number_{i}(x)" for i in range(4))
    write(tmp_path, {"a.py": f"def f(x):\n    if {cond}:\n        return 1\n    return 2\n"})
    page, _ = page_for(analyze_repo(tmp_path, "x"), "#tab=overview&f=a.py")
    page.wait_for_selector("#detail:not([hidden]) .symwrap")
    page.select_option("#level", "tech")
    open_chart(page, "f")
    node = page.locator('#chart .fc-node[data-kind="decision"]')
    assert "…" in node_text(node) and "cond_number_3(x)" in node.locator("title").text_content()


def test_boxes_are_keyboard_reachable_and_named(proj, page_for):
    page, _ = show_file(page_for, proj)
    open_chart(page, "check")
    node = page.locator('#chart .fc-node[data-kind="decision"]')
    assert node.get_attribute("role") == "button" and node.get_attribute("tabindex") == "0"
    assert "more than" in node.get_attribute("aria-label")
    node.focus()
    page.keyboard.press("Enter")
    assert "if item > 0:" in page.locator("#chart .fc-info").inner_text()


def test_code_in_a_condition_is_text_never_markup(proj, page_for):
    page, errors = show_file(page_for, proj)
    page.select_option("#level", "tech")
    open_chart(page, "tricky")
    assert "</script>" in box_text(page)
    assert page.locator("#chart svg b").count() == 0 and errors == []


def test_a_cut_off_chart_says_so(tmp_path, page_for):
    body = "".join(f"    if x == {i}:\n        a = {i}\n" for i in range(60))
    write(tmp_path, {"a.py": "def big(x):\n" + body + "    return 0\n"})
    page, _ = page_for(analyze_repo(tmp_path, "x"), "#tab=overview&f=a.py")
    page.wait_for_selector("#detail:not([hidden]) .symwrap")
    open_chart(page, "big")
    assert "first" in page.locator("#chart .fc-note").inner_text().lower()


# ----------------------------------------------------------------- selecting and diving
def test_selecting_a_box_shows_its_words_code_and_lines(proj, page_for):
    page, _ = show_file(page_for, proj)
    open_chart(page, "check")
    page.locator('#chart .fc-node[data-kind="decision"]').click()
    info = page.locator("#chart .fc-info").inner_text()
    assert "if item > 0:" in info and "more than" in info and "line 12" in info.lower()
    assert page.locator('#chart .fc-node.sel').count() == 1


def test_a_call_inside_a_box_opens_that_functions_own_chart_and_back_again(proj, page_for):
    page, errors = show_file(page_for, proj)
    open_chart(page, "clean")
    page.locator('#chart .fc-node[data-line="6"]').click()
    dive = page.locator("#chart .fc-dive", has_text="check")
    assert dive.count() == 1
    dive.click()
    assert "check" in page.locator("#chart-title").inner_text()
    assert page.locator("#chart .fc-crumbs").inner_text().replace("\n", " ").count("›") == 1
    page.locator("#chart .fc-crumbs button", has_text="clean").click()
    assert "clean" in page.locator("#chart-title").inner_text() and errors == []


def test_a_call_inside_a_return_box_can_be_followed(proj, page_for):
    page, _ = show_file(page_for, proj)
    open_chart(page, "main")
    page.locator('#chart .fc-node[data-kind="return"]', has_text="len").click()
    assert page.locator("#chart .fc-dive", has_text="clean").count() == 1


def test_show_in_code_closes_the_chart_and_opens_that_line(proj, page_for):
    page, _ = show_file(page_for, proj)
    open_chart(page, "clean")
    page.locator('#chart .fc-node[data-line="4"]').click()
    assert page.locator("#chart .fc-dive").count() == 0                         # the call to check() is on another line
    page.click("#chart .fc-showcode")
    assert page.locator("#chart").count() == 0
    assert "line=4" in page.evaluate("location.hash") and "f=a.py" in page.evaluate("location.hash")


# ----------------------------------------------------------------- moving around and exporting
def scale_of(page):
    t = page.locator("#chart .fc-g").get_attribute("transform")
    return float(t.split("scale(")[1].rstrip(")"))


def test_scrolling_zooms_and_fit_puts_it_back(proj, page_for):
    page, _ = show_file(page_for, proj)
    open_chart(page, "clean")
    start = scale_of(page)
    box = page.locator("#chart svg").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, -400)
    page.wait_for_function("(s) => parseFloat(document.querySelector('#chart .fc-g').getAttribute('transform').split('scale(')[1]) > s", arg=start)
    page.click("#chart-fit")
    assert scale_of(page) == pytest.approx(start, rel=0.01)


BIG = """def load(path, retries=3):
    data = None
    for attempt in range(retries):
        try:
            with open(path) as fh:
                data = fh.read()
        except FileNotFoundError:
            if attempt == retries - 1:
                raise
            continue
        except Exception as e:
            return None
        else:
            break
        finally:
            tick()
    if not data:
        return {}
    if data.startswith("{") and len(data) > 2:
        return parse_json(data)
    elif data.startswith("["):
        return parse_list(data)
    for line in data.splitlines():
        if line.startswith("#"):
            continue
        store(line)
    return done()
"""


def test_a_tall_chart_opens_readable_at_the_top_and_fit_shows_all_of_it(tmp_path, page_for):
    write(tmp_path, {"a.py": BIG})
    page, errors = page_for(analyze_repo(tmp_path, "x"), "#tab=overview&f=a.py")
    page.wait_for_selector("#detail:not([hidden]) .symwrap")
    open_chart(page, "load")
    opening = scale_of(page)
    canvas = page.locator("#chart .chart-canvas").bounding_box()
    start = page.locator('#chart .fc-node[data-kind="start"]').bounding_box()
    assert opening >= 0.6                                                    # text you can read
    assert canvas["y"] <= start["y"] <= canvas["y"] + canvas["height"]       # and it begins at the start
    page.click("#chart-fit")
    whole = scale_of(page)
    assert whole < opening
    nodes = page.locator("#chart .fc-node").evaluate_all("els => els.map(e => e.getBoundingClientRect().bottom)")
    assert max(nodes) <= canvas["y"] + canvas["height"] + 2                  # Fit really does show everything
    assert errors == []


def test_mermaid_text_can_be_copied_and_is_safe(proj, page_for):
    page, _ = show_file(page_for, proj)
    page.select_option("#level", "tech")
    open_chart(page, "tricky")
    page.click("#chart-mermaid")
    text = page.locator("#chart .fc-mermaid textarea").input_value()
    assert text.startswith("flowchart TD") and "n0([" in text and '-- "yes" -->' in text and '-- "no" -->' in text
    assert "<" not in text and ">" not in text.replace("-->", "") and "#lt;/script#gt;" in text


def test_the_mermaid_for_a_loop_has_its_back_arrow(proj, page_for):
    page, _ = show_file(page_for, proj)
    open_chart(page, "clean")
    page.click("#chart-mermaid")
    text = page.locator("#chart .fc-mermaid textarea").input_value()
    flow = sym_flow(proj, "clean")
    assert text.count(" --> ") + text.count('" -->') >= len(flow["edges"])
    assert '-- "each item" -->' in text and '-- "done" -->' in text


def test_the_picture_can_be_downloaded_as_a_standalone_svg(proj, page_for, tmp_path):
    page, _ = show_file(page_for, proj)
    open_chart(page, "check")
    with page.expect_download() as dl:
        page.click("#chart-svg")
    path = dl.value.path()
    assert dl.value.suggested_filename == "check-flowchart.svg"
    text = open(path, encoding="utf-8").read()
    assert text.lstrip().startswith("<svg") or text.lstrip().startswith("<?xml")
    flow = sym_flow(proj, "check")
    assert "<style" in text and "var(--" not in text and "more than" in text and "xmlns" in text
    assert f'width="{flow["w"]}"' in text and f'viewBox="0 0 {flow["w"]} {flow["h"]}"' in text


# ----------------------------------------------------------------- elsewhere in the app
def test_flow_steps_have_a_flowchart_button_too(proj, page_for):
    write(proj, {"a.py": CODE + '\n\nif __name__ == "__main__":\n    main([])\n'})        # makes main() a place a flow can start
    page, errors = page_for(analyze_repo(proj, "x"), "#tab=flow&root=a.py%3A%3Amain")
    page.wait_for_selector(".frow")
    page.locator(".frow").first.click()
    page.wait_for_selector(".card.step .chartbtn")
    page.locator(".card.step .chartbtn").first.click()
    page.wait_for_selector("#chart svg .fc-node")
    assert errors == []


@pytest.mark.skipif(not javascript_ts.AVAILABLE, reason="tree-sitter not installed")
def test_javascript_functions_get_charts_too(tmp_path, page_for):
    write(tmp_path, {"a.js": "export function f(xs) {\n  for (const x of xs) {\n    if (x > 0) { use(x); }\n  }\n  return 1;\n}\n"})
    page, errors = page_for(analyze_repo(tmp_path, "x"), "#tab=overview&f=a.js")
    page.wait_for_selector("#detail:not([hidden]) .symwrap")
    open_chart(page, "f")
    assert page.locator("#chart .fc-node").count() >= 5 and errors == []
