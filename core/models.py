from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from .config import LOCAL_CONFIG_ERROR, LOCAL_CONFIG_PATH, get_local_config


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    label: str
    sdk: str = "openai"
    api_key_name: str = ""
    api_url: str = ""
    max_tokens: int | str | None = None
    temperature: float | None = None
    top_p: float | None = None
    extra_params: dict[str, Any] = field(default_factory=dict)


def _as_non_empty_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _as_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_optional_max_tokens(value: Any) -> int | str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                return int(float(text))
            except (TypeError, ValueError):
                return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_extra_params(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items() if v is not None}
    return {}


def _parse_model_entry(index: int, item: Any) -> tuple[ModelSpec | None, str | None]:
    prefix = f"MODELS[{index}]"
    if not isinstance(item, dict):
        return None, f"{prefix} must be an object."

    model_id = _as_non_empty_str(item.get("model_id"))
    if not model_id:
        return None, f"{prefix}.model_id is required."

    sdk = _as_non_empty_str(item.get("sdk"), "openai").lower()

    spec = ModelSpec(
        model_id=model_id,
        label=_as_non_empty_str(item.get("label"), model_id),
        sdk=sdk,
        api_key_name=_as_non_empty_str(item.get("api_key_name"), sdk),
        api_url=_as_non_empty_str(item.get("api_url")),
        max_tokens=_as_optional_max_tokens(item.get("max_tokens")),
        temperature=_as_optional_float(item.get("temperature")),
        top_p=_as_optional_float(item.get("top_p")),
        extra_params=_as_extra_params(item.get("extra_params")),
    )
    return spec, None


def _load_model_specs() -> tuple[list[ModelSpec], list[str]]:
    config = get_local_config()
    raw_models = config.get("MODELS")
    errors: list[str] = []
    specs: list[ModelSpec] = []
    seen_ids: set[str] = set()

    if LOCAL_CONFIG_ERROR:
        errors.append(LOCAL_CONFIG_ERROR)
        return specs, errors

    if not isinstance(raw_models, list) or not raw_models:
        if LOCAL_CONFIG_PATH.exists():
            errors.append(f"配置文件缺少非空的 MODELS 列表：{LOCAL_CONFIG_PATH}")
        else:
            errors.append(
                f"未找到配置文件：{LOCAL_CONFIG_PATH}\n"
                "  请把 config.local.json 放在程序同目录下。\n"
                "  可以复制随程序附带的 config.example.json，改名后填入自己的 API Key。"
            )
        return specs, errors

    for index, item in enumerate(raw_models):
        spec, error = _parse_model_entry(index, item)
        if error or spec is None:
            errors.append(error or f"MODELS[{index}] is invalid.")
            continue
        if spec.model_id in seen_ids:
            errors.append(f"MODELS[{index}].model_id {spec.model_id!r} is duplicated.")
            continue
        specs.append(spec)
        seen_ids.add(spec.model_id)

    default_model_id = _as_non_empty_str(config.get("DEFAULT_MODEL_ID"))
    if default_model_id and default_model_id not in seen_ids:
        errors.append("DEFAULT_MODEL_ID is not present in MODELS.")

    return specs, errors


MODEL_SPECS, _MODEL_CONFIG_ERRORS = _load_model_specs()
_MODEL_MAP: Dict[str, ModelSpec] = {spec.model_id: spec for spec in MODEL_SPECS}


def validate_model_configuration() -> None:
    from .llm import get_provider_info, get_supported_sdks

    errors: list[str] = list(_MODEL_CONFIG_ERRORS)
    supported = get_supported_sdks()
    for index, spec in enumerate(MODEL_SPECS):
        info = get_provider_info(spec.sdk)
        if info is None:
            errors.append(
                f"MODELS[{index}].sdk {spec.sdk!r} is not registered. "
                f"Registered SDKs: {supported}."
            )
            continue
        if info.requires_api_url and not spec.api_url:
            errors.append(f"MODELS[{index}].api_url is required for sdk={spec.sdk}.")

    if errors:
        joined = "\n".join(f"- {message}" for message in errors)
        raise RuntimeError(f"模型配置有误：\n{joined}")


def _resolve_default_model_id() -> str:
    configured = _as_non_empty_str(get_local_config().get("DEFAULT_MODEL_ID"))
    if configured and configured in _MODEL_MAP:
        return configured
    if MODEL_SPECS:
        return MODEL_SPECS[0].model_id
    return ""


DEFAULT_MODEL_ID = _resolve_default_model_id()


def get_model_spec(model_id: str | None) -> ModelSpec:
    if not _MODEL_MAP:
        raise RuntimeError("No models configured. Please add MODELS to config.local.json.")
    if not model_id:
        return _MODEL_MAP[DEFAULT_MODEL_ID]
    return _MODEL_MAP.get(model_id, _MODEL_MAP[DEFAULT_MODEL_ID])
