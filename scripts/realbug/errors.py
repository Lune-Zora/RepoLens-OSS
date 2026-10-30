"""控制端异常类型。"""
from __future__ import annotations



class RestoreError(RuntimeError):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage
