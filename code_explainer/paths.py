"""Where Code Explainer keeps per-user state (never inside a repository)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def state_dir() -> Path:
    return Path(os.environ.get("CODE_EXPLAINER_HOME") or Path.home() / ".code-explainer")


def repo_id(root: Path) -> str:
    """A short stable name for a directory, used to keep its per-user state apart from other projects."""
    return hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:12]
