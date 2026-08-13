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
    try:
        from ctypes import windll

        windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        logger.debug("Failed to set AppUserModelID", exc_info=True)


def _apply_window_icon(root: tk.Tk) -> None:
    icon = resource_path(ICON_NAME)
    if not icon.exists():
        logger.warning("Icon not found: %s", icon)
        return
    try:
        root.iconbitmap(str(icon))
        root.iconbitmap(default=str(icon))
    except tk.TclError:
        logger.warning("Failed to apply icon: %s", icon, exc_info=True)


def _install_error_reporter(root: tk.Tk) -> None:

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
