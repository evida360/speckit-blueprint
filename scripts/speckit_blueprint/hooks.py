"""Claude Code event handlers, behind one internal command (`speckit.blueprint.hook`).

The event's JSON arrives on stdin; `hook_event_name` picks the handler:
- SessionStart: register the session on the board and print the short status block.
- PreToolUse: the guard (compiled files, managed blocks, guaranteed effort for blueprint and review
  writes) and, for Bash, `require-effort` and the one-epic-per-worktree check for compile/activate.
- Stop: register the session and summarise its transcript into the metrics store, with the feature
  active now; summarise again the sessions that ended since (their cost arrives when they end). In a
  blueprint feature, a Spec Kit recommendation that names no source keeps the agent going once.
- Review passes: a subagent that ran `require-effort review --pass N` writes only its feature's review.md
  and runs no command that changes the work; a second pass needs a fresh subagent.
- Segments: `segments start|next` in the session records its effort; a subagent's `segment start <S>`
  registers it as that segment's one builder (refused below the run's effort, or when another subagent
  holds the segment). A builder writes no bookkeeping or compiled file, nothing another running segment
  owns, nothing before its required skills are loaded; it never commits, and in a parallel stage never
  installs packages. While a stage runs, nobody else writes in the project.

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
    msg = uncited_defaults(root, cfg, payload)
    return block(msg) if msg else 0


def uncited_defaults(root: Path, cfg: dict, payload: dict) -> str | None:
    """A Spec Kit recommendation (`**Recommended:**`, `**Suggested:**`, `**Suggested Answers**`) in a
    blueprint feature that names no source (conventions §8). Once per turn: never when the Stop hook
    already kept the agent going."""
    if not cfg.get("guard", True) or payload.get("stop_hook_active"):
        return None
    fd = active_feature(root)
    if not fd or read_trace(root / fd) is None:
        return None
    from .answers import uncited

    lines = uncited(str(payload.get("last_assistant_message") or ""))
    if not lines:
        return None
    return ("speckit-blueprint: a proposed answer in a blueprint feature names no source: "
            + " · ".join(lines[:3]) + ". Look it up (`blueprint.py answers`, `search <words>`, `find <ID>`, "
            "`section <n>`) and tag it `[BLUEPRINT — <section or ID>]`, or say `[NOT IN BLUEPRINT — searched: …]` "
            "and mark it as your own (conventions §8). Restate the question with its source.")


def block(message: str) -> int:
    sys.stderr.write(message.rstrip() + "\n")
    return 2


def pre_tool_use(root: Path, cfg: dict, payload: dict) -> int:
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    if tool == "Bash":
        return bash_check(root, cfg, payload, str(ti.get("command") or ""))
    if tool == "Skill":
        skill_loaded(root, cfg, payload, str(ti.get("skill") or ""))
        return 0
    if tool not in WRITE_TOOLS:
        return 0
    fp = ti.get("file_path") or ti.get("notebook_path")
    if not fp:
        return 0
    msg = reviewer_write(root, cfg, payload, fp)  # before the hub check: code repositories live outside it
    if msg:
        return block(msg)
    msg = build_write(root, cfg, payload, fp)
    if msg:
        return block(msg)
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
    msg = build_bash(root, cfg, payload, command)
    if msg:
        return block(msg)
    m = REQUIRE_RE.search(command)
    if m:
        msg = shortfall(cfg, m.group(1), payload, f"the {m.group(1)} step")
        if msg:
            return block(msg)
        if m.group(1) == "review" and m.group(2) and payload.get("agent_id") and cfg.get("guard", True):
            msg = start_review_pass(root, payload, int(m.group(2)))
            if msg:
                return block(msg)
    who = reviewer_entry(root, cfg, payload)
    if who and MUTATING_RE.search(command):
        return block(REVIEWER_REFUSAL.format(what=f"`{command.strip()[:80]}`"))
    m = COMPILE_RE.search(command)
    if m and target_folder(root, cfg, m.group(2)) != active_feature(root):
        msg = same_tree_conflict(root, payload)
        if msg:
            return block(msg)
    return 0


REVIEWER_REFUSAL = ("speckit-blueprint: a review pass lists defects and fixes none: {what} would change the work "
                    "under review. Write it as a finding in this pass; the session fixes the findings after the pass "
                    "and a fresh pass reviews everything again.")
# Commands a reviewer may not run: they change the work, the index or the history.
MUTATING_RE = re.compile(r"\bgit\s+(add|commit|stash|checkout|switch|reset|restore|apply|rebase|merge|cherry-pick|rm|mv)\b"
                         r"|\bsed\s+(-[a-zA-Z]*i|--in-place)|\bperl\s+-[a-zA-Z]*i")


def reviewer_entry(root: Path, cfg: dict, payload: dict) -> dict | None:
    if not cfg.get("guard", True) or not payload.get("agent_id"):
        return None
    from .board import reviewer

    return reviewer(root, payload["agent_id"])


def start_review_pass(root: Path, payload: dict, n: int) -> str | None:
    """Register this subagent as review pass n; a subagent that already ran a pass may not run another."""
    from .board import register_reviewer

    feature = active_feature(root)
    earlier = register_reviewer(root, payload["agent_id"], {"feature": feature, "pass": n, "worktree": str(root.resolve())})
    if earlier and earlier.get("pass") != n:
        return (f"speckit-blueprint: review pass {n} must start in a fresh subagent; this one reviewed pass "
                f"{earlier.get('pass')}. Start a new subagent with the Agent tool (never SendMessage to an earlier "
                "reviewer), so the pass reviews everything from scratch.")
    return None


def reviewer_write(root: Path, cfg: dict, payload: dict, fp: str) -> str | None:
    """A registered reviewer writes its feature's review.md and nothing else."""
    who = reviewer_entry(root, cfg, payload)
    if not who:
        return None
    wt = Path(who.get("worktree") or root)
    allowed = (wt / (who.get("feature") or "") / "review.md").resolve() if who.get("feature") else None
    target = (root / fp).resolve()
    if allowed is not None and target == allowed:
        return None
    return REVIEWER_REFUSAL.format(what=f"writing {fp}")


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


