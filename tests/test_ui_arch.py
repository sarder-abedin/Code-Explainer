"""Browser tests for the Map tab: the parts of a project, how they connect, and what they talk to."""
import pytest

from code_explainer.core import analyze_repo
from conftest import write
from test_ui_ai_static import page_for  # noqa: F401  (shared fixture: opens a static report in a browser)

sync_api = pytest.importorskip("playwright.sync_api")

FILES = {
    "shop/__init__.py": "",
    "shop/api/__init__.py": "",
    "shop/api/routes.py": '"""Web handlers for the shop."""\nfrom flask import Flask\nfrom shop.core import pricing\nfrom shop.core import cart\n\napp = Flask(__name__)\n\n\ndef home():\n    return pricing.total(cart.items())\n',
    "shop/api/auth.py": "from shop.core import cart\n\n\ndef login():\n    return cart.items()\n",
    "shop/api/risky.py": "def run(s):\n    return eval(s)\n",
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


def open_map(page_for, proj, hash_="#tab=map"):  # noqa: F811
    data = analyze_repo(proj, "x")
    page, errors = page_for(data, hash_)
    page.wait_for_selector("#arch svg .ar-comp")
    return page, errors, data["architecture"]


def box_text(el):
    return el.evaluate("e => Array.from(e.querySelectorAll('tspan')).map(t => t.textContent).join(' ')")


def comp_box(page, name):
    return page.locator(f'#arch .ar-comp[data-name="{name}"]')


# ----------------------------------------------------------------- the picture
def test_the_map_tab_draws_every_component_outside_system_and_arrow(proj, page_for):
    page, errors, a = open_map(page_for, proj)
    assert page.locator("#arch .ar-comp").count() == len(a["components"]) == 3
    assert page.locator("#arch .ar-ext").count() == len(a["externals"]) == 2
    assert page.locator("#arch .ar-edge").count() == len(a["edges"])
    assert errors == []


def test_each_box_names_the_part_and_its_size(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    text = box_text(comp_box(page, "api"))
    assert "api" in text and "3 files" in text
    assert "A database" in box_text(page.locator('#arch .ar-ext[data-kind="database"]'))


def test_the_part_that_starts_the_program_is_marked(proj, page_for):
    write(proj, {"shop/db/__main__.py": "from shop.db import store\n\nif __name__ == '__main__':\n    store.load()\n"})
    page, _, _ = open_map(page_for, proj)
    assert comp_box(page, "db").locator(".ar-entry").count() == 1 and comp_box(page, "api").locator(".ar-entry").count() == 0


def test_boxes_are_keyboard_reachable_and_named(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    box = comp_box(page, "core")
    assert box.get_attribute("role") == "button" and box.get_attribute("tabindex") == "0" and "core" in box.get_attribute("aria-label")
    box.focus()
    page.keyboard.press("Enter")
    assert "core" in page.locator("#arch .ar-info").inner_text()


def test_a_name_that_looks_like_html_is_only_text(tmp_path, page_for):
    write(tmp_path, {f"pkg/{d}/m.py": "def f():\n    return 1\n" for d in ("x<b>y", "plain", "other")})
    page, errors, _ = open_map(page_for, tmp_path)
    assert page.locator("#arch svg b").count() == 0 and errors == []
    assert "x<b>y" in page.locator("#arch .ar-comp").evaluate_all("els => els.map(e => e.dataset.name)")


def test_a_project_with_no_code_says_so(tmp_path, page_for):
    write(tmp_path, {"README.md": "# hi\n"})
    page, errors = page_for(analyze_repo(tmp_path, "x"), "#tab=map")
    page.wait_for_selector("#arch")
    assert "no code" in page.locator("#arch").inner_text().lower() and errors == []


# ----------------------------------------------------------------- reading a part
def test_clicking_a_part_explains_it_and_lists_its_files(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    comp_box(page, "api").click()
    info = page.locator("#arch .ar-info").inner_text()
    assert "api" in info and "Web handlers for the shop." in info and "3 files" in info
    assert page.locator("#arch .ar-comp.sel").count() == 1
    assert page.locator("#arch .ar-info a.link", has_text="shop/api/routes.py").count() == 1


def test_a_file_link_opens_the_file_panel(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    comp_box(page, "api").click()
    page.locator("#arch .ar-info a.link", has_text="shop/api/routes.py").click()
    page.wait_for_selector("#detail:not([hidden])")
    assert "f=shop%2Fapi%2Froutes.py" in page.evaluate("location.hash") and "tab=map" in page.evaluate("location.hash")


def test_all_files_can_be_listed(proj, page_for):
    write(proj, {f"shop/api/extra{i}.py": f"def e{i}():\n    return {i}\n" for i in range(3)})          # six files in api
    page, _, a = open_map(page_for, proj)
    comp_box(page, "api").click()
    assert page.locator("#arch .ar-info a.ar-file").count() == 3                      # the most used first
    page.click("#arch .ar-allfiles")
    assert page.locator("#arch .ar-info a.ar-file").count() == 6


@pytest.mark.parametrize("level,verb", [("kid", "borrows tools from"), ("plain", "uses"), ("tech", "imports")])
def test_the_wording_follows_the_reading_level(proj, page_for, level, verb):
    page, _, _ = open_map(page_for, proj)
    page.select_option("#level", level)
    comp_box(page, "core").click()
    info = page.locator("#arch .ar-info").inner_text()
    assert verb in info and "db" in info


@pytest.mark.parametrize("level,verb", [("kid", "lends tools to"), ("plain", "is used by"), ("tech", "is imported by")])
def test_a_part_also_says_who_depends_on_it(proj, page_for, level, verb):
    page, _, _ = open_map(page_for, proj)
    page.select_option("#level", level)
    comp_box(page, "core").click()
    info = page.locator("#arch .ar-info").inner_text()
    assert verb in info and "api" in info


def test_the_panel_says_what_a_part_talks_to_and_how_well_it_is_tested(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    page.select_option("#level", "plain")
    comp_box(page, "db").click()
    info = page.locator("#arch .ar-info").inner_text()
    assert "A database" in info and "sqlite3" in info
    assert "Tests reach 1 of 1" in info
    comp_box(page, "api").click()
    assert "Tests reach 0 of 3" in page.locator("#arch .ar-info").inner_text()
    assert "worth a look" in page.locator("#arch .ar-info").inner_text().lower()


def test_clicking_an_outside_system_says_who_uses_it(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    page.locator('#arch .ar-ext[data-kind="web-server"]').click()
    info = page.locator("#arch .ar-info").inner_text()
    assert "flask" in info and "api" in info and "shop/api/routes.py" in info


def test_clicking_an_arrow_shows_the_imports_behind_it(proj, page_for):
    page, _, a = open_map(page_for, proj)
    page.locator('#arch .ar-edge[data-from="shop/api"][data-to="shop/core"] .ar-hit').dispatch_event("click")
    info = page.locator("#arch .ar-info").inner_text()
    assert "shop/api/routes.py" in info and "shop/core/pricing.py" in info and "3" in info


def test_clicking_the_background_clears_the_selection(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    comp_box(page, "api").click()
    page.locator("#arch svg").click(position={"x": 4, "y": 4})
    assert page.locator("#arch .ar-comp.sel").count() == 0 and "Click" in page.locator("#arch .ar-info").inner_text()


def test_the_selection_is_in_the_address_and_survives_a_reload(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    comp_box(page, "core").click()
    assert "comp=shop%2Fcore" in page.evaluate("location.hash")
    page.reload()
    page.wait_for_selector("#arch .ar-comp.sel")
    assert comp_box(page, "core").get_attribute("class").count("sel") == 1 and "core" in page.locator("#arch .ar-info").inner_text()


# ----------------------------------------------------------------- overlays and warnings
def test_the_test_overlay_colours_parts_by_how_much_a_test_reaches(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    page.select_option("#ar-overlay", "tests")
    assert "ov-good" in comp_box(page, "db").get_attribute("class")        # 1 of 1
    assert "ov-mid" in comp_box(page, "core").get_attribute("class")       # 1 of 2
    assert "ov-low" in comp_box(page, "api").get_attribute("class")        # 0 of 3


def test_a_part_with_nothing_to_test_is_left_uncoloured(tmp_path, page_for):
    write(tmp_path, {"pkg/a/m.py": "def f():\n    return 1\n", "pkg/b/m.py": "def g():\n    return 2\n", "pkg/consts/c.py": "X = 1\nY = 2\n"})
    page, _, _ = open_map(page_for, tmp_path)
    page.select_option("#ar-overlay", "tests")
    assert "ov-none" in comp_box(page, "consts").get_attribute("class")


def test_the_safety_overlay_marks_parts_with_findings(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    page.select_option("#ar-overlay", "safety")
    assert "ov-low" in comp_box(page, "api").get_attribute("class")        # eval() is serious
    assert "ov-good" in comp_box(page, "core").get_attribute("class")


def test_parts_that_need_each_other_are_drawn_as_a_problem(proj, page_for):
    write(proj, {"shop/db/audit.py": "from shop.api import auth\n\n\ndef audit():\n    return auth.login()\n"})
    page, _, _ = open_map(page_for, proj)
    assert page.locator("#arch .ar-comp.cycle").count() == 3
    assert page.locator('#arch .ar-edge.ar-cycle[data-from="shop/db"][data-to="shop/api"]').count() == 1
    comp_box(page, "db").click()
    assert "need each other" in page.locator("#arch .ar-info").inner_text().lower()


def test_no_overlay_means_no_colour_classes(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    assert page.locator("#arch .ar-comp.ov-good, #arch .ar-comp.ov-mid, #arch .ar-comp.ov-low").count() == 0


# ----------------------------------------------------------------- moving, exporting and linking
def scale_of(page):
    t = page.locator("#arch .ar-g").get_attribute("transform")
    return float(t.split("scale(")[1].rstrip(")"))


def test_scrolling_zooms_and_fit_puts_it_back(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    start = scale_of(page)
    box = page.locator("#arch svg").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, -400)
    page.wait_for_function("(s) => parseFloat(document.querySelector('#arch .ar-g').getAttribute('transform').split('scale(')[1]) > s", arg=start)
    page.click("#ar-fit")
    assert scale_of(page) == pytest.approx(start, rel=0.01)


def test_mermaid_text_describes_parts_arrows_and_outside_systems(proj, page_for):
    page, _, _ = open_map(page_for, proj)
    page.click("#ar-mermaid")
    text = page.locator("#arch .ar-mermaid textarea").input_value()
    assert text.startswith("flowchart TB") and "api (3 files)" in text and "core (2 files)" in text
    assert '-- "3 imports" -->' in text and '[("A database")]' in text and "<" not in text


def test_the_picture_can_be_downloaded_as_a_standalone_svg(proj, page_for):
    page, _, a = open_map(page_for, proj)
    with page.expect_download() as dl:
        page.click("#ar-svg")
    assert dl.value.suggested_filename == "architecture.svg"
    text = open(dl.value.path(), encoding="utf-8").read()
    assert text.lstrip().startswith("<svg") or text.lstrip().startswith("<?xml")
    assert "<style" in text and "var(--" not in text and "api" in text and "xmlns" in text
    assert f'viewBox="0 0 {a["w"]} {a["h"]}"' in text and f'width="{a["w"]}"' in text


def test_a_file_links_back_to_its_part_on_the_map(proj, page_for):
    page, errors = page_for(analyze_repo(proj, "x"), "#tab=overview&f=shop/core/cart.py")
    page.wait_for_selector("#detail:not([hidden])")
    page.locator("#detail .ar-partof").click()
    page.wait_for_selector("#arch .ar-comp.sel")
    assert "core" in page.locator("#arch .ar-info").inner_text() and "tab=map" in page.evaluate("location.hash") and errors == []


def test_a_file_that_is_not_on_the_map_has_no_such_link(proj, page_for):
    page, _ = page_for(analyze_repo(proj, "x"), "#tab=overview&f=tests/test_cart.py")
    page.wait_for_selector("#detail:not([hidden])")
    assert page.locator("#detail .ar-partof").count() == 0
