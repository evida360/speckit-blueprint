"""Claude Code event handlers, behind one internal command (`speckit.blueprint.hook`).

The event's JSON arrives on stdin; `hook_event_name` picks the handler:
- SessionStart: register the session on the board and print the short status block.
- PreToolUse: the guard (compiled files, managed blocks, guaranteed effort for blueprint and review
  writes) and, for Bash, `require-effort` and the one-epic-per-worktree check for compile/activate.
- Stop: register the session and summarise its transcript into the metrics store, with the feature
  active now; summarise again the sessions that ended since (their cost arrives when they end).

A hook never blocks a session because of its own failure: errors end in exit 0.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .core import COMPILED_FILES, EXT_ID, GENERATED_MARK, TEMPLATE_MARK, TRACE_FILE, active_feature, read_trace
from .config import bp_config, select_id
from .blocks import CONSTITUTION, managed_blocks
from .board import live, load as load_board, register_session, same_path
from .status import brief, full_status
from .effort import REQUIRE_RE, shortfall

WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
COMPILE_RE = re.compile(r"blueprint\.py[\"']?\s+(compile|activate)\s+([^\s;&|]+)")


def fresh_project(root: Path, cfg: dict) -> bool:
    """No feature folders yet and no constitution beyond the `specify init` template."""
    specs = root / cfg["specs_dir"]
    if specs.is_dir() and any(d.is_dir() for d in specs.iterdir()):
        return False
    c = root / CONSTITUTION
    return not c.is_file() or TEMPLATE_MARK in c.read_text(encoding="utf-8")


def touches_rules(ti: dict, text: str, spans: list[tuple[int, int]]) -> bool:
    """Whether an Edit/MultiEdit/Write would change a managed rules block of the constitution."""
    if "content" in ti:
        new = ti.get("content") or ""
        return any(text[s:e] not in new for s, e in spans)
    olds = [ti.get("old_string")] + [e.get("old_string") for e in ti.get("edits") or []]
    for o in olds:
        if not o:
            continue
        i = text.find(o)
        while i >= 0:
            if any(i < e and i + len(o) > s for s, e in spans):
                return True
            i = text.find(o, i + 1)
    return False


def read_payload() -> dict:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def dispatch(root: Path, cfg: dict, explicit: str | None = None) -> int:
    payload = read_payload()
    event = {"session-start": "SessionStart", "guard": "PreToolUse", "stop": "Stop"}.get(explicit or "", payload.get("hook_event_name", ""))
    if not event and payload.get("tool_name"):
        event = "PreToolUse"
    try:
        if event == "SessionStart":
            return session_start(root, cfg, payload)
        if event == "PreToolUse":
            return pre_tool_use(root, cfg, payload)
        if event == "Stop":
            return stop(root, cfg, payload)
    except Exception as exc:  # noqa: BLE001 - a hook must never break the session
        if event == "SessionStart":
            print(f"[speckit-blueprint] hook error: {exc}")
    return 0


def session_info(root: Path, payload: dict) -> dict:
    return {"worktree": str(root.resolve()), "transcript": payload.get("transcript_path"),
            "feature": active_feature(root), "cwd": payload.get("cwd")}


def session_start(root: Path, cfg: dict, payload: dict) -> int:
    register_session(root, payload.get("session_id", ""), {**session_info(root, payload), "started": True})
    if not any((root / bp_config(cfg, bid)["blueprint_path"]).is_file() for bid in cfg["blueprints"]):
        # Without a blueprint, speak only in a fresh project; an existing Spec Kit project keeps its own flow.
        if not (root / ".specify" / "extensions" / EXT_ID).is_dir() or not fresh_project(root, cfg):
            return 0
    try:
        print(brief(full_status(root, cfg, select_id(root, cfg))))
    except Exception as exc:  # a status failure must never block a session
        print(f"[speckit-blueprint] status unavailable: {exc}")
    return 0


def stop(root: Path, cfg: dict, payload: dict) -> int:
    """The turn's work goes to the feature active in this checkout (none when it is not a blueprint
    epic's); sessions that ended since their last summary are read again for the cost they recorded."""
    register_session(root, payload.get("session_id", ""), session_info(root, payload))
    if cfg.get("metrics", "local") != "off" and payload.get("transcript_path"):
        from .metrics import Ctx, refresh_finished, summarise_session

        ctx = Ctx(root, cfg)
        summarise_session(root, cfg, Path(payload["transcript_path"]), ctx, active=active_feature(root) or "")
        refresh_finished(root, cfg, ctx, skip=payload.get("session_id"))
    return 0


def block(message: str) -> int:
    sys.stderr.write(message.rstrip() + "\n")
    return 2


