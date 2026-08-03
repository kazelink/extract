from __future__ import annotations

import pandas as pd


_OUTPUT_COLUMN_KEYWORDS = ("output", "result", "answer", "reply", "输出", "结果", "答案", "回复")


def is_valid_input(value: str) -> bool:
    return value.strip() != ""


def format_cell(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def guess_output_column(columns: list[str]) -> str | None:
    for col in columns:
        lower = str(col).strip().lower()
        if any(keyword in lower for keyword in _OUTPUT_COLUMN_KEYWORDS):
            return str(col)
    return None


def drop_unnamed_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """丢掉 Excel/CSV 引擎带进来的空白 unnamed 列。"""
    drop_cols = []
    index_series = None
    for col in df.columns:
        col_str = str(col).strip()
        if not col_str or col_str.lower().startswith("unnamed"):
            series = df[col]
            if series.isna().all():
                drop_cols.append(col)
                continue
            ser_num = pd.to_numeric(series, errors="coerce")
            if ser_num.notna().all():
                if index_series is None:
                    index_series = pd.Series(range(len(df)), index=df.index)
                if ser_num.reset_index(drop=True).equals(index_series.reset_index(drop=True)):
                    drop_cols.append(col)
                    continue
            if series.astype(str).str.strip().eq("").all():
                drop_cols.append(col)
    if drop_cols:
        df = df.drop(columns=drop_cols)
    return df, [str(c) for c in drop_cols]


def dedupe_columns(columns: list[str]) -> list[str]:
    """重名列追加 _1/_2 后缀，避免 df.at 因重复列名返回 Series 导致界面崩溃。"""
    counts: dict[str, int] = {}
    used: set[str] = set()
    result: list[str] = []
    for col in columns:
        n = counts.get(col, 0) + 1
        counts[col] = n
        k = n - 1
        name = col if n == 1 else f"{col}_{k}"
        while name in used:
            k += 1
            name = f"{col}_{k}"
        used.add(name)
        result.append(name)
    return result
