"""The architecture map: components found from folders and imports, how they connect, and what they talk to."""
import json
import random

import pytest

from code_explainer import architecture
from code_explainer.analysis import javascript_ts
from code_explainer.core import analyze_repo
from conftest import write

LAYERED = {
    "shop/__init__.py": "",
    "shop/api/__init__.py": "",
    "shop/api/routes.py": '"""Web handlers for the shop."""\nfrom flask import Flask\nfrom shop.core import pricing\nfrom shop.core import cart\n\napp = Flask(__name__)\n\n\ndef home():\n    return pricing.total(cart.items())\n',
    "shop/api/auth.py": "from shop.core import cart\n\n\ndef login():\n    return cart.items()\n",
    "shop/core/__init__.py": "",
    "shop/core/pricing.py": '"""Price maths."""\nfrom shop.db import store\n\n\ndef total(items):\n    return store.load() + len(items)\n',
    "shop/core/cart.py": "from shop.db import store\n\n\ndef items():\n    return store.load()\n",
    "shop/db/__init__.py": "",
    "shop/db/store.py": '"""Saves and loads things."""\nimport sqlite3\n\n\ndef load():\n    return sqlite3.connect("x").execute("select 1")\n',
    "tests/test_cart.py": "from shop.core import cart\n\n\ndef test_items():\n    assert cart.items() is not None\n",
}


def arch(tmp_path, files=None):
    write(tmp_path, files or LAYERED)
    return analyze_repo(tmp_path, "x")["architecture"]


def comp(a, name):
    return next(c for c in a["components"] if c["name"] == name)


def edge(a, src, dst):
    return next((e for e in a["edges"] if e["from"] == src and e["to"] == dst), None)


# ----------------------------------------------------------------- finding the parts
def test_folders_become_components_and_a_shared_source_folder_is_skipped(tmp_path):
    a = arch(tmp_path)
    assert sorted(c["name"] for c in a["components"]) == ["api", "core", "db"]
    assert a["summary"]["grouping"] == "folders"
    assert comp(a, "api")["path"] == "shop/api" and comp(a, "api")["files"] == 2     # routes and auth


def test_every_code_file_belongs_to_exactly_one_component_and_tests_to_none(tmp_path):
    a = arch(tmp_path)
    real = {p for p in LAYERED if p.endswith(".py") and not p.startswith("tests/") and not p.endswith("__init__.py")}
    assert set(a["fileMap"]) == real                                         # empty __init__.py markers say nothing about the design
    assert "tests/test_cart.py" not in a["fileMap"]
    assert sum(c["files"] for c in a["components"]) == len(a["fileMap"])
    assert {a["fileMap"][p] for p in a["fileMap"]} == {c["id"] for c in a["components"]}


def test_an_init_file_with_content_counts_and_an_empty_one_does_not(tmp_path):
    files = {**LAYERED, "shop/db/__init__.py": '"""The storage layer."""\n', "shop/core/__init__.py": "from shop.core import cart\n"}
    a = arch(tmp_path, files)
    assert "shop/db/__init__.py" in a["fileMap"] and "shop/core/__init__.py" in a["fileMap"]
    assert "shop/api/__init__.py" not in a["fileMap"] and comp(a, "db")["doc"] == "The storage layer."


def test_a_component_describes_itself_from_its_own_docstrings(tmp_path):
    a = arch(tmp_path)
    assert comp(a, "api")["doc"] == "Web handlers for the shop."
    assert comp(a, "db")["doc"] == "Saves and loads things."
    assert comp(a, "core")["doc"] == "Price maths."


def test_the_most_used_files_come_first_in_a_component(tmp_path):
    a = arch(tmp_path)
    assert comp(a, "core")["top"][0] == "shop/core/cart.py" or comp(a, "core")["top"][0] == "shop/core/pricing.py"
    assert set(comp(a, "core")["top"]) <= set(a["fileMap"])


def test_the_entry_component_is_listed_first(tmp_path):
    files = {**LAYERED, "shop/db/__main__.py": "from shop.db import store\n\nif __name__ == '__main__':\n    store.load()\n"}
    a = arch(tmp_path, files)
    assert a["components"][0]["name"] == "db" and a["components"][0]["entry"] is True


