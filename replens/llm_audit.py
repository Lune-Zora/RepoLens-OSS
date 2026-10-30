"""Optional per-call HTTP model records, enabled by REPLENS_LLM_TRACE_DIR."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class LLMTrace:
    def __init__(self, request, purpose, context, api_key=""):
        directory = os.environ.get("REPLENS_LLM_TRACE_DIR")
        self.path = None
        self.api_key = api_key
        if not directory:
            return
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / (uuid4().hex + ".json")
        self.started = time.monotonic()
        self.record = {
            "schema_version": 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "purpose": purpose,
            "context": dict(context),
            "request": request,
            "status": "pending",
        }
        self._write()

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(self.api_key, "[REDACTED]") if self.api_key else value
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    def _write(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._redact(self.record), ensure_ascii=False,
                                        indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def finish(self, response_text="", parsed=None, error=None):
        if self.path is None:
            return
        self.record.update(
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=round(time.monotonic() - self.started, 6),
            status="error" if error else "success",
            response_text=response_text,
            parsed=parsed,
            error={"type": type(error).__name__, "message": str(error)} if error else None,
        )
        try:
            response = json.loads(response_text)
            if isinstance(response, dict):
                self.record["response_model"] = response.get("model")
                self.record["usage"] = response.get("usage")
        except (ValueError, TypeError):
            pass
        self._write()
