"""集中管理配色、字体与 ttk 样式。改这里即可全局换肤。"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont, ttk


COLORS = {
    # 取自 Windows 11 Fluent（计算器 / 记事本 / 设置）的浅色系统色
    "bg": "#f3f3f3",           # 窗口底色（计算器主体）
    "surface": "#ffffff",      # 卡片、记事本编辑区
    "surface_alt": "#f9f9f9",  # 表头、斑马纹（LayerFillColorDefault）
    "border": "#e5e5e5",       # ControlStrokeColorDefault
    "border_strong": "#c4c4c4",
    "text": "#000000",         # 正文纯黑（记事本）
    "text_muted": "#404040",   # 次要文字
    "text_faint": "#707070",   # 占位、禁用
    "accent": "#005fb8",       # Windows 11 强调蓝 AccentFillColorDefault
    "accent_hover": "#1a6ebe",
    "accent_active": "#00457e",
    "accent_soft": "#e5f1fb",  # 经典资源管理器悬停蓝
    "on_accent": "#ffffff",
    "danger": "#c42b1c",       # SystemFillColorCritical
    "danger_hover": "#b12a1b",
    "danger_active": "#8e2116",
    "danger_soft": "#fdf3f2",
    "success": "#0f7b0f",      # SystemFillColorSuccess
    "hover": "#f0f0f0",        # SubtleFillColorSecondary
    "row_selected": "#cce8ff", # 经典 Windows 列表选中蓝
    "row_ok": "#f2f8f2",
    "row_failed": "#fdf3f2",
    "row_running": "#eff6fc",
    "code_bg": "#ffffff",      # 记事本纯白
}

# setup_theme() 之后填充
FONTS: dict[str, tuple] = {}

_UI_FAMILIES = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "PingFang SC", "Noto Sans CJK SC")
_MONO_FAMILIES = ("Cascadia Mono", "Consolas", "Sarasa Mono SC", "Courier New")


def _pick_family(available: set[str], candidates: tuple[str, ...], fallback: str) -> str:
    lowered = {name.lower() for name in available}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return candidate
    return fallback


def enable_dpi_awareness() -> None:
    """Windows 高分屏下避免整体发虚。非 Windows 平台静默跳过。"""
    try:
        from ctypes import windll
    except ImportError:
        return
    for call in (
        lambda: windll.shcore.SetProcessDpiAwareness(1),
        lambda: windll.user32.SetProcessDPIAware(),
    ):
        try:
            call()
            return
        except Exception:
            continue


def apply_scaling(root: tk.Misc) -> None:
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except tk.TclError:
        return
    if dpi > 0:
        root.tk.call("tk", "scaling", dpi / 72.0)


def setup_theme(root: tk.Misc) -> None:
    apply_scaling(root)

    available = set(tkfont.families(root))
    ui = _pick_family(available, _UI_FAMILIES, "TkDefaultFont")
    mono = _pick_family(available, _MONO_FAMILIES, "TkFixedFont")
    FONTS.update(
        {
            "ui": (ui, 9),
            "ui_bold": (ui, 9, "bold"),
            "small": (ui, 8),
            "small_bold": (ui, 8, "bold"),
            "mono": (mono, 9),
            "mono_small": (mono, 8),
        }
    )

    style = ttk.Style(root)
    style.theme_use("clam")
    _configure_base(style)
    _configure_combobox(style, root)
    _configure_treeview(style)
    _configure_scrollbar(style)
    _configure_progressbar(style)


def _configure_base(style: ttk.Style) -> None:
    style.configure(".", font=FONTS["ui"], background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Card.TFrame", background=COLORS["surface"])


def _configure_combobox(style: ttk.Style, root: tk.Misc) -> None:
    style.configure(
        "TCombobox",
        padding=(6, 3),
        borderwidth=1,
        relief="flat",
        arrowsize=12,
        fieldbackground=COLORS["surface"],
        background=COLORS["surface"],
        bordercolor=COLORS["border_strong"],
        lightcolor=COLORS["surface"],
        darkcolor=COLORS["surface"],
        foreground=COLORS["text"],
        arrowcolor=COLORS["text_muted"],
    )
    # readonly 下把 selectbackground 设成底色，去掉选中后的蓝色高亮
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", COLORS["surface"]), ("disabled", COLORS["bg"])],
        background=[("readonly", COLORS["surface"]), ("disabled", COLORS["bg"])],
        selectbackground=[("readonly", COLORS["surface"]), ("!focus", COLORS["surface"])],
        selectforeground=[("readonly", COLORS["text"]), ("!focus", COLORS["text"])],
        foreground=[("disabled", COLORS["text_faint"])],
        arrowcolor=[("disabled", COLORS["text_faint"])],
        bordercolor=[("focus", COLORS["accent"]), ("hover", COLORS["border_strong"])],
    )
    root.option_add("*TCombobox*Listbox.background", COLORS["surface"])
    root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", COLORS["on_accent"])
    root.option_add("*TCombobox*Listbox.font", FONTS["ui"])
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.highlightThickness", 0)
    root.option_add("*TCombobox*Listbox.activeStyle", "none")


def _configure_treeview(style: ttk.Style) -> None:
    style.configure(
        "Data.Treeview",
        background=COLORS["surface"],
        fieldbackground=COLORS["surface"],
        foreground=COLORS["text"],
        rowheight=24,
        borderwidth=0,
        relief="flat",
        font=FONTS["ui"],
    )
    style.configure(
        "Data.Treeview.Heading",
        background=COLORS["surface_alt"],
        foreground=COLORS["text_muted"],
        font=FONTS["small_bold"],
        relief="flat",
        borderwidth=0,
        padding=(6, 5),
    )
    style.map(
        "Data.Treeview",
        background=[("selected", COLORS["row_selected"])],
        foreground=[("selected", COLORS["text"])],
    )
    style.map(
        "Data.Treeview.Heading",
        background=[("active", COLORS["border"])],
        relief=[("active", "flat"), ("pressed", "flat")],
    )


def _configure_scrollbar(style: ttk.Style) -> None:
    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"Slim.{orient}.TScrollbar",
            gripcount=0,
            borderwidth=0,
            relief="flat",
            arrowsize=11,
            troughcolor=COLORS["surface"],
            background=COLORS["border_strong"],
            bordercolor=COLORS["surface"],
            lightcolor=COLORS["border_strong"],
            darkcolor=COLORS["border_strong"],
            arrowcolor=COLORS["text_faint"],
        )
        style.map(
            f"Slim.{orient}.TScrollbar",
            background=[("active", COLORS["text_faint"])],
        )


def _configure_progressbar(style: ttk.Style) -> None:
    style.configure(
        "Thin.Horizontal.TProgressbar",
        troughcolor=COLORS["border"],
        background=COLORS["accent"],
        lightcolor=COLORS["accent"],
        darkcolor=COLORS["accent"],
        bordercolor=COLORS["border"],
        borderwidth=0,
        thickness=6,
    )
