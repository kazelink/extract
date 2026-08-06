from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import format_api_key_name, get_api_key
from .llm import LLMClient, RequestCancelledError
from .models import get_model_spec


logger = logging.getLogger(__name__)


def _describe(exc: Exception) -> str:
    """SDK 的连接类异常常常 str() 为空，只留类名也比一片空白强。"""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


class NonRetryableProcessingError(RuntimeError):
    """Raised when retrying the current row would not help."""


class RetryableProcessingError(RuntimeError):
    """Raised when retrying the current row may succeed."""


@dataclass(frozen=True)
class RunConfig:
    question: str
    input_cols: list[str] = field(default_factory=list)
    output_col: str = ""
    model_id: str = ""
    reasoning_effort: str = ""


@dataclass(frozen=True)
class RowProcessingResult:
    answer: str
    text_count: int
    reasoning: str = ""


_PLACEHOLDER_RE = re.compile(r"\{\{(.+?)\}\}")
_WHITESPACE_RE = re.compile(r"\s+")


def get_placeholder_columns(question: str) -> list[str]:
    """提示词中引用的列名（去首尾空白），用于按需取列。"""
    return [name.strip() for name in _PLACEHOLDER_RE.findall(question)]


def build_input_text(
    question: str,
    row_data: dict[str, str],
    input_cols: list[str],
) -> str:
    """拼装发给 LLM 的最终文本。

    提示词含 ``{{列名}}`` 占位符时按占位符替换；否则把选中列的值追加在提示词之后。
    """
    placeholders = _PLACEHOLDER_RE.findall(question)

    if placeholders:
        result = question
        for col in placeholders:
            key = col.strip()
            result = result.replace("{{" + col + "}}", row_data.get(key, ""))
        return result

    parts: list[str] = []
    for col in input_cols:
        value = row_data.get(col, "")
        if value:
            if len(input_cols) == 1:
                parts.append(value)
            else:
                parts.append(f"{col}: {value}")
    content = "\n".join(parts)

    if question.strip():
        return f"{question}\n\n{content}"
    return content


def validate_run_config(run_config: RunConfig) -> None:
    question = run_config.question.strip()
    if not question:
        raise NonRetryableProcessingError("提示词不能为空。")

    spec = get_model_spec(run_config.model_id)
    api_key_name = spec.api_key_name or spec.sdk
    if not get_api_key(api_key_name):
        raise NonRetryableProcessingError(
            f"缺少 API Key：{format_api_key_name(api_key_name)}。"
            "请在 config.local.json 中配置。"
        )


def process_input_value(
    llm: LLMClient,
    run_config: RunConfig,
    input_text: str,
    on_stream: Optional[Callable[[str], None]] = None,
    on_reasoning: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> RowProcessingResult:
    if not input_text.strip():
        raise NonRetryableProcessingError("输入内容为空")

    text_count = len(_WHITESPACE_RE.sub("", input_text))
    try:
        answer = llm.ask(
            input_text,
            run_config.model_id,
            on_stream=on_stream,
            on_reasoning=on_reasoning,
            should_stop=should_stop,
            reasoning_effort=run_config.reasoning_effort,
        )
    except RequestCancelledError:
        raise
    except Exception as exc:
        logger.warning("LLM call failed", exc_info=True)
        raise RetryableProcessingError(_describe(exc)) from exc

    reasoning = ""
    if isinstance(answer, tuple):
        reasoning, answer = answer

    final_answer = str(answer or "").strip()
    if not final_answer:
        raise RetryableProcessingError("模型返回内容为空")

    return RowProcessingResult(
        answer=final_answer,
        reasoning=str(reasoning or "").strip(),
        text_count=text_count,
    )
