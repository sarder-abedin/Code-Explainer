import json
import os
import stat

import pytest

from code_explainer.ai import params, settings
from code_explainer.ai.params import GenParams, clean_params, to_provider_options


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_EXPLAINER_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


# ----------------------------------------------------------------- parameters
def test_defaults_are_inside_their_own_ranges():
    d = GenParams().to_dict()
    for name, (typ, lo, hi, default, *_rest) in params.SPECS.items():
        if default is not None:
            assert lo <= d[name] <= hi and d[name] == default, name


@pytest.mark.parametrize("name, raw, expected", [
    ("temperature", 99, 2.0), ("temperature", -5, 0.0), ("temperature", "0.55", 0.55),
    ("top_p", 7, 1.0), ("top_k", 9999, 200), ("top_k", "12.9", 12), ("max_tokens", 1, 16),
    ("max_tokens", 10**9, 8192), ("context_size", 0, 512), ("repeat_penalty", 0.1, 0.8),
    ("seed", "42", 42), ("seed", "", None), ("seed", None, None), ("seed", -3, 0),
    # junk falls back to the default instead of raising or passing through
    ("temperature", "hot", 0.2), ("temperature", float("nan"), 0.2), ("temperature", float("inf"), 0.2),
    ("temperature", True, 0.2), ("top_k", [1], 40), ("top_k", {"a": 1}, 40),
])
def test_values_are_coerced_and_clamped(name, raw, expected):
    assert getattr(clean_params({name: raw}), name) == expected


def test_types_stay_correct_after_cleaning():
    p = clean_params({"top_k": "50.0", "max_tokens": 700.4, "temperature": 1, "seed": 3.0})
    assert isinstance(p.top_k, int) and isinstance(p.max_tokens, int) and isinstance(p.seed, int)
    assert isinstance(p.temperature, float)


def test_presets_then_explicit_values_win():
    assert clean_params({"preset": "creative"}).temperature == 0.8
    assert clean_params({"preset": "creative", "temperature": 0.3}).temperature == 0.3
    assert clean_params({"preset": "nope"}).temperature == 0.2
    base = GenParams(temperature=1.5)
    assert clean_params({"top_p": 0.5}, base).temperature == 1.5          # untouched fields survive a partial update


def test_level_length_and_instruction_are_validated():
    p = clean_params({"level": "plain", "length": "short", "instruction": "  be brief\x00!  "})
    assert (p.level, p.length, p.instruction) == ("plain", "short", "be brief!")
    p = clean_params({"level": "pirate", "length": "epic"})
    assert (p.level, p.length) == ("kid", "normal")
    assert len(clean_params({"instruction": "x" * 5000}).instruction) == params.MAX_INSTRUCTION
    assert clean_params("junk").to_dict() == GenParams().to_dict()


def test_provider_mapping_uses_each_providers_names_and_drops_unsupported():
    p = GenParams(seed=7)
    o = to_provider_options("ollama", p)
    assert o == {"temperature": 0.2, "top_p": 0.9, "top_k": 40, "min_p": 0.0, "num_predict": 600, "num_ctx": 4096,
                 "repeat_penalty": 1.1, "seed": 7}
    lm = to_provider_options("lmstudio", p)
    assert "context_size" not in lm and lm["max_tokens"] == 600 and "min_p" not in lm   # context is load-time there
    assert to_provider_options("lmstudio", p, for_load=True) == {"context_size": 4096}
    gen = to_provider_options("openai", p)
    assert set(gen) == {"temperature", "top_p", "max_tokens", "seed"}                  # strict servers reject extras
    assert "seed" not in to_provider_options("openai", GenParams())                    # unset seed is not sent


def test_schema_describes_every_control():
    s = params.schema()
    assert set(s["params"]) == set(params.SPECS) and s["presets"].keys() == {"precise", "balanced", "creative"}
    assert all(set(v) <= set(params.SPECS) for v in s["supported"].values())
    assert set(s["supported"]["openai"]) <= set(s["supported"]["ollama"])
    json.dumps(s)  # must be serialisable for the API


# ----------------------------------------------------------------- settings
def test_load_returns_defaults_when_missing_or_corrupt(home):
    assert settings.load() == settings.defaults()
    home.mkdir(parents=True)
    (home / "ai.json").write_text("{not json")
    assert settings.load() == settings.defaults()
    (home / "ai.json").write_text("[1, 2]")
    assert settings.load() == settings.defaults()


