from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional

from openai import OpenAI

from . import config
from .models import ModelSpec, get_model_spec


class RequestCancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderInfo:

    name: str
    requires_api_url: bool = False


class BaseProvider(ABC):
    def __init__(self, *, timeout: float, max_retries: int) -> None:
        self._timeout = timeout
        self._max_retries = max_retries

    @abstractmethod
    def ask(
        self,
        prompt: str,
        spec: ModelSpec,
        on_stream: Optional[Callable] = None,
        on_reasoning: Optional[Callable] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        reasoning_effort: Optional[str] = None,
    ) -> str | tuple[str, str]:
        ...


_PROVIDER_INFO: dict[str, ProviderInfo] = {}
_PROVIDER_FACTORIES: dict[str, type[BaseProvider]] = {}


def register_provider(info: ProviderInfo, factory: type[BaseProvider]) -> None:
    _PROVIDER_INFO[info.name] = info
    _PROVIDER_FACTORIES[info.name] = factory


def get_provider_info(name: str) -> ProviderInfo | None:
    return _PROVIDER_INFO.get(name)


def get_supported_sdks() -> list[str]:
    return sorted(_PROVIDER_INFO.keys())


class OpenAICompatibleProvider(BaseProvider):
    def __init__(self, *, timeout: float, max_retries: int) -> None:
        super().__init__(timeout=timeout, max_retries=max_retries)
        self._client: Optional[OpenAI] = None
        self._client_params: tuple | None = None

    def _get_client(self, api_url: str, api_key: str) -> OpenAI:
        params = (api_url, api_key, self._timeout, self._max_retries)
        if self._client is None or self._client_params != params:
            self._client = OpenAI(
                base_url=api_url,
                api_key=api_key,
                timeout=self._timeout,
                max_retries=self._max_retries,
            )
            self._client_params = params
        return self._client

    def ask(
        self,
        prompt: str,
        spec: ModelSpec,
        on_stream: Optional[Callable] = None,
        on_reasoning: Optional[Callable] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        reasoning_effort: Optional[str] = None,
    ) -> str | tuple[str, str]:
        api_key_name = spec.api_key_name or spec.sdk
        api_key = config.get_api_key(api_key_name)
        if not api_key:
            raise RuntimeError(
                f"缺少 API Key：{config.format_api_key_name(api_key_name)}。"
                "请在 config.local.json 中配置。"
            )

        client = self._get_client(api_url=spec.api_url, api_key=api_key)

        params = {
            "model": spec.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": self._timeout,
        }
        if spec.temperature is not None:
            params["temperature"] = spec.temperature
        if spec.top_p is not None:
            params["top_p"] = spec.top_p
        if spec.max_tokens is not None:
            params["max_tokens"] = spec.max_tokens
        if spec.extra_params:
            for key, value in spec.extra_params.items():
                params.setdefault(key, value)
        if reasoning_effort:
            params["reasoning_effort"] = reasoning_effort

        if on_stream:
            params["stream"] = True
            return self._handle_stream(client, params, on_stream, on_reasoning, should_stop)
        return self._handle_sync(client, params)

    def _handle_stream(
        self,
        client: OpenAI,
        params: dict,
        on_stream: Callable,
        on_reasoning: Optional[Callable],
        should_stop: Optional[Callable[[], bool]],
    ) -> str:
        parts = []
        stream = client.chat.completions.create(**params)
        try:
            for chunk in stream:
                if should_stop and should_stop():
                    raise RequestCancelledError("用户已停止")
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                reasoning = self._extract_reasoning(delta)
                if reasoning:
                    if on_reasoning:
                        on_reasoning(reasoning)
                    else:
                        on_stream(reasoning)
                if isinstance(delta.content, str) and delta.content:
                    parts.append(delta.content)
                    on_stream(delta.content)
        finally:
            stream.close()
        return "".join(parts).strip()

    def _handle_sync(self, client: OpenAI, params: dict) -> str | tuple[str, str]:
        resp = client.chat.completions.create(**params)
        if not resp.choices:
            return ""
        msg = resp.choices[0].message
        reasoning = self._extract_reasoning(msg)
        content = (msg.content or "").strip()
        return (reasoning, content) if reasoning else content

    @staticmethod
    def _extract_reasoning(obj: Any) -> str:
        reasoning = getattr(obj, "reasoning_content", None)
        if reasoning is None and hasattr(obj, "model_extra"):
            reasoning = (obj.model_extra or {}).get("reasoning_content")
        return str(reasoning) if reasoning else ""


register_provider(
    ProviderInfo(name="openai", requires_api_url=True),
    OpenAICompatibleProvider,
)


class LLMClient:
    def __init__(self):
        timeout = config.settings.llm_timeout
        max_retries = config.settings.llm_max_retries
        self._providers: dict[str, BaseProvider] = {
            name: cls(timeout=timeout, max_retries=max_retries)
            for name, cls in _PROVIDER_FACTORIES.items()
        }

    def ask(
        self,
        prompt: str,
        model_id: Optional[str] = None,
        on_stream: Optional[Callable] = None,
        on_reasoning: Optional[Callable] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        reasoning_effort: Optional[str] = None,
    ) -> str | tuple[str, str]:
        spec = get_model_spec(model_id)
        provider = self._providers.get(spec.sdk)
        if provider is None:
            supported = ", ".join(get_supported_sdks()) or "(无)"
            raise RuntimeError(
                f"不支持的 SDK：{spec.sdk!r}。已注册的 SDK：{supported}。"
            )
        return provider.ask(
            prompt,
            spec,
            on_stream=on_stream,
            on_reasoning=on_reasoning,
            should_stop=should_stop,
            reasoning_effort=reasoning_effort,
        )
