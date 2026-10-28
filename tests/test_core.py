from pathlib import Path

from code_explainer.core import analyze_repo
from code_explainer.report import render_html

FIX = Path(__file__).parent / "fixtures"


def by_path(data):
    return {f["path"]: f for f in data["files"]}


def test_python_imports_symbols_and_entry():
    d = analyze_repo(FIX / "pyapp")
    f = by_path(d)
    cli = f["src/shop/cli.py"]
    assert sorted(cli["imports"]) == ["src/shop/cart.py", "src/shop/pricing.py"]
    assert cli["doc"] == "Command line front door."
    assert any("console script `shop`" in e for e in cli["entry"])
    assert len(cli["entry"]) == 1
    assert [s["name"] for s in cli["symbols"]] == ["main"]
    # stdlib `json` must not resolve to anything in the repo
    assert f["src/shop/pricing.py"]["imports"] == []


def test_complexity_counts_branches_and_boolops():
    f = by_path(analyze_repo(FIX / "pyapp"))["src/shop/pricing.py"]
    total = next(s for s in f["symbols"] if s["name"] == "total")
    # 1 + `if` + comprehension + `and`
    assert total["complexity"] == 4


def test_unused_and_tests_and_parse_errors():
    f = by_path(analyze_repo(FIX / "pyapp"))
    assert f["src/shop/legacy.py"]["unused"] is True
    assert f["src/shop/cart.py"]["unused"] is False  # imported by cli + test
    assert f["src/shop/__init__.py"]["unused"] is False
    assert f["tests/test_cart.py"]["isTest"] and not f["tests/test_cart.py"]["unused"]
    assert f["src/shop/broken.py"]["parseError"]
    assert f["src/shop/broken.py"]["analyzed"] is False


def test_js_ts_imports_and_symbols():
    d = analyze_repo(FIX / "jsapp")
    f = by_path(d)
    idx = f["src/index.js"]
    assert sorted(idx["imports"]) == ["src/lib/greet.ts", "src/lib/util.js"]
    assert any("package.json main" in e for e in idx["entry"])
    greet = f["src/lib/greet.ts"]
    assert greet["symbols"][0]["name"] == "greet" and greet["symbols"][0]["doc"] == "Says hello."
    assert f["src/lib/orphan.js"]["unused"] is True
    assert f["src/lib/util.js"]["unused"] is False


def test_reading_order_starts_at_entry_points():
    d = analyze_repo(FIX / "pyapp")
    order = [o["path"] for o in d["readingOrder"]]
    assert order[0] == "src/shop/cli.py"
    assert "src/shop/legacy.py" not in order
    assert set(order[1:3]) == {"src/shop/cart.py", "src/shop/pricing.py"}


def test_report_embeds_data_safely():
    d = analyze_repo(FIX / "pyapp")
    d["files"][0]["source"] = "</script><script>alert(1)</script>"
    html = render_html(d)
    assert "</script><script>alert(1)" not in html
    assert html.count("<script") == 3  # data, serve flag, app code
    assert "{{" not in html


def test_js_section_dividers_are_not_docs(tmp_path):
    (tmp_path / "a.js").write_text("// ---------- helpers ----------\nfunction h() {}\n// Real doc.\nfunction g() {}\n")
    f = by_path(analyze_repo(tmp_path))["a.js"]
    assert [s["doc"] for s in f["symbols"]] == ["", "Real doc."]
