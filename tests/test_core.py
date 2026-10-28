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


import json

from code_explainer import core


def test_package_json_bin_and_poetry_scripts(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"main": "lib/index", "bin": {"tool": "./bin/cli.js"}}))
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/index.js").write_text("function a() {}\n")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/cli.js").write_text("function b() {}\n")
    (tmp_path / "pyproject.toml").write_text("[tool.poetry.scripts]\nrun = 'svc.app:start'\n")
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc/app.py").write_text("def start():\n    pass\n")
    f = by_path(analyze_repo(tmp_path))
    assert f["lib/index.js"]["entry"] == ["package.json main"]
    assert f["bin/cli.js"]["entry"] == ["package.json bin `tool`"]
    assert f["svc/app.py"]["entry"] == ["console script `run` (pyproject.toml)"]
    assert next(s for s in f["svc/app.py"]["symbols"] if s["name"] == "start")["entry"] == "console script `run`"


def test_malformed_manifests_do_not_break_analysis(tmp_path):
    (tmp_path / "package.json").write_text("{not json")
    (tmp_path / "pyproject.toml").write_text("= broken")
    (tmp_path / "a.py").write_text("x = 1\n")
    assert analyze_repo(tmp_path)["repo"]["files"] == 3


def test_large_files_are_analysed_but_not_embedded(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "MAX_SOURCE_BYTES", 50)
    (tmp_path / "big.py").write_text("def f():\n    pass\n" + "# pad\n" * 30)
    f = by_path(analyze_repo(tmp_path))["big.py"]
    assert f["truncated"] and f["source"] == "" and [s["name"] for s in f["symbols"]] == ["f"]


def test_total_source_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "MAX_TOTAL_SOURCE_BYTES", 30)
    for n in "abc":
        (tmp_path / f"{n}.py").write_text("x = 1\n" * 3)  # 18 bytes each
    f = by_path(analyze_repo(tmp_path))
    assert sum(1 for x in f.values() if x["source"]) == 1 and sum(1 for x in f.values() if x["truncated"]) == 2


def test_test_file_detection_and_unused_rules(tmp_path):
    write = lambda p, t: ((tmp_path / p).parent.mkdir(parents=True, exist_ok=True), (tmp_path / p).write_text(t))
    for p in ("tests/a.py", "pkg/test_b.py", "pkg/c_test.py", "web/x.spec.ts", "src/__tests__/y.js", "conftest.py"):
        write(p, "def f():\n    pass\n")
    write("pkg/orphan.py", "def f():\n    pass\n")
    write("pkg/__init__.py", "")
    f = by_path(analyze_repo(tmp_path))
    assert all(f[p]["isTest"] for p in ("tests/a.py", "pkg/test_b.py", "pkg/c_test.py", "web/x.spec.ts", "src/__tests__/y.js", "conftest.py"))
    assert not f["pkg/orphan.py"]["isTest"] and f["pkg/orphan.py"]["unused"]
    assert not f["pkg/__init__.py"]["unused"] and not any(f[p]["unused"] for p in f if f[p]["isTest"])


def test_reading_order_falls_back_to_central_files(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/core.py").write_text("x = 1\n")
    (tmp_path / "lib/a.py").write_text("from lib import core\n")
    (tmp_path / "lib/b.py").write_text("from lib import core\n")
    d = analyze_repo(tmp_path)
    assert [o["path"] for o in d["readingOrder"]] == ["lib/core.py"] and "2 files" in d["readingOrder"][0]["why"]


def test_empty_repository(tmp_path):
    d = analyze_repo(tmp_path)
    assert d["repo"]["files"] == 0 and d["files"] == [] and d["flowRoots"] == []
    assert d["explain"]["story"][0]["title"]["plain"] == "Overview"


def test_pyproject_scripts_work_without_tomllib(tmp_path):
    """Python 3.10 has no tomllib; console scripts must still be found through the tomli backport."""
    import subprocess
    import sys
    pytest = __import__("pytest")
    pytest.importorskip("tomli")
    (tmp_path / "pyproject.toml").write_text("[project.scripts]\nrun = 'svc.app:start'\n")
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc/app.py").write_text("def start():\n    pass\n")
    code = (
        "import sys; sys.modules['tomllib'] = None\n"  # `import tomllib` now raises ImportError, as on 3.10
        "from code_explainer.core import analyze_repo\n"
        f"d = analyze_repo({str(tmp_path)!r})\n"
        "f = {x['path']: x for x in d['files']}\n"
        "print(f['svc/app.py']['entry'])\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "console script `run`" in out.stdout


def test_the_ai_script_is_spliced_into_the_page_exactly_once():
    from code_explainer import report
    html = report.render_html(analyze_repo(FIX / "pyapp", "pyapp"))
    assert html.count("function renderAI(") == 1 and "@@AI@@" not in html and "@@HEALTH@@" not in html and "{{SCRIPT}}" not in html
