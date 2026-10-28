import pytest

from code_explainer.core import analyze_repo
from code_explainer.ingest import IngestError
from conftest import BODY, git

def test_change_review_end_to_end(repo):
    d = analyze_repo(repo, "r", base="HEAD~1", head="HEAD")
    ch = d["changes"]
    f = ch["files"]
    assert {p: e["status"] for p, e in f.items()} == {
        "app/main.py": "M", "app/lib.py": "M", "app/copy.py": "A", "app/legacy.py": "D"}
    lib = f["app/lib.py"]
    assert lib["symbols"]["added"] == ["fresh"] and lib["symbols"]["modified"] == ["load"]
    assert lib["added"] > 0 and "@@" in lib["patch"]
    assert "app/main.py" in [b["path"] for b in lib["blast"]]
    assert any(r["name"] == "main" for r in ch["reach"])           # main() can reach the changed load()
    kinds = {(x["kind"], x["qual"] or x["path"]) for x in ch["newDead"]}
    assert ("unused-new", "fresh") in kinds                        # added, nobody calls it
    assert ("unused-file", "app/copy.py") in kinds and ("unused-new", "sum_it") not in kinds  # said once
    assert ("orphaned", "report") in kinds                         # lost its last caller
    removed = [r for r in ch["risks"] if r["kind"] == "removed-still-used"]
    assert [r["what"] for r in removed] == ["app/legacy.py"] and "app/other.py" in removed[0]["users"]  # once, at file level
    assert len(ch["newDuplicates"]) == 1
    assert {m["qual"] for m in ch["newDuplicates"][0]["members"]} == {"load", "sum_it"}
    assert ch["reviewOrder"][0] in ("app/lib.py", "app/main.py", "app/copy.py")
    assert ch["summary"]["files"] == 4 and ch["summary"]["impactedFiles"] >= 1
    titles = [s["title"]["plain"] for s in d["explain"]["story"][:3]]
    assert titles[0] == "Overview" and titles[1].startswith("Changes vs") and titles[2] == "Review these first"
    assert "knocked down" in f["app/legacy.py"]["explain"]["kid"]


def test_working_tree_and_untracked_files_are_included(repo):
    (repo / "app/lib.py").write_text((repo / "app/lib.py").read_text() + "\ndef extra():\n    return 3\n")
    (repo / "app/brand_new.py").write_text("def hello():\n    return 1\n")
    ch = analyze_repo(repo, "r", base="HEAD")["changes"]
    assert ch["head"] == "working tree"
    assert ch["files"]["app/lib.py"]["symbols"]["added"] == ["extra"]
    assert ch["files"]["app/brand_new.py"]["status"] == "A" and "+def hello" in ch["files"]["app/brand_new.py"]["patch"]


def test_tests_are_reviewed_after_code(repo):
    (repo / "tests/test_lib.py").write_text("from app.lib import load\n\ndef test_load():\n    assert load([2])\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tests")
    order = analyze_repo(repo, "r", base="HEAD~2", head="HEAD")["changes"]["reviewOrder"]
    assert order[-1] == "tests/test_lib.py"


def test_bad_ref_is_a_clean_error(repo):
    with pytest.raises(IngestError):
        analyze_repo(repo, "r", base="no-such-ref")


def test_duplicates_overview_needs_enough_lines(tmp_path):
    (tmp_path / "a.py").write_text("def a(x):\n    return x\n\ndef b(x):\n    return x\n")
    assert analyze_repo(tmp_path)["duplicates"] == []  # tiny functions are not worth flagging
    (tmp_path / "c.py").write_text(f"def one(items):\n{BODY}\ndef two(items):\n{BODY}")
    dups = analyze_repo(tmp_path)["duplicates"]
    assert len(dups) == 1 and len(dups[0]["members"]) == 2
