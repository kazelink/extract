from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox

from core.config import LOG_FILE_PATH, resource_path
from core.models import validate_model_configuration
from .app_controller import App
from .theme import enable_dpi_awareness


logger = logging.getLogger(__name__)

ICON_NAME = "app.ico"
APP_USER_MODEL_ID = "kazelink.extract.table"


def _set_app_user_model_id() -> None:
    """Windows 任务栏按此 ID 分组。不设的话源码运行时任务栏会显示 python 的图标。"""
    try:
        from ctypes import windll

        windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        logger.debug("Failed to set AppUserModelID", exc_info=True)


def _apply_window_icon(root: tk.Tk) -> None:
    """标题栏左上角 + 任务栏图标。图标缺失只记日志，不影响启动。"""
    icon = resource_path(ICON_NAME)
    if not icon.exists():
        logger.warning("Icon not found: %s", icon)
        return
    try:
        root.iconbitmap(str(icon))
        # -default 让之后弹出的 Toplevel（单元格详情窗）也用同一个图标
        root.iconbitmap(default=str(icon))
    except tk.TclError:
        logger.warning("Failed to apply icon: %s", icon, exc_info=True)


def _install_error_reporter(root: tk.Tk) -> None:
    """Tk 回调里抛出的异常默认只打到 stderr，--windowed 下等于消失。
    改成写日志 + 弹窗，出问题时至少知道是什么错。
    """

    def report(exc_type, exc_value, exc_tb) -> None:
        logger.error("Unhandled Tk callback error", exc_info=(exc_type, exc_value, exc_tb))
        try:
            messagebox.showerror(
                "程序错误",
                f"{exc_type.__name__}: {exc_value}\n\n详细堆栈已写入日志文件：\n{LOG_FILE_PATH}",
            )
        except tk.TclError:
            pass

    root.report_callback_exception = report


def run() -> None:
    enable_dpi_awareness()
    _set_app_user_model_id()
    root = tk.Tk()
    # tk.Tk() 会立刻映射一个 200x200 的默认窗口，先藏起来，
    # 等 AppView 定好尺寸、面板都装配完再 deiconify，否则会先闪一个小框
    root.withdraw()
    _install_error_reporter(root)
    _apply_window_icon(root)
    try:
        validate_model_configuration()
    except RuntimeError as exc:
        root.withdraw()
        messagebox.showerror("配置错误", str(exc), parent=root)
        root.destroy()
        return
    App(root)
    root.mainloop()