def pre_tool_use(root: Path, cfg: dict, payload: dict) -> int:
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    if tool == "Bash":
        return bash_check(root, cfg, payload, str(ti.get("command") or ""))
    if tool not in WRITE_TOOLS:
        return 0
    fp = ti.get("file_path") or ti.get("notebook_path")
    if not fp:
        return 0
    try:
        rel = (root / fp).resolve().relative_to(root.resolve())
    except ValueError:
        return 0
    effort_msg = effort_check_write(root, cfg, payload, rel, ti)
    if effort_msg:
        return block(effort_msg)
    if not cfg.get("guard", True):
        return 0
    parts = rel.parts
    blocked = None
    sources = sorted({bp_config(cfg, bid)["blueprint_path"] for bid in cfg["blueprints"]})
    if rel.as_posix() == CONSTITUTION.as_posix():
        c = root / rel
        text = c.read_text(encoding="utf-8") if c.is_file() else ""
        blocks = managed_blocks(text, "rules:")
        if GENERATED_MARK in text:
            blocked = "the constitution is compiled from the blueprint (`blueprint.py bootstrap`)"
        elif blocks and touches_rules(ti, text, [(s, e) for _, s, e in blocks]):
            blocked = "its Blueprint Rules blocks are compiled from the blueprints (`blueprint.py bootstrap`); the rest of the file is yours"
    elif len(parts) >= 3 and parts[0] == cfg["specs_dir"] and (root / parts[0] / parts[1] / TRACE_FILE).is_file():
        inner = Path(*parts[2:]).as_posix()
        if inner in COMPILED_FILES or inner.startswith("contracts/"):
            blocked = f"{inner} is compiled from the blueprint (/speckit-blueprint-compile)"
            trace = read_trace(root / parts[0] / parts[1]) or {}
            if trace.get("blueprint"):
                sources = [trace["blueprint"]]
    if blocked:
        return block(
            f"speckit-blueprint guard: {rel.as_posix()} — {blocked}. Change {' or '.join(sources)} through "
            "/speckit-blueprint-amend and recompile instead. tasks.md and review.md stay editable. "
            "(Disable with `guard: false` in .specify/extensions/blueprint/blueprint-config.yml.)"
        )
    return 0


def new_text(ti: dict) -> str:
    return "\n".join([str(ti.get("content") or ""), str(ti.get("new_string") or "")]
                     + [str(e.get("new_string") or "") for e in ti.get("edits") or []])


def effort_check_write(root: Path, cfg: dict, payload: dict, rel: Path, ti: dict) -> str | None:
    """Blueprints (and their discovery checkpoints) and DONE reviews are written only at the guaranteed level."""
    if not cfg.get("effort"):
        return None
    posix = rel.as_posix()
    for bid in cfg["blueprints"]:
        path = bp_config(cfg, bid)["blueprint_path"]
        folder = Path(path).parent.as_posix()
        if posix == path:
            step = "amend" if (root / path).is_file() and "amend" in cfg["effort"] else "discover"
            return shortfall(cfg, step, payload, f"writing {path}")
        if posix.startswith(f"{folder}/discovery/" if folder != "." else "discovery/"):
            return shortfall(cfg, "discover", payload, f"writing {posix}")
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == cfg["specs_dir"] and parts[-1] == "review.md" \
            and (root / parts[0] / parts[1] / TRACE_FILE).is_file() and re.search(r"Status:\s*\**\s*DONE", new_text(ti)):
        return shortfall(cfg, "review", payload, f"marking {posix} DONE")
    return None


def bash_check(root: Path, cfg: dict, payload: dict, command: str) -> int:
    m = REQUIRE_RE.search(command)
    if m:
        msg = shortfall(cfg, m.group(1), payload, f"the {m.group(1)} step")
        if msg:
            return block(msg)
    m = COMPILE_RE.search(command)
    if m and target_folder(root, cfg, m.group(2)) != active_feature(root):
        msg = same_tree_conflict(root, payload)
        if msg:
            return block(msg)
    return 0


def target_folder(root: Path, cfg: dict, ref: str) -> str | None:
    """The feature folder a compile/activate argument names, if it is compiled here."""
    from .config import split_qualified
    from .compiler import find_feature_dir

    ref = ref.strip("'\"")
    if "/" in ref:
        return ref.rstrip("/")
    try:
        bid, key = split_qualified(cfg, ref)
        bcfg = bp_config(cfg, bid or select_id(root, cfg))
        num = re.sub(r"\D", "", key)
        d = find_feature_dir(root, bcfg, f"E{int(num):02d}") if num else None
    except Exception:  # noqa: BLE001
        return None
    return d.relative_to(root).as_posix() if d else None


def same_tree_conflict(root: Path, payload: dict) -> str | None:
    """Two live sessions in one worktree must not switch its active feature under each other."""
    me = payload.get("session_id")
    current = active_feature(root)
    for sid, s in load_board(root).get("sessions", {}).items():
        if sid == me or not same_path(s.get("worktree", ""), root) or not live(s):
            continue
        if s.get("feature") and s.get("feature") == current:
            return (
                f"speckit-blueprint: another live session in this worktree ({sid[:8]}) is working on {current}. "
                "Compiling or activating another epic here would switch its active feature. Build the other epic in "
                "its own worktree: `blueprint.py workspace <epic> --create`, then open a session there."
            )
    return None
