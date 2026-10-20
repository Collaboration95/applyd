"""Initialize a private applyd workspace and inspect local prerequisites."""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import platform
import shutil

import settings


CODE_ROOT = Path(__file__).resolve().parents[1]
JSON_FILES = {
    "settings.json": settings.DEFAULTS,
    "profile/answers.json": {"schema_version": 1, "answers": []},
    "projects/catalog.json": {"schema_version": 1, "projects": []},
    "resumes/registry.json": {"schema_version": 1, "releases": []},
    "resumes/presets.json": {},
}
DIRECTORIES = (
    "profile", "projects", "resumes", "resumes/drafts", "applications", "dashboard",
    "applications/records", "applications/runs", "applications/batches",
)
DASHBOARD_ASSETS = ("index.html", "app.js", "runs.js", "control.js", "style.css")


class SetupError(ValueError):
    """The selected root cannot be initialized without risking existing data."""


def _payloads(code_root: Path) -> dict[str, bytes]:
    validated_defaults = settings._validate(settings.DEFAULTS)
    payloads = {
        name: (json.dumps(validated_defaults if name == "settings.json" else value,
                          ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        for name, value in JSON_FILES.items()
    }
    for name in DASHBOARD_ASSETS:
        source = code_root / "dashboard" / name
        if not source.is_file():
            raise SetupError("Generic dashboard assets are unavailable")
        payloads[f"dashboard/{name}"] = source.read_bytes()
    return payloads


def init_workspace(root, *, code_root=CODE_ROOT) -> dict:
    """Create a fresh private workspace; existing entries are never overwritten.

    A missing root or an existing empty directory is accepted. Any existing
    child entry causes a preflight refusal before the function writes anything.
    File creation uses exclusive mode and rolls back only entries created here
    if an unexpected error interrupts initialization.
    """
    root = Path(root).expanduser().absolute()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise SetupError("Workspace root must be missing or an empty directory")
    payloads = _payloads(Path(code_root))

    created_dirs: list[Path] = []
    created_files: list[Path] = []
    try:
        if not root.exists():
            root.mkdir(parents=True, mode=0o700)
            created_dirs.append(root)
        for relative in DIRECTORIES:
            directory = root / relative
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            created_dirs.append(directory)
        for relative, content in payloads.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation prevents a concurrent setup from replacing data.
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created_files.append(path)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
    except FileExistsError as exc:
        for path in reversed(created_files):
            path.unlink(missing_ok=True)
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                pass
        raise SetupError("Workspace changed during initialization; no existing files were replaced") from exc
    except Exception:
        for path in reversed(created_files):
            path.unlink(missing_ok=True)
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                pass
        raise

    return {"root": str(root), "created_files": len(created_files), "created_directories": len(created_dirs)}


def _version_tuple(text: str) -> tuple[int, ...] | None:
    parts = []
    for piece in text.split("."):
        digits = "".join(c for c in piece if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def _meets(text: str, minimum: tuple[int, ...]) -> bool:
    found = _version_tuple(text)
    if found is None:
        return False
    width = max(len(found), len(minimum))
    return found + (0,) * (width - len(found)) >= minimum + (0,) * (width - len(minimum))


def _browser_check() -> dict:
    if platform.system() != "Darwin":
        return {"name": "macOS browser", "status": "unsupported",
                "required": False, "detail": "Browser capability is checked on macOS only"}

    candidates = (
        Path("/Applications/Safari.app"),
        Path("/Applications/Google Chrome.app"),
        Path("/Applications/Brave Browser.app"),
        Path("/Applications/Firefox.app"),
        Path("/System/Applications/Safari.app"),
    )
    available = any(path.is_dir() for path in candidates)
    return {"name": "macOS browser", "status": "available" if available else "missing",
            "required": True,
            "detail": "Browser application detected; automation and login are not verified" if available else
                      "No browser application was detected"}


def doctor(root=None, *, shared=True, python_version=None, node_version=None,
           system=None, browser_available=None, which=shutil.which) -> dict:
    """Return prerequisite checks without starting a browser or changing state.

    `shared=True` omits all paths and executable names from output. Optional
    version and platform inputs make dependency behavior deterministic in tests.
    """
    del root  # Accepted for CLI/API symmetry; diagnosis does not read workspace data.
    py_text = python_version if python_version is not None else platform.python_version()
    checks = [{"name": "Python", "status": "available" if _meets(py_text, (3, 11)) else "missing",
               "required": True, "detail": "Python 3.11 or newer"}]

    node_path = which("node")
    node_text = node_version
    if node_text is None and node_path:
        try:
            import subprocess
            result = subprocess.run([node_path, "--version"], capture_output=True,
                                    text=True, timeout=3, check=False)
            if result.returncode == 0:
                node_text = result.stdout.strip().removeprefix("v")
        except (OSError, subprocess.SubprocessError):
            node_text = None
    node_ok = node_text is not None and _meets(node_text.removeprefix("v"), (18,))
    checks.append({"name": "Node.js", "status": "available" if node_ok else "missing",
                   "required": True, "detail": "Node.js 18 or newer is required for dashboard tests"})

    os_name = system if system is not None else platform.system()
    if os_name != "Darwin":
        checks.append({"name": "macOS browser", "status": "unsupported", "required": False,
                       "detail": "Browser capability is checked on macOS only"})
    else:
        if browser_available is None:
            browser_result = _browser_check()
        else:
            browser_result = {"name": "macOS browser",
                              "status": "available" if browser_available else "missing",
                              "required": True,
                              "detail": "A supported browser application was detected" if browser_available
                              else "No supported browser application was detected"}
        checks.append(browser_result)

    pdf_tools = []
    for tool in ("pdftotext", "pdfinfo"):
        pdf_tools.append(tool if which(tool) else None)
    # Discover the optional package without importing its native extension.
    pymupdf = importlib.util.find_spec("fitz") is not None
    checks.append({"name": "PDF tools", "status": "available" if any(pdf_tools) or pymupdf else "optional_missing",
                   "required": False,
                   "detail": "Optional PDF extraction support" if any(pdf_tools) or pymupdf
                   else "Optional PDF extraction tools were not detected"})

    result = {"checks": checks,
              "ready": all(check["status"] == "available" for check in checks if check["required"])}
    if not shared:
        result["system"] = os_name
    return result
