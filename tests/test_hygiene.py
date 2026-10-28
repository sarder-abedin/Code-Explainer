"""The safety and hygiene lens: patterns worth a second look, found without running anything."""
import json

import pytest

from code_explainer.core import analyze_repo
from conftest import write


def scan(tmp_path, files):
    write(tmp_path, files)
    return analyze_repo(tmp_path, "x")["hygiene"]


def by_id(h, rule, path=None):
    return [f for f in h["findings"] if f["id"] == rule and (path is None or f["path"] == path)]


def one(tmp_path, code, rule, path="app.py"):
    found = by_id(scan(tmp_path, {path: code}), rule, path)
    return found


# ----------------------------------------------------------------- python patterns
@pytest.mark.parametrize("code,rule,line", [
    ("x = input()\nresult = eval(x)\n", "py-eval", 2),
    ("def f(s):\n    exec(s)\n", "py-exec", 2),
    ("import subprocess\n\nsubprocess.run(cmd, shell=True)\n", "shell-injection", 3),
    ("import os\n\nos.system('rm ' + name)\n", "shell-injection", 3),
    ("import os\n\nos.popen(f'ls {d}')\n", "shell-injection", 3),
    ("import pickle\n\nobj = pickle.loads(blob)\n", "py-pickle", 3),
    ("import yaml\n\ncfg = yaml.load(text)\n", "py-yaml-load", 3),
    ("cur.execute(f'select * from t where id = {i}')\n", "sql-string", 1),
    ("cur.execute('select * from t where id = ' + i)\n", "sql-string", 1),
    ("cur.execute('select * from t where id = %s' % i)\n", "sql-string", 1),
    ("cur.execute('select * from t where id = {}'.format(i))\n", "sql-string", 1),
    ("import requests\n\nrequests.get(url, verify=False)\n", "tls-verify-off", 3),
    ("import hashlib\n\nh = hashlib.md5(data)\n", "weak-hash", 3),
    ("import hashlib\n\nh = hashlib.sha1(data)\n", "weak-hash", 3),
    ("def f():\n    try:\n        g()\n    except:\n        pass\n", "bare-except", 4),
    ("def f():\n    try:\n        g()\n    except Exception:\n        pass\n", "swallowed-error", 4),
    ("import tempfile\n\np = tempfile.mktemp()\n", "py-mktemp", 3),
    ("app.run(debug=True)\n", "debug-on", 1),
])
def test_python_patterns_are_found_on_the_right_line(tmp_path, code, rule, line):
    found = one(tmp_path, code, rule)
    assert [f["line"] for f in found] == [line]
    f = found[0]
    assert f["path"] == "app.py" and f["title"] and f["why"] and f["fix"] and f["sev"] in ("high", "medium", "low")
    assert f["test"] is False and f["code"].strip()


@pytest.mark.parametrize("code,rule", [
    ("# eval(x) is dangerous\ns = 'exec(code)'\n", "py-eval"),
    ("s = 'exec(code)'\n", "py-exec"),
    ("import subprocess\n\nsubprocess.run(['ls', '-l'])\n", "shell-injection"),
    ("import subprocess\n\nsubprocess.run(cmd, shell=False)\n", "shell-injection"),
    ("import yaml\n\nyaml.safe_load(text)\n", "py-yaml-load"),
    ("import yaml\n\nyaml.load(text, Loader=yaml.SafeLoader)\n", "py-yaml-load"),
    ("cur.execute('select * from t where id = %s', (i,))\n", "sql-string"),
    ("cur.execute('select 1')\n", "sql-string"),
    ("import hashlib\n\nh = hashlib.sha256(data)\n", "weak-hash"),
    ("def f():\n    try:\n        g()\n    except ValueError:\n        pass\n", "swallowed-error"),
    ("def f():\n    try:\n        g()\n    except Exception as e:\n        log(e)\n", "swallowed-error"),
    ("import requests\n\nrequests.get(url, verify=True)\n", "tls-verify-off"),
    ("app.run(debug=False)\n", "debug-on"),
    ("model.evaluate(x)\nobj.exec_module(y)\n", "py-eval"),
])
def test_safe_look_alikes_are_left_alone(tmp_path, code, rule):
    assert one(tmp_path, code, rule) == []


def test_a_constant_shell_command_is_less_serious_than_a_built_one(tmp_path):
    h = scan(tmp_path, {"a.py": "import os\n\nos.system('clear')\nos.system('rm ' + name)\n"})
    assert [(f["line"], f["sev"]) for f in by_id(h, "shell-injection")] == [(4, "high"), (3, "medium")]


def test_a_file_that_does_not_parse_is_skipped_quietly_but_still_checked_for_secrets(tmp_path):
    h = scan(tmp_path, {"bad.py": "def (:\n    eval(x)\nKEY = 'AKIAABCDEFGHIJKLMNOP'\n"})
    assert by_id(h, "py-eval") == [] and len(by_id(h, "secret-token")) == 1


