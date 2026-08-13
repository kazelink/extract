from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable

from core.models import DEFAULT_MODEL_ID
from .prompt_editor import PromptEditor
from .theme import COLORS, FONTS, setup_theme
from .widgets import FlatButton, Panel, SplitButton, make_text_view


RUN_MODES: tuple[tuple[str, str], ...] = (
    ("selected", "选中行"),
    ("unprocessed", "未处理"),
    ("failed", "失败行"),
    ("all", "全部"),
)

FILTER_CONDITIONS: tuple[tuple[str, str], ...] = (
    ("nonempty", "非空"),
    ("empty", "为空"),
    ("contains", "包含"),
    ("not_contains", "不包含"),
)
_TEXT_CONDITIONS = {"contains", "not_contains"}

FILTER_JOINS: tuple[tuple[str, str], ...] = (
    ("all", "全部条件"),
    ("any", "任一条件"),
)


class _FilterRow:
    def __init__(self, parent, owner: "AppView") -> None:
        self._owner = owner
        self.frame = tk.Frame(parent, bg=COLORS["surface"])
        self.frame.pack(fill=tk.X, pady=(0, 3))

        self.col_var = tk.StringVar()
        self.col_cb = ttk.Combobox(self.frame, textvariable=self.col_var, values=[], state="readonly", width=14)
        self.col_cb.pack(side=tk.LEFT, padx=(0, 4))

        self.cond_var = tk.StringVar(value=FILTER_CONDITIONS[0][1])
        self.cond_cb = ttk.Combobox(
            self.frame,
            textvariable=self.cond_var,
            values=[label for _key, label in FILTER_CONDITIONS],
            state="readonly",
            width=7,
        )
        self.cond_cb.pack(side=tk.LEFT, padx=(0, 4))
        self.cond_cb.bind("<<ComboboxSelected>>", self._on_cond_change)

        self.btn_remove = FlatButton(self.frame, text="✕", command=self._remove, padx=6, pady=2)
        self.btn_remove.pack(side=tk.RIGHT)
        self.btn_add = FlatButton(self.frame, text="＋", command=self._add, padx=6, pady=2)
        self.btn_add.pack(side=tk.RIGHT, padx=(4, 4))

        self.text_var = tk.StringVar()
        self.entry = tk.Entry(
            self.frame,
            textvariable=self.text_var,
            font=FONTS["ui"],
            bg=COLORS["surface"],
            fg=COLORS["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            insertbackground=COLORS["text"],
            disabledbackground=COLORS["bg"],
        )
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.sync_entry_state()

    def _on_cond_change(self, _event=None) -> None:
        self.sync_entry_state()
        if self.condition() in _TEXT_CONDITIONS:
            self.entry.focus_set()

    def _add(self) -> None:
        self._owner.add_filter_row(after=self)

    def _remove(self) -> None:
        self._owner.remove_filter_row(self)

    def condition(self) -> str:
        label = self.cond_var.get()
        return next((key for key, text in FILTER_CONDITIONS if text == label), FILTER_CONDITIONS[0][0])

    def value(self) -> tuple[str, str, str]:
        return self.col_var.get().strip(), self.condition(), self.text_var.get()

    def sync_entry_state(self) -> None:
        self.entry.config(state=tk.NORMAL if self.condition() in _TEXT_CONDITIONS else tk.DISABLED)

    def set_columns(self, columns: list[str]) -> None:
        self.col_cb.config(values=columns)
        if self.col_var.get() not in columns:
            self.col_var.set(columns[0] if columns else "")

    def set_enabled(self, enabled: bool) -> None:
        state = "readonly" if enabled else tk.DISABLED
        self.col_cb.config(state=state)
        self.cond_cb.config(state=state)
        for button in (self.btn_add, self.btn_remove):
            button.config(state=tk.NORMAL if enabled else tk.DISABLED)
        if enabled:
            self.sync_entry_state()
        else:
            self.entry.config(state=tk.DISABLED)

    def reset(self) -> None:
        self.text_var.set("")
        self.cond_var.set(FILTER_CONDITIONS[0][1])
        self.sync_entry_state()

    def destroy(self) -> None:
        self.frame.destroy()


@dataclass
class Actions:
    on_open: Callable
    on_load_prompt: Callable
    on_save_prompt: Callable
    on_run: Callable
    on_stop: Callable
    on_model_change: Callable
    on_prompt_parts_change: Callable
    on_tree_select: Callable
    on_filter_apply: Callable
    on_filter_reset: Callable
    on_close: Callable


def _merge_runs(parts: list[tuple[str, str]]) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    for tag, chunk in parts:
        if merged and merged[-1][0] == tag:
            merged[-1] = (tag, merged[-1][1] + chunk)
        else:
            merged.append((tag, chunk))
    return merged


class AppView:
    STREAM_FLUSH_INTERVAL_MS = 120
    MAX_LOG_LINES = 200
    FOLLOW_BOTTOM_MARGIN_LINES = 6
    MAX_OUTPUT_LINES = 4000
    MAX_WIDGET_CHARS = 400_000
    MAX_PENDING_CHARS = 64_000
    MAX_FILTER_ROWS = 5
    TREE_INSERT_BATCH = 2_000

    def __init__(self, root: tk.Tk, model_specs, default_model_id: str) -> None:
        self.root = root
        self.root.title("表格批量提取")
        self._apply_initial_geometry()
        self.root.minsize(960, 600)
        setup_theme(root)
        self.root.configure(bg=COLORS["bg"])

        self._buf_lock = threading.Lock()
        self._stream_buffer: list[str] = []
        self._stream_chars = 0
        self._stream_dropped = 0
        self._stream_flush_scheduled = False
        self._stream_token = 0
        self._log_buffer: list[tuple[str, str]] = []
        self._log_chars = 0
        self._log_dropped = 0
        self._log_flush_scheduled = False
        self._layout_initialized = False
        self._file_loaded = False
        self._running = False
        self._row_status: dict[int, str] = {}
        self._known_columns: list[str] = []
        self._actions: Actions | None = None
        self._model_labels: list[str] = []
        self._model_label_to_id: dict[str, str] = {}
        used_labels: set[str] = set()
        for spec in model_specs:
            label = spec.label
            if label in used_labels:
                n = 2
                while f"{label} ({n})" in used_labels:
                    n += 1
                label = f"{label} ({n})"
            used_labels.add(label)
            self._model_labels.append(label)
            self._model_label_to_id[label] = spec.model_id
        self._model_reasoning_options: dict[str, list[str]] = {}
        self._model_reasoning_default: dict[str, str] = {}
        for spec in model_specs:
            self._model_reasoning_options[spec.model_id] = list(spec.reasoning_effort_options)
            effort = (spec.extra_params or {}).get("reasoning_effort")
            if effort:
                self._model_reasoning_default[spec.model_id] = str(effort)

        self._revealed = False
        try:
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

        self._build_ui(default_model_id)
        self.root.after(600, self._reveal)

    def _reveal(self) -> None:
        if self._revealed:
            return
        self._revealed = True
        try:
            self.root.attributes("-alpha", 1.0)
        except tk.TclError:
            pass
        self.root.deiconify()

    def _apply_initial_geometry(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(screen_w - 80, 1200)
        height = min(screen_h - 100, max(700, int(screen_h * 0.86)))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 24)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def bind_actions(self, actions: Actions) -> None:
        self._actions = actions
        self.btn_open.configure(command=actions.on_open)
        self.btn_load_prompt.configure(command=actions.on_load_prompt)
        self.btn_save_prompt.configure(command=actions.on_save_prompt)
        self.model_cb.bind("<<ComboboxSelected>>", actions.on_model_change)
        self.tree.bind("<<TreeviewSelect>>", actions.on_tree_select)
        self.root.protocol("WM_DELETE_WINDOW", actions.on_close)

    def _make_paned(self, parent, orient) -> tk.PanedWindow:
        return tk.PanedWindow(
            parent,
            orient=orient,
            sashwidth=6,
            sashrelief=tk.FLAT,
            showhandle=False,
            bd=0,
            bg=COLORS["bg"],
            opaqueresize=True,
        )

    def _build_ui(self, default_model_id: str) -> None:
        main = tk.Frame(self.root, bg=COLORS["bg"])
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        workspace = self._make_paned(main, tk.VERTICAL)
        workspace.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        self.workspace_paned = workspace

        top = self._make_paned(workspace, tk.HORIZONTAL)
        workspace.add(top, minsize=240, sticky="nsew")
        self.top_paned = top

        self._build_table_panel(top)

        right = self._make_paned(top, tk.VERTICAL)
        top.add(right, minsize=250, sticky="nsew")
        self.right_paned = right

        self._build_task_panel(right)
        self._build_log_panel(right)
        self._build_result_panel(workspace, default_model_id)

        self._build_action_bar(main)
        self.root.after_idle(self._set_initial_sash_positions)

    def _build_table_panel(self, parent) -> None:
        panel = Panel(parent, "", actions_first=True)
        parent.add(panel, minsize=380, sticky="nsew")
        self.table_panel = panel
        self.btn_open = panel.add_action("打开")
        self.btn_filter_toggle = panel.add_action("筛选", self._toggle_filter_bar)
        panel.set_subtitle("尚未载入数据")

        body = panel.body
        body.rowconfigure(0, weight=0)
        body.rowconfigure(1, weight=1)
        self._build_filter_bar(body)

        self.tree = ttk.Treeview(body, columns=(), show="tree headings", selectmode="extended", style="Data.Treeview")
        ybar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.tree.yview, style="Slim.Vertical.TScrollbar")
        xbar = ttk.Scrollbar(body, orient=tk.HORIZONTAL, command=self.tree.xview, style="Slim.Horizontal.TScrollbar")
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        ybar.grid(row=1, column=1, sticky="ns")
        xbar.grid(row=2, column=0, sticky="ew")
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        self.tree.tag_configure("even", background=COLORS["surface"])
        self.tree.tag_configure("odd", background=COLORS["surface_alt"])
        self.tree.tag_configure("ok", background=COLORS["row_ok"])
        self.tree.tag_configure("failed", background=COLORS["row_failed"])
        self.tree.tag_configure("running", background=COLORS["row_running"])

    def _build_filter_bar(self, parent) -> None:
        bar = tk.Frame(parent, bg=COLORS["surface"])
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 5))
        self.filter_bar = bar

        self.filter_rows_host = tk.Frame(bar, bg=COLORS["surface"])
        self.filter_rows_host.pack(fill=tk.X)
        self._filter_rows: list[_FilterRow] = []

        foot = tk.Frame(bar, bg=COLORS["surface"])
        foot.pack(fill=tk.X)
        tk.Label(foot, text="满足", font=FONTS["small"], fg=COLORS["text_muted"], bg=COLORS["surface"]).pack(side=tk.LEFT)
        self.filter_join_var = tk.StringVar(value=FILTER_JOINS[0][1])
        self.filter_join_cb = ttk.Combobox(
            foot,
            textvariable=self.filter_join_var,
            values=[label for _key, label in FILTER_JOINS],
            state="readonly",
            width=9,
        )
        self.filter_join_cb.pack(side=tk.LEFT, padx=(6, 0))

        self.btn_filter_reset = FlatButton(foot, text="重置", command=self._fire_filter_reset, padx=9, pady=2)
        self.btn_filter_reset.pack(side=tk.RIGHT)
        self.btn_filter = FlatButton(
            foot, text="筛选", command=self._fire_filter, kind="primary", padx=12, pady=2
        )
        self.btn_filter.pack(side=tk.RIGHT, padx=(4, 4))

        self.add_filter_row()
        bar.grid_remove()
        self._filter_visible = False

    def add_filter_row(self, after: "_FilterRow | None" = None) -> None:
        if len(self._filter_rows) >= self.MAX_FILTER_ROWS:
            return
        row = _FilterRow(self.filter_rows_host, self)
        row.set_columns(self._known_columns)
        if after is not None and after in self._filter_rows:
            row.frame.pack_configure(after=after.frame)
            self._filter_rows.insert(self._filter_rows.index(after) + 1, row)
        else:
            self._filter_rows.append(row)
        self._sync_filter_row_buttons()
        row.col_cb.focus_set()

    def remove_filter_row(self, row: "_FilterRow") -> None:
        if len(self._filter_rows) <= 1:
            row.reset()
            return
        self._filter_rows.remove(row)
        row.destroy()
        self._sync_filter_row_buttons()

    def _sync_filter_row_buttons(self) -> None:
        single = len(self._filter_rows) <= 1
        at_max = len(self._filter_rows) >= self.MAX_FILTER_ROWS
        for row in self._filter_rows:
            row.btn_remove.config(state=tk.DISABLED if single else tk.NORMAL)
            row.btn_add.config(state=tk.DISABLED if at_max else tk.NORMAL)

    def _toggle_filter_bar(self) -> None:
        self._filter_visible = not self._filter_visible
        if self._filter_visible:
            self.filter_bar.grid()
            self._filter_rows[0].col_cb.focus_set()
        else:
            self.filter_bar.grid_remove()

    def set_filter_active(self, active: bool) -> None:
        def _apply() -> None:
            self.btn_filter_toggle.set_kind("primary" if active else "ghost")

        self.ui(_apply)

    def _fire_filter(self) -> None:
        if self._actions:
            self._actions.on_filter_apply()

    def _fire_filter_reset(self) -> None:
        for row in self._filter_rows[1:]:
            row.destroy()
        del self._filter_rows[1:]
        self._filter_rows[0].reset()
        self._sync_filter_row_buttons()
        if self._actions:
            self._actions.on_filter_reset()

    def get_filters(self) -> list[tuple[str, str, str]]:
        return [row.value() for row in self._filter_rows]

    def get_filter_join(self) -> str:
        label = self.filter_join_var.get()
        return next((key for key, text in FILTER_JOINS if text == label), FILTER_JOINS[0][0])

    def _build_task_panel(self, parent) -> None:
        panel = Panel(parent, "提示词")
        parent.add(panel, minsize=160, sticky="nsew")
        tk.Label(
            panel.actions, text="间隔", font=FONTS["small"], fg=COLORS["text_muted"], bg=COLORS["surface"]
        ).pack(side=tk.LEFT, padx=(0, 2))
        self.delay_var = tk.StringVar(value="0")
        self.delay_entry = tk.Entry(
            panel.actions,
            textvariable=self.delay_var,
            width=5,
            font=FONTS["small"],
            justify=tk.CENTER,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            insertbackground=COLORS["text"],
        )
        self.delay_entry.pack(side=tk.LEFT, padx=(0, 2))
        tk.Label(
            panel.actions, text="秒", font=FONTS["small"], fg=COLORS["text_muted"], bg=COLORS["surface"]
        ).pack(side=tk.LEFT, padx=(0, 6))
        self.btn_insert_col = panel.add_action("＋ 插入列")
        self.btn_load_prompt = panel.add_action("载入")
        self.btn_save_prompt = panel.add_action("保存")

        self.prompt_editor = PromptEditor(panel.body, on_change=self._notify_prompt_parts_changed)
        self.prompt_editor.grid(row=0, column=0, sticky="nsew")
        self.btn_insert_col.configure(
            command=lambda: self.prompt_editor.popup_column_menu(self.btn_insert_col)
        )

        footer = panel.add_footer()
        tk.Frame(footer, bg=COLORS["border"], height=1).pack(fill=tk.X)
        row = tk.Frame(footer, bg=COLORS["surface"])
        row.pack(fill=tk.X, padx=8, pady=6)
        tk.Label(row, text="输出到", font=FONTS["small"], fg=COLORS["text_muted"], bg=COLORS["surface"]).pack(side=tk.LEFT)
        self.output_col_var = tk.StringVar()
        self.output_hint_var = tk.StringVar()
        tk.Label(
            row, textvariable=self.output_hint_var, font=FONTS["small"], fg=COLORS["accent"], bg=COLORS["surface"]
        ).pack(side=tk.RIGHT, padx=(6, 0))
        self.output_col_cb = ttk.Combobox(row, textvariable=self.output_col_var, values=[], state="readonly")
        self.output_col_cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        self.output_col_var.trace_add("write", self._refresh_output_hint)

    def _build_result_panel(self, parent, default_model_id: str) -> None:
        panel = Panel(parent, "结果")
        parent.add(panel, minsize=120, sticky="nsew")

        labels = self._model_labels
        default_label = next(
            (label for label, model_id in self._model_label_to_id.items() if model_id == default_model_id),
            labels[0] if labels else "",
        )
        tk.Label(
            panel.actions, text="模型", font=FONTS["small"], fg=COLORS["text_muted"], bg=COLORS["surface"]
        ).pack(side=tk.LEFT, padx=(0, 6))
        self.model_var = tk.StringVar(value=default_label)
        self.model_cb = ttk.Combobox(
            panel.actions, textvariable=self.model_var, values=labels, state="readonly", width=26
        )
        self.model_cb.pack(side=tk.LEFT)
        tk.Label(
            panel.actions, text="推理", font=FONTS["small"], fg=COLORS["text_muted"], bg=COLORS["surface"]
        ).pack(side=tk.LEFT, padx=(8, 4))
        self.reasoning_var = tk.StringVar()
        self.reasoning_cb = ttk.Combobox(
            panel.actions, textvariable=self.reasoning_var, values=[], state="readonly", width=8
        )
        self.reasoning_cb.pack(side=tk.LEFT)
        self.update_reasoning_for_model(default_model_id)
        panel.add_action("复制", self._copy_output)

        frame, self.output_log = make_text_view(
            panel.body, font=FONTS["mono"], background=COLORS["code_bg"], foreground=COLORS["text"]
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self.output_log.tag_config("content", foreground=COLORS["text"])

    def _build_log_panel(self, parent) -> None:
        panel = Panel(parent, "日志")
        parent.add(panel, minsize=100, sticky="nsew")
        panel.add_action("清空", self.clear_system_log)

        frame, self.system_log = make_text_view(
            panel.body, font=FONTS["mono_small"], background=COLORS["surface"], foreground=COLORS["text_muted"]
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self.system_log.tag_config("system", foreground=COLORS["text_muted"])
        self.system_log.tag_config("error", foreground=COLORS["danger"])
        self.system_log.tag_config("success", foreground=COLORS["success"])

    def _build_action_bar(self, parent) -> None:
        bar = tk.Frame(parent, bg=COLORS["surface"])
        bar.grid(row=1, column=0, sticky="ew")
        tk.Frame(bar, bg=COLORS["border"], height=1).pack(fill=tk.X)
        inner = tk.Frame(bar, bg=COLORS["surface"])
        inner.pack(fill=tk.X, padx=12, pady=7)
        inner.columnconfigure(1, weight=1)

        progress = tk.Frame(inner, bg=COLORS["surface"])
        progress.grid(row=0, column=0, sticky="w")
        self.progress_bar = ttk.Progressbar(progress, style="Thin.Horizontal.TProgressbar", length=170, maximum=1, value=0)
        self.progress_bar.pack(side=tk.LEFT)
        self.progress_var = tk.StringVar(value="0 / 0")
        tk.Label(progress, textvariable=self.progress_var, font=FONTS["small"], fg=COLORS["text_muted"], bg=COLORS["surface"]).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        self.status_var = tk.StringVar(value="就绪")
        tk.Label(inner, textvariable=self.status_var, font=FONTS["small"], fg=COLORS["text_muted"], bg=COLORS["surface"]).grid(
            row=0, column=1, sticky="e", padx=(12, 12)
        )

        self.run_button = SplitButton(
            inner,
            command=self._on_primary_click,
            options=RUN_MODES,
            on_option_change=lambda _key: self._refresh_run_button(),
        )
        self.run_button.grid(row=0, column=2, sticky="e")
        self.run_button.set_option("selected")
        self._refresh_run_button()

    def _set_initial_sash_positions(self) -> None:
        if self._layout_initialized:
            return
        width = self.top_paned.winfo_width()
        height = self.workspace_paned.winfo_height()
        if width <= 1 or height <= 1:
            self.root.after(50, self._set_initial_sash_positions)
            return
        self.workspace_paned.sash_place(0, 0, int(height * 0.72))
        self.top_paned.sash_place(0, int(width * 0.7), 0)
        self._layout_initialized = True
        self.root.after(40, self._place_right_sash)

    def _place_right_sash(self, attempts: int = 0) -> None:
        height = self.right_paned.winfo_height()
        if height <= 1:
            if attempts < 20:
                self.root.after(50, lambda: self._place_right_sash(attempts + 1))
            else:
                self._reveal()
            return
        self.right_paned.sash_place(0, 0, int(height * 0.62))
        self.root.update_idletasks()
        self._reveal()

    def _on_primary_click(self) -> None:
        if not self._actions:
            return
        if self._running:
            self._actions.on_stop()
        else:
            self._actions.on_run()

    def _refresh_run_button(self) -> None:
        if self._running:
            self.run_button.set_main("■ 停止", kind="danger")
            self.run_button.set_states(True, False)
            return
        if self._file_loaded:
            self.run_button.set_main(f"▶ 运行 · {self.run_button.option_label()}")
            self.run_button.set_states(True, True)
        else:
            self.run_button.set_main("▶ 运行")
            self.run_button.set_states(True, False)

    def get_run_mode(self) -> str:
        return self.run_button.get_option() or "selected"

    def set_idle_state(self, file_loaded: bool) -> None:
        def _apply() -> None:
            self._file_loaded = file_loaded
            self._running = False
            for button in (self.btn_open, self.btn_insert_col, self.btn_load_prompt, self.btn_save_prompt):
                button.config(state=tk.NORMAL)
            self.delay_entry.config(state=tk.NORMAL)
            self.btn_filter_toggle.config(state=tk.NORMAL if file_loaded else tk.DISABLED)
            self.model_cb.config(state="readonly")
            self.reasoning_cb.config(state="readonly")
            self.output_col_cb.config(state="normal" if file_loaded else tk.DISABLED)
            self.filter_join_cb.config(state="readonly" if file_loaded else tk.DISABLED)
            for button in (self.btn_filter, self.btn_filter_reset):
                button.config(state=tk.NORMAL if file_loaded else tk.DISABLED)
            for row in self._filter_rows:
                row.set_enabled(file_loaded)
            self._sync_filter_row_buttons()
            self.prompt_editor.set_enabled(True)
            self._refresh_run_button()

        self.ui(_apply)

    def set_running_state(self) -> None:
        def _apply() -> None:
            self._running = True
            for widget in (self.btn_open, self.btn_filter_toggle, self.btn_insert_col,
                           self.btn_load_prompt, self.btn_save_prompt):
                widget.config(state=tk.DISABLED)
            self.delay_entry.config(state=tk.DISABLED)
            self.model_cb.config(state=tk.DISABLED)
            self.reasoning_cb.config(state=tk.DISABLED)
            self.output_col_cb.config(state=tk.DISABLED)
            self.filter_join_cb.config(state=tk.DISABLED)
            for button in (self.btn_filter, self.btn_filter_reset):
                button.config(state=tk.DISABLED)
            for row in self._filter_rows:
                row.set_enabled(False)
            self.prompt_editor.set_enabled(False)
            self._refresh_run_button()

        self.ui(_apply)

    def set_loading_state(self, loading: bool) -> None:
        def _apply() -> None:
            if loading:
                for widget in (self.btn_open, self.btn_filter_toggle, self.btn_insert_col,
                               self.btn_load_prompt, self.btn_save_prompt):
                    widget.config(state=tk.DISABLED)
                self.delay_entry.config(state=tk.DISABLED)
                self.model_cb.config(state=tk.DISABLED)
                self.reasoning_cb.config(state=tk.DISABLED)
                self.output_col_cb.config(state=tk.DISABLED)
                self.filter_join_cb.config(state=tk.DISABLED)
                for button in (self.btn_filter, self.btn_filter_reset):
                    button.config(state=tk.DISABLED)
                for row in self._filter_rows:
                    row.set_enabled(False)
                self.prompt_editor.set_enabled(False)
                self.run_button.set_states(False, False)
            else:
                self.set_idle_state(self._file_loaded)

        self.ui(_apply)

    def set_stop_pending(self) -> None:
        def _apply() -> None:
            self.run_button.set_main("停止中…", kind="danger")
            self.run_button.set_states(False, False)

        self.ui(_apply)

    def get_prompt(self) -> str:
        return self.prompt_editor.get_prompt()

    def get_input_columns(self) -> list[str]:
        return self.prompt_editor.get_input_columns()

    def get_prompt_config(self) -> dict:
        return self.prompt_editor.get_config()

    def set_prompt_config(self, data: dict) -> None:
        self.prompt_editor.set_config(data)

    def get_output_column(self) -> str:
        return self.output_col_var.get().strip()

    def get_model_id(self) -> str:
        return self._model_label_to_id.get(self.model_var.get(), DEFAULT_MODEL_ID)

    def get_reasoning_effort(self) -> str:
        return self.reasoning_var.get().strip()

    def update_reasoning_for_model(self, model_id: str) -> None:
        options = list(self._model_reasoning_options.get(model_id) or [])
        fallback = self._model_reasoning_default.get(model_id, "")
        current = self.reasoning_var.get()
        if current not in options:
            current = fallback if fallback in options else (options[0] if options else "")
        self.reasoning_cb.config(values=options)
        self.reasoning_var.set(current)

    def get_row_delay(self) -> float:
        try:
            seconds = float(self.delay_var.get().strip())
        except (TypeError, ValueError):
            return 0.0
        if not 0 <= seconds <= 3600:
            return 0.0
        return seconds

    def set_columns(self, columns: list[str]) -> None:
        self._known_columns = list(columns)
        self.prompt_editor.set_columns(columns)
        self.output_col_cb.config(values=columns)
        for row in self._filter_rows:
            row.set_columns(columns)
        self._refresh_output_hint()

    def set_output_column(self, value: str | None) -> None:
        self.output_col_var.set(value or "")

    def _refresh_output_hint(self, *_args) -> None:
        name = self.output_col_var.get().strip()
        self.output_hint_var.set("＋ 新列" if name and name not in self._known_columns else "")

    def set_file_info(self, name: str, row_count: int, visible_count: int | None = None) -> None:
        def _apply() -> None:
            if not name:
                self.table_panel.set_subtitle("尚未载入数据")
                self.root.title("表格批量提取")
                return
            if visible_count is not None and visible_count != row_count:
                self.table_panel.set_subtitle(f"{name} · 筛选 {visible_count} / {row_count} 行")
            else:
                self.table_panel.set_subtitle(f"{name} · {row_count} 行")
            self.root.title(f"{name} — 表格批量提取")

        self.ui(_apply)

    def configure_tree_columns(self, columns: list[str], width_getter) -> None:
        self.tree.configure(columns=columns)
        self.tree.heading("#0", text="#")
        self.tree.column("#0", width=52, anchor="e", stretch=False, minwidth=44)
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width_getter(col), anchor="w", stretch=False, minwidth=80)

    def render_tree_rows(self, rows: list[tuple[int, list[str]]]) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._row_status.clear()
        for index, (idx, values) in enumerate(rows):
            self.tree.insert("", tk.END, iid=str(idx), text=str(idx + 1), values=values, tags=(self._row_tag(idx),))
            if index % self.TREE_INSERT_BATCH == self.TREE_INSERT_BATCH - 1:
                self.tree.update_idletasks()

    def update_tree_row(self, idx: int, values: list[str]) -> None:
        def _apply() -> None:
            iid = str(idx)
            if self.tree.exists(iid):
                self.tree.item(iid, values=values, text=str(idx + 1), tags=(self._row_tag(idx),))

        self.ui(_apply)

    def set_row_status(self, idx: int, status: str) -> None:
        def _apply() -> None:
            if status:
                self._row_status[idx] = status
            else:
                self._row_status.pop(idx, None)
            iid = str(idx)
            if self.tree.exists(iid):
                self.tree.item(iid, tags=(self._row_tag(idx),))

        self.ui(_apply)

    def clear_row_statuses(self) -> None:
        def _apply() -> None:
            stale = list(self._row_status)
            self._row_status.clear()
            for idx in stale:
                iid = str(idx)
                if self.tree.exists(iid):
                    self.tree.item(iid, tags=(self._row_tag(idx),))

        self.ui(_apply)

    def _row_tag(self, idx: int) -> str:
        return self._row_status.get(idx) or ("odd" if idx % 2 else "even")

    def get_selected_indices(self) -> list[int]:
        return sorted({int(iid) for iid in self.tree.selection()})

    def focus_row(self, idx: int) -> None:

        def _apply() -> None:
            iid = str(idx)
            if self.tree.exists(iid):
                self.tree.see(iid)

        self.ui(_apply)

    def _on_tree_double_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not iid:
            return
        if column == "#0":
            title, value = "#", self.tree.item(iid, "text")
        else:
            try:
                position = int(column[1:]) - 1
            except ValueError:
                return
            headings = list(self.tree.cget("columns") or ())
            values = self.tree.item(iid, "values")
            if position < 0 or position >= len(values) or position >= len(headings):
                return
            title = str(headings[position])
            value = str(values[position])
        self._show_cell_dialog(title, value, event.x_root, event.y_root)

    def _show_cell_dialog(self, title: str, value: str, x: int | None = None, y: int | None = None) -> None:
        top = tk.Toplevel(self.root)
        top.title(f"{title} — 完整内容")
        top.configure(bg=COLORS["surface"])
        top.transient(self.root)
        top.withdraw()
        frame, text = make_text_view(top, font=FONTS["mono"], background=COLORS["code_bg"], foreground=COLORS["text"])
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        text.insert("1.0", value)
        FlatButton(top, text="关闭", command=top.destroy, kind="primary", padx=16, pady=5).pack(pady=(0, 10))
        self._fit_dialog(top, text, value, x, y)

    def _fit_dialog(
        self, top: tk.Toplevel, text: tk.Text, value: str, x: int | None = None, y: int | None = None
    ) -> None:
        try:
            font = tkfont.Font(font=FONTS["mono"])
            line_h = font.metrics("linespace")
        except tk.TclError:
            font = None
            line_h = 16
        screen_w = top.winfo_screenwidth()
        screen_h = top.winfo_screenheight()
        max_w = min(screen_w - 80, 1000)
        max_h = max(130, screen_h - 120)
        min_w, min_h = 280, 130
        if x is None or y is None:
            pos = ""
        else:
            pos_x = max(16, min(x + 12, screen_w - 16 - min_w))
            pos_y = max(16, min(y + 12, screen_h - 16 - min_h))
            pos = f"+{pos_x}+{pos_y}"

        longest = max(value.splitlines() or [""], key=len)
        try:
            line_px = font.measure(longest[:2000]) if font else len(longest) * 8
        except tk.TclError:
            line_px = len(longest) * 8
        width = max(min_w, min(int(line_px) + 58, max_w))

        chrome = 12 + 20 + 28 + 10 + 31
        height = max(min_h, min(int((value.count("\n") + 1) * line_h + chrome), max_h))
        top.geometry(f"{width}x{height}{pos}")
        top.deiconify()
        self.root.update()
        if line_px > width - 58:
            try:
                counted = text.count("1.0", tk.END, "displaylines")
                lines = counted[0] if isinstance(counted, tuple) else counted
            except tk.TclError:
                return
            height = max(min_h, min(int(lines * line_h + chrome), max_h))
            top.geometry(f"{width}x{height}{pos}")

    def show_output_text(self, text: str) -> None:
        def _apply() -> None:
            with self._buf_lock:
                self._stream_token += 1
                self._stream_buffer = []
                self._stream_chars = 0
                self._stream_dropped = 0
                self._stream_flush_scheduled = False
            self.output_log.delete("1.0", tk.END)
            if text:
                self.output_log.insert(tk.END, text, "content")
            self.output_log.yview_moveto(0.0)

        self.ui(_apply)

    def append_output_text(self, text: str) -> None:
        if not text:
            return
        with self._buf_lock:
            self._stream_buffer.append(text)
            self._stream_chars += len(text)
            while self._stream_chars > self.MAX_PENDING_CHARS and len(self._stream_buffer) > 1:
                self._stream_chars -= len(self._stream_buffer.pop(0))
                self._stream_dropped += 1
            if self._stream_flush_scheduled:
                return
            self._stream_flush_scheduled = True
            token = self._stream_token
        self._schedule_flush(lambda: self._flush_stream_buffer(token))

    def _flush_stream_buffer(self, token: int) -> None:
        with self._buf_lock:
            self._stream_flush_scheduled = False
            if token != self._stream_token:
                return
            combined = "".join(self._stream_buffer)
            dropped = self._stream_dropped
            self._stream_buffer = []
            self._stream_chars = 0
            self._stream_dropped = 0
        if not combined and not dropped:
            return
        if dropped:
            self.output_log.insert(tk.END, f"\n[输出过快，已丢弃 {dropped} 段]\n", "content")
        self.output_log.insert(tk.END, combined, "content")
        self._trim(self.output_log, self.MAX_OUTPUT_LINES)
        self.output_log.see(tk.END)

    def _schedule_flush(self, callback) -> None:
        try:
            self.root.after(self.STREAM_FLUSH_INTERVAL_MS, callback)
        except tk.TclError:
            pass

    @classmethod
    def _trim(cls, widget: tk.Text, max_lines: int) -> None:
        line_count = int(widget.index("end-1c").split(".")[0])
        if line_count > max_lines:
            widget.delete("1.0", f"{line_count - max_lines + 1}.0")
        try:
            counted = widget.count("1.0", tk.END, "chars")
        except tk.TclError:
            return
        total = counted[0] if isinstance(counted, tuple) else counted
        if total and total > cls.MAX_WIDGET_CHARS:
            widget.delete("1.0", f"1.0+{int(total) - cls.MAX_WIDGET_CHARS}c")

    def clear_system_log(self) -> None:
        def _apply() -> None:
            with self._buf_lock:
                self._log_buffer = []
                self._log_chars = 0
                self._log_dropped = 0
            self.system_log.delete("1.0", tk.END)

        self.ui(_apply)

    def clear_logs(self) -> None:
        self.show_output_text("")
        self.clear_system_log()

    def log(self, msg: str, tag: str = "system") -> None:
        if not msg:
            return
        with self._buf_lock:
            self._log_buffer.append((tag, msg))
            self._log_chars += len(msg)
            while self._log_chars > self.MAX_PENDING_CHARS and len(self._log_buffer) > 1:
                self._log_chars -= len(self._log_buffer.pop(0)[1])
                self._log_dropped += 1
            if self._log_flush_scheduled:
                return
            self._log_flush_scheduled = True
        self._schedule_flush(self._flush_log_buffer)

    def _flush_log_buffer(self) -> None:
        with self._buf_lock:
            pending = self._log_buffer
            dropped = self._log_dropped
            self._log_buffer = []
            self._log_chars = 0
            self._log_dropped = 0
            self._log_flush_scheduled = False
        if not pending and not dropped:
            return
        follow = self._is_near_bottom(self.system_log)
        if dropped:
            self.system_log.insert(tk.END, f"[日志过多，已丢弃 {dropped} 段旧内容]\n", "error")
        for tag, chunk in _merge_runs(pending):
            self.system_log.insert(tk.END, chunk, tag)
        self._trim(self.system_log, self.MAX_LOG_LINES)
        if follow:
            self.system_log.yview_moveto(1.0)

    @staticmethod
    def _is_near_bottom(widget) -> bool:
        try:
            height = widget.winfo_height()
            if height <= 1:
                return True
            bottom = widget.index(f"@{0},{height}")
            gap = widget.count(bottom, "end-1c", "displaylines")
            if isinstance(gap, (tuple, list)):
                gap = gap[0]
            return gap <= AppView.FOLLOW_BOTTOM_MARGIN_LINES
        except tk.TclError:
            return True

    def _copy_output(self) -> None:
        text = self.output_log.get("1.0", "end-1c")
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status("已复制结果")

    def set_status(self, text: str) -> None:
        self.ui(lambda: self.status_var.set(text))

    def set_progress(self, processed: int, total: int, detail: str = "") -> None:
        def _apply() -> None:
            self.progress_bar.configure(maximum=max(total, 1), value=processed)
            suffix = f" · {detail}" if detail else ""
            self.progress_var.set(f"{processed} / {total}{suffix}")

        self.ui(_apply)

    def ui(self, callback) -> None:
        try:
            if threading.current_thread() is threading.main_thread():
                callback()
            else:
                self.root.after(0, callback)
        except (tk.TclError, RuntimeError):
            return

    def _notify_prompt_parts_changed(self) -> None:
        if self._actions:
            self._actions.on_prompt_parts_change()
