"""Keep one local service using a desktop data root at a time."""
from __future__ import annotations

import os
from pathlib import Path


class ServiceLock:
    def __init__(self, directory: Path):
        self.directory = directory
        self.handle = None

    def acquire(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = (self.directory / "service.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0, 2)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise RuntimeError("LJQCApp 正在使用此资料目录，请先关闭原程序后重试。") from error
        self.handle = handle

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None
