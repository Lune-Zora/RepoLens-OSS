"""Per-request model selection without mutating process-wide environment."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
_selected = ContextVar('replens_selected_model', default='')

def selected_model(default='qwen35-9b-unc:latest'):
    return _selected.get() or os.environ.get('REPLENS_LLM_MODEL') or default

@contextmanager
def using_model(model=''):
    token = _selected.set(model.strip())
    try:
        yield
    finally:
        _selected.reset(token)
