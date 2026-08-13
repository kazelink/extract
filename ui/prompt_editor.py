from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .theme import COLORS, FONTS
from .widgets import make_menu


PROMPT_PART_TEXT = "Text"
PROMPT_PART_COLUMN = "Column"

PLACEHOLDER_RE = re.compile(r"\{\{(.+?)\}\}")


class PromptEditor(ttk.Frame):

    REFRESH_DELAY_MS = 180

    def __init__(self, master, on_change: Callable[[], None]) -> None:
        super().__init__(master, style="Card.TFrame")
        self._columns: list[str] = []
        self._enabled = True
        self._on_change = on_change
        self._refresh_job: str | None = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        editor = tk.Frame(self, bg=COLORS["surface"])
        editor.grid(row=0, column=0, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(0, weight=1)

        self.text = tk.Text(
            editor,
            wrap=tk.WORD,
            font=FONTS["mono"],
            undo=True,
            maxundo=-1,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=8,
            background=COLORS["surface"],
            foreground=COLORS["text"],
            insertbackground=COLORS["accent"],
            insertwidth=2,
            selectbackground=COLORS["row_selected"],
            selectforeground=COLORS["text"],
            spacing1=1,
            spacing3=3,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(editor, orient=tk.VERTICAL, command=self.text.yview, style="Slim.Vertical.TScrollbar")
        ybar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=ybar.set)

        self.text.tag_configure(
            "chip",
            background=COLORS["accent_soft"],
            foreground=COLORS["accent_active"],
            borderwidth=0,
            font=FONTS["mono"],
        )
        self.text.tag_configure(
            "chip_bad",
            background=COLORS["danger_soft"],
            foreground=COLORS["danger_hover"],
            borderwidth=0,
            font=FONTS["mono"],
        )

        self.text.bind("<KeyRelease>", self._schedule_refresh)
        self.text.bind("<<Paste>>", self._schedule_refresh)
        self.text.bind("<<Cut>>", self._schedule_refresh)
        self.text.bind("<<Undo>>", self._schedule_refresh)
        self.text.bind("<<Redo>>", self._schedule_refresh)
        self.text.bind("<Double-1>", self._on_double_click)

        self.bar = tk.Frame(self, bg=COLORS["surface"])
        self.bar.grid(row=1, column=0, sticky="ew")
        tk.Frame(self.bar, bg=COLORS["border"], height=1).pack(fill=tk.X)
        inner = tk.Frame(self.bar, bg=COLORS["surface"])
        inner.pack(fill=tk.X, padx=8, pady=3)
        self.hint_var = tk.StringVar()
        tk.Label(
            inner,
            textvariable=self.hint_var,
            font=FONTS["small"],
            fg=COLORS["danger"],
            bg=COLORS["surface"],
            anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.bar.grid_remove()

        self._refresh(notify=False)

    def popup_column_menu(self, anchor) -> None:
        if not self._enabled:
            return
        self._popup_menu_at(
            anchor.winfo_rootx(),
            anchor.winfo_rooty() + anchor.winfo_height() + 2,
        )

    def set_columns(self, columns: list[str]) -> None:
        self._columns = list(columns)
        self._refresh(notify=False)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.text.config(state=tk.NORMAL if enabled else tk.DISABLED)

    def get_prompt(self) -> str:
        return self._content().strip()

    def get_input_columns(self) -> list[str]:
        selected: list[str] = []
        for name in PLACEHOLDER_RE.findall(self._content()):
            cleaned = name.strip()
            if cleaned and cleaned not in selected:
                selected.append(cleaned)
        return selected

    def get_config(self) -> dict:
        return {"version": 1, "prompt_parts": self._to_parts()}

    def set_config(self, data: dict) -> None:
        raw_parts = data.get("prompt_parts")
        if not isinstance(raw_parts, list):
            raise ValueError("提示词配置必须包含 prompt_parts 列表。")

        chunks: list[str] = []
        for item in raw_parts:
            if not isinstance(item, dict):
                raise ValueError("每个提示词片段必须是一个对象。")
            kind = str(item.get("kind") or item.get("type") or "").strip().lower()
            value = "" if item.get("value") is None else str(item.get("value"))
            if kind == "text":
                chunks.append(value)
            elif kind == "column":
                name = value.strip()
                if name:
                    chunks.append("{{" + name + "}}")
            else:
                raise ValueError(f"不支持的片段类型：{kind or '<空>'}")

        self.set_prompt("".join(chunks))

    def set_prompt(self, content: str) -> None:
        was_enabled = self._enabled
        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", content)
        self.text.edit_reset()
        if not was_enabled:
            self.text.config(state=tk.DISABLED)
        self._refresh()

    def _content(self) -> str:
        return self.text.get("1.0", "end-1c")

    def _to_parts(self) -> list[dict[str, str]]:
        content = self._content()
        parts: list[dict[str, str]] = []
        cursor = 0
        for match in PLACEHOLDER_RE.finditer(content):
            if match.start() > cursor:
                parts.append({"kind": PROMPT_PART_TEXT, "value": content[cursor : match.start()]})
            parts.append({"kind": PROMPT_PART_COLUMN, "value": match.group(1).strip()})
            cursor = match.end()
        if cursor < len(content):
            parts.append({"kind": PROMPT_PART_TEXT, "value": content[cursor:]})
        return parts or [{"kind": PROMPT_PART_TEXT, "value": ""}]

    def _schedule_refresh(self, _event=None) -> None:
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except tk.TclError:
                pass
        self._refresh_job = self.after(self.REFRESH_DELAY_MS, self._refresh)

    def _refresh(self, notify: bool = True) -> None:
        self._refresh_job = None
        content = self._content()
        for tag in ("chip", "chip_bad"):
            self.text.tag_remove(tag, "1.0", tk.END)

        unknown: list[str] = []
        for match in PLACEHOLDER_RE.finditer(content):
            name = match.group(1).strip()
            known = not self._columns or name in self._columns
            if not known and name not in unknown:
                unknown.append(name)
            self.text.tag_add(
                "chip" if known else "chip_bad",
                f"1.0+{match.start()}c",
                f"1.0+{match.end()}c",
            )

        if unknown:
            self.hint_var.set(f"⚠ 未知列：{'、'.join(unknown[:3])}{'…' if len(unknown) > 3 else ''}")
            self.bar.grid()
        else:
            self.hint_var.set("")
            self.bar.grid_remove()

        if notify:
            self._on_change()

    def _insert_column(self, column: str) -> None:
        if not self._enabled:
            return
        try:
            self.text.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            pass
        self.text.insert(tk.INSERT, "{{" + column + "}}")
        self.text.focus_set()
        self._refresh()

    def _column_menu(self) -> tk.Menu:
        menu = make_menu(self)
        if not self._columns:
            menu.add_command(label="请先打开数据文件", state=tk.DISABLED)
            return menu
        for column in self._columns:
            menu.add_command(label=column, command=lambda c=column: self._insert_column(c))
        return menu

    def _popup_menu_at(self, x: int, y: int) -> None:
        menu = self._column_menu()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _on_double_click(self, event) -> str | None:
        index = self.text.index(f"@{event.x},{event.y}")
        for tag in ("chip", "chip_bad"):
            ranges = self.text.tag_ranges(tag)
            for start, end in zip(ranges[::2], ranges[1::2]):
                if self.text.compare(index, ">=", start) and self.text.compare(index, "<", end):
                    self.text.tag_remove(tk.SEL, "1.0", tk.END)
                    self.text.tag_add(tk.SEL, start, end)
                    self.text.mark_set(tk.INSERT, end)
                    return "break"
        return None
