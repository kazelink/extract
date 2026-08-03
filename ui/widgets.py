"""通用小组件：扁平按钮、分体按钮、卡片面板、只读文本视图。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from .theme import COLORS, FONTS


_KINDS = {
    "primary": (COLORS["accent"], COLORS["on_accent"], COLORS["accent_hover"], COLORS["accent_active"]),
    "danger": (COLORS["danger"], COLORS["on_accent"], COLORS["danger_hover"], COLORS["danger_active"]),
}


def make_menu(parent) -> tk.Menu:
    return tk.Menu(
        parent,
        tearoff=0,
        font=FONTS["ui"],
        bg=COLORS["surface"],
        fg=COLORS["text"],
        activebackground=COLORS["accent"],
        activeforeground=COLORS["on_accent"],
        disabledforeground=COLORS["text_faint"],
        selectcolor=COLORS["accent"],
        activeborderwidth=0,
        borderwidth=1,
        relief="solid",
    )


class FlatButton(tk.Button):
    """无边框按钮。kind='ghost' 时与父级底色融为一体，仅在悬停时浮出。"""

    def __init__(
        self,
        master,
        text: str = "",
        command: Callable | None = None,
        kind: str = "ghost",
        surface: str | None = None,
        padx: int = 10,
        pady: int = 4,
        font: tuple | None = None,
        **kwargs,
    ) -> None:
        surface = surface or COLORS["surface"]
        if kind in _KINDS:
            bg, fg, self._hover, active = _KINDS[kind]
            self._hover_fg = fg
        else:
            bg, fg, self._hover, active = surface, COLORS["text"], COLORS["hover"], COLORS["border"]
            self._hover_fg = COLORS["text"]
        self._bg = bg
        self._fg = fg
        super().__init__(
            master,
            text=text,
            command=command,
            font=font or FONTS["small"],
            bg=bg,
            fg=fg,
            activebackground=active,
            activeforeground=self._hover_fg,
            disabledforeground=COLORS["text_faint"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=padx,
            pady=pady,
            cursor="hand2",
            **kwargs,
        )
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")

    def _on_enter(self, _event=None) -> None:
        if str(self["state"]) != tk.DISABLED:
            self.configure(bg=self._hover, fg=self._hover_fg)

    def _on_leave(self, _event=None) -> None:
        self.configure(bg=self._bg, fg=self._fg)

    def set_kind(self, kind: str, surface: str | None = None) -> None:
        if kind in _KINDS:
            bg, fg, self._hover, active = _KINDS[kind]
            self._hover_fg = fg
        else:
            bg = surface or COLORS["surface"]
            fg, self._hover, active = COLORS["text"], COLORS["hover"], COLORS["border"]
            self._hover_fg = COLORS["text"]
        self._bg = bg
        self._fg = fg
        self.configure(bg=bg, fg=fg, activebackground=active, activeforeground=self._hover_fg)


class SplitButton(tk.Frame):
    def __init__(
        self,
        master,
        command: Callable,
        options: tuple[tuple[str, str], ...],
        on_option_change: Callable[[str], None] | None = None,
        surface: str | None = None,
    ) -> None:
        super().__init__(master, bg=surface or COLORS["surface"])
        self._choices = options
        self._on_option_change = on_option_change
        self._value = tk.StringVar(value=options[0][0] if options else "")

        self.main = FlatButton(self, text="", command=command, kind="primary", padx=14, pady=5, font=FONTS["ui_bold"])
        self.main.pack(side=tk.LEFT)
        self._divider = tk.Frame(self, bg=COLORS["accent_active"], width=1)
        self._divider.pack(side=tk.LEFT, fill=tk.Y)
        self.arrow = FlatButton(self, text="▾", command=self._popup, kind="primary", padx=8, pady=5, font=FONTS["ui_bold"])
        self.arrow.pack(side=tk.LEFT)

        self._menu = make_menu(self)
        for key, label in options:
            self._menu.add_radiobutton(label=label, value=key, variable=self._value, command=self._on_pick)

    def _popup(self) -> None:
        try:
            self._menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + 2)
        finally:
            self._menu.grab_release()

    def _on_pick(self) -> None:
        if self._on_option_change:
            self._on_option_change(self._value.get())

    def get_option(self) -> str:
        return self._value.get()

    def set_option(self, key: str) -> None:
        if key in dict(self._choices):
            self._value.set(key)

    def option_label(self) -> str:
        return dict(self._choices).get(self._value.get(), "")

    def set_main(self, text: str, kind: str = "primary") -> None:
        self.main.configure(text=text)
        self.main.set_kind(kind)
        self.arrow.set_kind(kind)
        self._divider.configure(bg=COLORS["danger_active"] if kind == "danger" else COLORS["accent_active"])

    def set_states(self, main_enabled: bool, arrow_enabled: bool) -> None:
        self.main.configure(state=tk.NORMAL if main_enabled else tk.DISABLED)
        self.arrow.configure(state=tk.NORMAL if arrow_enabled else tk.DISABLED)


class Panel(tk.Frame):
    """带 1px 描边的卡片：顶部标题 + 操作区，下方内容区。

    title 为空时只显示 subtitle；actions_first=True 时按钮靠左，状态文字甩到最右。
    """

    def __init__(self, master, title: str, actions_first: bool = False) -> None:
        super().__init__(
            master,
            bg=COLORS["surface"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["border"],
        )
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        head = tk.Frame(self, bg=COLORS["surface"])
        head.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 3))
        head.columnconfigure(1, weight=1)

        self.actions = tk.Frame(head, bg=COLORS["surface"])
        self.subtitle_var = tk.StringVar()
        subtitle = tk.Label(
            head,
            textvariable=self.subtitle_var,
            font=FONTS["small_bold"],
            fg=COLORS["text_muted"],
            bg=COLORS["surface"],
            anchor="e" if actions_first else "w",
        )
        title_label = None
        if title:
            title_label = tk.Label(
                head,
                text=title,
                font=FONTS["small_bold"],
                fg=COLORS["text_muted"],
                bg=COLORS["surface"],
            )

        if actions_first:
            self.actions.grid(row=0, column=0, sticky="w")
            if title_label is not None:
                title_label.grid(row=0, column=1, sticky="w", padx=(8, 0))
            subtitle.grid(row=0, column=2, sticky="e", padx=(8, 2))
        else:
            if title_label is not None:
                title_label.grid(row=0, column=0, sticky="w")
            subtitle.grid(row=0, column=1, sticky="w", padx=(8 if title else 0, 0))
            self.actions.grid(row=0, column=2, sticky="e")
        self._actions_first = actions_first

        tk.Frame(self, bg=COLORS["border"], height=1).grid(row=1, column=0, sticky="ew")

        self.body = tk.Frame(self, bg=COLORS["surface"])
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_var.set(text)

    def add_action(self, text: str, command: Callable | None = None) -> FlatButton:
        button = FlatButton(self.actions, text=text, command=command, padx=9, pady=2)
        if self._actions_first:
            button.pack(side=tk.LEFT, padx=(0, 4))
        else:
            button.pack(side=tk.LEFT, padx=(4, 0))
        return button

    def add_footer(self) -> tk.Frame:
        footer = tk.Frame(self, bg=COLORS["surface"])
        footer.grid(row=3, column=0, sticky="ew")
        return footer


def make_text_view(parent, *, font: tuple, background: str, foreground: str):
    """返回 (容器, Text)。自带细滚动条，只读但可选中复制。"""
    frame = tk.Frame(parent, bg=background)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    text = tk.Text(
        frame,
        wrap=tk.WORD,
        font=font,
        bg=background,
        fg=foreground,
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        padx=10,
        pady=6,
        insertbackground=foreground,
        selectbackground=COLORS["row_selected"],
        selectforeground=COLORS["text"],
        spacing1=1,
        spacing3=2,
    )
    text.grid(row=0, column=0, sticky="nsew")
    ybar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview, style="Slim.Vertical.TScrollbar")
    ybar.grid(row=0, column=1, sticky="ns")
    text.configure(yscrollcommand=ybar.set)
    text.bind("<Key>", _block_edit)
    return frame, text


_NAV_KEYS = {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next", "Shift_L", "Shift_R", "Control_L", "Control_R"}


def _block_edit(event):
    if event.state & 0x4 and event.keysym.lower() in ("c", "a", "insert"):
        return None
    if event.keysym in _NAV_KEYS:
        return None
    return "break"