def test_entry_points_mark_their_component(tmp_path):
    files = {**LAYERED, "shop/api/__main__.py": "from shop.api import routes\n\nif __name__ == '__main__':\n    routes.home()\n"}
    a = arch(tmp_path, files)
    assert comp(a, "api")["entry"] is True and comp(a, "db")["entry"] is False


def test_a_flat_project_gets_one_component_per_file(tmp_path):
    files = {f"{n}.py": f"def {n}():\n    return 1\n" for n in ("a", "b", "c", "d")}
    files["a.py"] = "import b\nimport c\n"
    a = arch(tmp_path, files)
    assert a["summary"]["grouping"] == "files"
    assert sorted(c["name"] for c in a["components"]) == ["a", "b", "c", "d"]


def test_only_two_folders_is_too_few_to_call_a_map_so_files_are_boxes(tmp_path):
    files = {f"app/{d}/m{i}.py": f"def f{i}():\n    return {i}\n" for d in ("api", "core") for i in range(3)}
    a = arch(tmp_path, files)
    assert a["summary"]["grouping"] == "files" and len(a["components"]) == 6


def test_a_big_flat_project_is_split_by_how_files_import_each_other(tmp_path):
    files = {}
    for g in range(3):
        for i in range(6):
            files[f"m{g}_{i}.py"] = "".join(f"import m{g}_{j}\n" for j in range(6) if j != i) + f"def f{g}_{i}():\n    return 1\n"
    a = arch(tmp_path, files)
    assert a["summary"]["grouping"] == "clusters"
    assert 2 <= len(a["components"]) <= architecture.MAX_COMPONENTS
    by_cluster = {}
    for p, cid in a["fileMap"].items():
        by_cluster.setdefault(cid, set()).add(p.split("_")[0])
    assert all(len(v) == 1 for v in by_cluster.values())                  # files that import each other end up together


def test_a_file_that_nothing_connects_to_ends_up_in_other(tmp_path):
    files = {}
    for g in range(2):
        for i in range(8):
            files[f"m{g}_{i}.py"] = "".join(f"import m{g}_{j}\n" for j in range(8) if j != i) + "def f():\n    return 1\n"
    files["loner.py"] = "def lonely():\n    return 1\n"
    a = arch(tmp_path, files)
    assert a["summary"]["grouping"] == "clusters" and a["fileMap"]["loner.py"] == "(other)"
    assert comp(a, "(other)")["files"] == 1


def test_too_many_folders_are_merged_into_an_other_component(tmp_path):
    files = {f"pkg{i}/mod.py": f"def f{i}():\n    return {i}\n" for i in range(20)}
    a = arch(tmp_path, files)
    assert len(a["components"]) <= architecture.MAX_COMPONENTS
    assert "(other)" in [c["name"] for c in a["components"]]
    assert sum(c["files"] for c in a["components"]) == 20


def test_two_folders_with_the_same_name_are_told_apart(tmp_path):
    files = {f"{top}/{sub}/m.py": f"def f():\n    return 1\n" for top in ("a", "b") for sub in ("utils", "core")}
    a = arch(tmp_path, files)
    names = [c["name"] for c in a["components"]]
    assert len(set(names)) == len(names)


# ----------------------------------------------------------------- how they connect
def test_arrows_count_the_imports_between_components(tmp_path):
    a = arch(tmp_path)
    e = edge(a, "shop/api", "shop/core")
    assert e["imports"] == 3 and e["cycle"] is False                      # routes: 2, auth: 1
    assert sorted(map(tuple, e["examples"])) == [("shop/api/auth.py", "shop/core/cart.py"), ("shop/api/routes.py", "shop/core/cart.py"),
                                                 ("shop/api/routes.py", "shop/core/pricing.py")]
    assert edge(a, "shop/core", "shop/db")["imports"] == 2
    assert edge(a, "shop/db", "shop/core") is None and edge(a, "shop/api", "shop/db") is None


def test_calls_between_components_are_counted_too(tmp_path):
    a = arch(tmp_path)
    assert edge(a, "shop/api", "shop/core")["calls"] >= 2


