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
