import subprocess
import sys
from pathlib import Path

import pytest

from code_explainer import __version__
from code_explainer.cli import main

FIX = Path(__file__).parent / "fixtures"


def test_analyze_writes_report(tmp_path, capsys):
    out = tmp_path / "r.html"
    assert main(["analyze", str(FIX / "pyapp"), "-o", str(out)]) == 0
    html = out.read_text()
    assert "<title>pyapp" in html and "{{" not in html
    assert "files" in capsys.readouterr().err


def test_bad_target_is_a_clean_error(tmp_path, capsys):
    assert main(["analyze", str(tmp_path / "nope"), "-o", str(tmp_path / "x.html")]) == 1
    assert "error:" in capsys.readouterr().err
    assert not (tmp_path / "x.html").exists()


def test_review_flags(repo, tmp_path, capsys):
    out = tmp_path / "rev.html"
    assert main(["analyze", str(repo), "--last", "1", "-o", str(out)]) == 0
    assert "Reviewed HEAD~1..working tree: 4 files changed" in capsys.readouterr().err
    assert '"changes"' in out.read_text()
    assert main(["analyze", str(repo), "--base", "HEAD~1", "--head", "HEAD", "-o", str(out)]) == 0


def test_bad_git_ref_reports_error(repo, tmp_path, capsys):
    assert main(["analyze", str(repo), "--base", "nope", "-o", str(tmp_path / "x.html")]) == 1
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["analyze", ".", "--last", "0"],
    ["analyze", ".", "--last", "2", "--base", "main"],
    ["analyze", ".", "--head", "HEAD"],
])
def test_argument_validation(argv):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2


def test_serve_head_needs_base(capsys):
    assert main(["serve", ".", "--head", "HEAD"]) == 2
    assert "--head needs --base" in capsys.readouterr().err


def test_serve_bad_target(tmp_path, capsys):
    assert main(["serve", str(tmp_path / "nope")]) == 1


def test_version_and_module_entry_point(tmp_path):
    out = subprocess.run([sys.executable, "-m", "code_explainer", "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and __version__ in out.stdout
    out = subprocess.run([sys.executable, "-m", "code_explainer", "analyze", str(FIX / "jsapp"), "-o", str(tmp_path / "j.html")],
                         capture_output=True, text=True)
    assert out.returncode == 0 and (tmp_path / "j.html").is_file()
