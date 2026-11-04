"""Claude models the dashboard can use, and their prices (USD per million tokens) for the cost shown per change.
Prices are Anthropic's list prices as of PRICES_AS_OF — update this table when Anthropic changes them."""
from decimal import Decimal

PRICES_AS_OF = "2026-10-06"
DEFAULT_MODEL = "claude-opus-5-5"

# model id: (label, input, output, cache read)   — cache writes cost 1.25 × input (5-minute cache)
MODELS = {
    "claude-opus-5-5": ("Claude Opus 5.5 — best for code", "4.00", "20.00", "0.20"),
    "claude-sonnet-5-5": ("Claude Sonnet 5.5 — cheaper, fast", "2.00", "10.00", "0.20"),
    "claude-haiku-5-5": ("Claude Haiku 5.5 — cheapest", "0.10", "0.50", "0.01"),   # ≤100K-token prompts; longer: $0.50 / $2.50
    "claude-fable-5-1": ("Claude Fable 5.1 — most capable, most expensive", "10.00", "50.00", "0.25"),
}
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
# Haiku has no server-side refusal fallback — only these models get `fallbacks: "default"`
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"}

# What the person picks per request in the chat (owner's choice to trade quality for cost, 2026-10-14)
PRESETS = {
    "quick": ("Quick", "small text / colour / wording changes", "claude-haiku-5-5", "low"),
    "normal": ("Normal", "most changes", "claude-sonnet-5-5", "medium"),
    "best": ("Best", "bigger or tricky changes", "claude-opus-5-5", "high"),
}
DEFAULT_PRESET = "normal"


def preset(key):
    return PRESETS.get(key) or PRESETS[DEFAULT_PRESET]


def cost_usd(model, usage):
    """Cost of one API call from its usage block (input, output, cache write, cache read)."""
    _, p_in, p_out, p_cache = MODELS.get(model, MODELS[DEFAULT_MODEL])
    p_in, p_out, p_cache = Decimal(p_in), Decimal(p_out), Decimal(p_cache)
    million = Decimal(1_000_000)
    inp = Decimal(getattr(usage, "input_tokens", 0) or 0)
    out = Decimal(getattr(usage, "output_tokens", 0) or 0)
    c_write = Decimal(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    c_read = Decimal(getattr(usage, "cache_read_input_tokens", 0) or 0)
    total = (inp * p_in + out * p_out + c_write * p_in * Decimal("1.25") + c_read * p_cache) / million
    return total.quantize(Decimal("0.0001"))
