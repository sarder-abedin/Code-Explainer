"""Where Code Explainer keeps per-user state (never inside a repository)."""

from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    return Path(os.environ.get("CODE_EXPLAINER_HOME") or Path.home() / ".code-explainer")
