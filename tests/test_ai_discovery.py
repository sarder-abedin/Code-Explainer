import json
import os
import stat

import pytest

from ai_fakes import (LMS_LS_JSON, LMS_V1, OPENAI_MODELS, FakeServer, free_port, ollama_routes, write_ollama_models)
from code_explainer.ai import cli_tools, discovery, settings
from code_explainer.ai.http import ProviderError


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_EXPLAINER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))          # no real ~/.lmstudio/bin/lms
    monkeypatch.setenv("PATH", str(tmp_path / "emptybin"))          # no real ollama / lms either
    (tmp_path / "emptybin").mkdir()


@pytest.fixture
def serve():
    started = []

    def make(routes):
        s = FakeServer(routes)
        started.append(s)
        return s
    yield make
    for s in started:
        s.stop()


def fake_binary(directory, name, script):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


# ----------------------------------------------------------------- CLI parsing
def test_scan_ollama_store_reads_manifests(tmp_path):
    root = write_ollama_models(tmp_path / "store")
    ms = {m.id: m for m in cli_tools.scan_ollama_models(root)}
    assert set(ms) == {"llama3.2:3b", "nomic-embed-text:latest", "someuser/custom:v2", "hf.co/bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M"}
    assert ms["llama3.2:3b"].size_bytes == 2119 and ms["llama3.2:3b"].loaded is None and ms["llama3.2:3b"].provider == "ollama"
    assert ms["nomic-embed-text:latest"].kind == "embedding" and ms["llama3.2:3b"].kind == "llm"


def test_scan_ollama_store_survives_junk(tmp_path):
    assert cli_tools.scan_ollama_models(tmp_path / "missing") == []
    root = tmp_path / "store"
    (root / "manifests" / "registry.ollama.ai" / "library" / "bad").mkdir(parents=True)
    (root / "manifests" / "registry.ollama.ai" / "library" / "bad" / "latest").write_text("{not json")
    (root / "manifests" / "registry.ollama.ai" / "library" / "weird").mkdir()
    (root / "manifests" / "registry.ollama.ai" / "library" / "weird" / "latest").write_text('{"layers": "no"}')
    (root / "manifests" / "stray-file").write_text("{}")
    names = {m.id for m in cli_tools.scan_ollama_models(root)}
    assert names == {"bad:latest", "weird:latest"}                   # unreadable sizes become unknown, not a crash
    assert all(m.size_bytes is None for m in cli_tools.scan_ollama_models(root))


def test_ollama_store_location_honours_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "custom"))
    assert cli_tools.ollama_store_dirs()[0] == tmp_path / "custom"
    monkeypatch.delenv("OLLAMA_MODELS")
    assert cli_tools.ollama_store_dirs()[0] == tmp_path / "userhome" / ".ollama" / "models"


def test_parse_lms_json_shapes():
    ms = cli_tools.parse_lms_json(json.dumps(LMS_LS_JSON))
    assert ms[0].id == "qwen/qwen2.5-coder-7b" and ms[0].quant == "Q4_K_M" and ms[0].context_max == 32768
    assert ms[0].name == "Qwen2.5 Coder 7B" and ms[1].kind == "embedding"
    assert [m.id for m in cli_tools.parse_lms_json(json.dumps({"models": [{"path": "a/b.gguf"}]}))] == ["a/b.gguf"]
    with pytest.raises(ProviderError):
        cli_tools.parse_lms_json("not json")
    with pytest.raises(ProviderError):
        cli_tools.parse_lms_json('"just a string"')


def test_find_binary_checks_path_then_known_install_dirs(tmp_path, monkeypatch):
    assert cli_tools.find_binary("lms") is None
    lms = fake_binary(tmp_path / "userhome" / ".lmstudio" / "bin", "lms", "exit 0\n")
    assert cli_tools.find_binary("lms") == str(lms)
    on_path = fake_binary(tmp_path / "bin", "lms", "exit 0\n")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert cli_tools.find_binary("lms") == str(on_path)               # PATH wins


def test_lms_listing_runs_the_real_command_without_a_shell(tmp_path, monkeypatch):
    fake_binary(tmp_path / "bin", "lms", f"[ \"$1\" = ls ] && [ \"$2\" = --json ] && echo '{json.dumps(LMS_LS_JSON)}'\n")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert [m.id for m in cli_tools.list_lmstudio_cli()][:1] == ["qwen/qwen2.5-coder-7b"]


def test_lms_listing_errors_are_provider_errors(tmp_path, monkeypatch):
    with pytest.raises(ProviderError) as e:
        cli_tools.list_lmstudio_cli()
    assert e.value.kind == "unsupported"                             # not installed
    fake_binary(tmp_path / "bin", "lms", "echo boom >&2; exit 3\n")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    with pytest.raises(ProviderError) as e:
        cli_tools.list_lmstudio_cli()
    assert "boom" in str(e.value)
    fake_binary(tmp_path / "bin", "lms", "sleep 5\n")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin")  # the script needs `sleep`
    monkeypatch.setattr(cli_tools, "CLI_TIMEOUT", 0.3)
    with pytest.raises(ProviderError) as e:
        cli_tools.list_lmstudio_cli()
    assert e.value.kind == "timeout"


