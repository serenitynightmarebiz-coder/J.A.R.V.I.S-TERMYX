"""Self-modification: JARVIS can read, rewrite and test its own source.

Guard rails, because a model editing its own code is a loaded gun:

  * writes are confined to the project directory (never config.json, data/, logs/, .git)
  * only text file types are editable
  * every write is backed up to data/backups/ first
  * every edit is syntax-checked; Python that does not compile is rolled back
  * `self_test` runs the real test suite and reports the tail
  * reloading is an explicit step (`/reload` or the daemon's reload), so a broken
    half-edit never silently replaces a working assistant
"""

from __future__ import annotations

import json
import os
import py_compile
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .. import shims
from ..config import APP_DIR
from .registry import tool

PROJECT = Path(APP_DIR).resolve()
BACKUPS = PROJECT / "data" / "backups"
RESTART_FLAG = PROJECT / "data" / "restart.flag"

EDITABLE_SUFFIXES = {".py", ".md", ".json", ".sh", ".txt", ".yml", ".yaml", ".toml", ".cfg"}
PROTECTED = {"config.json", ".gitignore", "LICENSE"}

_CFG = None


def bind(cfg) -> None:
    global _CFG
    _CFG = cfg


def _enabled() -> tuple[bool, str]:
    if _CFG is not None and not getattr(_CFG, "allow_self_modify", True):
        return False, "Self-modification is disabled (allow_self_modify=false)."
    return True, ""


def _resolve(path: str) -> tuple[Path | None, str]:
    """Resolve a project-relative path, refusing anything outside the project."""
    raw = os.path.expanduser(str(path).strip())
    if os.path.isabs(raw):
        candidate = Path(raw).resolve()
    else:
        candidate = (PROJECT / raw).resolve()
    if PROJECT not in candidate.parents and candidate != PROJECT:
        return None, f"FAILED: {path} is outside the project directory."
    if candidate.name in PROTECTED or candidate.parts[-1:] == ("config.json",):
        return None, f"FAILED: {candidate.name} is protected."
    rel_parts = candidate.relative_to(PROJECT).parts
    if rel_parts and rel_parts[0] in {"data", "logs", ".git"}:
        return None, f"FAILED: {rel_parts[0]}/ is off limits."
    return candidate, ""


def _validate(path: Path) -> tuple[bool, str]:
    suffix = path.suffix.lower()
    if suffix not in EDITABLE_SUFFIXES:
        return False, f"FAILED: {suffix or 'no extension'} files are not editable."
    if suffix == ".py":
        try:
            py_compile.compile(str(path), doraise=True, cfile="/tmp/jarvis_compile_check.pyc")
        except py_compile.PyCompileError as exc:
            return False, f"Python syntax error: {str(exc).splitlines()[-1][:300]}"
    elif suffix == ".json":
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            return False, f"Invalid JSON: {exc}"
    elif suffix == ".sh":
        rc, _, err = shims.shell(f"bash -n {path}")
        if rc != 0:
            return False, f"Shell syntax error: {err.strip()[:300]}"
    return True, ""


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUPS / f"{path.name}.{stamp}.bak"
    shutil.copy2(path, dest)
    return dest


def _restore(path: Path, backup: Path | None) -> str:
    if backup and backup.exists():
        shutil.copy2(backup, path)
        return f"rolled back to {backup.name}"
    if path.exists():
        path.unlink()
    return "removed the new file"


# ------------------------------------------------------------------- inspection
@tool("self_inspect", "Inspect JARVIS's own source tree: files, sizes and tool count.",
      {"path": {"type": "string", "default": "."}}, category="self")
def self_inspect(path: str = ".") -> str:
    target, err = _resolve(path)
    if not target:
        return err
    if target.is_file():
        return f"{target.relative_to(PROJECT)} — {target.stat().st_size} bytes"
    rows = []
    for item in sorted(target.iterdir()):
        if item.name in {".git", "__pycache__", "data", "logs"}:
            continue
        size = item.stat().st_size if item.is_file() else sum(
            f.stat().st_size for f in item.rglob("*") if f.is_file()
        )
        rows.append(f"{item.name}{'/' if item.is_dir() else ''} {size}b")
    from .registry import REGISTRY

    return "\n".join(rows) + f"\n\n{len(REGISTRY)} tools registered."


@tool("self_read", "Read one of JARVIS's own source files.",
      {"path": {"type": "string", "required": True},
       "start": {"type": "integer", "default": 1},
       "lines": {"type": "integer", "default": 400}}, category="self")
def self_read(path: str, start: int = 1, lines: int = 400) -> str:
    target, err = _resolve(path)
    if not target:
        return err
    if not target.exists() or not target.is_file():
        return f"FAILED: {path} does not exist."
    body = target.read_text(errors="replace").splitlines()
    chunk = body[max(0, start - 1) : max(0, start - 1) + max(1, lines)]
    numbered = "\n".join(f"{i + start:4}  {line}" for i, line in enumerate(chunk))
    return f"{target.relative_to(PROJECT)} ({len(body)} lines)\n{numbered}"[:8000]