def test_imports_inside_a_component_are_not_arrows(tmp_path):
    files = {**LAYERED, "shop/api/routes.py": LAYERED["shop/api/routes.py"] + "from shop.api import auth\n"}
    a = arch(tmp_path, files)
    assert all(e["from"] != e["to"] for e in a["edges"])
    assert edge(a, "shop/api", "shop/core")["imports"] == 3                  # the new import inside api added nothing


def test_a_call_that_is_only_a_guess_does_not_make_an_arrow(tmp_path):
    files = {"a/u.py": "def use(o):\n    return o.run()\n", "b/k.py": "class K:\n    def run(self):\n        return 1\n",
             "c/z.py": "def z():\n    return 1\n"}
    a = arch(tmp_path, files)
    assert edge(a, "a", "b") is None


def test_an_arrow_between_two_separate_loops_is_not_part_of_either(tmp_path):
    files = {"a/m.py": "from b import m\n", "b/m.py": "from a import m\n", "c/m.py": "from d import m\nfrom a import m as am\n", "d/m.py": "from c import m\n"}
    a = arch(tmp_path, files)
    assert a["summary"]["cycles"] == 2
    assert edge(a, "c", "a")["cycle"] is False and edge(a, "a", "b")["cycle"] is True


def test_components_that_need_each_other_are_flagged(tmp_path):
    files = {**LAYERED, "shop/db/audit.py": "from shop.api import auth\n\n\ndef audit():\n    return auth.login()\n"}
    a = arch(tmp_path, files)
    assert comp(a, "api")["cycle"] and comp(a, "core")["cycle"] and comp(a, "db")["cycle"]
    assert edge(a, "shop/db", "shop/api")["cycle"] is True and a["summary"]["cycles"] == 1


def test_a_project_without_cycles_flags_nothing(tmp_path):
    a = arch(tmp_path)
    assert not any(c["cycle"] for c in a["components"]) and a["summary"]["cycles"] == 0


# ----------------------------------------------------------------- what they talk to
def test_outside_systems_are_found_from_imports(tmp_path):
    a = arch(tmp_path)
    kinds = {x["kind"]: x for x in a["externals"]}
    assert set(kinds) == {"database", "web-server"}
    assert kinds["database"]["modules"] == ["sqlite3"] and kinds["web-server"]["modules"] == ["flask"]
    assert edge(a, "shop/db", "x:database")["files"] == 1 and edge(a, "shop/api", "x:web-server")["files"] == 1


@pytest.mark.parametrize("module,kind", [
    ("requests", "web-client"), ("sqlalchemy", "database"), ("redis", "database"), ("fastapi", "web-server"),
    ("subprocess", "processes"), ("shutil", "files"), ("celery", "queue"), ("boto3", "cloud"), ("openai", "ai"),
    ("click", "command-line"), ("smtplib", "email"), ("socket", "network"), ("sqlalchemy.orm", "database"),
])
def test_known_libraries_map_to_kinds_of_outside_system(tmp_path, module, kind):
    a = arch(tmp_path, {"app/a.py": f"import {module}\n\ndef f():\n    return 1\n", "app/b.py": "import os\n"})
    assert kind in {x["kind"] for x in a["externals"]}


def test_outside_systems_are_listed_most_used_first(tmp_path):
    files = {"app/a.py": "import requests\n", "app/b.py": "import requests\n", "app/c.py": "import requests\n", "app/d.py": "import sqlite3\n"}
    a = arch(tmp_path, files)
    assert [x["kind"] for x in a["externals"]] == ["web-client", "database"] and a["externals"][0]["files"] == 3


def test_common_standard_libraries_are_not_outside_systems(tmp_path):
    a = arch(tmp_path, {"app/a.py": "import os, sys, json, re, pathlib\n\ndef f():\n    return 1\n", "app/b.py": "import collections\n"})
    assert a["externals"] == []


def test_a_library_this_project_defines_itself_is_not_outside(tmp_path):
    files = {"redis.py": "def get():\n    return 1\n", "main.py": "import redis\n\ndef f():\n    return redis.get()\n"}
    a = arch(tmp_path, files)
    assert all(x["kind"] != "database" for x in a["externals"])


