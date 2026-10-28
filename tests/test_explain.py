from pathlib import Path

from code_explainer.core import analyze_repo
from code_explainer.explain import _name_meaning, _words
from code_explainer.model import Symbol

FIX = Path(__file__).parent / "fixtures"


def sym(name, parent=None):
    return Symbol(name, "method" if parent else "function", 1, 3, parent=parent)


def test_words_splits_snake_and_camel():
    assert _words("get_user_name") == ["get", "user", "name"]
    assert _words("parseHTTPResponse") == ["parse", "http", "response"]
    assert _words("__init__") == ["init"]


def test_name_meaning_conventions():
    assert _name_meaning(sym("get_user_name"))[1] == "Gets user name."
    assert "yes-or-no" in _name_meaning(sym("is_valid"))[0]
    assert "sets up a brand-new Cart" in _name_meaning(sym("__init__", "Cart"))[0]
    kid, plain = _name_meaning(sym("frobnicate"))
    assert "frobnicate" in kid and "frobnicate" in plain  # unknown verbs still say something honest


def test_file_and_symbol_explanations_are_grounded():
    d = analyze_repo(FIX / "pyapp")
    f = {x["path"]: x for x in d["files"]}
    cli = f["src/shop/cli.py"]["explain"]
    assert "room" in cli["kid"] and "room" not in cli["plain"]
    assert "cart.py" in cli["kid"] and "pricing.py" in cli["plain"]
    assert "Command line front door." in cli["kid"]  # the author's own words are quoted
    legacy = f["src/shop/legacy.py"]["explain"]
    assert "Nobody seems to visit" in legacy["kid"] and "Nothing imports" in legacy["plain"]
    main = next(s for s in f["src/shop/cli.py"]["symbols"] if s["qual"] == "main")
    assert "total" in main["explain"]["plain"] and "items" in main["explain"]["plain"]
    # guesses from names are labelled as guesses
    total = next(s for s in f["src/shop/pricing.py"]["symbols"] if s["qual"] == "total")
    assert "guess" in total["explain"]["kid"] and "inferred" in total["explain"]["plain"]
    tests = f["tests/test_cart.py"]["explain"]
    assert "inspectors" in tests["kid"]


def test_folders_story_and_glossary():
    d = analyze_repo(FIX / "pyapp")
    ex = d["explain"]
    assert ex["folders"]["tests"]["hinted"] and "inspectors" in ex["folders"]["tests"]["kid"]
    story = ex["story"]
    assert story[0]["title"]["kid"] == "The big picture"
    assert any(s.get("symbol") == "src/shop/cli.py::main" for s in story)  # starts at the console script
    assert story[-1]["path"] == "src/shop/cli.py"
    for step in story:  # every step has both reading levels, no leftover template braces
        for lvl in ("kid", "plain"):
            assert step["title"][lvl] and step["text"][lvl] and "{" not in step["text"][lvl]
    terms = {g["term"] for g in ex["glossary"]}
    assert {"Function", "Blast radius", "Diff"} <= terms
    assert all(g["kid"] and g["plain"] for g in ex["glossary"])


def test_story_without_entry_points_still_works(tmp_path):
    (tmp_path / "a.py").write_text("def helper():\n    return 1\n")
    story = analyze_repo(tmp_path)["explain"]["story"]
    assert story[0]["title"]["plain"] == "Overview"


import pytest

from code_explainer.analysis import analyze_file
from code_explainer.explain import _list, _plural, _shape, _size_word, explain_file, explain_folders, explain_symbol


@pytest.mark.parametrize("n, word", [(3, "tiny"), (14, "tiny"), (15, "small"), (60, "medium-sized"), (200, "big"), (600, "huge")])
def test_size_words(n, word):
    assert _size_word(n) == word


def test_list_and_plural_helpers():
    assert _list([]) == "" and _list(["a"]) == "`a`" and _list(["a", "b"]) == "`a` and `b`"
    assert _list(["a", "b", "c", "d", "e", "f"], 3) == "`a`, `b`, `c` and 3 more"
    assert _list(["a", "b"], quote=False) == "a and b"
    assert _plural(1, "room") == "1 room" and _plural(2, "class", "classes") == "2 classes"


