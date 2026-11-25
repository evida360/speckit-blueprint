"""Building an epic in segments (DESIGN.md "Building an epic in segments").

The plan comes from tasks_graph: stages of segments. Every segment, the serial trunk and joins as much as
each parallel chain, is built by exactly one subagent, task after task, at the session's effort. The
session that runs /speckit-implement only orchestrates: it starts the subagents with the prompts printed
here, relays their questions to the user and the answers back, and runs the joins. Everything it needs to
decide is decided here.

State: the run record on the board (`builds`, keyed by worktree and feature), the subagents registered by
the PreToolUse hook (`builders`), tasks.md ticks, and build.md in the feature folder (append-only log:
runs, segment notes, questions, answers, joins).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .board import file_lock, load as load_board, now_iso, state_dir, update
from .config import bp_config, trace_blueprint_id
from .core import active_feature, read_trace
from .tasks_graph import TASK_RE, parse, plan as make_plan, render

SCRIPT = "python3 .specify/extensions/blueprint/scripts/blueprint.py"
PENDING_FRESH_S = 300  # how long the effort the hook saw on `segments start|next` stays the session's
RUN_STALE_S = 24 * 3600  # a run nobody touched for a day is no longer active
ACTIVE_SEGMENT = ("running", "waiting")
GUIDE = ".specify/extensions/blueprint/references/segments.md"


# --------------------------------------------------------------------------------------------
# Lookups shared with the hook

def build_key(root: Path, feature: str) -> str:
    return f"{root.resolve()}::{feature}"


def active_run(board: dict, root: Path, feature: str | None = None) -> dict | None:
    """The running build of this worktree (of `feature`, or of the active feature)."""
    feature = feature or active_feature(root)
    if not feature:
        return None
    run = board.get("builds", {}).get(build_key(root, feature))
    if not run or run.get("status") != "running":
        return None
    try:
        if time.time() - _dt.datetime.fromisoformat(run.get("updated", "")).timestamp() > RUN_STALE_S:
            return None
    except ValueError:
        return None
    return run


def running_segments(run: dict) -> list:
    stage = run.get("current", 0)
    return [s for s in run["segments"].values() if s["stage"] == stage and s["status"] in ACTIVE_SEGMENT]


def builder(board: dict, agent_id: str | None) -> dict | None:
    if not agent_id:
        return None
    return board.get("builders", {}).get(str(agent_id))


def run_of_builder(board: dict, who: dict) -> dict | None:
    run = board.get("builds", {}).get(who.get("key", ""))
    if not run or run.get("status") != "running" or run.get("run") != who.get("run"):
        return None
    return run


# --------------------------------------------------------------------------------------------
# The feature being built

class Feature:
    def __init__(self, root: Path, cfg: dict):
        self.root = root
        fd = active_feature(root)
        trace = read_trace(root / fd) if fd else None
        if not trace:
            raise SystemExit("error: the active feature was not compiled from a blueprint (segments build blueprint epics)")
        self.rel, self.dir, self.trace = fd, root / fd, trace
        self.bcfg = bp_config(cfg, trace_blueprint_id(cfg, trace) or None)
        self.epic = trace.get("epic", "")
        self.tasks_path = self.dir / "tasks.md"
        self.build_md = self.dir / "build.md"
        rows = sorted(trace.get("repositories") or [], key=lambda r: int(r.get("order") or 0))
        self.repo_order = [r["name"] for r in rows]
        self.repo_names = sorted(set(cfg.get("repositories", {})) | set(self.repo_order))
        self.key = build_key(root, fd)

    def tasks_text(self) -> str:
        if not self.tasks_path.is_file():
            raise SystemExit(f"error: {self.rel}/tasks.md does not exist; run /speckit-tasks first")
        return self.tasks_path.read_text(encoding="utf-8")

    def plan(self) -> dict:
        b = self.bcfg["build"]
        return make_plan(self.tasks_text(), self.repo_names, self.repo_order, b["max_parallel"], b["min_chain_tasks"])

    def repo_paths(self) -> dict:
        """repository name -> the path to work in (the epic's worktree when it has one); '' is the hub."""
        out = {"": str(self.root.resolve())}
        try:
            from .facts import epic_repo_rows
            from .model import Blueprint

            bp = Blueprint(self.root / self.bcfg["blueprint_path"])
            epic = bp.epic(self.epic[1:])
            if epic:
                for r in epic_repo_rows(self.root, self.bcfg, epic, self.trace):
                    if r.get("path"):
                        out[r["name"]] = str(Path(r["path"]).resolve()) if Path(r["path"]).is_absolute() \
                            else str((self.root / r["path"]).resolve())
        except Exception:  # noqa: BLE001 - paths are a convenience for the guard
            pass
        return out


def absolute_paths(files: list, repo_paths: dict) -> list:
    out = []
    for f in files:
        repo, sep, rel = f.partition(":")
        if not sep or repo not in repo_paths:
            repo, rel = "", f
        base = Path(repo_paths.get(repo) or repo_paths[""])
        p = str((base / rel).resolve())
        out.append(p + "/" if rel.endswith("/") else p)
    return out


def path_hits(target: str, declared: str) -> bool:
    return target == declared.rstrip("/") or (declared.endswith("/") and target.startswith(declared))


# --------------------------------------------------------------------------------------------
# build.md

def log(f: Feature, text: str) -> None:
    if not f.build_md.is_file():
        f.build_md.write_text(f"# Build log — {f.epic} {f.trace.get('title', '')}\n\n"
                              "Written by `blueprint.py segments` and `segment` (append-only). Runs, the notes each "
                              "segment left for the next, questions, answers and joins.\n", encoding="utf-8")
    with f.build_md.open("a", encoding="utf-8") as fh:
        fh.write("\n" + text.rstrip() + "\n")


# --------------------------------------------------------------------------------------------
# Run records

def new_segment(s: dict, kind: str, repo_paths: dict) -> dict:
    return {**s, "kind": kind, "status": "pending", "agent": None, "paths": absolute_paths(s["files"], repo_paths),
            "leases": [], "questions": [], "answers": [], "notes": "", "attempts": 0}


def session_pending(board: dict, root: Path) -> dict:
    p = board.get("build_pending", {}).get(str(root.resolve())) or {}
    return p if time.time() - float(p.get("at") or 0) < PENDING_FRESH_S else {}


def transcript_skills(transcript: str | None) -> list:
    """Skills the orchestrating session invoked (a hint for the segments; speckit-* are the flow's own)."""
    out: list = []
    if not transcript:
        return out
    try:
        with open(transcript, encoding="utf-8") as fh:
            for line in fh:
                if '"Skill"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                for c in ((e.get("message") or {}).get("content") or []):
                    if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Skill":
                        name = str((c.get("input") or {}).get("skill") or "")
                        if name and not name.startswith("speckit") and name not in out:
                            out.append(name)
    except OSError:
        pass
    return out


def start(root: Path, cfg: dict, resume: bool) -> dict:
    f = Feature(root, cfg)
    b = f.bcfg["build"]
    if b["mode"] != "segments":
        raise SystemExit("error: build.mode is sequential in the extension config; set `build: {mode: segments}` "
                         "to build in segments, or run /speckit-implement as usual")
    board = load_board(root)
    pending = session_pending(board, root)
    current = board.get("builds", {}).get(f.key)
    if current and current.get("status") == "running" and not resume and pending.get("session") \
            and current.get("session") == pending.get("session") and active_run(board, root, f.rel):
        return {"run": current["run"], "resumed": False, "already_running": True, **summary(current)}
    p = f.plan()
    if p["errors"]:
        return {"ok": False, "errors": p["errors"], "warnings": p["warnings"],
                "fix": "Fix tasks.md (`blueprint.py tasks-graph` shows the findings), then run `segments start` again."}
    repo_paths = f.repo_paths()
    segs: dict = {}
    for st in p["stages"]:
        for s in st["segments"]:
            segs[s["id"]] = new_segment(s, st["kind"], repo_paths)
    history = []
    if current:
        for s in current.get("segments", {}).values():
            if s.get("notes"):
                history.append({"segment": f"{current['run']}/{s['id']}", "notes": s["notes"]})
        history = (current.get("history") or []) + history
    run_id = "r" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run = {
        "run": run_id, "status": "running", "feature": f.rel, "epic": f.epic, "blueprint_id": f.bcfg["id"],
        "worktree": str(root.resolve()), "session": pending.get("session"), "effort": pending.get("effort"),
        "permission_mode": pending.get("permission_mode"), "plan_hash": p.get("hash"), "plan": render(p),
        "stages": [{"n": st["n"], "kind": st["kind"], "segments": [s["id"] for s in st["segments"]], "join": None}
                   for st in p["stages"]],
        "current": p["stages"][0]["n"] if p["stages"] else 0, "segments": segs, "history": history,
        "skills": list(b["skills"]), "skill_hints": transcript_skills(pending.get("transcript")),
        "roots": sorted(set(repo_paths.values())), "started": now_iso(), "updated": now_iso(),
        "stats": p["stats"], "warnings": p["warnings"],
    }
    lost = []

    def change(board: dict) -> None:
        old = board["builds"].get(f.key)
        if old:
            lost.extend(s["id"] for s in old.get("segments", {}).values() if s["status"] in ACTIVE_SEGMENT)
            for aid in [a for a, w in board["builders"].items() if w.get("key") == f.key]:
                del board["builders"][aid]
        board["builds"][f.key] = run if p["stages"] else {**run, "status": "finished"}

    update(root, change)
    log(f, f"## Run {run_id} — {now_iso()}\n\nEffort: {run['effort'] or 'the session’s (not reported)'} · "
           f"plan: {run['plan']}" + (f" · replaces {current['run']} (lost: {', '.join(lost) or 'none'})" if current else ""))
    out = {"ok": True, "run": run_id, "resumed": bool(current), "lost": lost, **summary(run)}
    if run["permission_mode"] == "default":
        out["warning"] = ("The session asks before every edit (permission mode `default`): each segment's edits "
                          "will prompt here. Suggest accept-edits or auto mode, or allow rules, before the segments start.")
    if not p["stages"]:
        out["done"] = True
    return out


def summary(run: dict) -> dict:
    return {"feature": run["feature"], "epic": run["epic"], "effort": run.get("effort"), "plan": run["plan"],
            "current_stage": run["current"], "stages": run["stages"], "skills": run.get("skills", []),
            "stats": run.get("stats"), "warnings": run.get("warnings", [])}


def load_run(root: Path, f: Feature) -> dict:
    run = active_run(load_board(root), root, f.rel)
    if not run:
        raise SystemExit("error: no build in segments is running for this feature; run `blueprint.py segments start`")
    return run


def save_segment(root: Path, f: Feature, sid: str, fn) -> dict:
    out: dict = {}

    def change(board: dict) -> None:
        run = board["builds"].get(f.key)
        if not run or run.get("status") != "running" or sid not in run["segments"]:
            raise SystemExit(f"error: no segment {sid} in the running build")
        fn(run, run["segments"][sid])
        run["updated"] = now_iso()
        out.update(run)

    update(root, change)
    return out


def ticked(f: Feature) -> set:
    return {t["id"] for t in parse(f.tasks_text())["tasks"] if t["done"]}


def stage_of(run: dict, n: int) -> dict | None:
    return next((st for st in run["stages"] if st["n"] == n), None)


def prompt_for(run: dict, f: Feature, seg: dict) -> str:
    st = stage_of(run, seg["stage"]) or {}
    others = [x for x in st.get("segments", []) if x != seg["id"]]
    lines = [
        f"You build segment {seg['id']} of epic {run['epic']} ({run['feature']}), run {run['run']}."
        + (f" Segments {', '.join(others)} are built at the same time in this working tree by other subagents."
           if others else " No other segment runs while you work."),
        "You are a subagent: you cannot ask the user anything; questions go through the commands below.",
        f"1. First run `{SCRIPT} segment start {seg['id']}`. If it is refused, stop and return the refusal verbatim. "
        "It prints your tasks, the files you own, the notes of earlier segments, the answers you already have and "
        "the skills to load.",
        f"2. Follow `{GUIDE}`, section \"Segment agent\", for everything else: one task at a time, in order; "
        f"`{SCRIPT} tick <task>` after each; questions with `segment ask`; decisions with `decision add`; "
        "`segment done` at the end.",
    ]
    if seg["kind"] == "fix":
        lines.append(f"3. Your work: {seg.get('goal')}")
    else:
        lines.append(f"3. Your tasks, in this order: {', '.join(seg['tasks'])}.")
    lines.append(f"Return at most 10 lines: `DONE {seg['id']}`, `CHECKPOINT {seg['id']}: <question IDs>` or "
                 f"`FAILED {seg['id']}: <reason>`, then anything the orchestrator must know.")
    return "\n".join(lines)


def next_segments(root: Path, cfg: dict, retry: list) -> dict:
    f = Feature(root, cfg)
    board = load_board(root)
    last = board.get("builds", {}).get(f.key) or {}
    if last.get("status") == "finished" and not retry:
        return {"done": True, "next": "Every stage is built and joined: return to /speckit-implement (every task is ticked)."}
    run = load_run(root, f)
    pending = session_pending(board, root)
    effort = pending.get("effort") or run.get("effort")
    done_tasks = ticked(f)
    if retry:
        def reset(run: dict, seg: dict) -> None:
            if seg["status"] == "done":
                raise SystemExit(f"error: {seg['id']} is done; nothing to retry")
            seg.update(status="pending", agent=None, leases=[])

        for sid in retry:
            run = save_segment(root, f, sid, reset)
        log(f, f"### Retry {', '.join(retry)} — {now_iso()}\n\nA fresh subagent continues from the first unticked task.")
    st = stage_of(run, run["current"])
    if st is None:
        return {"done": True, "next": "Every stage is built and joined: return to /speckit-implement (every task is ticked)."}
    segs = [run["segments"][s] for s in st["segments"]]
    if all(s["status"] == "done" for s in segs):
        return {"stage": st["n"], "join": True, "next": f"Every segment of stage {st['n']} is done: run `{SCRIPT} segments join`."}
    start_now = [s for s in segs if s["status"] == "pending"]
    if effort and effort != run.get("effort"):
        def set_effort(run: dict, seg: dict) -> None:
            run["effort"] = effort

        save_segment(root, f, st["segments"][0], set_effort)
    out = []
    for s in start_now:
        open_tasks = [t for t in s["tasks"] if t not in done_tasks]
        item = {"segment": s["id"], "subagent_type": "general-purpose", "description": f"{run['epic']} {s['id']}",
                "prompt": prompt_for(run, f, s), "open_tasks": open_tasks}
        if effort:
            item["effort"] = effort
        out.append(item)
    waiting = [{"segment": s["id"], "agent": s["agent"], "questions": s["questions"]} for s in segs if s["status"] == "waiting"]
    return {
        "stage": st["n"], "kind": st["kind"], "start": out, "running": [s["id"] for s in segs if s["status"] == "running"],
        "waiting": waiting,
        "how": ("Start every item of `start` with the Agent tool in ONE message: subagent_type, description, prompt and "
                "effort exactly as given (no model: the session's). Then end your turn; each subagent's completion "
                "notification starts a turn: run `segments status`.") if out else
               "Nothing to start: wait for the running segments (their completion notifications), relay `waiting` questions.",
    }


def status(root: Path, cfg: dict) -> dict:
    f = Feature(root, cfg)
    board = load_board(root)
    run = active_run(board, root, f.rel) or board.get("builds", {}).get(f.key)
    if not run:
        return {"running": False, "mode": f.bcfg["build"]["mode"]}
    done_tasks = ticked(f)
    segs = []
    for sid, s in run["segments"].items():
        segs.append({"segment": sid, "stage": s["stage"], "kind": s["kind"], "status": s["status"], "agent": s["agent"],
                     "tasks": f"{sum(t in done_tasks for t in s['tasks'])}/{len(s['tasks'])}",
                     **({"questions": s["questions"]} if s["status"] == "waiting" else {})})
    st = stage_of(run, run["current"])
    waiting = [s for s in segs if s["status"] == "waiting"]
    if run.get("status") != "running":
        nxt = "The build is not running (finished or stopped)."
    elif st is None:
        nxt = "Every stage is joined: return to /speckit-implement."
    elif waiting:
        nxt = ("Ask the user every question of every waiting segment now, with their tags (AskUserQuestion for closed "
               f"ones). Then for each segment: `{SCRIPT} segments answer <segment>` with the answers verbatim on stdin, "
               "and SendMessage the printed text to its agent.")
    elif all(run["segments"][x]["status"] == "done" for x in st["segments"]):
        nxt = f"Stage {st['n']} is done: `{SCRIPT} segments join`."
    elif any(run["segments"][x]["status"] == "pending" for x in st["segments"]):
        nxt = f"`{SCRIPT} segments next` and start what it prints."
    else:
        nxt = ("Wait for the running segments. A subagent that returned without `segment done`: SendMessage it to "
               f"finish the protocol; one that failed or cannot be resumed: `{SCRIPT} segments next --retry <segment>`.")
    return {"running": run.get("status") == "running", "run": run["run"], "status": run.get("status"), "plan": run["plan"],
            "current_stage": run["current"], "effort": run.get("effort"), "segments": segs, "next": nxt}


def answer(root: Path, cfg: dict, sid: str, text: str) -> dict:
    f = Feature(root, cfg)
    text = text.strip()
    if not text:
        raise SystemExit("error: give the user's answers on stdin, verbatim")
    done_tasks = ticked(f)

    def fn(run: dict, seg: dict) -> None:
        if seg["status"] != "waiting":
            raise SystemExit(f"error: {sid} is not waiting for answers (status {seg['status']})")
        seg["answers"].append({"at": now_iso(), "text": text, "for": [q["id"] for q in seg["questions"]]})
        seg["questions"] = []
        seg["status"] = "running"

    run = save_segment(root, f, sid, fn)
    seg = run["segments"][sid]
    log(f, f"### Answers {sid} — {now_iso()}\n\n{text}")
    nxt = next((t for t in seg["tasks"] if t not in done_tasks), None)
    send = f"The user's answers, verbatim:\n{text}\n\n" + (f"Continue from {nxt}." if nxt else "Continue.")
    return {"segment": sid, "agent": seg["agent"], "send": send,
            "how": "SendMessage `send` to `agent` (it resumes with its context and its effort). If it cannot be resumed: "
                   f"`{SCRIPT} segments next --retry {sid}` starts a fresh subagent that reads the answers."}


def join(root: Path, cfg: dict) -> dict:
    f = Feature(root, cfg)
    run = load_run(root, f)
    st = stage_of(run, run["current"])
    if st is None:
        return {"done": True}
    segs = [run["segments"][s] for s in st["segments"]]
    open_segs = [s["id"] for s in segs if s["status"] != "done"]
    if open_segs:
        raise SystemExit(f"error: stage {st['n']} is not done: {', '.join(open_segs)}")
    done_tasks = ticked(f)
    unticked = [t for s in segs for t in s["tasks"] if t not in done_tasks]
    if unticked:
        raise SystemExit(f"error: stage {st['n']} left tasks unticked: {', '.join(unticked)}")
    repos = sorted({r for s in segs for r in s.get("repositories", [])})
    gates, red = [], []
    from .facts import gate as run_gate
    from .model import Blueprint

    bp = Blueprint(f.root / f.bcfg["blueprint_path"])
    epic = bp.epic(f.epic[1:])
    res = run_gate(f.root, f.bcfg, bp, epic, None) if epic and not repos else None
    results = (res or {}).get("results", [])
    no_gate = (res or {}).get("no_gate_configured") or []
    if repos and epic:
        for r in repos:
            results += run_gate(f.root, f.bcfg, bp, epic, r).get("results", [])
    if not results:  # no repository table: the hub's own gate, when the config gives it one
        from .repos import hub_entry, registry, run_gate as run_one

        hub = hub_entry(registry(f.bcfg, f.root))
        if hub and hub.get("gate"):
            r = run_one(hub["gate"], f.root)
            r["repository"] = hub["name"]
            results.append(r)
            no_gate = []
    for r in results:
        gates.append({k: r.get(k) for k in ("repository", "ok", "exit", "command")})
        if not r.get("ok"):
            red.append(r)
    fix = None
    if red:
        tail = "\n".join(f"[{r.get('repository')}] `{r.get('command')}` exit {r.get('exit')}\n{str(r.get('tail') or '')[-1500:]}" for r in red)
        fix = f"S{st['n']}-fix"

    def change(board: dict) -> None:
        run = board["builds"][f.key]
        stg = stage_of(run, run["current"])
        stg["join"] = {"at": now_iso(), "ok": not red, "gates": gates}
        idx = run["stages"].index(stg)
        if fix:
            goal = ("the joined gate is red after stage " + str(stg["n"]) + ": make it green without weakening it. "
                    "Gate output (tail):\n" + tail)
            run["segments"][fix] = {"id": fix, "stage": stg["n"] + 0.5, "kind": "fix", "tasks": [], "files": [],
                                    "repositories": [r.get("repository") for r in red if r.get("repository")],
                                    "stories": [], "status": "pending", "agent": None, "paths": [], "leases": [],
                                    "questions": [], "answers": [], "notes": "", "attempts": 0, "goal": goal}
            run["stages"].insert(idx + 1, {"n": stg["n"] + 0.5, "kind": "serial", "segments": [fix], "join": None})
        nxt = run["stages"][idx + 1] if idx + 1 < len(run["stages"]) else None
        run["current"] = nxt["n"] if nxt else None
        if nxt is None:
            run["status"] = "finished"
        run["updated"] = now_iso()
        for aid in [a for a, w in board["builders"].items() if w.get("key") == f.key and w.get("segment") in st["segments"]]:
            del board["builders"][aid]

    update(root, change)
    log(f, f"### Join stage {st['n']} — {'green' if not red else 'RED'} — {now_iso()}\n\n"
           + ("\n".join(f"- {g['repository']}: {'ok' if g['ok'] else 'exit ' + str(g['exit'])} (`{g['command']}`)" for g in gates)
              or "- no gate configured")
           + (f"\n\nA fix segment {fix} comes next." if fix else ""))
    out = {"stage": st["n"], "ok": not red, "gates": gates, "next": f"`{SCRIPT} segments next`"}
    if no_gate:
        out["no_gate_configured"] = no_gate
        out["note"] = "No gate is configured for these repositories: the next segment's agent runs the plan's `check` first."
    if fix:
        out["fix_segment"] = fix
    return out


def stop_run(root: Path, cfg: dict) -> dict:
    f = Feature(root, cfg)
    stopped = {}

    def change(board: dict) -> None:
        run = board["builds"].get(f.key)
        if run and run.get("status") == "running":
            run["status"] = "stopped"
            run["updated"] = now_iso()
            stopped.update(run)
        for aid in [a for a, w in board["builders"].items() if w.get("key") == f.key]:
            del board["builders"][aid]

    update(root, change)
    if stopped:
        log(f, f"### Stopped {stopped['run']} — {now_iso()}\n\nTicked tasks stay; `segments start` plans the rest again.")
    return {"stopped": bool(stopped), "run": stopped.get("run"),
            "next": "Ticked tasks stay done. `segments start` plans the open tasks again (a fresh subagent per segment)."}


# --------------------------------------------------------------------------------------------
# Segment agents

def segment_start(root: Path, cfg: dict, sid: str) -> str:
    f = Feature(root, cfg)
    board = load_board(root)
    run = load_run(root, f)
    seg = run["segments"].get(sid)
    if not seg:
        raise SystemExit(f"error: no segment {sid} in run {run['run']} ({', '.join(run['segments'])})")
    if seg["stage"] != run["current"]:
        raise SystemExit(f"error: {sid} belongs to stage {seg['stage']}; stage {run['current']} is being built")
    if seg["status"] == "done":
        raise SystemExit(f"error: {sid} is done")
    if seg["status"] == "pending":
        def fn(run: dict, s: dict) -> None:
            s["status"] = "running"
            s["attempts"] = s.get("attempts", 0) + 1

        run = save_segment(root, f, sid, fn)
        seg = run["segments"][sid]
    text = f.tasks_text()
    lines = {m.group(2): ln.strip() for ln in text.split("\n") for m in [TASK_RE.match(ln)] if m}
    done_tasks = ticked(f)
    st = stage_of(run, seg["stage"]) or {}
    others = [run["segments"][x] for x in st.get("segments", []) if x != sid]
    out = [f"Segment {sid} · run {run['run']} · epic {run['epic']} · feature {run['feature']}",
           f"Effort: {run.get('effort') or 'the session’s'} · stage {seg['stage']} ({st.get('kind', 'serial')})"]
    if seg.get("agent") is None:
        out.append("Note: the hook did not register this subagent (an older Claude Code or the guard is off); "
                   "the rules below still hold.")
    out += ["", f"Read first: {run['feature']}/spec.md (only your stories: {', '.join(seg.get('stories') or []) or 'see your tasks'}), "
            f"{run['feature']}/plan.md, then `{SCRIPT} rules --blueprint {run['blueprint_id']}`.", ""]
    if seg["kind"] == "fix":
        out += ["Your work:", seg.get("goal", ""), ""]
    else:
        out.append("Tasks, in order (one at a time; tick each with `blueprint.py tick <ID>` once it is done and its checks pass):")
        for t in seg["tasks"]:
            out.append(("  [done] " if t in done_tasks else "  ") + lines.get(t, t))
        if any(t in done_tasks for t in seg["tasks"]):
            out.append("Some tasks are already ticked: an earlier agent worked here. Check `git status` and the diff of "
                       "your files first; an unticked task may be half done.")
        out.append("")
    if seg["files"]:
        out.append("Files your tasks name (yours while this stage runs): " + ", ".join(seg["files"]))
    for o in others:
        if o["status"] != "done":
            out.append(f"Do not write files of {o['id']} ({o['status']}): " + (", ".join(o["files"]) or "its tasks name none"))
    out.append("")
    skills = run.get("skills") or []
    if skills:
        out.append("Load these skills with the Skill tool before your first edit (the hook refuses edits until you do): "
                   + ", ".join(skills))
    hints = [h for h in run.get("skill_hints") or [] if h not in skills]
    if hints:
        out.append("The orchestrating session also used: " + ", ".join(hints) + " (load one if your tasks need it).")
    notes = [*(run.get("history") or []),
             *({"segment": s["id"], "notes": s["notes"]} for s in run["segments"].values() if s.get("notes"))]
    if notes:
        out += ["", "Notes from earlier segments:"] + [f"- {n['segment']}: {n['notes']}" for n in notes]
    if seg.get("answers"):
        out += ["", "Answers the user gave you:"] + [f"- {a['text']}" for a in seg["answers"]]
    parallel = st.get("kind") == "parallel"
    out += ["", "Rules (details: " + GUIDE + "):",
            f"- Checks: {'only the tests and lint of your own files (other segments are mid-work); never a repository-wide fix-mode formatter' if parallel else 'run the repository gate after each task (`blueprint.py gate ' + run['epic'] + ' --repo <name>` when configured), as in a normal build'}.",
            f"- Commands that share ports, a database or caches: `{SCRIPT} exclusive <name> -- <command>`.",
            "- Never commit, push, stash, switch branches or install packages" + (" (a parallel stage)" if parallel else "") + ".",
            f"- A technical decision nothing covers: `{SCRIPT} decision add` (title on the first line, then Context, "
            "Options, Decision, Consequences; the Context keeps `[NOT IN BLUEPRINT — searched: …]`).",
            f"- A product question, an amendment or a blocker: `{SCRIPT} segment ask {sid}` (see the guide), then return "
            f"`CHECKPOINT {sid}: …` and stop.",
            f"- At the end: `{SCRIPT} segment done {sid}` with your handoff notes on stdin (what you built, conventions, "
            f"decision IDs, files), then return `DONE {sid}`."]
    return "\n".join(out)


Q_RE = re.compile(r"^\s*(?:Q\d+\s*\|)?\s*(T\d+|-|—)?\s*\|\s*(closed|open)\s*\|(.*)$", re.I)


def segment_ask(root: Path, cfg: dict, sid: str, text: str) -> str:
    f = Feature(root, cfg)
    text = text.strip()
    if not text:
        raise SystemExit("error: give the questions on stdin, one per line: `Q1 | T014 | closed | question | options | default [tag]`")
    qs = []
    run = load_run(root, f)
    n0 = sum(len(a.get("for", [])) for a in run["segments"].get(sid, {}).get("answers", []))
    for ln in text.split("\n"):
        m = Q_RE.match(ln)
        if not m:
            continue
        cells = [c.strip() for c in m.group(3).split("|")]
        qs.append({"id": f"Q-{sid}-{n0 + len(qs) + 1}", "task": (m.group(1) or "").strip("-— "), "kind": m.group(2).lower(),
                   "question": cells[0] if cells else "", "options": cells[1] if len(cells) > 2 else "",
                   "default": cells[-1] if len(cells) > 1 else ""})
    if not qs:
        raise SystemExit("error: no question line found; write `Q1 | T014 | closed | question | A … · B … | default A [BLUEPRINT — …]`")
    rest = "\n".join(ln for ln in text.split("\n") if not Q_RE.match(ln)).strip()

    def fn(run: dict, seg: dict) -> None:
        seg["questions"] = qs
        seg["status"] = "waiting"
        if rest:
            seg["progress"] = rest

    save_segment(root, f, sid, fn)
    log(f, f"### Questions {sid} — {now_iso()}\n\n" + "\n".join(
        f"- {q['id']} ({q['task'] or 'segment'}, {q['kind']}): {q['question']}" + (f" — options: {q['options']}" if q["options"] else "")
        + f" — default: {q['default']}" for q in qs) + (f"\n\n{rest}" if rest else ""))
    return f"CHECKPOINT {sid}: " + ", ".join(q["id"] for q in qs) + "\nReturn now with that line; the answers come back to you."


def segment_done(root: Path, cfg: dict, sid: str, notes: str) -> str:
    f = Feature(root, cfg)
    run = load_run(root, f)
    seg = run["segments"].get(sid)
    if not seg:
        raise SystemExit(f"error: no segment {sid}")
    done_tasks = ticked(f)
    left = [t for t in seg["tasks"] if t not in done_tasks]
    if left:
        raise SystemExit(f"error: {sid} still has unticked tasks: {', '.join(left)}. Finish and tick them "
                         f"(`blueprint.py tick <ID>`), or `segment ask {sid}` if you are blocked.")
    notes = notes.strip()
    if not notes:
        raise SystemExit("error: give your handoff notes on stdin: what you built, conventions the next segments must "
                         "follow, decision IDs, the files you changed")

    def fn(run: dict, s: dict) -> None:
        s["status"] = "done"
        s["notes"] = notes[:4000]
        s["finished"] = now_iso()

    save_segment(root, f, sid, fn)
    log(f, f"### {sid} done — {now_iso()}\n\n{notes}")
    return f"DONE {sid}"


# --------------------------------------------------------------------------------------------
# Ticks, decisions, exclusive commands

def tick(root: Path, cfg: dict, ids: list) -> dict:
    f = Feature(root, cfg)
    if not ids or not all(re.match(r"^T\d+$", i) for i in ids):
        raise SystemExit("usage: blueprint.py tick T012 [T013 …]")
    out = {"ticked": [], "already": [], "missing": []}
    with file_lock(f.dir / ".tasks.lock", what="tasks.md"):
        lines = f.tasks_path.read_text(encoding="utf-8").split("\n")
        found = set()
        for k, ln in enumerate(lines):
            m = TASK_RE.match(ln)
            if not m or m.group(2) not in ids:
                continue
            found.add(m.group(2))
            if m.group(1) in "xX":
                out["already"].append(m.group(2))
                continue
            lines[k] = re.sub(r"\[ \]", "[X]", ln, count=1)
            out["ticked"].append(m.group(2))
        out["missing"] = [i for i in ids if i not in found]
        tmp = f.tasks_path.with_name(f".tasks.{os.getpid()}.tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        os.replace(tmp, f.tasks_path)
    return out


def decision_add(root: Path, cfg: dict, text: str) -> dict:
    from .facts import next_decision_id

    f = Feature(root, cfg)
    body = text.strip().split("\n")
    title = ""
    while body and not title:
        title = re.sub(r"^#+\s*|^title:\s*", "", body.pop(0).strip(), flags=re.I)
    if not title:
        raise SystemExit("error: the decision's title goes on the first line of stdin, then Context, Options, Decision, Consequences")
    rest = "\n".join(body).strip()
    path = root / f.bcfg["decisions_path"]
    with file_lock(state_dir(root) / "decisions.lock", what="the decisions log"):
        ident = next_decision_id(root, f.bcfg, f.epic)
        doc = path.read_text(encoding="utf-8") if path.is_file() else f"# DECISIONS\n\n## Index\n\n| ID | Title | Origin | Status |\n|---|---|---|---|\n\n## Session decisions\n"
        row = f"| {ident} | {title[:90]} | {f.epic} build | Accepted |"
        lines = doc.split("\n")
        idx = next((k for k, ln in enumerate(lines) if ln.strip() == "## Index"), None)
        if idx is not None:
            k = idx + 1
            last = None
            while k < len(lines) and not lines[k].startswith("## "):
                if lines[k].startswith("|"):
                    last = k
                k += 1
            if last is not None:
                lines.insert(last + 1, row)
        date = _dt.date.today().isoformat()
        record = f"\n## {ident} — {title}\n**Status** Accepted — {date} · Epic {f.epic} · Supersedes: none\n{rest}\n"
        doc = "\n".join(lines).rstrip() + "\n" + record
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(doc, encoding="utf-8")
        os.replace(tmp, path)
    return {"id": ident, "decisions_log": f.bcfg["decisions_path"], "indexed": idx is not None}


def exclusive(root: Path, name: str, command: list) -> int:
    if not name or not re.match(r"^[A-Za-z0-9._-]+$", name) or not command:
        print("usage: blueprint.py exclusive <name> -- <command …>", file=sys.stderr)
        return 1
    lock = state_dir(root) / f"exclusive-{name}.lock"
    with file_lock(lock, wait_s=3600, stale_s=4 * 3600, what=f"`{name}`"):
        return subprocess.run(shlex.join(command), shell=True).returncode


# --------------------------------------------------------------------------------------------
# CLI

def read_stdin() -> str:
    try:
        return sys.stdin.read() if not sys.stdin.isatty() else ""
    except OSError:
        return ""


def graph_command(root: Path, cfg: dict, flags: set) -> int:
    fd = active_feature(root)
    trace = read_trace(root / fd) if fd else None
    if not trace:
        out = {"applies": False, "why": "the active feature was not compiled from a blueprint"}
        print(json.dumps(out, indent=2))
        return 0
    f = Feature(root, cfg)
    if not f.tasks_path.is_file():
        print(json.dumps({"applies": True, "ok": False, "errors": [f"{f.rel}/tasks.md does not exist"]}, indent=2))
        return 1
    p = f.plan()
    out = {"applies": True, "ok": not p["errors"], "feature_directory": f.rel, "mode": f.bcfg["build"]["mode"],
           "plan": render(p), "errors": p["errors"], "warnings": p["warnings"], "stats": p["stats"]}
    if "--json" in flags:
        out["stages"] = p["stages"]
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 1 if ("--check" in flags and p["errors"]) else 0


def command(root: Path, cfg: dict, cmd: str, args: list) -> int:
    keys = [a for a in args if not a.startswith("--")]
    flags = {a for a in args if a.startswith("--")}
    dump = lambda d: print(json.dumps(d, indent=2, ensure_ascii=False))  # noqa: E731
    if cmd == "tasks-graph":
        return graph_command(root, cfg, flags)
    if cmd == "tick":
        res = tick(root, cfg, keys)
        dump(res)
        return 1 if res["missing"] else 0
    if cmd == "decision":
        if not keys or keys[0] != "add":
            print("usage: blueprint.py decision add  (title and record on stdin)", file=sys.stderr)
            return 1
        dump(decision_add(root, cfg, read_stdin()))
        return 0
    if cmd == "segments":
        verb = keys[0] if keys else "status"
        if verb == "start":
            res = start(root, cfg, "--resume" in flags)
            dump(res)
            return 0 if res.get("ok", True) else 1
        if verb == "next":
            retry = []
            for a in args:
                if a.startswith("--retry="):
                    retry += [x for x in a.split("=", 1)[1].split(",") if x]
            if "--retry" in args:
                i = args.index("--retry")
                retry += [x for x in (args[i + 1] if i + 1 < len(args) else "").split(",") if x]
            dump(next_segments(root, cfg, retry))
            return 0
        if verb == "status":
            dump(status(root, cfg))
            return 0
        if verb == "answer":
            if len(keys) < 2:
                print("usage: blueprint.py segments answer <segment>  (answers on stdin)", file=sys.stderr)
                return 1
            dump(answer(root, cfg, keys[1], read_stdin()))
            return 0
        if verb == "join":
            res = join(root, cfg)
            dump(res)
            return 0
        if verb == "stop":
            dump(stop_run(root, cfg))
            return 0
        print("usage: blueprint.py segments start|next|status|answer|join|stop", file=sys.stderr)
        return 1
    if cmd == "segment":
        verb, sid = (keys + ["", ""])[:2]
        if not sid:
            print("usage: blueprint.py segment start|ask|done <segment>", file=sys.stderr)
            return 1
        if verb == "start":
            print(segment_start(root, cfg, sid))
            return 0
        if verb == "ask":
            print(segment_ask(root, cfg, sid, read_stdin()))
            return 0
        if verb == "done":
            print(segment_done(root, cfg, sid, read_stdin()))
            return 0
        print("usage: blueprint.py segment start|ask|done <segment>", file=sys.stderr)
        return 1
    return 1
