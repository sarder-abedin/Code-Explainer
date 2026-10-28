"""Shared fixtures: a tiny git repository with a base commit and a change commit."""
import subprocess

import pytest

BODY = """    total = 0
    for item in items:
        if item > 0:
            total += item
    average = total / max(len(items), 1)
    return total, average
"""


def git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


def write(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    write(tmp_path, {
        "app/main.py": "from app.lib import load, report\nfrom app import other\n\ndef main():\n    load([1])\n    report()\n    other.go()\n\nif __name__ == '__main__':\n    main()\n",
        "app/__init__.py": "",
        "app/lib.py": f"def load(items):\n{BODY}\ndef report():\n    return 1\n",
        "app/legacy.py": "def old():\n    return 0\n",
        "app/other.py": "from app.legacy import old\n\ndef go():\n    return old()\n",
        "tests/test_lib.py": "from app.lib import load\n\ndef test_load():\n    assert load([1])\n",
    })
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    write(tmp_path, {
        "app/main.py": "from app.lib import load\nfrom app import other\n\ndef main():\n    load([1])\n    other.go()\n\nif __name__ == '__main__':\n    main()\n",
        "app/lib.py": f"def load(items):\n    if not items:\n        return 0, 0\n{BODY}\ndef report():\n    return 1\n\ndef fresh():\n    return 2\n",
        "app/copy.py": f"def sum_it(items):\n    if not items:\n        return 0, 0\n{BODY}",
    })
    (tmp_path / "app/legacy.py").unlink()
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "change")
    return tmp_path