# --------------------------------------------------------------------------------------------
# Segments (segments.py): the one subagent per segment, and what it may write and run

SEGMENTS_RE = re.compile(r"blueprint\.py[\"']?\s+segments\s+(start|next)\b")
SEGMENT_START_RE = re.compile(r"blueprint\.py[\"']?\s+segment\s+start\s+([A-Za-z0-9.-]+)")
SEGMENT_ANY_RE = re.compile(r"blueprint\.py[\"']?\s+segment\s+(ask|done)\s+([A-Za-z0-9.-]+)")
ORCHESTRATOR_RE = re.compile(r"blueprint\.py[\"']?\s+segments\b")
TICK_CMD_RE = re.compile(r"blueprint\.py[\"']?\s+tick((?:\s+T\d+)+)")
BUILDER_GIT_RE = re.compile(r"\bgit\s+(add|commit|push|stash|checkout|switch|reset|restore|rebase|merge|cherry-pick|tag)\b")
INSTALL_RE = re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:i|install|add|ci)\b|\bpip3?\s+install\b|\bpoetry\s+add\b"
                        r"|\buv\s+(?:add|pip\s+install|sync)\b|\bgo\s+get\b|\bcargo\s+add\b|\bbundle\s+(?:add|install)\b"
                        r"|\bcomposer\s+(?:require|install)\b|\bdotnet\s+add\b")
BUILD_REFUSAL = "speckit-blueprint (segments): {what}"


def guard_on(cfg: dict) -> bool:
    return bool(cfg.get("guard", True))


