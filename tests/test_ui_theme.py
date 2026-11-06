"""Browser checks of the look: cream background, a logo, real-looking buttons, and readable text on every tab."""
import re

import pytest

from code_explainer.core import analyze_repo
from conftest import git, write
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
    "shop/db/audit.py": "from shop.api import risky\n\n\ndef audit():\n    return risky.run('1')\n",
    "tests/test_cart.py": "from shop.core import cart\n\n\ndef test_items():\n    assert cart.items() is not None\n",
}
CREAM = (248, 241, 224)

# Contrast of every piece of visible text against what is actually behind it.
AUDIT = """() => {
  const parse = c => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(",").map(s => parseFloat(s)); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = c => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  const over = (top, bottom) => ({ r: top.r * top.a + bottom.r * (1 - top.a), g: top.g * top.a + bottom.g * (1 - top.a), b: top.b * top.a + bottom.b * (1 - top.a), a: 1 });
  const bgOf = el => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { layers.push(c); if (c.a >= 0.99) break; } }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    for (const c of layers.reverse()) base = over(c, base);
    return base;
  };
  const opacityOf = el => { let o = 1; for (let e = el; e; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity); return o; };
  const out = [];
  for (const el of document.querySelectorAll("body *")) {
    if (el.closest("svg") || el.closest("script,style") || el.disabled || el.closest("[disabled]")) continue;
    const own = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") continue;
    const color = parse(cs.color), bg = bgOf(el), op = opacityOf(el);
    const fg = over({ ...color, a: color.a * op }, bg);
    const ratio = (Math.max(lum(fg), lum(bg)) + 0.05) / (Math.min(lum(fg), lum(bg)) + 0.05);
    const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
    if (ratio < need) out.push({ tag: el.tagName.toLowerCase(), cls: el.className && el.className.baseVal === undefined ? el.className : "", text: el.textContent.trim().slice(0, 30), ratio: +ratio.toFixed(2), need });
  }
  return out;
}"""

# The same for text drawn inside diagrams: against the shape it sits on (blended with the page behind it).
SVG_AUDIT = """() => {
  const parse = c => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(",").map(s => parseFloat(s)); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = c => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  const over = (top, bottom) => ({ r: top.r * top.a + bottom.r * (1 - top.a), g: top.g * top.a + bottom.g * (1 - top.a), b: top.b * top.a + bottom.b * (1 - top.a), a: 1 });
  const pageBg = el => { const layers = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { layers.push(c); if (c.a >= 0.99) break; } } let b = { r: 255, g: 255, b: 255, a: 1 }; for (const c of layers.reverse()) b = over(c, b); return b; };
  const out = [];
  for (const g of document.querySelectorAll(".tile, .fc-node, .ar-comp, .ar-ext")) {
    const shape = g.querySelector("rect, polygon");
    if (!shape) continue;
    const sc = getComputedStyle(shape), fill = parse(sc.fill);
    if (!fill) continue;
    const bg = over({ ...fill, a: fill.a * parseFloat(sc.fillOpacity) }, pageBg(g.closest("svg").parentElement));
    for (const t of g.querySelectorAll("text, tspan")) {
      if (!Array.from(t.childNodes).some(n => n.nodeType === 3 && n.textContent.trim())) continue;
      const fg = parse(getComputedStyle(t).fill);
      if (!fg) continue;
      const f = over(fg, bg), ratio = (Math.max(lum(f), lum(bg)) + 0.05) / (Math.min(lum(f), lum(bg)) + 0.05);
      if (ratio < 4.5) out.push({ cls: g.getAttribute("class"), text: t.textContent.trim().slice(0, 30), ratio: +ratio.toFixed(2) });
    }
  }
  return out;
}"""


@pytest.fixture
def proj(tmp_path):
    write(tmp_path, FILES)
    return tmp_path


def open_page(page_for, proj, hash_="#tab=story", **kw):  # noqa: F811
    page, errors = page_for(analyze_repo(proj, "shop", **kw), hash_)
    page.wait_for_selector("header")
    return page, errors


def parse_rgb(css: str):
    return tuple(int(float(x)) for x in re.findall(r"[\d.]+", css)[:3])


# ----------------------------------------------------------------- cream and logo
def test_the_page_is_cream(proj, page_for):
    page, _ = open_page(page_for, proj)
    assert parse_rgb(page.evaluate("getComputedStyle(document.body).backgroundColor")) == CREAM


def test_the_header_shows_the_logo_and_the_product_name_beside_the_project(proj, page_for):
    page, errors = open_page(page_for, proj)
    logo = page.locator("header svg.logo")
    box = logo.bounding_box()
    assert logo.is_visible() and 28 <= box["width"] <= 64 and abs(box["width"] - box["height"]) < 2
    assert logo.get_attribute("role") == "img" and "Code Explainer" in logo.locator("title").text_content()
    assert page.locator("header .product").inner_text().strip() == "Code Explainer"
    assert "shop" in page.locator("#brand").inner_text() and errors == []


def test_the_logo_links_home_to_the_first_tab(proj, page_for):
    page, _ = open_page(page_for, proj, "#tab=health")
    page.wait_for_selector(".health")
    page.locator("header a.logo-link").click()
    page.wait_for_selector(".card.story")
    assert "tab=story" in page.evaluate("location.hash")


def test_the_tab_has_a_favicon_and_a_title(proj, page_for):
    page, _ = open_page(page_for, proj)
    assert page.locator('link[rel="icon"]').count() == 1
    assert "Code Explainer" in page.title()


