# SPDX-License-Identifier: GPL-3.0-or-later
"""Append-only JSONL audit log of everything the assistant reads, proposes and applies.

Written to ``<project>/.gamestudio/ai/audit.jsonl`` (or the system temp dir when the
.blend is not inside a project). Safe to call from any thread.
"""

import datetime
import json
import pathlib
import tempfile
import threading

_lock = threading.Lock()
_MAX_FIELD = 4000


def audit_path(project_root: pathlib.Path | None) -> pathlib.Path:
    if project_root is not None:
        return project_root / ".gamestudio" / "ai" / "audit.jsonl"
    return pathlib.Path(tempfile.gettempdir()) / "gamestudio_ai_audit.jsonl"


def _clip(value):
    if isinstance(value, str) and len(value) > _MAX_FIELD:
        return value[:_MAX_FIELD] + f"... [{len(value) - _MAX_FIELD} more chars]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value[:200]]
    return value


def log(project_root: pathlib.Path | None, event: str, **fields) -> None:
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        **_clip(fields),
    }
    path = audit_path(project_root)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            # Auditing must never break the editor; the transcript still shows events.
            pass