@pytest.mark.parametrize("cx, kid_bit, plain_bit", [
    (1, "straight line", "no branching"), (3, "couple of choices", "A few branches"),
    (6, "quite a few choices", "Several branches"), (20, "tricky", "Highly branched"),
])
def test_shape_thresholds(cx, kid_bit, plain_bit):
    k, p = _shape(cx, 12)
    assert kid_bit in k and plain_bit in p and "12 lines" in k


def finfo(path, src="", lang=None, **kw):
    from code_explainer.analysis import detect_language
    f = analyze_file(path, lang or detect_language(path), src)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


@pytest.mark.parametrize("path, kid_bit, plain_bit", [
    ("README.md", "welcome note", "readme"),
    ("Dockerfile", "recipe for packing", "container"),
    (".gitignore", "should not be saved", "ignore"),
    ("data/config.json", "tidy list", "JSON"),
    ("db/schema.sql", "database", "database"),
    ("scripts/run.sh", "to-do list of commands", "shell script"),
    ("app/utils.py", "toolbox", "shared helpers"),
    ("app/models.py", "shapes", "data shapes"),
    ("app/views.py", "doorbell", "incoming requests"),
    ("app/__init__.py", "group", "package"),
])
def test_file_role_guesses_are_phrased_as_guesses(path, kid_bit, plain_bit):
    e = explain_file(finfo(path, "x = 1\n" if path.endswith(".py") else "x"))
    assert kid_bit in e["kid"] and plain_bit.lower() in e["plain"].lower()
    if path.endswith(".py"):
        assert "from its name" in e["kid"].lower() and "judging by its name" in e["plain"].lower()


def test_file_explanation_flags():
    f = finfo("a.py", "def f():\n    pass\n", churn=12, possibly_unused=True)
    e = explain_file(f)
    assert "changed this room a lot (12 times)" in e["kid"] and "12 commits" in e["plain"]
    assert "Nobody seems to visit" in e["kid"]
    f = finfo("b.py", "x = 1\n", entry_reasons=["named like main"], imports=["c.py"], imported_by=["d.py", "e.py"])
    e = explain_file(f)
    assert "front door" in e["kid"] and "Entry point: named like main." in e["plain"]
    assert "d.py" in e["kid"] and "borrows tools from: c.py" in e["kid"]


def test_class_module_and_entry_symbol_explanations():
    f = finfo("a.py", '"""Doc."""\nclass Cart:\n    """Holds items."""\n    def add(self):\n        pass\n    def _hidden(self):\n        pass\n\nclass Empty:\n    pass\n\n@app.route("/x")\ndef index():\n    pass\n\nprint(1)\n')
    ex = {s.qual: explain_symbol("a.py", s, f, {}, {}) for s in f.symbols}
    assert "recipe" in ex["Cart"]["kid"] and "Holds items." in ex["Cart"]["kid"] and "add" in ex["Cart"]["kid"] and "_hidden" not in ex["Cart"]["kid"]
    assert "does not list any special tricks" in ex["Empty"]["kid"]
    assert "wakes up" in ex["<module>"]["kid"] or "wakes" in ex["<module>"]["kid"] or "file wakes up" in ex["<module>"]["kid"]
    assert "outside world can start here" in ex["index"]["kid"] and "Entry point" in ex["index"]["plain"]
    assert "No helper in the project asks" not in ex["index"]["kid"]  # entry points are expected to have no callers


def test_folder_explanations_hinted_and_generic():
    files = {"tests/test_a.py": finfo("tests/test_a.py", "x=1\n"), "src/pkg/mod.py": finfo("src/pkg/mod.py", "x=1\n"),
             "src/pkg/two.py": finfo("src/pkg/two.py", "x=1\n")}
    fo = explain_folders(files)
    assert fo["tests"]["hinted"] and fo["tests"]["emoji"] == "🔍"
    assert fo["src/pkg"]["hinted"] is False and "2 rooms" in fo["src/pkg"]["kid"] and "Mostly Python" in fo["src/pkg"]["plain"]
    assert set(fo) == {"tests", "src", "src/pkg"}
