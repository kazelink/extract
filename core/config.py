from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT_DIR = _base_dir()
LOCAL_CONFIG_PATH = ROOT_DIR / "config.local.json"
LOG_FILE_PATH = ROOT_DIR / "extract.log"
CONFIG_FILE_ENCODING = "utf-8-sig"


def resource_path(name: str) -> Path:
    bundle = getattr(sys, "_MEIPASS", "")
    base = Path(bundle) if bundle else Path(__file__).resolve().parent.parent
    return base / name


def _read_local_config() -> tuple[dict[str, Any], str]:
    if not LOCAL_CONFIG_PATH.exists():
        return {}, ""
    try:
        data = json.loads(LOCAL_CONFIG_PATH.read_text(encoding=CONFIG_FILE_ENCODING))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load local config from %s", LOCAL_CONFIG_PATH, exc_info=True)
        return {}, f"读取配置文件失败：{LOCAL_CONFIG_PATH}\n  {exc}"
    if not isinstance(data, dict):
        return {}, f"配置文件的顶层必须是一个 JSON 对象：{LOCAL_CONFIG_PATH}"
    return data, ""


_LOCAL_CONFIG, LOCAL_CONFIG_ERROR = _read_local_config()


def get_local_config() -> dict[str, Any]:
    return _LOCAL_CONFIG


def _resolve_int(key: str, default: int) -> int:
    raw = os.getenv(key) or _LOCAL_CONFIG.get(key)
    if raw in (None, ""):
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("Invalid integer value for %s=%r. Falling back to %s.", key, raw, default)
        return default


def _resolve_api_keys() -> dict[str, str]:
    raw = _LOCAL_CONFIG.get("API_KEYS")
    api_keys: dict[str, str] = {}
    if not isinstance(raw, dict):
        return api_keys

    for name, value in raw.items():
        key_name = str(name).strip()
        if not key_name:
            continue
        env_value = os.getenv(f"API_KEY_{key_name}") or os.getenv(key_name)
        final_value = env_value if env_value not in (None, "") else ("" if value is None else str(value))
        api_keys[key_name] = final_value
    return api_keys


@dataclass
class Settings:
    api_keys: dict[str, str]
    llm_timeout: int
    llm_max_retries: int


def _clamp_int(key: str, value: int, low: int, high: int, default: int) -> int:
    if not low <= value <= high:
        logger.warning("%s=%s out of range [%s, %s]; using %s.", key, value, low, high, default)
        return default
    return value


_llm_timeout = _clamp_int("LLM_TIMEOUT", _resolve_int("LLM_TIMEOUT", 90), 1, 3600, 90)
_llm_max_retries = _clamp_int("LLM_MAX_RETRIES", _resolve_int("LLM_MAX_RETRIES", 2), 0, 20, 2)

settings = Settings(
    api_keys=_resolve_api_keys(),
    llm_timeout=_llm_timeout,
    llm_max_retries=_llm_max_retries,
)


def get_api_key(name: str) -> str:
    key_name = (name or "").strip()
    if not key_name:
        return ""
    return settings.api_keys.get(key_name, "")


def format_api_key_name(name: str) -> str:
    key_name = (name or "").strip()
    if not key_name:
        return "API Key"
    parts = key_name.replace("-", " ").replace("_", " ").split()
    formatted = []
    for part in parts:
        if len(part) <= 4:
            formatted.append(part.upper())
        else:
            formatted.append(part[:1].upper() + part[1:])
    return " ".join(formatted) or key_name
