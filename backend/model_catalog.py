"""Curated model choices for the two user-facing execution tiers."""

from __future__ import annotations

from typing import Any


def _model(provider: str, model: str, name: str, badge: str, description: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "name": name,
        "badge": badge,
        "description": description,
    }


FAST_KILO_MODELS = [
    "kilo-auto/free",
    "poolside/laguna-s-2.1:free",
    "poolside/laguna-xs-2.1:free",
    "inclusionai/ling-3.0-flash-sante:free",
    "inclusionai/ling-3.0-flash-fin:free",
    "liquid/lfm-2.5-2.6b:free",
    "cohere/north-mini-code:free",
    "stepfun/step-3.7-flash:free",
    "nex-agi/nex-n2.5-mini:free",
    "thinkingmachines/inkling-small:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

COMPLEX_KILO_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nex-agi/nex-n2.5-pro:free",
    "thinkingmachines/inkling:free",
    "dots-studio/dots-3-note-preview:free",
    "stepfun/step-3.7-flash:free",
    "cohere/north-mini-code:free",
    "openrouter/free",
    "kilo-auto/free",
]


FAST_MODELS = [
    _model("free_pool", "fast-auto", "Auto Fast", "Free cloud pool", "Routes across the fastest available free cloud models."),
    _model("kilo", "kilo-auto/free", "Kilo Auto Free", "Automatic", "Kilo chooses an available free model."),
    _model("kilo", "poolside/laguna-s-2.1:free", "Poolside Laguna S 2.1", "Fast code", "Low-latency coding and repository questions."),
    _model("kilo", "poolside/laguna-xs-2.1:free", "Poolside Laguna XS 2.1", "Fast code", "Small coding model for short edits and explanations."),
    _model("kilo", "inclusionai/ling-3.0-flash-sante:free", "Ling 3.0 Flash Sante", "Fast", "Quick instruction following and edits."),
    _model("kilo", "inclusionai/ling-3.0-flash-fin:free", "Ling 3.0 Flash Fin", "Fast", "Quick structured answers and analysis."),
    _model("kilo", "liquid/lfm-2.5-2.6b:free", "Liquid LFM 2.5 2.6B", "Lightweight", "Short answers and simple command assistance."),
    _model("kilo", "cohere/north-mini-code:free", "Cohere North Mini Code", "Code", "Compact software-engineering model."),
    _model("kilo", "stepfun/step-3.7-flash:free", "StepFun 3.7 Flash", "Reasoning", "Fast multi-step reasoning."),
    _model("kilo", "nex-agi/nex-n2.5-mini:free", "Nex N2.5 Mini", "Fast", "Quick general and coding work."),
    _model("kilo", "thinkingmachines/inkling-small:free", "Inkling Small", "Fast", "Compact reasoning model."),
    _model("kilo", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "Nemotron Nano Omni 30B", "Reasoning", "Efficient reasoning for bounded tasks."),
    _model("pollinations", "openai-fast", "Pollinations OpenAI Fast", "Keyless", "Public keyless model for quick replies."),
]

COMPLEX_MODELS = [
    _model("free_pool", "complex-auto", "Auto Complex", "Free cloud pool", "Routes across higher-capability free cloud models with failover."),
    _model("kilo", "kilo-auto/free", "Kilo Auto Free", "Automatic", "Kilo chooses an available free model."),
    _model("kilo", "nvidia/nemotron-3-super-120b-a12b:free", "Nemotron 3 Super 120B", "Large model", "Architecture, refactoring, and deeper reasoning."),
    _model("kilo", "nvidia/nemotron-3-ultra-550b-a55b:free", "Nemotron 3 Ultra 550B", "Heavy reasoning", "High-capability analysis where latency is secondary."),
    _model("kilo", "nvidia/nemotron-3.5-lightning:free", "Nemotron 3.5 Lightning", "Long context", "Repository-scale analysis and synthesis."),
    _model("kilo", "nex-agi/nex-n2.5-pro:free", "Nex N2.5 Pro", "Deep work", "Long-form implementation and review."),
    _model("kilo", "thinkingmachines/inkling:free", "Inkling", "Deep work", "Reasoning-heavy tasks and reports."),
    _model("kilo", "dots-studio/dots-3-note-preview:free", "Dots 3 Note Preview", "Preview", "Long-context preview model; select explicitly when experimentation is acceptable."),
    _model("kilo", "stepfun/step-3.7-flash:free", "StepFun 3.7 Flash", "Reasoning", "Multi-step reasoning with lower latency."),
    _model("kilo", "cohere/north-mini-code:free", "Cohere North Mini Code", "Code", "Codebase analysis and implementation planning."),
    _model("kilo", "openrouter/free", "OpenRouter Free", "Multi-model", "Routes through OpenRouter's available free models."),
    _model("aihorde", "koboldcpp/Mistral-Nemo-12B-Instruct", "AI Horde Mistral Nemo 12B", "Community", "Anonymous community-grid fallback."),
]


def build_model_catalog(cfg: dict, local_models: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    providers = cfg.get("providers", {})
    fast = [dict(item, tier="fast") for item in FAST_MODELS]
    complex_models = [dict(item, tier="complex") for item in COMPLEX_MODELS]

    configured = {
        "gemini": ("Google Gemini", "Cloud free tier"),
        "cerebras": ("Cerebras", "Cloud free tier"),
        "groq": ("Groq", "Cloud free tier"),
        "openrouter": ("OpenRouter", "Cloud"),
        "openai": ("OpenAI", "Cloud"),
        "anthropic": ("Anthropic", "Cloud"),
    }
    for provider, (label, badge) in configured.items():
        profile = providers.get(provider, {})
        if profile.get("enabled") and profile.get("api_key") and profile.get("model"):
            entry = _model(provider, profile["model"], f"{label} · {profile['model']}", badge, "Configured provider model.")
            target = fast if provider in {"cerebras", "groq"} else complex_models
            target.append(dict(entry, tier="fast" if target is fast else "complex"))

    for custom in cfg.get("custom_providers", []):
        if custom.get("enabled") and custom.get("model"):
            entry = _model(f"custom_{custom['id']}", custom["model"], f"{custom.get('name', 'Custom')} · {custom['model']}", "Custom", "Configured custom provider.")
            fast.append(dict(entry, tier="fast"))
            complex_models.append(dict(entry, tier="complex"))

    for local in local_models:
        entry = _model("ollama", local["name"], f"Ollama · {local['name']}", "Local", "Runs privately on this machine.")
        fast.append(dict(entry, tier="fast"))
        complex_models.append(dict(entry, tier="complex"))
    return fast, complex_models
