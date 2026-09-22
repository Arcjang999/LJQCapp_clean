"""Readable storage messages; technical failure details stay in application logs."""
from __future__ import annotations

import logging
import re


_LOGGER = logging.getLogger(__name__)


def storage_message(value: object) -> str:
    message = str(value)
    if isinstance(value, Exception):
        _LOGGER.warning("Storage operation failed: %s", message, exc_info=True)
    if "tkinter" in message or "系统原生选择窗口调用失败" in message:
        return "无法打开文件选择窗口，请重新打开软件后重试。"
    if re.search(r"\[Errno|\[WinError|Traceback|OperationalError|PermissionError", message):
        return "操作未完成，请检查文件或文件夹的访问权限后重试。"
    return (message.replace(" SQLite 数据库", "数据文件")
            .replace("SQLite 数据库", "数据文件")
            .replace("数据库结构校验", "数据文件检查")
            .replace("数据库校验", "数据文件检查")
            .replace("数据库", "数据文件")
            .replace("自动初始化", "自动建立")
            .replace("无需迁移", "无需更改")
            .replace("系统原生选择窗口", "文件选择窗口"))
