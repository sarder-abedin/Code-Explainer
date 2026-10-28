"""Getting a repository onto disk and listing the files worth analysing."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor",
    "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "site-packages", "dist", "build",
    ".next", ".nuxt", "target", "coverage", ".idea", ".vscode",
}
IGNORED_SUFFIXES = (".min.js", ".min.css", ".map", ".lock", ".pyc", ".svg")
IGNORED_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock"}
MAX_FILE_BYTES = 1_000_000

_URL_RE = re.compile(r"^(https?://|git@|ssh://|git://)")
_SHORTHAND_RE = re.compile(r"^(?:github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


class IngestError(RuntimeError):
    pass


def _git(root: Path, *args: str, timeout: int = 60) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


def _clone_url(target: str) -> str | None:
    if _URL_RE.match(target):
        return target
    if not os.path.exists(target):
        m = _SHORTHAND_RE.match(target)
        if m:
            return f"https://github.com/{m.group(1)}/{m.group(2)}.git"
    return None


@contextmanager
def open_repo(target: str, depth: int = 500):
    """Yield (root, display_name) for a local path or a git URL."""
    url = _clone_url(target)
    if url is None:
        root = Path(target).expanduser().resolve()
        if not root.is_dir():
            raise IngestError(f"not a directory or a git URL: {target}")
        yield root, root.name
        return
    tmp = tempfile.mkdtemp(prefix="code-explainer-")
    try:
        dest = Path(tmp) / "repo"
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--depth", str(depth), url, str(dest)],
                capture_output=True, text=True, check=True, timeout=600,
            )
        except subprocess.CalledProcessError as e:
            raise IngestError(f"git clone failed: {e.stderr.strip()}") from e
        except (OSError, subprocess.TimeoutExpired) as e:
            raise IngestError(f"git clone failed: {e}") from e
        name = url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
        yield dest, name
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ignored(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in IGNORED_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    return name in IGNORED_NAMES or name.endswith(IGNORED_SUFFIXES)


def _is_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\0" in f.read(8192)
    except OSError:
        return True


def list_files(root: Path) -> list[str]:
    """Relative posix paths of text files we should analyse, sorted."""
    rels: list[str] | None = None
    if (root / ".git").exists():
        out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        if out is not None:
            rels = [p for p in out.split("\0") if p]
    if rels is None:
        rels = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
            for fn in filenames:
                rels.append(Path(dirpath, fn).relative_to(root).as_posix())
    keep = []
    for rel in sorted(set(rels)):
        full = root / rel
        if _ignored(rel) or not full.is_file() or full.is_symlink():
            continue
        try:
            if full.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        if _is_binary(full):
            continue
        keep.append(rel)
    return keep


def git_churn(root: Path, limit: int = 2000) -> dict[str, int] | None:
    """Commits touching each file, or None when there is no usable history."""
    if not (root / ".git").exists():
        return None
    out = _git(root, "log", f"-n{limit}", "--name-only", "--no-renames", "--pretty=format:", timeout=120)
    if out is None:
        return None
    return dict(Counter(line for line in out.splitlines() if line))


def git_summary(root: Path) -> dict:
    if not (root / ".git").exists():
        return {}
    head = (_git(root, "rev-parse", "--short", "HEAD") or "").strip()
    date = (_git(root, "log", "-1", "--format=%cs") or "").strip()
    count = (_git(root, "rev-list", "--count", "HEAD") or "").strip()
    return {"head": head, "lastCommit": date, "commits": int(count) if count.isdigit() else None}
