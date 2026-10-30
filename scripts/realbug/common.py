"""小工具：哈希、路径包含判断、写入。"""
from __future__ import annotations

from pathlib import Path
import hashlib


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _is_within(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _save(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