def build_bash(root: Path, cfg: dict, payload: dict, command: str) -> str | None:
    from .segments import builder, run_of_builder

    agent = payload.get("agent_id")
    if not agent and SEGMENTS_RE.search(command):
        record_pending(root, payload)  # the effort the segments get, guard or not
        return None
    if not guard_on(cfg):
        return None
    board = load_board(root)
    m = SEGMENT_START_RE.search(command)
    if m and agent:
        return register_builder(root, cfg, payload, m.group(1))
    who = builder(board, agent)
    if not who:
        return None
    run = run_of_builder(board, who)
    if not run:
        return None
    seg = run["segments"].get(who["segment"]) or {}
    if ORCHESTRATOR_RE.search(command):
        return BUILD_REFUSAL.format(what="`segments` commands belong to the orchestrating session; a segment uses `segment …`, `tick` and `decision add`.")
    m = SEGMENT_ANY_RE.search(command)
    if m and m.group(2) != who["segment"]:
        return BUILD_REFUSAL.format(what=f"you build {who['segment']}, not {m.group(2)}.")
    m = TICK_CMD_RE.search(command)
    if m:
        foreign = [t for t in m.group(1).split() if t not in seg.get("tasks", [])]
        if foreign:
            return BUILD_REFUSAL.format(what=f"{', '.join(foreign)} belong(s) to another segment; tick only your own tasks "
                                             f"({', '.join(seg.get('tasks', [])) or 'none'}).")
    if BUILDER_GIT_RE.search(command):
        return BUILD_REFUSAL.format(what=f"`{command.strip()[:80]}` changes the index, the branch or the history; "
                                         "commits stay with the user in the main session. Leave your changes in the working tree.")
    stage = next((st for st in run["stages"] if st["n"] == seg.get("stage")), {})
    if stage.get("kind") == "parallel" and INSTALL_RE.search(command):
        return BUILD_REFUSAL.format(what=f"`{command.strip()[:80]}` changes the dependencies other segments build against "
                                         "at the same time. Return a CHECKPOINT naming the package; a serial segment adds it.")
    return None


def record_pending(root: Path, payload: dict) -> None:
    """The orchestrating session's effort, for `segments start|next` to give its subagents."""
    from .board import update
    from .effort import payload_level
    import time as _time

    entry = {"effort": payload_level(payload), "session": payload.get("session_id"),
             "transcript": payload.get("transcript_path"), "permission_mode": payload.get("permission_mode"), "at": _time.time()}

    def fn(board: dict) -> None:
        board.setdefault("build_pending", {})[str(root.resolve())] = entry

    try:
        update(root, fn)
    except SystemExit:
        pass


def register_builder(root: Path, cfg: dict, payload: dict, sid: str) -> str | None:
    """A subagent's `segment start <S>`: it becomes S's one builder, at no less than the run's effort."""
    from .board import update
    from .config import effort_rank
    from .effort import payload_level
    from .segments import active_run, build_key
    import time as _time

    agent = str(payload["agent_id"])
    feature = active_feature(root)
    problem: list = []

    def fn(board: dict) -> None:
        run = active_run(board, root, feature)
        if not run:
            problem.append("no build in segments is running for the active feature; the orchestrator runs `segments start`.")
            return
        seg = run["segments"].get(sid)
        if not seg:
            problem.append(f"no segment {sid} in run {run['run']}.")
            return
        if seg["stage"] != run["current"]:
            problem.append(f"{sid} belongs to stage {seg['stage']}; stage {run['current']} is being built.")
            return
        mine = board["builders"].get(agent)
        if mine and mine.get("run") == run["run"] and mine.get("segment") != sid:
            problem.append(f"this subagent builds {mine['segment']}; every segment gets its own fresh subagent.")
            return
        if seg.get("agent") and seg["agent"] != agent and seg["status"] in ("running", "waiting"):
            problem.append(f"{sid} is held by another subagent ({seg['agent']}); `segments next --retry {sid}` hands it to a fresh one.")
            return
        need, have = run.get("effort"), payload_level(payload)
        if need and have and effort_rank(have) < effort_rank(need):
            import os as _os

            env = _os.environ.get("CLAUDE_CODE_EFFORT_LEVEL") or _os.environ.get("CLAUDE_CODE_SUBAGENT_EFFORT")
            cause = (f"an effort environment variable is set ({env})" if env else
                     "it was started without the printed `effort`, or an organization cap or the model limits it")
            problem.append(f"this subagent runs at effort {have}, below the session's {need} ({cause}). "
                           f"Start it again with the Agent tool's `effort: {need}`.")
            return
        seg["agent"] = agent
        if seg["status"] == "pending":
            seg["status"] = "running"
            seg["attempts"] = seg.get("attempts", 0) + 1
        board["builders"][agent] = {"key": build_key(root, feature), "run": run["run"], "segment": sid,
                                    "worktree": str(root.resolve()), "effort": have, "skills_loaded": [], "at": _time.time()}
        cutoff = _time.time() - 2 * 86400
        board["builders"] = {a: w for a, w in board["builders"].items() if w.get("at", 0) >= cutoff}

    update(root, fn)
    return BUILD_REFUSAL.format(what=problem[0] + " Nothing was done.") if problem else None


