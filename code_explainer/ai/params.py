"""User-tunable generation parameters: ranges, presets and per-provider mapping.

Ranges are enforced here (never trusted from the browser). Providers support different
subsets, so `SUPPORTED` tells the UI which controls to grey out.
"""

from __future__ import annotations

from dataclasses import dataclass

# name: (type, min, max, default, step, label, help)
SPECS: dict[str, tuple] = {
    "temperature": (float, 0.0, 2.0, 0.2, 0.05, "Temperature", "Lower is more focused and repeatable; higher is more varied."),
    "top_p": (float, 0.0, 1.0, 0.9, 0.05, "Top-p", "Only consider the smallest set of words whose probabilities add up to this."),
    "top_k": (int, 0, 200, 40, 1, "Top-k", "Only consider the k most likely next words (0 turns it off)."),
    "min_p": (float, 0.0, 1.0, 0.0, 0.01, "Min-p", "Drop words less likely than this fraction of the best one (0 turns it off)."),
    "max_tokens": (int, 16, 8192, 600, 16, "Max answer length", "The longest answer, in tokens (a token is about three-quarters of a word)."),
    "context_size": (int, 512, 131072, 4096, 512, "Context size", "How much text the model can read at once. Bigger needs more memory."),
    "repeat_penalty": (float, 0.8, 2.0, 1.1, 0.05, "Repeat penalty", "Above 1 discourages repeating itself."),
    "seed": (int, 0, 2**31 - 1, None, 1, "Seed", "A fixed number makes answers repeatable. Empty means random."),
}
LEVELS = ("kid", "plain", "tech")
LENGTHS = ("short", "normal", "detailed")
MAX_INSTRUCTION = 1000

PRESETS = {
    "precise": {"temperature": 0.1, "top_p": 0.8, "top_k": 20, "repeat_penalty": 1.05},
    "balanced": {"temperature": 0.2, "top_p": 0.9, "top_k": 40, "repeat_penalty": 1.1},
    "creative": {"temperature": 0.8, "top_p": 0.95, "top_k": 80, "repeat_penalty": 1.1},
}

# Which controls each provider can honour. LM Studio loads the context size when the model is
# loaded; a generic OpenAI-compatible server only reliably takes the standard fields.
SUPPORTED = {
    "ollama": {"temperature", "top_p", "top_k", "min_p", "max_tokens", "context_size", "repeat_penalty", "seed"},
    "lmstudio": {"temperature", "top_p", "top_k", "max_tokens", "context_size", "repeat_penalty", "seed"},
    "openai": {"temperature", "top_p", "max_tokens", "seed"},
}
# Controls whose value is applied when the model is loaded rather than on each request.
LOAD_TIME = {"lmstudio": {"context_size"}}


@dataclass
class GenParams:
    temperature: float = 0.2
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.0
    max_tokens: int = 600
    context_size: int = 4096
    repeat_penalty: float = 1.1
    seed: int | None = None
    level: str = "kid"
    length: str = "normal"
    instruction: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _coerce(name: str, value):
    typ, lo, hi, default, *_ = SPECS[name]
    if value is None or value == "" or isinstance(value, bool):
        return default
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if v != v or v in (float("inf"), float("-inf")):  # NaN / infinity
        return default
    v = min(max(v, lo), hi)
    return int(v) if typ is int else v


def clean_params(raw: object, base: GenParams | None = None) -> GenParams:
    """Merge user input over `base`, coercing and clamping everything."""
    out = GenParams(**(base.to_dict() if base else {}))
    if not isinstance(raw, dict):
        return out
    preset = raw.get("preset")
    if isinstance(preset, str) and preset in PRESETS:
        for k, v in PRESETS[preset].items():
            setattr(out, k, v)
    for name in SPECS:
        if name in raw:
            setattr(out, name, _coerce(name, raw[name]))
    if raw.get("level") in LEVELS:
        out.level = raw["level"]
    if raw.get("length") in LENGTHS:
        out.length = raw["length"]
    if isinstance(raw.get("instruction"), str):
        out.instruction = raw["instruction"].replace("\x00", "")[:MAX_INSTRUCTION].strip()
    return out


def to_provider_options(provider: str, p: GenParams, for_load: bool = False) -> dict:
    """The generation fields a provider should receive. Unsupported ones are left out
    rather than sent, because strict servers reject unknown fields."""
    ok = SUPPORTED.get(provider, SUPPORTED["openai"])
    load_only = LOAD_TIME.get(provider, set())
    vals = {k: getattr(p, k) for k in SPECS if k in ok and getattr(p, k) is not None}
    if provider == "ollama":
        mapping = {"max_tokens": "num_predict", "context_size": "num_ctx"}
        return {mapping.get(k, k): v for k, v in vals.items()}
    if for_load:
        return {k: v for k, v in vals.items() if k in load_only}
    return {k: v for k, v in vals.items() if k not in load_only}


def schema() -> dict:
    """Everything the settings panel needs to draw its controls."""
    return {
        "params": {n: {"type": "int" if s[0] is int else "float", "min": s[1], "max": s[2], "default": s[3],
                       "step": s[4], "label": s[5], "help": s[6]} for n, s in SPECS.items()},
        "presets": PRESETS, "levels": list(LEVELS), "lengths": list(LENGTHS),
        "supported": {k: sorted(v) for k, v in SUPPORTED.items()},
        "loadTime": {k: sorted(v) for k, v in LOAD_TIME.items()},
        "maxInstruction": MAX_INSTRUCTION,
    }