# ----------------------------------------------------------------- secrets
TOKENS = {
    "aws": "AKIAABCDEFGHIJKLMNOP",
    "github": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "slack": "xoxb-1234567890-abcdefghijkl",
    "stripe": "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc",
    "google": "AIza" + "SyA-1234567890abcdefghijklmnopqrstu",
}


@pytest.mark.parametrize("name", sorted(TOKENS))
def test_well_known_token_formats_are_found_and_masked(tmp_path, name):
    secret = TOKENS[name]
    h = scan(tmp_path, {"cfg.py": f"VALUE = '{secret}'\n"})
    found = by_id(h, "secret-token")
    assert len(found) == 1 and found[0]["sev"] == "high" and found[0]["line"] == 1
    assert secret not in json.dumps(found) and "…" in found[0]["code"]


def test_a_private_key_block_is_found(tmp_path):
    h = scan(tmp_path, {"k.txt": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n"})
    f = by_id(h, "secret-private-key")
    assert len(f) == 1 and f[0]["sev"] == "high" and f[0]["line"] == 1


@pytest.mark.parametrize("line", [
    'DB_PASSWORD = "s3cr3t-hunter42"',
    "api_key: 'k9x82hd7Qw1zz'",
    'const authToken = "abcd1234efgh5678";',
    'client_secret = "Zx81-qq0pAAbb"',
])
def test_hard_coded_passwords_and_keys_are_found(tmp_path, line):
    ext = "js" if line.startswith("const") else "py"
    h = scan(tmp_path, {f"c.{ext}": line + "\n"})
    f = by_id(h, "secret-assignment")
    assert len(f) == 1 and f[0]["sev"] == "medium"
    assert "hunter42" not in json.dumps(f) and "k9x82hd7Qw1zz" not in json.dumps(f)


@pytest.mark.parametrize("line", [
    'PASSWORD = os.environ["PASSWORD"]',
    'password = ""',
    'password = "changeme"',
    'API_KEY = "your-api-key-here"',
    'TOKEN = "<token>"',
    'secret = "${SECRET}"',
    'msg = "Enter your password to continue"',
    'TOKEN_URL = "https://example.com/oauth/token"',
    'token_header = "access_token"',
    'password_field = "password"',
    'password = "short"',
    'API_KEY = "xxxxxxxxxxxx"',
    'password = get_password()',
    'tokenizer = "gpt2-large-vocab"',
    'SESSION_TOKEN = "session_id_cookie"',
])
def test_placeholders_lookups_and_labels_are_not_secrets(tmp_path, line):
    h = scan(tmp_path, {"c.py": line + "\n"})
    assert by_id(h, "secret-assignment") == [] and by_id(h, "secret-token") == []


def test_secrets_in_tests_are_less_alarming_but_still_reported(tmp_path):
    h = scan(tmp_path, {"tests/test_a.py": f"def test_a():\n    key = '{TOKENS['aws']}'\n"})
    f = by_id(h, "secret-token")
    assert len(f) == 1 and f[0]["test"] is True and f[0]["sev"] == "low"


def test_minified_or_generated_lines_are_not_scanned(tmp_path):
    h = scan(tmp_path, {"bundle.py": "x = '" + "a" * 2500 + " " + TOKENS["aws"] + "'\n"})
    assert by_id(h, "secret-token") == []


def test_environment_files_that_hold_values_are_flagged_but_examples_are_not(tmp_path):
    h = scan(tmp_path, {".env": "DB_URL=postgres://u:p@h/db\nDEBUG=1\n", ".env.example": "DB_URL=postgres://user:pass@host/db\n", "app.py": "x = 1\n",
                        "certs/server.pem": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"})
    paths = {f["path"] for f in by_id(h, "secret-file")}
    assert ".env" in paths and ".env.example" not in paths
    assert "certs/server.pem" in {f["path"] for f in by_id(h, "secret-private-key")}


# ----------------------------------------------------------------- javascript
@pytest.mark.parametrize("code,rule", [
    ("const r = eval(input);\n", "js-eval"),
    ("const f = new Function('a', body);\n", "js-eval"),
    ("el.innerHTML = userText;\n", "js-inner-html"),
    ("el.outerHTML = userText;\n", "js-inner-html"),
    ("<div dangerouslySetInnerHTML={{ __html: html }} />;\n", "js-inner-html"),
    ("document.write(s);\n", "js-inner-html"),
    ("const { exec } = require('child_process');\nexec(`ls ${dir}`);\n", "shell-injection"),
    ("cp.execSync('rm ' + name);\n", "shell-injection"),
])
def test_javascript_patterns_are_found(tmp_path, code, rule):
    found = by_id(scan(tmp_path, {"a.js": code}), rule, "a.js")
    assert len(found) == 1 and found[0]["line"] == code.count("\n")


@pytest.mark.parametrize("code", [
    "// eval(x) would be bad\n",
    "/* el.innerHTML = x */\nconst a = 1;\n",
    " * document.write(s)\n",
    "el.textContent = userText;\n",
    "el.innerHTML = '';\n",
    "exec('ls');\n",
    "const evaluate = 1; medieval(x);\n",
])
def test_javascript_look_alikes_are_left_alone(tmp_path, code):
    h = scan(tmp_path, {"a.js": code})
    assert [f for f in h["findings"] if f["id"] in ("js-eval", "js-inner-html", "shell-injection")] == []


# ----------------------------------------------------------------- tests, ordering and size
def test_findings_in_test_files_are_marked_and_made_less_serious(tmp_path):
    h = scan(tmp_path, {"tests/test_a.py": "def test_a():\n    eval('1+1')\n"})
    f = by_id(h, "py-eval")
    assert len(f) == 1 and f[0]["test"] is True and f[0]["sev"] == "info"


def test_the_worst_findings_come_first_and_the_summary_adds_up(tmp_path):
    h = scan(tmp_path, {"a.py": "import hashlib\n\nhashlib.md5(b'')\neval(x)\n", "b.py": "app.run(debug=True)\n"})
    sevs = [f["sev"] for f in h["findings"]]
    assert sevs == sorted(sevs, key=["high", "medium", "low", "info"].index)
    s = h["summary"]
    assert s["total"] == len(h["findings"]) == s["high"] + s["medium"] + s["low"] + s["info"]
    assert s["high"] == 1 and s["medium"] == 1 and s["low"] == 1 and s["truncated"] is False


def test_a_flood_of_findings_is_capped_but_still_counted(tmp_path):
    h = scan(tmp_path, {f"m{i}.py": "eval(x)\n" for i in range(400)})
    assert len(h["findings"]) == 300 and h["summary"]["truncated"] is True and h["summary"]["total"] == 400


def test_one_noisy_file_cannot_crowd_out_the_rest(tmp_path):
    h = scan(tmp_path, {"noisy.py": "".join(f"eval(a{i})\n" for i in range(100)), "quiet.py": "eval(x)\n"})
    assert len(by_id(h, "py-eval", "noisy.py")) == 20 and len(by_id(h, "py-eval", "quiet.py")) == 1
    assert h["summary"]["total"] == 101 and h["summary"]["truncated"] is True and len(h["findings"]) == 21


# ----------------------------------------------------------------- import cycles
def test_files_that_import_each_other_in_a_circle_are_reported(tmp_path):
    h = scan(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": "from pkg import b\n\n\ndef fa():\n    return b.fb()\n",
                        "pkg/b.py": "from pkg import a\n\n\ndef fb():\n    return a.fa()\n", "pkg/c.py": "from pkg import a\n"})
    assert len(h["cycles"]) == 1
    assert sorted(h["cycles"][0]["files"]) == ["pkg/a.py", "pkg/b.py"] and h["cycles"][0]["benign"] is False
    f = by_id(h, "import-cycle")
    assert len(f) == 1 and f[0]["sev"] == "medium" and f[0]["path"] == "pkg/a.py"


def test_a_package_init_in_the_circle_is_only_a_hint(tmp_path):
    h = scan(tmp_path, {"pkg/__init__.py": "from pkg.a import fa\n\nVERSION = 1\n", "pkg/a.py": "from pkg import VERSION\n\n\ndef fa():\n    return VERSION\n"})
    cyc = h["cycles"]
    assert len(cyc) == 1 and cyc[0]["benign"] is True
    assert by_id(h, "import-cycle")[0]["sev"] == "info"


def test_no_cycles_means_no_cycle_findings(tmp_path):
    h = scan(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    assert h["cycles"] == [] and by_id(h, "import-cycle") == []


# ----------------------------------------------------------------- todos
def test_todo_markers_are_counted_per_file(tmp_path):
    h = scan(tmp_path, {"a.py": "# TODO: one\n# FIXME two\nx = 1  # HACK\n", "b.js": "// TODO later\n/* XXX */\n", "c.py": "x = 'TODO in a string'\n",
                        "README.md": "TODO write docs\n"})
    t = h["todos"]
    assert t["total"] == 5
    assert t["files"][0] == {"path": "a.py", "count": 3} and {"path": "b.js", "count": 2} in t["files"]
    assert all(f["path"] != "c.py" for f in t["files"])


# ----------------------------------------------------------------- the whole analysis
def test_a_clean_project_has_an_empty_report(tmp_path):
    h = scan(tmp_path, {"a.py": "def f(x):\n    return x + 1\n"})
    assert h["findings"] == [] and h["cycles"] == [] and h["todos"]["total"] == 0 and h["summary"]["total"] == 0


def test_the_report_carries_it_and_the_base_snapshot_of_a_review_does_not_need_it(repo):
    d = analyze_repo(repo, "demo", base="HEAD~1")
    assert "hygiene" in d and "tests" in d
    assert json.loads(json.dumps(d["hygiene"])) == d["hygiene"]