# --------------------------------------------------------------------- editing
@tool(
    "self_edit",
    "Change JARVIS's own code by replacing an exact snippet with new text. "
    "Use this to fix a bug, add a capability or tune behaviour. Backs up and "
    "syntax-checks automatically; a broken edit is rolled back.",
    {
        "path": {"type": "string", "required": True, "description": "project-relative file"},
        "find": {"type": "string", "required": True, "description": "exact existing text, unique in the file"},
        "replace": {"type": "string", "required": True, "description": "text to put in its place"},
    },
    danger=True,
    category="self",
)
def self_edit(path: str, find: str, replace: str) -> str:
    ok, why = _enabled()
    if not ok:
        return why
    target, err = _resolve(path)
    if not target:
        return err
    if not target.exists():
        return f"FAILED: {path} does not exist (use self_write to create files)."
    original = target.read_text()
    count = original.count(find)
    if count == 0:
        return "FAILED: snippet not found. Read the file again and copy the exact text."
    if count > 1:
        return f"FAILED: snippet appears {count} times — include more surrounding text."
    updated = original.replace(find, replace, 1)
    if updated == original:
        return "No change: replacement is identical to the original."
    backup = _backup(target)
    target.write_text(updated)
    good, problem = _validate(target)
    if not good:
        note = _restore(target, backup)
        return f"REJECTED: {problem}. {note}."
    return (
        f"Edited {target.relative_to(PROJECT)} (+{len(updated) - len(original)} chars). "
        f"Backup: {backup.name if backup else 'none'}. Run self_test, then /reload to apply."
    )


@tool(
    "self_write",
    "Create or overwrite a file in JARVIS's own project (new tool module, notes, docs). "
    "Python is syntax-checked and rolled back if it does not compile.",
    {"path": {"type": "string", "required": True},
     "content": {"type": "string", "required": True}},
    danger=True,
    category="self",
)
def self_write(path: str, content: str) -> str:
    ok, why = _enabled()
    if not ok:
        return why
    target, err = _resolve(path)
    if not target:
        return err
    if target.suffix.lower() not in EDITABLE_SUFFIXES:
        return f"FAILED: {target.suffix or 'no extension'} files are not editable."
    backup = _backup(target) if target.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    good, problem = _validate(target)
    if not good:
        note = _restore(target, backup)
        return f"REJECTED: {problem}. {note}."
    return (
        f"Wrote {len(content)} bytes to {target.relative_to(PROJECT)}. "
        f"Run self_test, then /reload to apply."
    )


@tool("self_rollback", "Restore a file from the newest backup made by self_edit/self_write.",
      {"path": {"type": "string", "required": True}}, danger=True, category="self")
def self_rollback(path: str) -> str:
    target, err = _resolve(path)
    if not target:
        return err
    candidates = sorted(BACKUPS.glob(f"{target.name}.*.bak"))
    if not candidates:
        return f"FAILED: no backups for {target.name}."
    shutil.copy2(candidates[-1], target)
    return f"Restored {target.relative_to(PROJECT)} from {candidates[-1].name}."


# ------------------------------------------------------------------ validation
@tool("self_test", "Run JARVIS's own test suite and report the result.",
      {"suite": {"type": "string", "description": "optional module, e.g. tests.test_core", "default": ""},
       "timeout": {"type": "integer", "default": 180}},
      category="self")
def self_test(suite: str = "", timeout: int = 180) -> str:
    cmd = [sys.executable, "-m", "unittest"]
    cmd += [suite] if suite else ["discover", "-s", "tests"]
    cmd += ["-v"] if suite else []
    try:
        proc = subprocess.run(
            cmd, cwd=str(PROJECT), capture_output=True, text=True,
            timeout=min(int(timeout), 600), errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"FAILED: test suite exceeded {timeout}s."
    output = (proc.stderr or "") + (proc.stdout or "")
    tail = "\n".join(output.strip().splitlines()[-14:])
    verdict = "PASSED" if proc.returncode == 0 else "FAILED"
    return f"Tests {verdict} (exit {proc.returncode})\n{tail[:3000]}"


@tool("self_logs", "Read the tail of JARVIS's own logs.",
      {"lines": {"type": "integer", "default": 40},
       "file": {"type": "string", "default": "daemon.log"}}, category="self")
def self_logs(lines: int = 40, file: str = "daemon.log") -> str:
    log = PROJECT / "logs" / Path(file).name
    if not log.exists():
        available = [p.name for p in (PROJECT / "logs").glob("*")] if (PROJECT / "logs").exists() else []
        return f"No {log.name}. Available: {', '.join(available) or 'none'}."
    body = log.read_text(errors="replace").splitlines()
    return "\n".join(body[-max(1, min(int(lines), 300)) :])[:5000]


@tool("self_restart", "Apply code changes made by self_edit/self_write (reload and restart).",
      category="self")
def self_restart() -> str:
    RESTART_FLAG.parent.mkdir(parents=True, exist_ok=True)
    RESTART_FLAG.write_text(str(int(time.time())))
    return (
        "Restart requested. In a terminal session run /reload; the background "
        "daemon restarts itself within a second."
    )


@tool("self_capabilities", "Summarise JARVIS's own architecture, files and safety limits.",
      category="self")
def self_capabilities() -> str:
    from .registry import by_category

    cats = {name: len(items) for name, items in by_category().items()}
    return json.dumps(
        {
            "project": str(PROJECT),
            "entry": "jarvis.py",
            "packages": sorted(p.name for p in (PROJECT / "jarvis").glob("*.py")),
            "tools": cats,
            "self_modify": _CFG.allow_self_modify if _CFG else True,
            "editable": sorted(EDITABLE_SUFFIXES),
            "protected": sorted(PROTECTED) + ["data/", "logs/", ".git/"],
            "reload": "/reload (terminal) or automatic (daemon)",
        },
        indent=2,
    )
