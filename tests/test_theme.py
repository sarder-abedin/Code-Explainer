"""The look of the report: a cream background, readable colours everywhere, and a logo."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from code_explainer import report
from code_explainer.core import analyze_repo
from conftest import write

WEB = Path(__file__).resolve().parent.parent / "code_explainer" / "web"
ROOT = WEB.parent.parent
CSS = (WEB / "style.css").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")


def variables(css=CSS) -> dict[str, str]:
    block = re.search(r":root\{(.*?)\}", css, re.S).group(1)
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", block))


V = variables()


def rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def luminance(c) -> float:
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(x) for x in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def blend(top, bottom, alpha):
    return tuple(t * alpha + b * (1 - alpha) for t, b in zip(top, bottom))


def col(name: str):
    return rgb(V[name])


# ----------------------------------------------------------------- the background
def test_the_page_background_is_cream():
    r, g, b = col("bg")
    assert r > g > b and r >= 0.93 * 255 and b >= 0.80 * 255              # light and warm, not white and not yellow
    assert 12 <= r - b <= 40
    assert 36 <= _hue(col("bg")) <= 52


def _hue(c) -> float:
    import colorsys
    h, _l, _s = colorsys.rgb_to_hls(*(x / 255 for x in c))
    return h * 360


def test_panels_are_a_lighter_warm_white_so_they_stand_out_from_the_cream():
    assert luminance(col("panel")) > luminance(col("bg"))
    assert col("panel")[0] >= col("panel")[2]                              # warm, not a cold white


def test_there_is_no_dark_mode_that_would_replace_the_cream():
    assert "prefers-color-scheme" not in CSS


# ----------------------------------------------------------------- text is readable
@pytest.mark.parametrize("fg", ["ink", "muted", "primary", "warn", "ok", "amber"])
@pytest.mark.parametrize("bg", ["bg", "panel", "hover"])
def test_text_colours_are_readable_on_every_surface(fg, bg):
    assert contrast(col(fg), col(bg)) >= 4.5, (fg, bg, contrast(col(fg), col(bg)))


@pytest.mark.parametrize("fg", ["ink", "muted"])
def test_text_is_readable_on_a_selection_highlight(fg):
    assert contrast(col(fg), col("sel")) >= 4.5


def test_text_on_a_filled_primary_button_is_readable():
    assert contrast(col("on-primary"), col("primary")) >= 4.5
    assert contrast(col("on-primary"), col("primary-hover")) >= 4.5


def test_ordinary_buttons_have_readable_text_and_a_visible_edge():
    assert contrast(col("ink"), col("btn")) >= 4.5
    assert contrast(col("btn-line"), col("bg")) >= 1.8                     # an outline you can see against the cream
    assert contrast(col("ink"), col("btn-hover")) >= 4.5


@pytest.mark.parametrize("name", ["ov-good", "ov-mid", "ov-low", "ov-none"])
def test_the_colours_that_fill_parts_of_a_diagram_keep_dark_text_readable(name):
    assert contrast(col("ink"), col(name)) >= 4.5
    assert contrast(col("muted"), col(name)) >= 4.5


def test_the_good_middling_and_poor_fills_look_green_amber_and_red():
    assert 70 <= _hue(col("ov-good")) <= 170
    assert 38 <= _hue(col("ov-mid")) <= 62
    assert _hue(col("ov-low")) <= 25 or _hue(col("ov-low")) >= 340


@pytest.mark.parametrize("name", ["tk-kw", "tk-str", "tk-cm", "tk-num"])
def test_syntax_colours_are_readable_on_the_code_background(name):
    assert contrast(col(name), col("panel")) >= 4.5
    assert contrast(col(name), col("sel")) >= 4.5          # also under the highlighted line


# ----------------------------------------------------------------- nothing hard-coded that bypasses the palette
def test_no_stylesheet_text_colour_is_a_raw_hex_that_could_be_unreadable():
    uses = re.findall(r"(?<![\w-])color:(#[0-9a-fA-F]{3,6})", CSS)
    assert uses == [], f"use the palette variables instead of {uses}"


def test_severity_colours_come_from_the_palette():
    assert "#c77d00" not in CSS and "#e0a030" not in CSS


# ----------------------------------------------------------------- treemap and graph fills
def _palette():
    block = re.search(r"const PALETTE = \[(.*?)\];", APP, re.S).group(1)
    return re.findall(r"#[0-9a-fA-F]{6}", block)


def test_every_language_colour_keeps_the_tile_label_readable():
    for hex_color in _palette():
        tile = blend(rgb(hex_color), col("panel"), 0.85)
        assert contrast(col("ink"), tile) >= 4.5, hex_color


def test_the_other_tile_colours_keep_the_label_readable():
    fills = {m.group(1): float(m.group(2)) for m in re.finditer(r'fill: "(#[0-9a-fA-F]{6})", op: ([0-9.]+)', APP)}
    assert fills, "tile colours not found"
    for hex_color, op in fills.items():
        assert contrast(col("ink"), blend(rgb(hex_color), col("panel"), op)) >= 4.5, (hex_color, op)


def test_the_darkest_step_of_the_sequential_scale_still_has_readable_labels():
    m = re.search(r'return \{ fill: "(#[0-9a-fA-F]{6})", op: ([0-9.]+) \+ ([0-9.]+) \* t \};', APP)
    assert m, "the sequential scale must use a fixed colour (not a variable)"
    hex_color, lo, span = m.group(1), float(m.group(2)), float(m.group(3))
    assert contrast(col("ink"), blend(rgb(hex_color), col("panel"), lo + span)) >= 4.5


# ----------------------------------------------------------------- the logo
def test_the_logo_is_a_small_valid_svg_with_a_title():
    root = ET.fromstring((WEB / "logo.svg").read_text(encoding="utf-8"))
    assert root.tag == "{http://www.w3.org/2000/svg}svg" and root.get("viewBox") == "0 0 64 64"
    tags = {el.tag.split("}")[1] for el in root.iter()}
    assert tags <= {"svg", "title", "desc", "rect", "path", "circle", "line", "g", "defs", "linearGradient", "stop", "polyline"}
    assert next(el for el in root.iter() if el.tag.endswith("title")).text == "Code Explainer"
    assert len((WEB / "logo.svg").read_bytes()) < 4000


def test_the_logo_uses_the_palette_colours():
    text = (WEB / "logo.svg").read_text(encoding="utf-8").lower()
    assert V["primary"].lower() in text and V["bg"].lower() in text


def test_the_logo_has_enough_contrast_inside_itself():
    text = (WEB / "logo.svg").read_text(encoding="utf-8").lower()
    mark = V["bg"] if V["bg"].lower() in text else None
    assert mark and contrast(col("primary"), rgb(mark)) >= 4.5


def test_the_page_carries_the_logo_and_a_matching_favicon(tmp_path):
    write(tmp_path, {"a.py": "def f():\n    return 1\n"})
    html = report.render_html(analyze_repo(tmp_path, "demo"))
    assert '<svg' in html.split("<header>")[1].split("</header>")[0] and 'class="logo"' in html
    assert re.search(r'<link rel="icon" type="image/svg\+xml" href="data:image/svg\+xml,[^"]+">', html)
    assert "{{LOGO}}" not in html and "{{FAVICON}}" not in html


def test_the_favicon_decodes_to_the_same_logo(tmp_path):
    import urllib.parse
    write(tmp_path, {"a.py": "def f():\n    return 1\n"})
    html = report.render_html(analyze_repo(tmp_path, "demo"))
    href = re.search(r'<link rel="icon"[^>]*href="data:image/svg\+xml,([^"]+)"', html).group(1)
    svg = urllib.parse.unquote(href)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg") and "Code Explainer" in svg


def test_the_readme_shows_the_logo():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "code_explainer/web/logo.svg" in readme.split("\n## ")[0]