@pytest.mark.skipif(not javascript_ts.AVAILABLE, reason="tree-sitter not installed")
def test_javascript_imports_are_recognised_too(tmp_path):
    files = {"package.json": '{"name": "x"}', "src/api/server.js": "import express from 'express';\nimport fs from 'node:fs';\nimport { load } from '../db/store.js';\nexport const app = express();\n",
             "src/db/store.js": "import { Pool } from 'pg';\nexport function load() { return new Pool(); }\n",
             "src/ui/view.js": "import { load } from '../db/store.js';\nexport const v = load;\n"}
    a = arch(tmp_path, files)
    assert {"web-server", "database", "files"} <= {x["kind"] for x in a["externals"]}
    assert edge(a, "src/api", "src/db")["imports"] == 1


# ----------------------------------------------------------------- overlays
def test_each_component_knows_its_test_coverage_and_findings(tmp_path):
    files = {**LAYERED, "shop/api/risky.py": "def run(s):\n    return eval(s)\n"}
    a = arch(tmp_path, files)
    core = comp(a, "core")["tests"]
    assert core["symbols"] == 2 and core["reached"] == 1                  # cart.items is tested, pricing.total is not
    assert comp(a, "db")["tests"]["reached"] >= 0
    assert comp(a, "api")["findings"]["high"] == 1 and comp(a, "api")["findings"]["total"] >= 1
    assert comp(a, "core")["findings"] == {"high": 0, "medium": 0, "total": 0}


def test_findings_in_test_files_do_not_count(tmp_path):
    files = {**LAYERED, "tests/test_x.py": "def test_x():\n    eval('1')\n"}
    a = arch(tmp_path, files)
    assert all(c["findings"]["high"] == 0 for c in a["components"])


# ----------------------------------------------------------------- the picture
def test_every_box_has_a_place_and_every_arrow_a_path(tmp_path):
    a = arch(tmp_path)
    for b in a["components"] + a["externals"]:
        assert all(k in b for k in ("x", "y", "w", "h"))
    for e in a["edges"]:
        assert len(e["path"]) >= 2 and "lx" in e and isinstance(e["back"], bool)
    assert a["w"] > 0 and a["h"] > 0


def test_things_that_use_others_sit_above_them(tmp_path):
    a = arch(tmp_path)
    y = {c["id"]: c["y"] for c in a["components"]}
    assert y["shop/api"] < y["shop/core"] < y["shop/db"]
    db, ext = comp(a, "db"), next(x for x in a["externals"] if x["kind"] == "database")
    assert db["y"] < ext["y"]


def test_boxes_never_overlap(tmp_path):
    a = arch(tmp_path)
    boxes = a["components"] + a["externals"]
    for i, p in enumerate(boxes):
        for q in boxes[i + 1:]:
            assert p["x"] + p["w"] <= q["x"] or q["x"] + q["w"] <= p["x"] or p["y"] + p["h"] <= q["y"] or q["y"] + q["h"] <= p["y"]


def test_the_map_is_plain_json_and_the_same_every_time(tmp_path):
    a, b = arch(tmp_path / "one"), arch(tmp_path / "two")
    assert json.loads(json.dumps(a)) == a and a == b


def test_random_projects_never_break_it(tmp_path):
    rng = random.Random(5)
    for n in range(6):
        files = {}
        count = rng.randint(1, 30)
        for i in range(count):
            folder = rng.choice(["", "a/", "b/", "a/x/", "c/y/z/"])
            others = rng.sample(range(count), min(3, count))
            files[f"{folder}f{i}.py"] = "".join(f"import f{j}\n" for j in others) + "def g():\n    return 1\n"
        a = arch(tmp_path / str(n), files)
        assert 1 <= len(a["components"]) <= architecture.MAX_COMPONENTS
        assert sum(c["files"] for c in a["components"]) == count
        assert set(a["fileMap"].values()) <= {c["id"] for c in a["components"]}


def test_a_project_with_no_code_has_an_empty_map(tmp_path):
    a = arch(tmp_path, {"README.md": "# hi\n", "data.json": "{}"})
    assert a["components"] == [] and a["edges"] == [] and a["externals"] == [] and a["summary"]["components"] == 0


def test_the_base_side_of_a_review_does_not_build_one(repo):
    d = analyze_repo(repo, "demo", base="HEAD~1")
    assert "architecture" in d and d["architecture"]["components"]
