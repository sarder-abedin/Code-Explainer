"""Which tests reach which code (a static approximation from the call graph)."""
import pytest

from code_explainer.core import analyze_repo
from code_explainer.testmap import annotate_changes
from conftest import git, write

LIB = '''def tax(x):
    return x * 0.2


def price(x):
    if x < 0:
        raise ValueError("negative")
    return x + tax(x)


def lonely(x):
    if x:
        for i in range(x):
            if i % 2:
                return i
    return 0


class Cart:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)

    def total(self):
        return sum(price(i) for i in self.items)

    def __repr__(self):
        return "Cart"
'''
FILES = {
    "shop/__init__.py": "",
    "shop/lib.py": LIB,
    "shop/consts.py": "RATE = 0.2\nNAME = 'shop'\n",
    "shop/cli.py": "from shop.lib import Cart\n\n\ndef main():\n    c = Cart()\n    c.add(1)\n    return c.total()\n",
    "tests/test_lib.py": "from shop.lib import price\n\n\ndef test_price():\n    assert price(1) == 1.2\n",
    "tests/test_cli.py": "from shop import cli\n\n\ndef test_main():\n    assert cli.main() > 0\n",
}


def analyzed(tmp_path, **override):
    write(tmp_path, {**FILES, **override})
    return analyze_repo(tmp_path, "shop")


def entry(data, path):
    return data["tests"]["files"][path]


def test_a_function_called_by_a_test_is_direct_and_what_it_calls_is_indirect(tmp_path):
    e = entry(analyzed(tmp_path), "shop/lib.py")
    assert "price" in e["direct"] and "tax" in e["indirect"]
    assert "tax" not in e["direct"]


def test_code_reached_only_through_other_code_is_indirect(tmp_path):
    e = entry(analyzed(tmp_path), "shop/lib.py")
    assert "Cart.total" in e["indirect"] or "Cart.total" in e["maybe"]      # test_main -> main -> c.total()
    assert "lonely" in e["untested"]


def test_each_file_names_the_tests_that_reach_it(tmp_path):
    d = analyzed(tmp_path)
    assert entry(d, "shop/lib.py")["tests"] == ["tests/test_cli.py", "tests/test_lib.py"]
    assert entry(d, "shop/cli.py")["tests"] == ["tests/test_cli.py"]


def test_file_states(tmp_path):
    d = analyzed(tmp_path, **{"shop/dead.py": "def never(a):\n    return a\n", "shop/imp.py": "def thing(a):\n    return a\n",
                              "tests/test_imp.py": "import shop.imp\n\n\ndef test_it():\n    assert True\n"})
    assert entry(d, "shop/lib.py")["state"] == "partial"             # lonely() has no test
    assert entry(d, "shop/cli.py")["state"] == "tested"
    assert entry(d, "shop/dead.py")["state"] == "untested"
    assert entry(d, "shop/imp.py")["state"] == "imported"            # a test imports it but nothing in it is called
    assert "shop/consts.py" not in d["tests"]["files"]               # no functions, nothing to test
    assert "tests/test_lib.py" not in d["tests"]["files"]            # tests are not measured against tests


def test_special_methods_do_not_count_as_untested(tmp_path):
    e = entry(analyzed(tmp_path), "shop/lib.py")
    assert "Cart.__repr__" not in e["untested"] and "Cart" not in e["untested"]


def test_a_class_is_reached_through_its_constructor_and_methods(tmp_path):
    e = entry(analyzed(tmp_path), "shop/lib.py")
    reached = set(e["direct"]) | set(e["indirect"]) | set(e["maybe"])
    assert "Cart.__init__" in reached and "Cart.add" in reached


def test_calls_that_can_only_be_guessed_are_kept_apart(tmp_path):
    d = analyzed(tmp_path, **{"shop/cli.py": "from shop.lib import Cart\n\n\ndef main(c):\n    c.add(1)\n    return c.total()\n"})
    e = entry(d, "shop/lib.py")
    assert "Cart.add" in e["maybe"] and "Cart.add" not in e["untested"]
    assert "Cart.add" not in e["direct"] + e["indirect"]


def test_the_summary_counts_files_and_functions(tmp_path):
    s = analyzed(tmp_path)["tests"]["summary"]
    assert s["testFiles"] == 2 and s["codeFiles"] == 2
    assert s["symbols"] == s["direct"] + s["indirect"] + s["maybe"] + s["untested"]
    assert s["untested"] >= 1 and 0 < s["pct"] < 100


def test_hotspots_are_complex_untested_functions_most_used_first(tmp_path):
    d = analyzed(tmp_path)
    hot = d["tests"]["hotspots"]
    assert [h["qual"] for h in hot][0] == "lonely"
    h = hot[0]
    assert h["path"] == "shop/lib.py" and h["line"] == 11 and h["complexity"] >= 3 and h["risk"] > 0
    assert all(x["complexity"] >= 2 for x in hot) and all(x["qual"] != "Cart.__repr__" for x in hot)
    d2 = analyzed(tmp_path / "x", **{"shop/dead.py": "def a(x):\n    if x:\n        return 1\n    return 2\n\n\ndef b():\n    return a(1)\n\n\ndef c():\n    return a(2)\n"})
    quals = [h["qual"] for h in d2["tests"]["hotspots"]]
    assert all(h["complexity"] >= 2 for h in d2["tests"]["hotspots"]) and "c" not in quals
    assert quals.index("a") < quals.index("lonely")                          # two callers outrank a slightly more complex function with none