def test_save_roundtrip_is_private_and_atomic(home):
    s = settings.update({"params": {"temperature": 0.7}, "selected": {"provider": "ollama", "endpoint": "http://127.0.0.1:11434", "model": "llama3.2:3b"}})
    assert settings.load() == s and s["params"]["temperature"] == 0.7
    path = settings.settings_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert [p.name for p in home.iterdir()] == ["ai.json"]                  # no temp files left behind


def test_unknown_keys_and_bad_values_are_dropped():
    s = settings.clean_settings({"evil": 1, "autostart": "yes", "idle_minutes": "abc", "allow_remote": 1,
                                 "selected": {"provider": "skynet", "model": "x"}, "params": "nope"})
    assert "evil" not in s and s["autostart"] is True and s["idle_minutes"] == 10 and s["allow_remote"] is False
    assert s["selected"] == {"provider": "", "endpoint": "", "model": ""} and s["params"] == GenParams().to_dict()
    assert settings.clean_settings({"idle_minutes": 0})["idle_minutes"] == 1
    assert settings.clean_settings({"idle_minutes": 10**6})["idle_minutes"] == 240


def test_endpoints_are_validated_deduped_and_capped():
    eps = [{"provider": "openai", "url": "http://127.0.0.1:8080/"}, {"provider": "openai", "url": "http://127.0.0.1:8080"},
           {"provider": "openai", "url": "http://example.com"}, {"provider": "bogus", "url": "http://127.0.0.1:1"},
           {"provider": "ollama", "url": "http://u:p@127.0.0.1:1"}, "junk", {"provider": "lmstudio", "url": "http://localhost:1234", "name": "n" * 200}]
    out = settings.clean_settings({"endpoints": eps})["endpoints"]
    assert [(e["provider"], e["url"]) for e in out] == [("openai", "http://127.0.0.1:8080"), ("lmstudio", "http://localhost:1234")]
    assert len(out[1]["name"]) == 60
    many = [{"provider": "openai", "url": f"http://127.0.0.1:{9000 + i}"} for i in range(50)]
    assert len(settings.clean_settings({"endpoints": many})["endpoints"]) == settings.MAX_ENDPOINTS


def test_remote_endpoints_need_the_explicit_opt_in_in_the_same_update():
    remote = {"provider": "openai", "url": "http://my-box.lan:1234"}
    assert settings.clean_settings({"endpoints": [remote]})["endpoints"] == []
    s = settings.clean_settings({"allow_remote": True, "endpoints": [remote]})
    assert [e["url"] for e in s["endpoints"]] == ["http://my-box.lan:1234"]
    # turning the opt-in back off drops a remote selection instead of silently keeping it
    s["selected"] = {"provider": "openai", "endpoint": "http://my-box.lan:1234", "model": "m"}
    off = settings.clean_settings({"allow_remote": False, "endpoints": [remote]}, s)
    assert off["selected"]["endpoint"] == "" and off["endpoints"] == []


def test_secrets_are_referenced_by_env_var_name_only(monkeypatch):
    s = settings.clean_settings({"endpoints": [{"provider": "openai", "url": "http://127.0.0.1:8080", "api_key_env": "MY_KEY",
                                                "api_key": "sk-secret"}]})
    ep = s["endpoints"][0]
    assert ep["api_key_env"] == "MY_KEY" and "api_key" not in ep and "sk-secret" not in json.dumps(s)
    bad = settings.clean_settings({"endpoints": [{"provider": "openai", "url": "http://127.0.0.1:8080", "api_key_env": "has space; rm"}]})
    assert bad["endpoints"][0]["api_key_env"] == ""
    monkeypatch.setenv("MY_KEY", "tok")
    assert settings.api_key(ep) == "tok" and settings.api_key({"api_key_env": ""}) is None
    monkeypatch.delenv("MY_KEY")
    assert settings.api_key(ep) is None


def test_partial_update_keeps_other_settings():
    settings.update({"params": {"temperature": 0.9}, "autostart": False})
    s = settings.update({"params": {"top_k": 10}})
    assert s["params"]["temperature"] == 0.9 and s["params"]["top_k"] == 10 and s["autostart"] is False
