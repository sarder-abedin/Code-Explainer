import json
import os
import time

import pytest

from code_explainer.ai.cache import ExplainCache

PARAMS = {"temperature": 0.2, "top_p": 0.9, "top_k": 40, "min_p": 0.0, "max_tokens": 600, "repeat_penalty": 1.1,
          "seed": None, "level": "kid", "length": "normal", "instruction": ""}


def key(**over):
    args = dict(kind="symbol", target="a.py::f", content="CODE", provider="ollama", model="m:7b", params=PARAMS, version=1)
    args.update(over)
    return ExplainCache.make_key(**args)


# ----------------------------------------------------------------- the key
def test_the_key_is_stable_hex():
    k = key()
    assert k == key() and len(k) == 64 and set(k) <= set("0123456789abcdef")


@pytest.mark.parametrize("change", [
    {"kind": "file"}, {"target": "a.py::g"}, {"content": "CODE changed"}, {"provider": "lmstudio"}, {"model": "other:3b"},
    {"version": 2},
    {"params": {**PARAMS, "temperature": 0.9}}, {"params": {**PARAMS, "top_p": 0.5}}, {"params": {**PARAMS, "top_k": 10}},
    {"params": {**PARAMS, "min_p": 0.1}}, {"params": {**PARAMS, "max_tokens": 100}},
    {"params": {**PARAMS, "repeat_penalty": 1.5}}, {"params": {**PARAMS, "seed": 7}}, {"params": {**PARAMS, "level": "tech"}},
    {"params": {**PARAMS, "length": "short"}}, {"params": {**PARAMS, "instruction": "In French."}},
])
def test_anything_that_changes_the_answer_changes_the_key(change):
    assert key(**change) != key()


def test_key_does_not_depend_on_dict_order_or_irrelevant_fields():
    shuffled = dict(reversed(list(PARAMS.items())))
    assert key(params=shuffled) == key()
    assert key(params={**PARAMS, "unrelated": "x"}) == key()       # only output-affecting fields count


def test_fields_cannot_be_confused_by_moving_text_between_them():
    assert key(kind="ab", target="c") != key(kind="a", target="bc")
    assert key(content="a", model="bc") != key(content="ab", model="c")


# ----------------------------------------------------------------- storage
@pytest.fixture
def cache(tmp_path):
    return ExplainCache(tmp_path / "cache", max_entries=5)


ENTRY = {"text": "It adds things.", "stats": {"completion_tokens": 4}, "model": "m:7b", "provider": "ollama",
         "sources": [{"path": "a.py", "line": 1, "end": 3, "label": "f", "reason": "x"}], "options": {"temperature": 0.2}}


def test_roundtrip_and_miss(cache):
    k = key()
    assert cache.get(k) is None
    assert cache.put(k, ENTRY) is True
    got = cache.get(k)
    assert got["text"] == "It adds things." and got["stats"] == {"completion_tokens": 4} and got["sources"][0]["path"] == "a.py"
    assert got["created"] > 0


def test_files_are_private_and_the_directory_is_created_on_demand(cache, tmp_path):
    k = key()
    cache.put(k, ENTRY)
    files = [p for p in (tmp_path / "cache").rglob("*.json")]
    assert len(files) == 1 and (os.stat(files[0]).st_mode & 0o777) == 0o600
    assert [p for p in (tmp_path / "cache").rglob("*.tmp")] == []


@pytest.mark.parametrize("bad", ["", "../../etc/passwd", "a/b", "G" * 64, "abc", "a" * 63, "A" * 64, "x\x00y", None, 5])
def test_keys_that_are_not_sha256_hex_never_touch_the_disk(cache, tmp_path, bad):
    assert cache.get(bad) is None
    assert cache.put(bad, ENTRY) is False
    assert not (tmp_path / "cache").exists() or list((tmp_path / "cache").rglob("*.json")) == []


def test_corrupt_or_wrong_shaped_files_are_a_miss_and_are_removed(cache, tmp_path):
    for i, content in enumerate(["{not json", "[]", '{"text": 5}', '{"no_text": true}', "", "\x00\x01"]):
        k = key(target=f"t{i}")
        cache.put(k, ENTRY)
        path = next(p for p in (tmp_path / "cache").rglob(f"{k}.json"))
        path.write_text(content)
        assert cache.get(k) is None
        assert not path.exists()


def test_only_the_expected_fields_are_stored_and_returned(cache):
    k = key()
    cache.put(k, {**ENTRY, "messages": [{"role": "system", "content": "SECRET SOURCE CODE"}], "evil": "x"})
    got = cache.get(k)
    assert "messages" not in got and "evil" not in got                          # the prompt contains source code; never persisted


def test_oversized_entries_are_refused(tmp_path):
    c = ExplainCache(tmp_path / "c", max_entry_bytes=1000)
    assert c.put(key(), {**ENTRY, "text": "x" * 5000}) is False and c.stats() == {"entries": 0, "bytes": 0}


def test_the_oldest_entries_are_evicted_when_over_the_limit(cache):
    keys = [key(target=f"t{i}") for i in range(8)]
    for i, k in enumerate(keys):
        cache.put(k, {**ENTRY, "text": f"answer {i}"})
        time.sleep(0.01)
    assert cache.stats()["entries"] == 5
    assert [cache.get(k) is not None for k in keys] == [False, False, False, True, True, True, True, True]


def test_reading_an_entry_keeps_it_alive_longer(cache):
    keys = [key(target=f"t{i}") for i in range(5)]
    for k in keys:
        cache.put(k, ENTRY)
        time.sleep(0.01)
    time.sleep(0.02)
    assert cache.get(keys[0])                                                    # touch the oldest
    cache.put(key(target="new"), ENTRY)
    assert cache.get(keys[0]) is not None and cache.get(keys[1]) is None


def test_overwriting_replaces_the_text(cache):
    k = key()
    cache.put(k, ENTRY)
    cache.put(k, {**ENTRY, "text": "second answer"})
    assert cache.get(k)["text"] == "second answer" and cache.stats()["entries"] == 1


def test_stats_and_clear(cache):
    for i in range(3):
        cache.put(key(target=f"t{i}"), ENTRY)
    st = cache.stats()
    assert st["entries"] == 3 and st["bytes"] > 0
    assert cache.clear() == 3 and cache.stats() == {"entries": 0, "bytes": 0} and cache.clear() == 0


def test_a_missing_or_unwritable_directory_never_raises(tmp_path):
    c = ExplainCache(tmp_path / "does" / "not" / "exist")
    assert c.get(key()) is None and c.stats() == {"entries": 0, "bytes": 0} and c.clear() == 0
    blocked = tmp_path / "file"
    blocked.write_text("i am a file, not a directory")
    assert ExplainCache(blocked / "sub").put(key(), ENTRY) is False


def test_concurrent_writers_never_leave_a_torn_file(cache, tmp_path):
    import threading
    k = key()
    threads = [threading.Thread(target=lambda i=i: cache.put(k, {**ENTRY, "text": f"writer {i} " + "z" * 2000})) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = cache.get(k)
    assert got is not None and got["text"].startswith("writer ") and got["text"].endswith("z" * 2000)
    assert json.loads(next((tmp_path / "cache").rglob(f"{k}.json")).read_text())["text"] == got["text"]
