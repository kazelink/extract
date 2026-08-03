from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox

import pandas as pd

from core import config as core_config
from core.execution import (
    NonRetryableProcessingError,
    RetryableProcessingError,
    RunConfig,
    build_input_text,
    get_placeholder_columns,
    process_input_value,
    validate_run_config,
)
from core.io import read_data_file, sweep_stale_temp_files, write_data_file
from core.llm import LLMClient, RequestCancelledError
from core.models import DEFAULT_MODEL_ID, MODEL_SPECS
from core.utils import (
    dedupe_columns,
    drop_unnamed_columns,
    format_cell,
    guess_output_column,
    is_valid_input,
)
from .app_view import Actions, AppView
from .state import RunState

logger = logging.getLogger(__name__)

RUN_START_MESSAGE = ">>> 开始运行 <<<\n"

# 表里没有现成的输出列时，预填这个名字，运行时自动建列
DEFAULT_OUTPUT_COLUMN = "提取结果"

# 大表格自动保存节流：逐行整文件重写太慢，攒够行数或时间再落盘一次
_AUTOSAVE_ROW_INTERVAL = 20
_AUTOSAVE_TIME_INTERVAL = 5.0
_LARGE_BATCH_ROWS = 500

_FILTER_DESC = {"nonempty": "非空", "empty": "为空", "contains": "包含", "not_contains": "不包含"}
_TEXT_CONDITIONS = {"contains", "not_contains"}


@dataclass(frozen=True)
class BatchItem:
    idx: int
    input_text: str


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


