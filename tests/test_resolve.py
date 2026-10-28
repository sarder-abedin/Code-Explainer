from code_explainer.analysis import analyze_file, detect_language
from code_explainer.resolve import js_target, py_index, py_module_target, resolve_imports


def graph(files):
    infos = {p: analyze_file(p, detect_language(p), src) for p, src in files.items()}
    resolve_imports(infos)
    return {p: i.imports for p, i in infos.items()}


def test_python_absolute_relative_and_package_imports():
    g = graph({
        "pkg/__init__.py": "from .a import x\n",
        "pkg/a.py": "from . import b\nfrom .. import top\nx = 1\n",
        "pkg/b.py": "import pkg.sub.deep\nimport json\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/deep.py": "from ..a import x\nfrom pkg import a\n",
        "top.py": "",
    })
    assert g["pkg/__init__.py"] == ["pkg/a.py"]
    assert g["pkg/a.py"] == ["pkg/b.py", "top.py"]
    assert g["pkg/b.py"] == ["pkg/sub/deep.py"]           # json is external
    assert g["pkg/sub/deep.py"] == ["pkg/a.py"]           # `from pkg import a` and `from ..a import x` hit the same file


def test_python_source_root_is_dropped_but_packages_are_not():
    idx = py_index({"src/app/__init__.py", "src/app/mod.py", "lib/helper.py"})
    assert idx["app.mod"] == "src/app/mod.py" and idx["helper"] == "lib/helper.py"
    assert "mod" not in idx                                # `app` is a package, so it is not a source root


def test_py_module_target_levels():
    paths = {"a/b/c.py", "a/b/__init__.py", "a/x.py"}
    assert py_module_target("a/b/c.py", paths, {}, 1, "") == "a/b/__init__.py"
    assert py_module_target("a/b/c.py", paths, {}, 2, "x") == "a/x.py"
    assert py_module_target("a/b/c.py", paths, {}, 1, "missing") is None


def test_js_resolution_variants():
    paths = {"src/a.ts", "src/lib/index.js", "src/b.tsx", "src/data.json", "src/c.ts"}
    assert js_target("src/main.js", "./a", paths) == "src/a.ts"
    assert js_target("src/main.js", "./lib", paths) == "src/lib/index.js"
    assert js_target("src/main.js", "./b", paths) == "src/b.tsx"
    assert js_target("src/main.js", "./data.json", paths) == "src/data.json"
    assert js_target("src/main.js", "./c.js", paths) == "src/c.ts"      # TS imports ".js" for ".ts"
    assert js_target("src/main.js", "react", paths) is None
    assert js_target("src/main.js", "./nope", paths) is None
    assert js_target("src/lib/index.js", "../a", paths) == "src/a.ts"


def test_imported_by_is_the_inverse_and_sorted():
    infos = {p: analyze_file(p, "Python", s) for p, s in {"a.py": "", "b.py": "import a\n", "c.py": "import a\n"}.items()}
    resolve_imports(infos)
    assert infos["a.py"].imported_by == ["b.py", "c.py"]


def test_self_import_is_ignored():
    assert graph({"a.py": "import a\n"})["a.py"] == []