# ----------------------------------------------------------------- discovery
def test_discovers_running_providers_and_their_models(serve):
    o = serve(ollama_routes())
    l = serve({("GET", "/api/v1/models"): LMS_V1})
    g = serve({("GET", "/v1/models"): OPENAI_MODELS})
    st = settings.clean_settings({"endpoints": [
        {"provider": "ollama", "url": o.url}, {"provider": "lmstudio", "url": l.url, "name": "my lms"},
        {"provider": "openai", "url": g.url}]})
    found = discovery.discover(st, include_defaults=False)
    by = {(s["provider"]): s for s in found}
    assert set(by) == {"ollama", "lmstudio", "openai"} and all(s["reachable"] for s in found)
    assert by["ollama"]["version"] == "0.5.7" and by["ollama"]["modelsSource"] == "api"
    assert {m["id"] for m in by["ollama"]["models"]} == {"llama3.2:3b", "nomic-embed-text:latest", "qwen2.5-coder:7b"}
    assert by["lmstudio"]["name"] == "my lms" and by["lmstudio"]["source"] == "config"
    assert by["openai"]["models"][0]["provider"] == "openai"
    json.dumps(found)                                                # API-serialisable


def test_unreachable_endpoint_is_reported_not_raised():
    port = free_port()
    st = settings.clean_settings({"endpoints": [{"provider": "ollama", "url": f"http://127.0.0.1:{port}"}]})
    [s] = discovery.discover(st, include_defaults=False)
    assert s["reachable"] is False and s["errorKind"] == "unreachable" and s["models"] == [] and s["installed"] is False


def test_defaults_are_probed_and_deduplicated_against_configured_endpoints(serve, monkeypatch):
    o = serve(ollama_routes())
    monkeypatch.setattr(discovery, "DEFAULT_ENDPOINTS", [("ollama", o.url), ("lmstudio", f"http://127.0.0.1:{free_port()}")])
    st = settings.clean_settings({"endpoints": [{"provider": "ollama", "url": o.url}]})
    found = discovery.discover(st)
    assert [(s["provider"], s["reachable"], s["source"]) for s in found] == [("ollama", True, "config"), ("lmstudio", False, "default")]


def test_not_running_ollama_falls_back_to_the_model_store(tmp_path, monkeypatch):
    write_ollama_models(tmp_path / "store")
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "store"))
    fake_binary(tmp_path / "bin", "ollama", "exit 0\n")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(discovery, "DEFAULT_ENDPOINTS", [("ollama", f"http://127.0.0.1:{free_port()}")])
    [s] = discovery.discover(settings.defaults())
    assert s["reachable"] is False and s["installed"] is True and s["binary"].endswith("ollama")
    assert s["modelsSource"] == "disk" and len(s["models"]) == 4 and s["models"][0]["loaded"] is None


def test_not_running_lmstudio_falls_back_to_lms_ls(tmp_path, monkeypatch):
    fake_binary(tmp_path / "bin", "lms", f"[ \"$1\" = ls ] && echo '{json.dumps(LMS_LS_JSON)}'\n")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(discovery, "DEFAULT_ENDPOINTS", [("lmstudio", f"http://127.0.0.1:{free_port()}")])
    [s] = discovery.discover(settings.defaults())
    assert s["installed"] is True and s["modelsSource"] == "cli" and s["models"][0]["id"] == "qwen/qwen2.5-coder-7b"


def test_a_failing_fallback_is_reported_but_does_not_hide_the_main_error(tmp_path, monkeypatch):
    fake_binary(tmp_path / "bin", "lms", "exit 2\n")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(discovery, "DEFAULT_ENDPOINTS", [("lmstudio", f"http://127.0.0.1:{free_port()}")])
    [s] = discovery.discover(settings.defaults())
    assert s["reachable"] is False and s["errorKind"] == "unreachable" and s["models"] == [] and s["modelsSource"] == "none"


def test_offline_fallback_never_applies_to_remote_or_generic_endpoints(tmp_path, monkeypatch):
    write_ollama_models(tmp_path / "store")
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "store"))
    st = settings.clean_settings({"endpoints": [{"provider": "openai", "url": f"http://127.0.0.1:{free_port()}"}]})
    [s] = discovery.discover(st, include_defaults=False)
    assert s["modelsSource"] == "none" and s["models"] == []
    remote = settings.clean_settings({"allow_remote": True, "endpoints": [{"provider": "ollama", "url": "http://203.0.113.9:11434"}]})
    [r] = discovery.discover(remote, include_defaults=False, timeout=0.3)
    assert r["models"] == []                                         # this machine's disk says nothing about another host


def test_api_key_comes_from_the_environment_and_blocked_endpoints_do_not_crash(serve, monkeypatch):
    g = serve({("GET", "/v1/models"): OPENAI_MODELS})
    monkeypatch.setenv("BIONIC_KEY", "tok123")
    st = settings.clean_settings({"endpoints": [{"provider": "openai", "url": g.url, "api_key_env": "BIONIC_KEY"}]})
    discovery.discover(st, include_defaults=False)
    assert g.requests[0]["headers"]["Authorization"] == "Bearer tok123"
    # a hand-edited config that points at a remote host is never contacted
    bad = settings.defaults()
    bad["endpoints"] = [{"provider": "openai", "url": "http://203.0.113.9:1234", "name": "", "api_key_env": ""}]
    [s] = discovery.discover(bad, include_defaults=False)
    assert s["reachable"] is False and s["errorKind"] == "blocked"


def test_slow_servers_do_not_block_the_others(serve):
    from ai_fakes import slow
    fast = serve(ollama_routes())
    stuck = serve({("GET", "/api/tags"): slow(3)})
    st = settings.clean_settings({"endpoints": [{"provider": "ollama", "url": stuck.url}, {"provider": "ollama", "url": fast.url}]})
    import time
    t = time.time()
    found = discovery.discover(st, include_defaults=False, timeout=0.5)
    assert time.time() - t < 2.5
    assert [s["reachable"] for s in found] == [False, True] and found[0]["errorKind"] == "timeout"
