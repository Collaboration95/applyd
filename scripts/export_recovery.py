"""Track derived export freshness independently of committed application changes.

Callers hold the workspace writer lock through mutation and export. The injected
exporter accepts a root and rebuilds all outputs from one caller-owned snapshot;
this module never reads application records or invokes application actions.
"""

import json
import os
import shlex
import tempfile
from pathlib import Path


STATE_PATH = Path("applications/export-state.json")
_STATUSES = {"pending", "deferred", "failed", "clean"}


def _state(dirty, status):
    return {"schema_version": 1, "dirty": dirty, "status": status}


def _write_state(root, status):
    path = Path(root) / STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".export-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(_state(status != "clean", status), stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_state(root):
    """Read only the marker; absent, unreadable or invalid means unknown/dirty."""
    try:
        value = json.loads((Path(root) / STATE_PATH).read_text(encoding="utf-8"))
        if (isinstance(value, dict) and type(value.get("schema_version")) is int
                and value["schema_version"] == 1
                and value.get("status") in _STATUSES
                and type(value.get("dirty")) is bool
                and value["dirty"] == (value["status"] != "clean")):
            return _state(value["dirty"], value["status"])
    except (OSError, ValueError, UnicodeError, TypeError):
        pass
    return _state(True, "unknown")


def _retry_command(root):
    return shlex.join(["python3", "scripts/workspace.py", "--workspace",
                       str(Path(root).resolve()), "export"])


def _failure(root, code):
    # Never serialize exception messages: they may contain private record data.
    return {"ok": False, "status": "failed", "error_code": code,
            "retry_command": _retry_command(root)}


def recover(root, exporter):
    """Rebuild outputs only, even if the marker was clean or missing.

    The exporter must raise on any incomplete output and return JSON-compatible
    metadata on success. Ordinary failures are returned without exception text.
    Interruptions propagate, leaving the previously flushed dirty marker intact.
    """
    try:
        _write_state(root, "pending")
    except Exception:
        return _failure(root, "export_state_write_failed")
    try:
        result = exporter(Path(root))
    except Exception:
        try:
            _write_state(root, "failed")
        except Exception:
            pass  # The pending marker already records the need to recover.
        return _failure(root, "export_failed")
    try:
        _write_state(root, "clean")
    except Exception:
        return _failure(root, "export_state_write_failed")
    return {"ok": True, "status": "exported", "result": result}


def after_mutation(root, result, exporter, deferred=False):
    """Report an already committed mutation separately from its derived export.

    Do not pass an unexecuted action here. Mutation failures belong to the caller
    and must propagate before this function is reached. ``result`` is a mapping
    of the successful action's existing metadata; it is neither persisted nor
    changed. A deferred export has ``ok=None`` because it has not been attempted.
    """
    mutation = {**result, "ok": True}
    if not deferred:
        outcome = recover(root, exporter)
    else:
        try:
            _write_state(root, "deferred")
        except Exception:
            outcome = _failure(root, "export_state_write_failed")
        else:
            outcome = {"ok": None, "status": "deferred",
                       "retry_command": _retry_command(root)}
    return {"mutation": mutation, "export": outcome}