class App:
    def __init__(self, root) -> None:
        self.root = root
        self.view = AppView(root, MODEL_SPECS, DEFAULT_MODEL_ID)
        self.view.bind_actions(
            Actions(
                on_open=self._load_file,
                on_load_prompt=self._load_prompt_config,
                on_save_prompt=self._save_prompt_config,
                on_run=self._on_run_clicked,
                on_stop=self._request_stop,
                on_model_change=self._on_model_change,
                on_prompt_parts_change=self._on_prompt_parts_change,
                on_tree_select=self._on_tree_select,
                on_filter_apply=self._apply_filter,
                on_filter_reset=self._reset_filter,
                on_close=self._on_close,
            )
        )
        self.df: pd.DataFrame | None = None
        self.file_path: str | None = None
        self.file_encoding = "utf-8-sig"
        self.columns: list[str] = []
        self.display_columns: list[str] = []
        self.input_cols: list[str] = []
        self.output_col = "Answer"
        self.model_id = DEFAULT_MODEL_ID
        # None = 未筛选；否则是当前可见（也就是允许运行）的行号
        self.filtered_indices: list[int] | None = None
        self.run_state = RunState()
        self.llm = LLMClient()
        self._closing = False
        self.view.set_idle_state(False)

    def _column_width(self, col_name: str) -> int:
        lower = str(col_name).lower()
        if "content" in lower or "text" in lower:
            return 260
        return 160

    def _row_values(self, idx: int) -> list[str]:
        if self.df is None:
            return []
        return [format_cell(self.df.at[idx, col]) for col in self.display_columns]

    def _all_row_values(self) -> list[list[str]]:
        """整表一次性向量化取展示字符串，替代逐格 df.at（大表下这是主要卡顿源）。"""
        if self.df is None or not self.display_columns:
            return []
        block = self.df.loc[:, self.display_columns].fillna("").astype(str)
        return block.values.tolist()

    def _rebuild_tree_columns(self) -> None:
        if self.df is None:
            return
        self.display_columns = list(self.df.columns)
        self.view.configure_tree_columns(self.display_columns, self._column_width)

    def _visible_indices(self) -> list[int]:
        if self.df is None:
            return []
        if self.filtered_indices is None:
            return list(self.df.index)
        return list(self.filtered_indices)

    def _fill_treeview(self) -> None:
        if self.df is None or not self.display_columns:
            self.view.render_tree_rows([])
            return
        values = self._all_row_values()
        position = {idx: i for i, idx in enumerate(self.df.index)}
        rows = [(idx, values[position[idx]]) for idx in self._visible_indices()]
        self.view.render_tree_rows(rows)

    def _update_file_info(self) -> None:
        if self.df is None or self.file_path is None:
            self.view.set_file_info("", 0)
            return
        self.view.set_file_info(
            Path(self.file_path).name, len(self.df), len(self._visible_indices())
        )

    def _condition_mask(self, col: str, cond: str, needle: str):
        values = self.df[col].fillna("").astype(str).str.strip()
        if cond == "nonempty":
            return values != ""
        if cond == "empty":
            return values == ""
        hit = values.str.contains(needle, case=False, regex=False)
        return hit if cond == "contains" else ~hit

    def _apply_filter(self) -> None:
        if self.df is None or self.run_state.is_running:
            return

        rules: list[tuple[str, str, str]] = []
        for col, cond, text in self.view.get_filters():
            needle = text.strip()
            if not col or col not in self.df.columns:
                continue
            if cond in _TEXT_CONDITIONS and not needle:
                continue  # 条件填了一半就忽略这行，不打断输入
            rules.append((col, cond, needle))

        if not rules:
            self._reset_filter(quiet=self.filtered_indices is None)
            return

        join = self.view.get_filter_join()
        mask = None
        for col, cond, needle in rules:
            current = self._condition_mask(col, cond, needle)
            if mask is None:
                mask = current
            elif join == "any":
                mask = mask | current
            else:
                mask = mask & current

        self.filtered_indices = self.df[mask].index.tolist()
        self._fill_treeview()
        self._update_file_info()
        self.view.set_filter_active(True)
        joiner = " 或 " if join == "any" else " 且 "
        summary = joiner.join(
            f"{col} {_FILTER_DESC.get(cond, cond)}"
            + (f"「{needle}」" if cond in _TEXT_CONDITIONS else "")
            for col, cond, needle in rules
        )
        self.view.log(f"筛选：{summary} → {len(self.filtered_indices)} / {len(self.df)} 行\n")

    def _reset_filter(self, quiet: bool = False) -> None:
        if self.df is None or self.run_state.is_running:
            return
        had_filter = self.filtered_indices is not None
        self.filtered_indices = None
        self.view.set_filter_active(False)
        if not had_filter:
            return
        self._fill_treeview()
        self._update_file_info()
        if not quiet:
            self.view.log("已重置筛选，恢复全部行。\n")

    def _update_tree_row(self, idx: int) -> None:
        if self.df is None or not self.display_columns:
            return
        self.view.update_tree_row(idx, self._row_values(idx))

    _ROW_STATUS_TAGS = {"RUNNING": "running", "SUCCESS": "ok", "FAILED": "failed"}

    def _mark_row_status(self, idx: int, status: str, error_message: str = "") -> None:
        upper = status.upper()
        if upper == "FAILED":
            self.run_state.last_failed_indices.add(idx)
        elif upper == "SUCCESS":
            self.run_state.last_failed_indices.discard(idx)
        self.view.set_row_status(idx, self._ROW_STATUS_TAGS.get(upper, ""))
        if error_message:
            self.view.log(f"第 {idx + 1} 行出错：{error_message}\n", "error")
        if upper != "RUNNING":
            self.run_state.pending_save_count += 1

    def _sync_settings_from_view(self) -> None:
        self.input_cols = self.view.get_input_columns()
        self.output_col = self.view.get_output_column()
        self.model_id = self.view.get_model_id()
        if self.df is not None and self.output_col in self.df.columns:
            self.df[self.output_col] = self.df[self.output_col].astype(object).fillna("")
        if self.df is not None:
            self.columns = self.df.columns.tolist()
            if self.display_columns != self.columns:
                self._rebuild_tree_columns()
                self._fill_treeview()

    def _init_column_settings(self) -> None:
        if self.df is None:
            return
        self.columns = self.df.columns.tolist()
        self.view.set_columns(self.columns)
        self.view.set_output_column(guess_output_column(self.columns) or DEFAULT_OUTPUT_COLUMN)
        self.input_cols = self.view.get_input_columns()
        self.output_col = self.view.get_output_column()
        if self.output_col and self.output_col in self.df.columns:
            self.df[self.output_col] = self.df[self.output_col].astype(object).fillna("")
        self._rebuild_tree_columns()

    def _load_file(self) -> None:
        if self.run_state.is_running:
            return
        filepath = filedialog.askopenfilename(
            title="打开数据文件",
            filetypes=(
                ("数据文件", "*.csv *.xlsx *.xls *.xlsm *.tsv *.txt"),
                ("CSV 文件", "*.csv"),
                ("Excel 文件", "*.xlsx *.xls *.xlsm"),
                ("所有文件", "*.*"),
            ),
        )
        if not filepath:
            return
        try:
            df, enc = read_data_file(filepath)
            df = df.reset_index(drop=True)
            df.columns = [str(c).strip() for c in df.columns]
            df, dropped = drop_unnamed_columns(df)
            df.columns = dedupe_columns(df.columns.tolist())
            self.df = df
            self.file_path = filepath
            self.file_encoding = enc
            self.run_state.last_failed_indices.clear()
            self.run_state.pending_save_count = 0
            self.filtered_indices = None
            self.view.set_filter_active(False)
            self.view.clear_row_statuses()
            self._init_column_settings()
            self._fill_treeview()
            self._update_file_info()
            self.view.log(f"已载入 {len(self.df)} 行：{filepath}\n")
            if dropped:
                self.view.log(f"已忽略空列：{', '.join(dropped)}\n")
            swept = sweep_stale_temp_files(filepath)
            if swept:
                self.view.log(f"已清理 {swept} 个遗留的临时文件。\n")
            self.view.set_status("已载入文件")
            self.view.set_idle_state(True)
        except (RuntimeError, ValueError, OSError) as exc:
            messagebox.showerror("错误", f"载入文件失败：\n{exc}")

    def _load_prompt_config(self) -> None:
        if self.run_state.is_running:
            return
        filepath = filedialog.askopenfilename(
            title="载入提示词配置",
            filetypes=(("提示词配置", "*.json"), ("所有文件", "*.*")),
        )
        if not filepath:
            return
        try:
            data = json.loads(Path(filepath).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("提示词配置必须是一个 JSON 对象。")
            self.view.set_prompt_config(data)
            self.input_cols = self.view.get_input_columns()
            self.view.log(f"已载入提示词配置：{filepath}\n")
            self.view.set_status("已载入提示词")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            messagebox.showerror("错误", f"载入提示词配置失败：\n{exc}")

    def _save_prompt_config(self) -> None:
        if self.run_state.is_running:
            return
        filepath = filedialog.asksaveasfilename(
            title="保存提示词配置",
            defaultextension=".json",
            filetypes=(("提示词配置", "*.json"), ("所有文件", "*.*")),
        )
        if not filepath:
            return
        try:
            data = self.view.get_prompt_config()
            Path(filepath).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            self.view.log(f"已保存提示词配置：{filepath}\n")
            self.view.set_status("已保存提示词")
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("错误", f"保存提示词配置失败：\n{exc}")

    def _persist_dataframe(self, force: bool = False) -> None:
        if self.df is None or self.file_path is None:
            return
        if not force and self.run_state.pending_save_count == 0:
            return
        try:
            write_data_file(self.df, self.file_path, self.file_encoding)
            self.run_state.pending_save_count = 0
        except Exception as exc:
            self.view.log(f"[错误] 保存文件失败：{exc}\n", "error")

    def _get_processed_mask(self):
        if self.df is None:
            return pd.Series([], dtype=bool)
        if not self.output_col or self.output_col not in self.df.columns:
            return pd.Series([False] * len(self.df), index=self.df.index)
        return self.df[self.output_col].fillna("").astype(str).str.strip() != ""

    def _get_unprocessed_indices(self) -> list[int]:
        if self.df is None:
            return []
        return self.df[~self._get_processed_mask()].index.tolist()

    def _get_failed_indices(self) -> list[int]:
        if self.df is None:
            return []
        return sorted(i for i in self.run_state.last_failed_indices if i in self.df.index)

    def _on_tree_select(self, _event=None) -> None:
        if self.run_state.is_running or self.df is None:
            return
        selected = self.view.get_selected_indices()
        if not selected:
            self.view.show_output_text("")
            return
        self._show_row_output(selected[0])

    def _show_row_output(self, idx: int) -> None:
        if self.df is None:
            return
        output_col = self.view.get_output_column() or self.output_col
        if not output_col or output_col not in self.df.columns:
            self.view.show_output_text("")
            return
        value = self.df.at[idx, output_col]
        self.view.show_output_text("" if pd.isna(value) else str(value))

    def _request_stop(self) -> None:
        if not self.run_state.is_running:
            return
        self.run_state.stop_requested = True
        self.view.log("\n>>> 已请求停止，正在结束当前任务…\n", "error")
        self.view.set_stop_pending()
        self.view.set_status("停止中…")

    def _on_run_clicked(self) -> None:
        if self.run_state.is_running:
            return
        if self.df is None:
            self._start_direct_run()
            return
        self._start_extraction(self.view.get_run_mode())

    def _on_model_change(self, *_args) -> None:
        self.model_id = self.view.get_model_id()

    def _on_prompt_parts_change(self, *_args) -> None:
        self.input_cols = self.view.get_input_columns()

    def _on_close(self) -> None:
        if self._closing:
            return
        if self.run_state.is_running:
            wait_seconds = max(60, core_config.settings.llm_timeout * 3 + 30)
            if not messagebox.askyesno(
                "正在运行",
                "任务仍在运行中，退出前会等待当前行处理完成并自动保存"
                f"（最多约 {wait_seconds} 秒）。\n确定退出吗？",
                icon=messagebox.WARNING,
                default=messagebox.NO,
            ):
                return
            self._closing = True
            self.run_state.stop_requested = True
            self.view.log("\n>>> 已请求停止，正在结束当前任务…\n", "error")
            self.view.set_stop_pending()
            self.view.set_status("停止中…")
            self._schedule_close_after_run(time.monotonic() + wait_seconds)
            return
        self._closing = True
        self._persist_dataframe(force=True)
        self.root.destroy()

    def _schedule_close_after_run(self, deadline: float) -> None:
        """保持主循环运转，等当前行收尾（工作线程依赖主循环做界面更新），
        完成后由主线程做最后一次保存再退出，避免与工作线程并发写 DataFrame。"""
        if self.run_state.is_running and time.monotonic() < deadline:
            self.root.after(100, lambda: self._schedule_close_after_run(deadline))
            return
        self._persist_dataframe(force=True)
        self.root.destroy()

    def _build_run_config(self) -> RunConfig:
        self._sync_settings_from_view()
        return RunConfig(
            question=self.view.get_prompt(),
            input_cols=self.input_cols,
            output_col=self.output_col,
            model_id=self.model_id,
        )

    def _validate_columns(self, run_config: RunConfig) -> bool:
        if self.df is None:
            return False
        invalid_input_cols = [col for col in run_config.input_cols if col not in self.df.columns]
        if invalid_input_cols:
            messagebox.showwarning("提示", f"提示词引用了不存在的列：{invalid_input_cols[0]}")
            return False
        if not run_config.output_col:
            messagebox.showwarning("提示", "请先选择输出列。")
            return False
        if run_config.output_col in run_config.input_cols:
            messagebox.showwarning(
                "提示",
                f"输出列「{run_config.output_col}」同时也是提示词使用的输入列，"
                "运行会覆盖源数据。请改用其他列名（可直接输入新列名）。",
            )
            return False
        return True

    def _ensure_output_column(self, name: str) -> None:
        if self.df is None or not name or name in self.df.columns:
            return
        self.df[name] = ""
        self.columns = self.df.columns.tolist()
        self.view.set_columns(self.columns)
        self._rebuild_tree_columns()
        self._fill_treeview()
        self.view.log(f"已新建输出列：{name}\n")

    def _collect_task_indices(self, mode: str, selected: list[int]) -> list[int]:
        if self.df is None:
            return []
        # 筛选后所有运行范围都只在可见行里取，跟表格显示保持一致
        allowed = set(self._visible_indices())
        if mode == "unprocessed":
            start = selected[0] if selected else None
            indices = [
                i for i in self._get_unprocessed_indices()
                if i in allowed and (start is None or i >= start)
            ]
            if not indices:
                messagebox.showinfo("提示", "没有待处理的行。")
            return indices
        if mode == "failed":
            indices = [i for i in self._get_failed_indices() if i in allowed]
            if not indices:
                messagebox.showinfo("提示", "没有失败的行。")
            return indices
        if mode == "all":
            return self._visible_indices()
        if not selected:
            messagebox.showinfo("提示", "请先在表格中选择要处理的行。")
        return selected

    def _snapshot_batch_items(self, indices: list[int], run_config: RunConfig) -> list[BatchItem]:
        if self.df is None:
            return []
        # 只需提示词实际引用的列：向量化取数，避免对整表逐行逐格 df.at
        needed_cols = sorted(
            {
                col
                for col in get_placeholder_columns(run_config.question) + run_config.input_cols
                if col in self.df.columns
            }
        )
        if needed_cols:
            block = self.df.loc[:, needed_cols].fillna("").astype(str)
            for col in block.columns:
                block[col] = block[col].str.strip()
            records = block.loc[indices].to_dict("records")
        else:
            records = [{} for _ in indices]

        valid: list[BatchItem] = []
        invalid: list[int] = []
        for idx, row_data in zip(indices, records):
            input_text = build_input_text(run_config.question, row_data, run_config.input_cols)
            if is_valid_input(input_text):
                valid.append(BatchItem(idx=idx, input_text=input_text))
            else:
                invalid.append(idx)
        if invalid:
            reason = "拼装后的提示词为空"
            for idx in invalid:
                self._mark_row_status(idx, "FAILED", reason)
            self.view.log(f"已跳过 {len(invalid)} 个无效行（{reason}）。\n", "error")
            self._persist_dataframe(force=True)
        return valid

    def _start_direct_run(self) -> None:
        if self.run_state.is_running:
            return
        prompt = self.view.get_prompt()
        if not is_valid_input(prompt):
            messagebox.showwarning("提示", "请先填写提示词内容。")
            return

        run_config = RunConfig(
            question=prompt,
            input_cols=[],
            output_col="",
            model_id=self.view.get_model_id(),
        )
        try:
            validate_run_config(run_config)
        except NonRetryableProcessingError as exc:
            messagebox.showwarning("提示", str(exc))
            return

        self.model_id = run_config.model_id
        self.run_state.is_running = True
        self.run_state.stop_requested = False
        try:
            self.view.clear_logs()
            self.view.set_running_state()
            self.view.set_status("运行中")
            self.view.log(RUN_START_MESSAGE, "success")
            threading.Thread(target=self._run_direct_prompt, args=(prompt, run_config), daemon=True).start()
        except Exception as exc:
            logger.exception("Failed to start direct prompt")
            self.run_state.is_running = False
            self.view.log(f"[错误] 启动失败：{exc}\n", "error")
            self.view.set_status("启动失败")
            self.view.set_idle_state(self.df is not None)
            messagebox.showerror("启动失败", f"{type(exc).__name__}: {exc}")

    def _run_direct_prompt(self, prompt: str, run_config: RunConfig) -> None:
        had_error = False
        try:
            self.view.show_output_text("")
            result = process_input_value(
                self.llm,
                run_config,
                prompt,
                on_stream=self.view.append_output_text,
                on_reasoning=lambda chunk: self.view.log(chunk, "system"),
                should_stop=lambda: self.run_state.stop_requested,
            )
            if result.reasoning:
                self.view.log(f"\n[推理过程]\n{result.reasoning}\n")
            self.view.show_output_text(result.answer)
        except RequestCancelledError:
            had_error = True
        except (NonRetryableProcessingError, RetryableProcessingError) as exc:
            had_error = True
            self.view.log(f"[错误] {exc}\n", "error")
            self.view.set_status("出错")
        except Exception as exc:
            had_error = True
            logger.exception("Unexpected error during direct prompt run")
            self.view.log(f"[错误] 未预期的异常：{type(exc).__name__}: {exc}\n", "error")
            self.view.set_status("出错")
        finally:
            self._finish_direct_run(had_error)

    def _finish_direct_run(self, had_error: bool) -> None:
        stopped = self.run_state.stop_requested
        self.run_state.is_running = False
        self.run_state.stop_requested = False
        if stopped:
            self.view.log("\n>>> 已停止 <<<\n", "error")
            self.view.set_status("已停止")
        elif had_error:
            self.view.log("\n>>> 运行失败 <<<\n", "error")
            self.view.set_status("出错")
        else:
            self.view.log("\n>>> 运行完成 <<<\n", "success")
            self.view.set_status("已完成")
        self.view.set_idle_state(False)

    def _start_extraction(self, mode: str = "selected") -> None:
        if self.df is None:
            return
        # 新建列会重建表格并清掉选中态，先把选中行取出来
        selected = self.view.get_selected_indices()
        run_config = self._build_run_config()
        if not self._validate_columns(run_config):
            return
        try:
            validate_run_config(run_config)
        except NonRetryableProcessingError as exc:
            messagebox.showwarning("提示", str(exc))
            return
        if mode != "failed":
            self.run_state.last_failed_indices.clear()
            self.view.clear_row_statuses()
        indices = self._collect_task_indices(mode, selected)
        if not indices:
            return
        items = self._snapshot_batch_items(indices, run_config)
        if not items:
            messagebox.showinfo("提示", "没有可处理的有效行。")
            return
        self.run_state.is_running = True
        self.run_state.stop_requested = False
        self.run_state.pending_save_count = 0
        try:
            self.view.clear_logs()
            self.view.set_running_state()
            self.view.set_status("运行中")
            self.view.log(RUN_START_MESSAGE, "success")
            self._ensure_output_column(run_config.output_col)
            self.view.set_progress(0, len(items))
            row_delay = self.view.get_row_delay()
            if row_delay > 0:
                self.view.log(f"行间停顿 {row_delay:g} 秒。\n")
            threading.Thread(target=self._run_batch, args=(items, run_config, row_delay), daemon=True).start()
        except Exception as exc:
            # 线程还没起来就出错的话必须把运行态收回来，
            # 否则界面会一直卡在「停止」上，只能重开程序
            logger.exception("Failed to start batch")
            self.run_state.is_running = False
            self.view.log(f"[错误] 启动失败：{exc}\n", "error")
            self.view.set_status("启动失败")
            self.view.set_idle_state(self.df is not None)
            messagebox.showerror("启动失败", f"{type(exc).__name__}: {exc}")

    def _run_batch(self, items: list[BatchItem], run_config: RunConfig, row_delay: float = 0.0) -> None:
        total_items = len(items)
        had_unexpected_error = False
        started_at = time.monotonic()
        last_save_time = started_at
        throttle_save = total_items >= _LARGE_BATCH_ROWS
        try:
            for pos, item in enumerate(items, start=1):
                if self.run_state.stop_requested:
                    break
                self.view.focus_row(item.idx)
                self.view.set_progress(pos - 1, total_items, self._eta(started_at, pos - 1, total_items))
                self.view.log(f"[{pos}/{total_items}] 正在处理第 {item.idx + 1} 行…\n")
                self._mark_row_status(item.idx, "RUNNING")
                success = False
                error_message = ""
                for attempt in range(2):
                    if self.run_state.stop_requested:
                        break
                    try:
                        self._process_single(item, run_config)
                        success = True
                        break
                    except RequestCancelledError:
                        break
                    except NonRetryableProcessingError as exc:
                        error_message = str(exc)
                        self.view.log(f"  第 {attempt + 1} 次尝试失败（不可重试）：{exc}\n", "error")
                        break
                    except RetryableProcessingError as exc:
                        error_message = str(exc)
                        self.view.log(f"  第 {attempt + 1} 次尝试失败：{exc}\n", "error")
                        if attempt < 1:
                            self._sleep_interruptible(2)
                if success:
                    self._mark_row_status(item.idx, "SUCCESS")
                elif self.run_state.stop_requested:
                    self.run_state.last_failed_indices.add(item.idx)
                    self.view.set_row_status(item.idx, "failed")
                    self.view.log(f"  [已停止] 第 {item.idx + 1} 行未完成\n", "error")
                else:
                    self._mark_row_status(item.idx, "FAILED", error_message or "未知错误")
                    self.view.log(f"  [失败] 第 {item.idx + 1} 行\n", "error")
                if (
                    not throttle_save
                    or self.run_state.pending_save_count >= _AUTOSAVE_ROW_INTERVAL
                    or time.monotonic() - last_save_time >= _AUTOSAVE_TIME_INTERVAL
                ):
                    self._persist_dataframe()
                    last_save_time = time.monotonic()
                self.view.set_progress(pos, total_items, self._eta(started_at, pos, total_items))
                if row_delay > 0 and pos < total_items:
                    self._sleep_interruptible(row_delay)
        except Exception as exc:
            had_unexpected_error = True
            logger.exception("Unexpected error during batch run")
            self.view.log(f"[错误] 未预期的异常：{type(exc).__name__}: {exc}\n", "error")
            self.view.set_status("出错")
        finally:
            self._finish_batch(had_unexpected_error, time.monotonic() - started_at)

    def _sleep_interruptible(self, seconds: float) -> None:
        """行间停顿：分段小睡，随时响应“停止”。"""
        deadline = time.monotonic() + seconds
        while not self.run_state.stop_requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.2, remaining))

    @staticmethod
    def _eta(started_at: float, done: int, total: int) -> str:
        elapsed = time.monotonic() - started_at
        if done <= 0 or done >= total:
            return f"用时 {_format_duration(elapsed)}"
        remaining = elapsed / done * (total - done)
        return f"剩余 {_format_duration(remaining)}"

    def _finish_batch(self, had_unexpected_error: bool, elapsed: float) -> None:
        self._persist_dataframe(force=True)
        stopped = self.run_state.stop_requested
        self.run_state.is_running = False
        self.run_state.stop_requested = False
        if stopped:
            self.view.log("\n>>> 批处理已停止 <<<\n", "error")
            self.view.set_status("已停止")
        elif had_unexpected_error:
            self.view.log("\n>>> 批处理失败 <<<\n", "error")
            self.view.set_status("出错")
        else:
            self.view.log(f"\n>>> 批处理完成，用时 {_format_duration(elapsed)} <<<\n", "success")
            self.view.set_status("已完成")
        self.view.set_idle_state(self.df is not None)

    def _process_single(self, item: BatchItem, run_config: RunConfig) -> None:
        self.view.set_status(f"正在处理第 {item.idx + 1} 行")
        self.view.show_output_text("")
        result = process_input_value(
            self.llm,
            run_config,
            item.input_text,
            on_stream=self.view.append_output_text,
            on_reasoning=lambda chunk: self.view.log(chunk, "system"),
            should_stop=lambda: self.run_state.stop_requested,
        )
        self.view.log(f"  提取完成，共 {result.text_count} 字符。\n")
        if result.reasoning:
            self.view.log(f"\n[推理过程]\n{result.reasoning}\n")
        if self.df is None:
            raise RetryableProcessingError("数据表已不可用。")
        self.df.at[item.idx, run_config.output_col] = result.answer
        self._update_tree_row(item.idx)
        self.view.show_output_text(result.answer)
        self.view.log(f"  [成功] 第 {item.idx + 1} 行\n", "success")
