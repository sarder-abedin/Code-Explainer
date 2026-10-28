"""Fallback for languages without a dedicated analyzer: size only, plus a rough
complexity estimate so the treemap colouring still means something."""

from __future__ import annotations

import re

from ..model import FileInfo

_BRANCH = re.compile(r"\b(if|elif|else if|for|while|case|catch|except|when)\b|&&|\|\|")


def analyze(info: FileInfo, source: str) -> None:
    if info.lang in ("Markdown", "JSON", "YAML", "TOML", "HTML", "CSS", "Other"):
        return
    info.complexity = len(_BRANCH.findall(source))
