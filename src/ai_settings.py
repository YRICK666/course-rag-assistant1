import json
import os
from pathlib import Path
from typing import Any, Literal, TypedDict

from dotenv import load_dotenv

from retriever import PROJECT_ROOT


load_dotenv(PROJECT_ROOT / ".env")

ProviderName = Literal["deepseek", "openai_compatible"]
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
SETTINGS_PATH = PROJECT_ROOT / "config" / "ai_settings.local.json"


class AIProviderProfile(TypedDict):
    base_url: str
    model: str
    api_key: str


class AISettings(TypedDict):
    enabled: bool
    provider: ProviderName
    base_url: str
    model: str
    api_key: str
    profiles: dict[ProviderName, AIProviderProfile]


def _default_profiles() -> dict[ProviderName, AIProviderProfile]:
    return {
        "deepseek": {
            "base_url": os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            "model": os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            "api_key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
        },
        "openai_compatible": {
            "base_url": os.getenv("OPENAI_COMPATIBLE_BASE_URL", "").strip(),
            "model": os.getenv("OPENAI_COMPATIBLE_MODEL", "").strip(),
            "api_key": os.getenv("OPENAI_COMPATIBLE_API_KEY", "").strip(),
        },
    }


def _default_settings() -> AISettings:
    profiles = _default_profiles()
    active_provider: ProviderName = "deepseek"
    active_profile = profiles[active_provider]
    return {
        "enabled": os.getenv("AI_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"},
        "provider": active_provider,
        "base_url": active_profile["base_url"],
        "model": active_profile["model"],
        "api_key": active_profile["api_key"],
        "profiles": profiles,
    }


def _normalize_provider(value: Any) -> ProviderName:
    return "openai_compatible" if value == "openai_compatible" else "deepseek"


def _profile_defaults(provider: ProviderName) -> AIProviderProfile:
    defaults = _default_profiles()
    return dict(defaults[provider])  # type: ignore[return-value]


def _normalize_profile(provider: ProviderName, payload: Any, fallback: AIProviderProfile | None = None) -> AIProviderProfile:
    profile = dict(fallback or _profile_defaults(provider))  # type: ignore[assignment]
    if isinstance(payload, dict):
        if isinstance(payload.get("base_url"), str) and payload["base_url"].strip():
            profile["base_url"] = payload["base_url"].strip()
        if isinstance(payload.get("model"), str) and payload["model"].strip():
            profile["model"] = payload["model"].strip()
        if isinstance(payload.get("api_key"), str) and payload["api_key"].strip():
            profile["api_key"] = payload["api_key"].strip()
    if provider == "deepseek":
        profile["base_url"] = profile["base_url"].strip() or DEFAULT_BASE_URL
        profile["model"] = profile["model"].strip() or DEFAULT_MODEL
    else:
        profile["base_url"] = profile["base_url"].strip()
        profile["model"] = profile["model"].strip()
    profile["api_key"] = profile["api_key"].strip()
    return profile  # type: ignore[return-value]


def _attach_active_profile(settings: AISettings) -> AISettings:
    active_profile = settings["profiles"][settings["provider"]]
    settings["base_url"] = active_profile["base_url"]
    settings["model"] = active_profile["model"]
    settings["api_key"] = active_profile["api_key"]
    return settings


def load_ai_settings() -> AISettings:
    settings = _default_settings()
    if not SETTINGS_PATH.exists():
        return settings

    try:
        payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings

    if isinstance(payload.get("enabled"), bool):
        settings["enabled"] = payload["enabled"]
    settings["provider"] = _normalize_provider(payload.get("provider"))
    if isinstance(payload.get("profiles"), dict):
        raw_profiles = payload["profiles"]
        for provider in ("deepseek", "openai_compatible"):
            settings["profiles"][provider] = _normalize_profile(
                provider,
                raw_profiles.get(provider),
                settings["profiles"][provider],
            )

    legacy_profile_payload = {
        "base_url": payload.get("base_url"),
        "model": payload.get("model"),
        "api_key": payload.get("api_key"),
    }
    if any(isinstance(value, str) and value.strip() for value in legacy_profile_payload.values()):
        settings["profiles"][settings["provider"]] = _normalize_profile(
            settings["provider"],
            legacy_profile_payload,
            settings["profiles"][settings["provider"]],
        )

    return _attach_active_profile(settings)


def save_ai_settings(updates: dict[str, Any]) -> AISettings:
    current = load_ai_settings()
    next_provider = _normalize_provider(updates.get("provider", current["provider"]))
    next_profiles = {
        "deepseek": dict(current["profiles"]["deepseek"]),
        "openai_compatible": dict(current["profiles"]["openai_compatible"]),
    }
    updated_profile = dict(next_profiles[next_provider])
    if "base_url" in updates:
        updated_profile["base_url"] = str(updates.get("base_url") or updated_profile["base_url"]).strip()
    if "model" in updates:
        updated_profile["model"] = str(updates.get("model") or updated_profile["model"]).strip()
    if isinstance(updates.get("api_key"), str) and updates["api_key"].strip():
        updated_profile["api_key"] = updates["api_key"].strip()
    next_profiles[next_provider] = _normalize_profile(next_provider, updated_profile, next_profiles[next_provider])

    active_profile = next_profiles[next_provider]
    next_settings: AISettings = {
        "enabled": bool(updates.get("enabled", current["enabled"])),
        "provider": next_provider,
        "base_url": active_profile["base_url"],
        "model": active_profile["model"],
        "api_key": active_profile["api_key"],
        "profiles": next_profiles,  # type: ignore[typeddict-item]
    }

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(next_settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return next_settings


def public_ai_settings(settings: AISettings | None = None) -> dict[str, Any]:
    current = settings or load_ai_settings()
    return {
        "enabled": current["enabled"],
        "provider": current["provider"],
        "base_url": current["base_url"],
        "model": current["model"],
        "has_api_key": bool(current["api_key"]),
        "profiles": {
            provider: {
                "base_url": profile["base_url"],
                "model": profile["model"],
                "has_api_key": bool(profile["api_key"]),
            }
            for provider, profile in current["profiles"].items()
        },
    }