def test_calls_made_by_test_helpers_count_for_the_test_that_uses_them(tmp_path):
    d = analyzed(tmp_path, **{"shop/via.py": "def via(a):\n    return a\n",
                              "tests/helpers.py": "from shop.via import via\n\n\ndef make():\n    return via(1)\n",
                              "tests/test_h.py": "from helpers import make\n\n\ndef test_h():\n    make()\n"})
    e = entry(d, "shop/via.py")
    assert e["state"] == "tested" and e["direct"] == ["via"]
    assert "tests/test_h.py" in e["tests"]                          # the helper is test code, so using it is not an extra hop


def test_a_guessed_call_does_not_make_a_function_look_used(tmp_path):
    d = analyzed(tmp_path, **{"shop/k.py": "class K:\n    def m(self, x):\n        if x:\n            return 1\n        return 0\n\n\ndef u(o):\n    return o.m(1)\n"})
    h = next(h for h in d["tests"]["hotspots"] if h["qual"] == "K.m")
    assert h["callers"] == 0


def test_a_project_without_tests_says_so(tmp_path):
    write(tmp_path, {"a.py": "def f(x):\n    if x:\n        return 1\n    return 0\n"})
    d = analyze_repo(tmp_path, "a")
    assert d["tests"]["summary"]["testFiles"] == 0 and d["tests"]["summary"]["pct"] == 0
    assert d["tests"]["files"]["a.py"]["state"] == "untested"


def test_languages_without_call_data_are_unknown_not_untested(tmp_path):
    write(tmp_path, {"main.go": "package main\n\nfunc F() int {\n\treturn 1\n}\n", "a.py": "def f():\n    return 1\n",
                     "tests/test_a.py": "from a import f\n\n\ndef test_f():\n    assert f()\n"})
    d = analyze_repo(tmp_path, "m")
    assert "main.go" not in d["tests"]["files"] or d["tests"]["files"]["main.go"]["state"] == "unknown"
    assert d["tests"]["summary"]["untested"] == 0


def test_javascript_tests_reach_code_through_their_callbacks(tmp_path):
    write(tmp_path, {
        "package.json": '{"name": "x"}',
        "src/math.js": "export function double(x) {\n  return x * 2;\n}\n\nexport function unused(x) {\n  if (x) { return 1; }\n  return 0;\n}\n",
        "src/math.test.js": "import { double } from './math.js';\n\ndescribe('double', () => {\n  it('works', () => {\n    expect(double(2)).toBe(4);\n  });\n});\n",
    })
    e = analyze_repo(tmp_path, "x")["tests"]["files"]["src/math.js"]
    assert "double" in e["direct"] and "unused" in e["untested"] and e["tests"] == ["src/math.test.js"]


def test_the_lists_are_capped_so_a_huge_project_stays_small(tmp_path):
    body = "\n\n".join(f"def f{i}(x):\n    return x" for i in range(150))
    write(tmp_path, {"big.py": body + "\n"})
    d = analyze_repo(tmp_path, "x")
    e = d["tests"]["files"]["big.py"]
    assert len(e["untested"]) == 60 and e["untestedMore"] == 90
    assert d["tests"]["summary"]["untested"] == 150


# ----------------------------------------------------------------- reviewing a change
def test_changed_code_that_no_test_reaches_is_called_out(repo):
    d = analyze_repo(repo, "demo", base="HEAD~1")
    lib = d["changes"]["files"]["app/lib.py"]
    assert "fresh" in lib["symbols"]["added"] and lib["untested"] == ["fresh"]
    assert d["changes"]["files"]["app/copy.py"]["untested"] == ["sum_it"]
    assert d["changes"]["files"]["app/main.py"]["untested"] == ["main"]
    assert d["changes"]["summary"]["untestedChanged"] == 3


def test_changed_code_that_a_test_reaches_is_not_called_out(repo):
    d = analyze_repo(repo, "demo", base="HEAD~1")
    assert "load" in d["changes"]["files"]["app/lib.py"]["symbols"]["modified"]
    assert "load" not in d["changes"]["files"]["app/lib.py"]["untested"]


def test_removed_files_have_nothing_to_call_out(repo):
    d = analyze_repo(repo, "demo", base="HEAD~1")
    assert d["changes"]["files"]["app/legacy.py"].get("untested", []) == []


def test_annotating_is_safe_without_either_part(repo):
    data = {"files": [], "changes": {"files": {}, "summary": {}}}
    annotate_changes(data)                                         # no "tests" key at all
    assert data["changes"]["summary"]["untestedChanged"] == 0
    annotate_changes({"files": []})                                 # no changes either