def skill_loaded(root: Path, cfg: dict, payload: dict, skill: str) -> None:
    if not guard_on(cfg) or not payload.get("agent_id") or not skill:
        return
    from .board import update

    agent = str(payload["agent_id"])

    def fn(board: dict) -> None:
        w = board.get("builders", {}).get(agent)
        if w is not None and skill not in w.setdefault("skills_loaded", []):
            w["skills_loaded"].append(skill)

    try:
        update(root, fn)
    except SystemExit:
        pass


def bookkeeping(root: Path, cfg: dict, run: dict, target: Path) -> str | None:
    """Files only the extension's commands write while segments build."""
    feat = (Path(run["worktree"]) / run["feature"]).resolve()
    if target.parent == feat:
        if target.name in ("tasks.md", "build.md"):
            return f"{target.name} is written by `blueprint.py {'tick' if target.name == 'tasks.md' else 'segment …'}`"
        if target.name == "review.md":
            return "review.md belongs to the review that follows the build"
        if target.name in COMPILED_FILES or target.name == TRACE_FILE:
            return f"{target.name} is compiled from the blueprint"
    if feat in target.parents and "contracts" in target.relative_to(feat).parts[:1]:
        return "contracts/ is compiled from the blueprint"
    bcfg = bp_config(cfg, run.get("blueprint_id"))
    wt = Path(run["worktree"])
    if target == (wt / bcfg["decisions_path"]).resolve():
        return "the decisions log is written by `blueprint.py decision add`"
    for bid in cfg["blueprints"]:
        if target == (wt / bp_config(cfg, bid)["blueprint_path"]).resolve():
            return "the blueprint changes only through /speckit-blueprint-amend (a CHECKPOINT)"
    return None


def build_write(root: Path, cfg: dict, payload: dict, fp: str) -> str | None:
    if not guard_on(cfg):
        return None
    from .segments import active_run, builder, path_hits, run_of_builder, running_segments

    board = load_board(root)
    target = (root / fp).resolve()
    who = builder(board, payload.get("agent_id"))
    run = run_of_builder(board, who) if who else None
    if who and run:
        sid = who["segment"]
        why = bookkeeping(root, cfg, run, target)
        if why:
            return BUILD_REFUSAL.format(what=f"{sid} may not write {fp}: {why}.")
        missing = [s for s in run.get("skills") or [] if s not in who.get("skills_loaded", [])]
        if missing:
            return BUILD_REFUSAL.format(what=f"load {', '.join(missing)} with the Skill tool before your first edit.")
        t = str(target)
        for other in run["segments"].values():
            if other["id"] == sid or other["stage"] != run["current"] or other["status"] not in ("running", "waiting", "pending"):
                continue
            if any(path_hits(t, d) for d in other.get("paths", []) + other.get("leases", [])):
                return BUILD_REFUSAL.format(what=f"{fp} belongs to {other['id']}, which runs at the same time. "
                                                 "If your task needs it, return a CHECKPOINT saying so.")
        mine = run["segments"].get(sid) or {}
        if not any(path_hits(t, d) for d in mine.get("paths", []) + mine.get("leases", [])):
            lease(root, who["key"], sid, t)
        return None
    run = active_run(board, root)
    if not run or not running_segments(run):
        return None
    if not any(Path(r) == target or Path(r) in target.parents for r in run.get("roots") or [str(root.resolve())]):
        return None
    if payload.get("agent_id") and reviewer_entry(root, cfg, payload):
        return None
    names = ", ".join(s["id"] for s in running_segments(run))
    who_txt = "an unregistered subagent" if payload.get("agent_id") else "the orchestrating session"
    return BUILD_REFUSAL.format(what=f"{who_txt} may not write {fp} while segments {names} build in this tree. "
                                     "Segment subagents start with `blueprint.py segment start <S>`; the orchestrator "
                                     "writes nothing (`blueprint.py segments stop` ends the run).")


def lease(root: Path, key: str, sid: str, path: str) -> None:
    from .board import update

    def fn(board: dict) -> None:
        run = board.get("builds", {}).get(key)
        seg = (run or {}).get("segments", {}).get(sid)
        if seg is not None and path not in seg.setdefault("leases", []):
            seg["leases"].append(path)

    try:
        update(root, fn)
    except SystemExit:
        pass
