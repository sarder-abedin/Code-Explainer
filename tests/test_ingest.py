import subprocess

import pytest

from code_explainer import ingest
from code_explainer.ingest import IngestError, _clone_url, git_churn, git_summary, list_files, open_repo
from conftest import git, write


def test_list_files_skips_noise(tmp_path):
    write(tmp_path, {
        "src/a.py": "x = 1\n",
        "node_modules/dep/index.js": "1",
        "vendor/v.py": "1",
        "src/__pycache__/a.cpython-313.pyc": "1",
        "dist/bundle.min.js": "1",
        "package-lock.json": "{}",
        "poetry.lock": "x",
        "app.js.map": "{}",
        "logo.svg": "<svg/>",
    })
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01binary")
    (tmp_path / "big.txt").write_text("x" * (ingest.MAX_FILE_BYTES + 1))
    assert list_files(tmp_path) == ["src/a.py"]


def test_list_files_respects_gitignore_and_includes_untracked(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    write(tmp_path, {".gitignore": "secret.py\n", "a.py": "1", "secret.py": "1"})
    git(tmp_path, "add", "a.py", ".gitignore")
    git(tmp_path, "commit", "-q", "-m", "x")
    write(tmp_path, {"new.py": "1"})  # untracked but not ignored
    assert list_files(tmp_path) == [".gitignore", "a.py", "new.py"]


def test_list_files_ignores_symlinks(tmp_path):
    (tmp_path / "real.py").write_text("1")
    (tmp_path / "link.py").symlink_to(tmp_path / "real.py")
    assert list_files(tmp_path) == ["real.py"]


def test_churn_and_summary(repo):
    churn = git_churn(repo)
    assert churn["app/lib.py"] == 2 and churn["app/copy.py"] == 1
    s = git_summary(repo)
    assert s["commits"] == 2 and len(s["head"]) >= 7 and s["lastCommit"]


def test_no_history_without_git(tmp_path):
    assert git_churn(tmp_path) is None and git_summary(tmp_path) == {}


@pytest.mark.parametrize("target, expected", [
    ("https://github.com/o/r.git", "https://github.com/o/r.git"),
    ("git@github.com:o/r.git", "git@github.com:o/r.git"),
    ("o/r", "https://github.com/o/r.git"),
    ("github.com/o/r", "https://github.com/o/r.git"),
    ("o/r.git", "https://github.com/o/r.git"),
])
def test_clone_url_forms(target, expected, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no local dir called o/r
    assert _clone_url(target) == expected


def test_existing_directory_is_not_a_url(tmp_path, monkeypatch):
    (tmp_path / "o" / "r").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert _clone_url("o/r") is None


def test_open_repo_local_and_missing(tmp_path):
    with open_repo(str(tmp_path)) as (root, name):
        assert root == tmp_path.resolve() and name == tmp_path.name
    with pytest.raises(IngestError):
        with open_repo(str(tmp_path / "missing")):
            pass


def test_open_repo_clone_failure_is_clean(monkeypatch):
    def boom(*a, **k):
        raise subprocess.CalledProcessError(128, "git", stderr="fatal: repository not found")
    monkeypatch.setattr(ingest.subprocess, "run", boom)
    with pytest.raises(IngestError, match="repository not found"):
        with open_repo("https://example.invalid/o/r.git"):
            pass


def test_open_repo_clone_cleans_up(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **k):
        dest = cmd[-1]
        seen["dest"] = dest
        import os
        os.makedirs(dest)
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    with open_repo("https://example.invalid/o/my-repo.git") as (root, name):
        assert name == "my-repo" and root.is_dir()
    assert not root.exists()  # temp clone removed afterwards