# ----------------------------------------------------------------- buttons look like buttons
def test_the_current_tab_is_a_filled_primary_button(proj, page_for):
    page, _ = open_page(page_for, proj)
    on = page.locator("#tabs button.on")
    bg = parse_rgb(on.evaluate("e => getComputedStyle(e).backgroundColor"))
    fg = parse_rgb(on.evaluate("e => getComputedStyle(e).color"))
    primary = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--primary').trim()")
    assert bg == tuple(int(primary[i:i + 2], 16) for i in (1, 3, 5))     # the brand colour
    assert sum(fg) > sum(bg)                                              # with light text on it


def test_other_tabs_and_ordinary_buttons_have_a_visible_edge_or_fill(proj, page_for):
    page, _ = open_page(page_for, proj, "#tab=overview")
    page.wait_for_selector(".grid")
    buttons = page.locator("header button:visible, #view button:visible")
    assert buttons.count() >= 6
    bad = buttons.evaluate_all("""els => els.filter(e => {
      const c = getComputedStyle(e), edge = parseFloat(c.borderTopWidth) > 0 && !/rgba\\(.*, 0\\)|transparent/.test(c.borderTopColor);
      const fill = !/rgba\\(.*, 0\\)|transparent/.test(c.backgroundColor);
      return !(edge || fill) && !e.classList.contains('x');
    }).map(e => e.textContent.trim().slice(0, 20))""")
    assert bad == []


def test_the_primary_action_is_filled_and_the_rest_are_not(proj, page_for):
    page, _ = open_page(page_for, proj, "#tab=story")
    page.wait_for_selector(".card.story")
    primary = page.locator(".nav2 .primary")
    secondary = page.locator(".nav2 button:not(.primary)").first
    pbg = parse_rgb(primary.evaluate("e => getComputedStyle(e).backgroundColor"))
    sbg = parse_rgb(secondary.evaluate("e => getComputedStyle(e).backgroundColor"))
    assert pbg != sbg and sum(pbg) < sum(sbg)


def test_buttons_have_a_clear_keyboard_focus_ring(proj, page_for):
    page, _ = open_page(page_for, proj)
    page.locator("#tabs button").nth(2).focus()
    page.keyboard.press("Tab")
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    width = page.evaluate("parseFloat(getComputedStyle(document.activeElement).outlineWidth)")
    assert outline != "none" and width >= 2


# ----------------------------------------------------------------- everything readable
TABS = [("story", ".card.story"), ("map", "#arch svg .ar-comp"), ("overview", ".grid"), ("flow", ".frow"), ("treemap", "#tm .tile"),
        ("graph", "#gr .node"), ("health", ".health"), ("ai", ".ai-static"), ("words", ".words .card")]


@pytest.mark.parametrize("tab,ready", TABS)
@pytest.mark.parametrize("level", ["kid", "tech"])
def test_all_text_is_readable_on_every_tab(proj, page_for, tab, ready, level):
    page, errors = open_page(page_for, proj, f"#tab={tab}")
    page.select_option("#level", level)
    page.wait_for_selector(ready)
    assert page.evaluate(AUDIT) == [] and page.evaluate(SVG_AUDIT) == [] and errors == []


def test_text_is_readable_in_the_change_review(repo, page_for):
    page, errors = page_for(analyze_repo(repo, "demo", base="HEAD~1"), "#tab=changes")
    page.wait_for_selector(".chg")
    page.locator(".chg button", has_text="Show the changes").first.click()
    assert page.evaluate(AUDIT) == [] and errors == []


def test_text_is_readable_in_the_file_panel_with_code_findings_and_symbols(proj, page_for):
    page, errors = open_page(page_for, proj, "#tab=overview&f=shop/api/risky.py")
    page.wait_for_selector("#detail:not([hidden]) .symwrap")
    assert page.evaluate(AUDIT) == [] and errors == []


@pytest.mark.parametrize("colour_by", ["lang", "complexity", "churn", "popular", "unused", "understood"])
def test_treemap_labels_are_readable_in_every_colour_mode(proj, page_for, colour_by):
    page, _ = open_page(page_for, proj, "#tab=treemap")
    page.wait_for_selector("#tm .tile")
    options = page.locator('select[aria-label="Colour by"] option').evaluate_all("els => els.map(e => e.value)")
    if colour_by not in options:
        pytest.skip(f"no {colour_by} mode here")
    page.select_option('select[aria-label="Colour by"]', colour_by)
    page.wait_for_selector("#tm .tile")
    assert page.evaluate(SVG_AUDIT) == []


@pytest.mark.parametrize("overlay", ["none", "tests", "safety"])
def test_map_boxes_are_readable_under_every_overlay_and_selected(proj, page_for, overlay):
    page, _ = open_page(page_for, proj, "#tab=map")
    page.select_option("#ar-overlay", overlay)
    page.locator("#arch .ar-comp").first.click()
    assert page.evaluate(SVG_AUDIT) == [] and page.evaluate(AUDIT) == []


def test_flowchart_boxes_and_dialog_are_readable(proj, page_for):
    page, errors = open_page(page_for, proj, "#tab=overview&f=shop/api/risky.py")
    page.wait_for_selector("#detail:not([hidden]) .symwrap")
    page.locator("#detail .chartbtn").first.click()
    page.wait_for_selector("#chart .fc-node")
    page.locator('#chart .fc-node[data-kind="decision"]').first.click()
    assert page.evaluate(SVG_AUDIT) == [] and page.evaluate(AUDIT) == [] and errors == []
    page.click("#chart-mermaid")
    assert page.evaluate(AUDIT) == []


def test_text_stays_readable_when_the_page_is_narrow(proj, page_for):
    page, _ = open_page(page_for, proj, "#tab=overview")
    page.set_viewport_size({"width": 420, "height": 800})
    page.wait_for_selector(".grid")
    assert page.evaluate(AUDIT) == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1") is True
