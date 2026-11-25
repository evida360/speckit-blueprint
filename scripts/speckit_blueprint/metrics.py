"""Metrics from Claude Code transcripts (DESIGN.md "Metrics").

The Stop hook summarises a session's transcript into `<git-common-dir>/speckit-blueprint/metrics/
sessions/<session>.json`: per epic (and per blueprint for discovery and amendments) the agent's active
time split into model and tool time, the time it waited for the user, tokens, cost, models, effort
levels, phases, repositories touched, task ticks and story times. Summaries are idempotent: the whole
transcript is read again each time.

Work belongs to the feature active in the session's checkout (`.specify/feature.json`) at the time, and to
no epic when that feature was not compiled from a blueprint: reading an epic's files or naming it does not
make the work its. The hook records the active feature at the end of every turn; a compile or activate the
session ran, or Spec Kit's report of the active feature (check-prerequisites), marks the moment it changed.

Time is the agent's time in its turns. A turn runs from a message (the user's, or a background task's
report) to the model's last answer, the stop hook's record or an interruption; what Claude Code writes
between turns (queue operations, links, file snapshots) is no work. From the end of a turn to the user's
next message, and from a question (AskUserQuestion, ExitPlanMode) to its answer, the agent waits for the
user. Inside a turn, a stretch of more than IDLE_GAP_S in which neither the session nor its subagents wrote
anything is idle: a request that hung, a machine asleep.

Transcripts are Claude Code's own record, not a public API: every field is read defensively and the
Claude Code version is kept with each summary.
"""

from __future__ import annotations

import bisect
import datetime as _dt
import json
import os
import re
import shlex
import time
from pathlib import Path

from .core import TRACE_FILE, read_trace
from .config import bp_config, default_id, split_qualified, trace_blueprint_id
from .repos import git, main_root, registry
from .board import load as load_board, state_dir
from .units import cost as fmt_cost, duration, tokens as fmt_tokens

WAIT_CAP_S = 4 * 3600
# Measured on 112 sessions: tool calls ran up to 12 min and answers took up to 8 min with nothing written
# meanwhile; the stretches longer than 30 min were requests that hung for hours
IDLE_GAP_S = 30 * 60
GATE_TOOLS = ("AskUserQuestion", "ExitPlanMode")  # calls that wait for the user's answer
END_REASONS = ("end_turn", "stop_sequence", "refusal")  # the model's answer ends the turn
TICK_BATCH_S = 60
WEIGHTS = {"input": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0}
PHASE_RE = re.compile(r"speckit-(?:blueprint-)?(discover|amend|compile|tasks|analyze|implement|review)\b")
PHASE_OF = {"discover": "discover", "amend": "amend", "compile": "plan", "tasks": "plan", "analyze": "plan",
            "implement": "build", "review": "review"}
TICK_RE = re.compile(r"-\s*\[[xX]\]\s*(T\d+)([^\n]*)")
TICK_CMD_RE = re.compile(r"blueprint\.py[\"']?\s+tick((?:\s+T\d+)+)")
SEGMENT_START_RE = re.compile(r"blueprint\.py[\"']?\s+segment\s+start\s+([A-Za-z0-9.-]+)")
OPEN_RE = re.compile(r"-\s*\[ \]\s*(T\d+)")
SEED_STORY_RE = re.compile(r"\b(?:[A-Za-z][A-Za-z0-9]*:)?(E\d{2}-S\d{2})-T\d+")
US_RE = re.compile(r"\[US(\d+)\]")
ACTIVATE_RE = re.compile(r"blueprint\.py[\"']?\s+(?:compile|activate)\s+([^\s;&|]+)")
BLUEPRINT_OPT_RE = re.compile(r"--blueprint[=\s]+([^\s;&|]+)")
FEATURE_DIR_RE = re.compile(r"\"feature_directory\"\s*:\s*\"([^\"]+)\"")
# Spec Kit's scripts report the active feature: check-prerequisites and setup-plan print FEATURE_DIR;
# create-new-feature (/speckit.specify) makes its new feature active and prints its SPEC_FILE
SPECKIT_RE = re.compile(r"(?:check-prerequisites|setup-plan|setup-tasks|create-new-feature)\.(?:sh|ps1)")
SPECKIT_DIR_RE = re.compile(r"\"FEATURE_DIR\"\s*:\s*\"([^\"]+)\"|^FEATURE_DIR:\s*(\S[^\n]*?)\s*$", re.M)
SPEC_FILE_RE = re.compile(r"\"SPEC_FILE\"\s*:\s*\"([^\"]+)\"")
FOLDER_NAME_RE = re.compile(r"^\d{3,}-(?:([a-z0-9][a-z0-9-]*?)-)?e(\d{2})-")
PATH_KEYS = ("file_path", "path", "notebook_path")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
LOOK_AGAIN = 5  # finished sessions a Stop event summarises again, to pick up their cost


def parse_ts(s) -> float | None:
    try:
        return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def iso(t: float | None) -> str | None:
    return _dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds") if t else None


def metrics_dir(root: Path) -> Path:
    return state_dir(root) / "metrics"


def transcripts_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "projects"


def transcript_dir(path: Path) -> Path:
    """Where Claude Code keeps the transcripts of sessions started in `path`."""
    return transcripts_home() / re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


class Ctx:
    """What attribution needs: feature folder → epic, repository paths, blueprint paths."""

    def __init__(self, root: Path, cfg: dict):
        self.root, self.cfg = root, cfg
        self.default = default_id(cfg)
        self.specs = cfg.get("specs_dir", "specs")
        self.specs_dirs = sorted({self.specs} | {bp_config(cfg, bid)["specs_dir"] for bid in cfg["blueprints"]})
        self.folder_re = re.compile(r"(?:^|[/\s\"'`=(])" + re.escape(self.specs) + r"/(\d{3,}-[A-Za-z0-9._-]+?)(?=/|[\s\"'`)]|$)")
        self.folders: dict = {}
        self.stories: dict = {}
        for bid in cfg["blueprints"]:
            specs = root / bp_config(cfg, bid)["specs_dir"]
            if specs.is_dir():
                for d in specs.iterdir():
                    t = read_trace(d)
                    if t and t.get("epic"):
                        key = f"{trace_blueprint_id(cfg, t) or bid}:{t['epic']}"
                        self.folders[d.name] = key
                        self.stories[key] = list((t.get("acs") or {}).keys())
        board = load_board(root)
        self.sessions = board.get("sessions", {})
        for key, e in board.get("epics", {}).items():
            if e.get("feature"):
                self.folders.setdefault(Path(e["feature"]).name, key)
        reg = registry(cfg, root)
        paths = []
        for name, r in reg.items():
            paths.append((str(Path(r["main_path"])), name))
            paths.append((str(Path(r["path"])), name))
        for e in board.get("epics", {}).values():
            for name, rr in (e.get("repositories") or {}).items():
                if rr.get("worktree"):
                    paths.append((str(Path(rr["worktree"])), name))
            if e.get("worktree"):
                hub = next((n for n, r in reg.items() if r["hub"]), None)
                if hub:
                    paths.append((str(Path(e["worktree"])), hub))
        self.repo_paths = sorted(set(paths), key=lambda p: -len(p[0]))
        self.hub = next((n for n, r in reg.items() if r["hub"]), None)
        self.blueprints = {bid: bp_config(cfg, bid)["blueprint_path"] for bid in cfg["blueprints"]}

    def task_text(self, key: str, tid: str) -> str:
        """The rest of a task's line in its feature's tasks.md (for the story a subagent's tick belongs to)."""
        cache = self.__dict__.setdefault("_task_lines", {})
        if key not in cache:
            cache[key] = {}
            for name, k in self.folders.items():
                if k != key:
                    continue
                for specs in self.specs_dirs:
                    tp = self.root / specs / name / "tasks.md"
                    if tp.is_file():
                        for m in re.finditer(r"-\s*\[[ xX]\]\s*(T\d+)([^\n]*)", tp.read_text(encoding="utf-8")):
                            cache[key][m.group(1)] = m.group(2)
        return cache[key].get(tid, "")

    def key_of_folder(self, name: str) -> str:
        if name in self.folders:
            return self.folders[name]
        m = FOLDER_NAME_RE.match(name)
        if not m:
            return ""
        bid = m.group(1) if m.group(1) in self.cfg["blueprints"] else self.default
        return f"{bid}:E{m.group(2)}"

    def active_key(self, folder: str | None, worktree: str | None = None) -> str:
        """The epic an active feature folder belongs to; "" for a folder not compiled from a blueprint.
        Unlike `key_of_folder`, a name that only looks like an epic's (`023-desk-e05-hotfix`) is none."""
        if not folder:
            return ""
        name = Path(folder).name
        if name in self.folders:
            return self.folders[name]
        t = read_trace(Path(worktree) / folder) if worktree else None
        if t and t.get("epic"):
            return f"{trace_blueprint_id(self.cfg, t) or self.default}:{t['epic']}"
        return ""

    def folder_of(self, path: str, bases: list) -> str | None:
        """`specs/<folder>` when `path` lies in a feature folder of one of these checkouts."""
        paths = {path, os.path.realpath(path)}
        for base in {b for x in bases if x for b in (str(x), os.path.realpath(str(x)))}:
            for specs in self.specs_dirs:
                prefix = os.path.join(base, specs) + os.sep
                for p in paths:
                    name = p[len(prefix):].split(os.sep, 1)[0] if p.startswith(prefix) else ""
                    if re.match(r"\d{3,}-", name):
                        return f"{specs}/{name}"
        return None

    def activated(self, command: str, output: str, worktree: str | None) -> str | None:
        """The epic a compile or activate that ran made active ("" for a folder of no epic): the folder
        its output names, else its argument."""
        dirs = FEATURE_DIR_RE.findall(output)
        if dirs:
            return self.active_key(dirs[-1], worktree)
        if re.search(r"(?m)^(?:error|usage):", output):
            return None
        ref = ACTIVATE_RE.findall(command)[-1].strip("'\"")
        if "/" in ref:
            return self.active_key(ref.rstrip("/"), worktree)
        opt = BLUEPRINT_OPT_RE.search(command)
        try:
            bid, ref = split_qualified(self.cfg, ref if ":" in ref or not opt else f"{opt.group(1)}:{ref}")
        except Exception:  # noqa: BLE001
            return None
        num = re.sub(r"\D", "", ref)
        return f"{bid or self.default}:E{int(num):02d}" if num else None

    def reported(self, output: str, worktree: str | None) -> str | None:
        """The active feature a Spec Kit script printed ("" for a folder of no epic)."""
        dirs = [a or b for a, b in SPECKIT_DIR_RE.findall(output)]
        if dirs:
            return self.active_key(dirs[-1], worktree)
        spec = SPEC_FILE_RE.findall(output)
        if spec and not re.search(r"\"DRY_RUN\"\s*:\s*true", output):
            return self.active_key(os.path.dirname(spec[-1]), worktree)
        return None

    def blueprint_written(self, path: str) -> str | None:
        for bid, rel in self.blueprints.items():
            folder = str(Path(rel).parent)
            if path.endswith(rel) or ("/discovery/" in path and folder in path):
                return bid
        return None

    def repo_of(self, text: str, cwd: str) -> str | None:
        cands = []
        for word in re.findall(r"(?:^|\s)(?:cd|-C)\s+([^\s;&|]+)", text):
            cands.append(word)
        cands += re.findall(r"(/[^\s\"'`;&|]+)", text)
        for c in cands:
            p = c if os.path.isabs(c) else os.path.normpath(os.path.join(cwd, c))
            for base, name in self.repo_paths:
                if p == base or p.startswith(base + os.sep):
                    return name
        return None


def new_agg() -> dict:
    return {"active_s": 0.0, "tool_s": 0.0, "wait_s": 0.0, "gates": 0, "turns": 0,
            "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}, "weighted": 0.0,
            "models": {}, "efforts": {}, "phases": {}, "repos": {}, "ticks": [], "intervals": [],
            "subagents": {}, "first": None, "last": None}


def usage_of(msg: dict) -> dict:
    u = msg.get("usage") or {}
    return {"input": int(u.get("input_tokens") or 0), "output": int(u.get("output_tokens") or 0),
            "cache_read": int(u.get("cache_read_input_tokens") or 0),
            "cache_write": int(u.get("cache_creation_input_tokens") or 0)}


def add_tokens(agg: dict, u: dict) -> None:
    for k, v in u.items():
        agg["tokens"][k] += v
    agg["weighted"] += sum(WEIGHTS[k] * v for k, v in u.items())


def bump(d: dict, key, n=1) -> None:
    if key:
        d[key] = d.get(key, 0) + n


def read_jsonl(path: Path) -> list:
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    out.append(e)
    except OSError:
        pass
    return out


def turn_event(e: dict) -> str:
    """What an entry says about the agent's time. A turn starts at "prompt" (a message the user typed),
    "notice" (a background task the session started reports back) or "wake" (a message nobody typed: a
    scheduled task, another session) and ends at "end" (the stop hook ran, the user interrupted) or at an
    answer that ends it (`ends_turn`). "work" (the agent's messages and tool results) and "busy" (requests,
    retries, a subagent's messages in older transcripts) show the agent at work; "work" after the end of a
    turn starts another (a stop hook sent the agent on). Anything else ("": queue operations, links, file
    snapshots, attachments, a compaction's summary) says nothing about the agent's time."""
    typ = e.get("type")
    if typ in ("assistant", "user") and e.get("isSidechain"):
        return "busy"
    if typ == "assistant":
        return "work"
    if typ == "user":
        if tool_results(e):
            return "work"
        if e.get("isMeta") or e.get("isCompactSummary"):
            return ""
        text = text_of(e).lstrip()
        if text.startswith("[Request interrupted by user"):
            return "end"
        if text.startswith("<task-notification>"):
            return "notice"
        origin = e.get("origin")
        return "wake" if isinstance(origin, dict) and origin.get("kind") in ("task-notification", "peer") else "prompt"
    if typ == "system":
        sub = e.get("subtype")
        return "end" if sub in ("stop_hook_summary", "turn_duration") else "busy" if sub == "api_error" else ""
    return "busy" if typ in ("api-request", "progress") else ""


def ends_turn(e: dict) -> bool:
    return (e.get("type") == "assistant" and not e.get("isSidechain")
            and (e.get("message") or {}).get("stop_reason") in END_REASONS)


def text_of(e: dict) -> str:
    content = (e.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
    return ""


def subagent_usage(transcript: Path, session_id: str, agent_id: str) -> dict:
    """Tokens, models and effort levels of one subagent run, and when its messages were written."""
    path = transcript.parent / session_id / "subagents" / f"agent-{agent_id}.jsonl"
    out = {"tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}, "models": {}, "efforts": {},
           "times": [], "ticks": [], "segment": None}
    for e in read_jsonl(path):
        t = parse_ts(e.get("timestamp")) if e.get("type") in ("assistant", "user") else None
        if t is not None:
            out["times"].append(t)
        if e.get("type") != "assistant":
            continue
        msg = e.get("message") or {}
        for c in tool_uses(e):  # a segment's subagent ticks its own tasks (segments.py)
            inp = c.get("input") or {}
            cmd = str(inp.get("command") or "") if c.get("name") == "Bash" else ""
            m = SEGMENT_START_RE.search(cmd)
            if m and not out["segment"]:
                out["segment"] = m.group(1)
            m = TICK_CMD_RE.search(cmd)
            if m and t is not None:
                out["ticks"] += [(tid, t, "") for tid in m.group(1).split()]
            fp = str(inp.get("file_path") or "")
            if fp.endswith("tasks.md") and t is not None:
                for old, new in [(inp.get("old_string") or "", inp.get("new_string") or "")]:
                    opened = set(OPEN_RE.findall(old))
                    out["ticks"] += [(tid, t, rest) for tid, rest in TICK_RE.findall(new) if tid in opened]
        for k, v in usage_of(msg).items():
            out["tokens"][k] += v
        bump(out["models"], msg.get("model"))
        bump(out["efforts"], e.get("effort"))
    return out


def tool_uses(e: dict) -> list:
    content = (e.get("message") or {}).get("content")
    return [c for c in content if isinstance(c, dict) and c.get("type") == "tool_use"] if isinstance(content, list) else []


def tool_results(e: dict) -> list:
    content = (e.get("message") or {}).get("content")
    return [c for c in content if isinstance(c, dict) and c.get("type") == "tool_result"] if isinstance(content, list) else []


def result_text(c: dict, res) -> str:
    """A tool result's text: its content and, for Bash, its stdout."""
    content = c.get("content")
    if isinstance(content, list):
        content = " ".join(str(x.get("text") or "") for x in content if isinstance(x, dict))
    return str(content or "") + ("\n" + str(res.get("stdout") or "") if isinstance(res, dict) else "")


def load_summary(root: Path, session_id: str) -> dict | None:
    try:
        data = json.loads((metrics_dir(root) / "sessions" / f"{session_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def change_points(events: list, seen: list, turns: list) -> list:
    """Where the active feature changed, as (time, key, seen) in order. A compile or activate changes it at
    its time. An observation at the end of a turn that differs from what is known changes it from the
    start of that turn (`turns`: when each turn started), since the transcript does not show where. The
    first point also covers the time before it: as known when it is an observation (nothing changed
    before it), as a guess when it is a compile (a session that starts by compiling an epic is getting
    that epic ready)."""
    points: list = []
    last_seen = None
    for t, kind, key in sorted([(t, 0, k) for t, k in events] + [(t, 1, k) for t, k in seen], key=lambda x: (x[0], x[1])):
        at = t
        if kind == 1 and points and points[-1][1] != key:
            after = max(points[-1][0], last_seen if last_seen is not None else points[-1][0])
            starts = [p for p in turns if after < p <= t]
            at = starts[-1] if starts else after + 1e-6
        if not points or points[-1][1] != key:
            points.append((at, key, kind == 1))
        if kind == 1:
            last_seen = t
    return points


def summarise_session(root: Path, cfg: dict, transcript: Path, ctx: Ctx | None = None, write: bool = True,
                      active: str | None = None) -> dict | None:
    """Summarise one transcript. `active` is the feature folder active in the checkout now ("" for none):
    the Stop hook passes it for the session that is running, and the summary keeps it with the time
    (`active_seen`). Other callers leave it out: the recorded observations decide, else the board's last
    record of the session. Within a session, a compile or activate and Spec Kit's own report of the
    active feature mark the moment it changed; a session the hook never saw also counts writes into a
    feature folder."""
    entries = [e for e in read_jsonl(transcript) if parse_ts(e.get("timestamp")) is not None or e.get("type") == "cost-state"]
    timed = [e for e in entries if parse_ts(e.get("timestamp")) is not None]
    if not timed:
        return None
    ctx = ctx or Ctx(root, cfg)
    session_id = next((e.get("sessionId") for e in entries if e.get("sessionId")), transcript.stem)
    cwd = next((e.get("cwd") for e in timed if e.get("cwd")), str(root))
    version = next((e.get("version") for e in reversed(timed) if e.get("version")), None)
    end_t = parse_ts(timed[-1].get("timestamp"))

    prev = load_summary(root, session_id) or {}
    seen = [[x[0], x[1] or ""] for x in prev.get("active_seen") or []
            if isinstance(x, list) and len(x) == 2 and isinstance(x[0], (int, float))]
    worktree = prev.get("worktree")
    if active is not None:
        worktree = str(root.resolve())
        if not seen or seen[-1][1] != active:
            seen.append([max(end_t, seen[-1][0]) if seen else end_t, active])
    elif not seen and session_id in ctx.sessions:
        rec = ctx.sessions[session_id]
        seen = [[end_t, rec.get("feature") or ""]]
        worktree = worktree or rec.get("worktree")
    worktree = worktree or cwd

    # Pass 1: the phases, where the active feature changed, the blueprint each discovery or amendment wrote
    phase, stretch = "other", 0
    stretch_bp: dict = {}  # stretch -> the first blueprint it wrote
    pre: list = []  # per timed entry: (phase, stretch) before its own changes
    post: list = []  # and after them
    events: list = []
    turns: list = []  # when each turn started
    acts: dict = {}  # Bash tool_use id -> a compile or activate, or a Spec Kit script that reports the feature

    def to_phase(new: str) -> None:
        nonlocal phase, stretch
        if new != phase:
            phase, stretch = new, stretch + 1

    for e in timed:
        t = parse_ts(e.get("timestamp"))
        pre.append((phase, stretch))
        ev = turn_event(e)
        if ev in ("prompt", "notice", "wake"):
            turns.append(t)
            m = PHASE_RE.search(text_of(e)) if ev == "prompt" else None
            if m:
                to_phase(PHASE_OF[m.group(1)])
        elif e.get("type") == "assistant":
            for c in tool_uses(e):
                name, inp = c.get("name"), c.get("input") or {}
                m = PHASE_RE.search(json.dumps(inp, ensure_ascii=False)) if name in ("Skill", "SlashCommand") else None
                if m:
                    to_phase(PHASE_OF[m.group(1)])
                path = next((str(inp.get(k)) for k in PATH_KEYS if inp.get(k)), "")
                if name in WRITE_TOOLS and path:
                    bid = ctx.blueprint_written(path)
                    if bid:
                        stretch_bp.setdefault(stretch, bid)
                    folder = None if seen else ctx.folder_of(path, [worktree, cwd, str(root)])
                    if folder:  # the hook never saw this session: a write into a feature folder
                        events.append((t, ctx.active_key(folder, worktree)))
                cmd = str(inp.get("command") or "") if name == "Bash" else ""
                if ACTIVATE_RE.search(cmd) or SPECKIT_RE.search(cmd):
                    acts[c.get("id")] = cmd
        elif e.get("type") == "user":
            for c in tool_results(e):
                cmd = acts.pop(c.get("tool_use_id"), None)
                if cmd and not c.get("is_error"):
                    out = result_text(c, e.get("toolUseResult"))
                    key = ctx.activated(cmd, out, worktree) if ACTIVATE_RE.search(cmd) else ctx.reported(out, worktree)
                    if key is not None:
                        events.append((t, key))
        post.append((phase, stretch))

    points = change_points(events, [(t, ctx.active_key(f, worktree)) for t, f in seen], turns)
    times = [p[0] for p in points]

    def key_of(state: tuple, t: float) -> str:
        """The active epic's key. Without one ("" or, before the session's first compile, only a guess), a
        discovery or amendment that wrote its blueprint belongs to `<blueprint>:discover|amend`."""
        i = bisect.bisect_right(times, t) - 1
        key, known = (points[max(i, 0)][1], i >= 0 or points[0][2]) if points else ("", False)
        ph, st = state
        if (key and known) or ph not in ("discover", "amend") or st not in stretch_bp:
            return key
        return f"{stretch_bp[st]}:{ph}"

    # Pass 2: time, tokens, tools and ticks, each to the key of its moment
    per: dict = {}
    tools: dict = {}  # tool_use id -> (key, the active clock at the call, repo, name)
    session_cost = None
    covered: dict = {}  # key -> weighted tokens the session's cost record covers
    subs: dict = {}  # agentId -> the subagent's tokens, models, effort levels and when it wrote
    for e in timed:
        res = e.get("toolUseResult")
        if isinstance(res, dict) and res.get("agentId") and str(res["agentId"]) not in subs:
            subs[str(res["agentId"])] = subagent_usage(transcript, session_id, str(res["agentId"]))
    evidence = sorted(x for s in subs.values() for x in s["times"])  # subagents at work while the session is quiet
    in_turn, last, ended = False, None, None  # in a turn; the time accounted for; when the last turn ended
    pending: dict = {}  # tool calls in flight: id -> name
    asked: set = set()  # questions waiting for the user's answer (GATE_TOOLS)
    asked_at = 0.0  # when the first of them was asked
    clock = 0.0  # active seconds so far: a tool call's time is how far it moves during the call
    i = -1

    def agg(key: str) -> dict:
        return per.setdefault(key, new_agg())

    def work(key: str, s: float, e_: float, ph: str, tool: bool) -> None:
        """[s, e_] is the agent's time, but for stretches of more than IDLE_GAP_S in which neither the
        session nor its subagents wrote anything: those are idle."""
        nonlocal clock
        cuts = evidence[bisect.bisect_right(evidence, s):bisect.bisect_left(evidence, e_)]
        for x, y in zip([s] + cuts, cuts + [e_]):
            if 0 < y - x <= IDLE_GAP_S:
                credit(key, "active", (x, y, ph, tool))
                clock += y - x

    def answered(t: float, state: tuple) -> None:
        """The open questions are over: since the first was asked, the agent waited for the user."""
        nonlocal last
        agg(key_of(state, asked_at))["wait_s"] += min(max(0.0, t - asked_at), WAIT_CAP_S)
        asked.clear()
        last = max(last or t, t)

    def credit(key: str, kind: str, payload) -> None:
        a = agg(key)
        if kind == "active":
            s, e_, ph, tool = payload
            a["active_s"] += e_ - s
            if tool:
                a["tool_s"] += e_ - s
            bump(a["phases"], ph, e_ - s)
            if a["intervals"] and abs(a["intervals"][-1][1] - s) < 1:
                a["intervals"][-1][1] = e_
            else:
                a["intervals"].append([s, e_])
            a["first"] = s if a["first"] is None else min(a["first"], s)
            a["last"] = e_ if a["last"] is None else max(a["last"], e_)
        elif kind == "tokens":
            u, model, effort = payload
            add_tokens(a, u)
            bump(a["models"], model)
            bump(a["efforts"], effort)
        elif kind == "tool":
            repo, secs = payload
            if repo:
                r = a["repos"].setdefault(repo, {"calls": 0, "tool_s": 0.0})
                r["calls"] += 1
                r["tool_s"] += secs
        elif kind == "sub":
            agent_type, sub = payload
            add_tokens(a, sub["tokens"])
            for m, n in sub["models"].items():
                bump(a["models"], m, n)
            s = a["subagents"].setdefault(agent_type or "subagent", {"runs": 0, "efforts": {}})
            s["runs"] += 1
            for lv, n in sub["efforts"].items():
                bump(s["efforts"], lv, n)
            for tid, at, rest in sub.get("ticks") or []:
                a["ticks"].append({"task": tid, "story": story_of(ctx, key, rest or ctx.task_text(key, tid)), "t": at,
                                   **({"segment": sub["segment"]} if sub.get("segment") else {})})
            if sub.get("segment") and sub["times"]:
                seg = a.setdefault("segments", {}).setdefault(sub["segment"], {"runs": 0, "agent_s": 0.0, "weighted": 0.0})
                seg["runs"] += 1
                seg["agent_s"] += max(sub["times"]) - min(sub["times"])
                seg["weighted"] += sum(sub["tokens"][k] * WEIGHTS[k] for k in WEIGHTS)

    for e in entries:
        if e.get("type") == "cost-state":
            try:
                session_cost = float(e.get("totalCostUSD"))
            except (TypeError, ValueError):
                continue
            covered = {k: a["weighted"] for k, a in per.items()}  # the total so far: the work before it
            continue
        t = parse_ts(e.get("timestamp"))
        i += 1
        ev = turn_event(e)
        # The time since `last`: waiting for the user, the agent at work, or idle
        if ev in ("prompt", "notice", "wake"):
            if asked:  # the user wrote instead of answering: the question's wait ends here
                if ev == "prompt":
                    answered(t, pre[i])
            elif last is not None and t > last:
                key = key_of(pre[i], last)
                if ev == "prompt":  # since the end of the turn (or the agent's last word), waiting for the user
                    agg(key)["wait_s"] += min(t - last, WAIT_CAP_S)
                    agg(key)["gates"] += 1
                elif in_turn or ev == "notice":  # after its turn, the agent waited for its background task
                    work(key, last, t, pre[i][0], bool(pending) or not in_turn)
            if ev == "prompt":
                agg(key_of(post[i], t))["turns"] += 1
            if ev == "prompt" or not in_turn:
                pending.clear()
            in_turn, last = True, max(last or t, t)
            continue
        if ev in ("work", "busy", "end"):
            if not in_turn:  # the agent went on by itself; or a late record of the turn that ended
                if ev == "work" and (ended is None or t > ended):
                    in_turn, last = True, max(last or t, t)
            elif t > last and not asked:  # while a question is open, the wait is counted at its answer
                work(key_of(pre[i], last), last, t, pre[i][0], bool(pending))
                last = t
        if e.get("type") == "assistant":
            msg = e.get("message") or {}
            key = key_of(post[i], t)
            for c in tool_uses(e):
                inp = c.get("input") or {}
                path = next((str(inp.get(k)) for k in PATH_KEYS if inp.get(k)), "")
                repo = ctx.repo_of(path or str(inp.get("command") or ""), cwd) or (ctx.hub if c.get("name") in ("Bash", "Edit", "Write", "MultiEdit", "Read") else None)
                tools[c.get("id")] = (key, clock, repo, c.get("name"))
                if in_turn and ev == "work":
                    if c.get("name") in GATE_TOOLS:
                        asked_at = asked_at if asked else t
                        asked.add(c.get("id"))
                        agg(key)["gates"] += 1
                    else:
                        pending[c.get("id")] = c.get("name")
                if path.endswith("tasks.md"):
                    pairs = [(inp.get("old_string") or "", inp.get("new_string") or "")] + [
                        (x.get("old_string") or "", x.get("new_string") or "") for x in inp.get("edits") or [] if isinstance(x, dict)]
                    folder = ctx.folder_re.search(path)
                    tkey = ctx.key_of_folder(folder.group(1)) if folder else key
                    for old, new in pairs:
                        opened = set(OPEN_RE.findall(old))
                        for tid, rest in TICK_RE.findall(new):
                            if tid in opened and tkey:
                                agg(tkey)["ticks"].append({"task": tid, "story": story_of(ctx, tkey, rest), "t": t})
            credit(key, "tokens", (usage_of(msg), msg.get("model"), e.get("effort")))
        elif e.get("type") == "user":
            for c in tool_results(e):
                tid = c.get("tool_use_id")
                pending.pop(tid, None)
                if tid in asked:
                    asked.discard(tid)
                    if not asked:
                        answered(t, pre[i])
                if tid in tools:
                    key, start, repo, name = tools.pop(tid)
                    credit(key, "tool", (repo, clock - start))
                    res = e.get("toolUseResult")
                    if name in ("Agent", "Task") and isinstance(res, dict) and res.get("agentId"):
                        credit(key, "sub", (res.get("agentType"), subs[str(res["agentId"])]))
        if in_turn and (ev == "end" or ends_turn(e)):
            if asked:
                answered(t, pre[i])
            in_turn, ended = False, max(last or t, t)
            pending.clear()
    for a in per.values():
        a["tool_s"] = min(a["tool_s"], a["active_s"])
        a["model_s"] = a["active_s"] - a["tool_s"]
        a["stories"] = story_times(a)
    # The cost record is the session's total so far: shared out by weighted tokens over the work before
    # it. Work after it (a session resumed after the record) is left to `collect`: the learned rate, or unknown.
    total_w = sum(covered.values()) if session_cost is not None else 0.0
    for k, a in per.items():
        w = covered.get(k, 0.0) if total_w > 0 else 0.0
        a["cost_usd"] = session_cost * w / total_w if w > 0 else None
        a["weighted_unpriced"] = max(0.0, a["weighted"] - w)
    out = {
        "session_id": session_id, "transcript": str(transcript), "cwd": cwd, "worktree": worktree, "version": version,
        "start": timed[0].get("timestamp"), "end": timed[-1].get("timestamp"), "cost_usd": session_cost,
        "active_seen": seen, "summarised_at": iso(_dt.datetime.now().timestamp()), "features": per,
    }
    if write:
        d = metrics_dir(root) / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{session_id}.json.tmp"
        tmp.write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, d / f"{session_id}.json")
    return out


def story_of(ctx: Ctx, key: str, rest: str) -> str | None:
    m = SEED_STORY_RE.search(rest)
    if m:
        return m.group(1)
    m = US_RE.search(rest)
    stories = ctx.stories.get(key) or []
    if m and 0 < int(m.group(1)) <= len(stories):
        return stories[int(m.group(1)) - 1]
    return None


def active_between(a: dict, start: float, end: float) -> float:
    return sum(max(0.0, min(e, end) - max(s, start)) for s, e in a["intervals"])


def story_times(a: dict) -> dict:
    """Active time per story, measured per batch of ticks (ticks come in batches, so per-task times would
    be wrong): the time since the previous batch goes to the stories ticked in this batch."""
    ticks = sorted(a["ticks"], key=lambda x: x["t"])
    out: dict = {}
    prev_end = a["first"] or (ticks[0]["t"] if ticks else 0)
    i = 0
    while i < len(ticks):
        j = i
        while j + 1 < len(ticks) and ticks[j + 1]["t"] - ticks[j]["t"] <= TICK_BATCH_S:
            j += 1
        batch = ticks[i:j + 1]
        stories = sorted({x["story"] for x in batch if x["story"]})
        secs = active_between(a, prev_end, batch[-1]["t"])
        for s in stories:
            out[s] = out.get(s, 0.0) + secs / len(stories)
        prev_end = batch[-1]["t"]
        i = j + 1
    return out


# --------------------------------------------------------------------------------------------
# Aggregation

def load_sessions(root: Path) -> list:
    d = metrics_dir(root) / "sessions"
    out = []
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return out


def merge_intervals(intervals: list) -> list:
    out: list = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def unpriced(a: dict) -> float:
    """Weighted tokens of a feature aggregate that no cost record covers (summaries before 0.5.4 priced
    a feature in whole or not at all)."""
    if "weighted_unpriced" in a:
        return float(a.get("weighted_unpriced") or 0.0)
    return float(a.get("weighted") or 0.0) if a.get("cost_usd") is None else 0.0


def combine(parts: list) -> dict:
    """Sum feature aggregates from several sessions. The cost is None (unknown) when no part has one; a
    sum that includes estimated or unknown parts is marked `cost_estimated` (shown with "+")."""
    total = new_agg()
    total.update(model_s=0.0, cost_usd=None, stories={}, sessions=0, cost_estimated=False)
    unknown = False
    intervals = []
    for p in parts:
        total["sessions"] += 1
        for k in ("active_s", "tool_s", "model_s", "wait_s", "gates", "turns", "weighted"):
            total[k] += p.get(k) or 0
        for k, v in (p.get("tokens") or {}).items():
            total["tokens"][k] = total["tokens"].get(k, 0) + v
        for field in ("models", "efforts", "phases"):
            for k, v in (p.get(field) or {}).items():
                bump(total[field], k, v)
        for name, r in (p.get("repos") or {}).items():
            t = total["repos"].setdefault(name, {"calls": 0, "tool_s": 0.0})
            t["calls"] += r.get("calls", 0)
            t["tool_s"] += r.get("tool_s", 0.0)
        for s, v in (p.get("stories") or {}).items():
            total["stories"][s] = total["stories"].get(s, 0.0) + v
        for k, v in (p.get("subagents") or {}).items():
            t = total["subagents"].setdefault(k, {"runs": 0, "efforts": {}})
            t["runs"] += v.get("runs", 0)
            for lv, n in (v.get("efforts") or {}).items():
                bump(t["efforts"], lv, n)
        for k, v in (p.get("segments") or {}).items():
            t = total.setdefault("segments", {}).setdefault(k, {"runs": 0, "agent_s": 0.0, "weighted": 0.0})
            for f in ("runs", "agent_s", "weighted"):
                t[f] += v.get(f, 0)
        total["ticks"] += p.get("ticks") or []
        intervals += p.get("intervals") or []
        if p.get("cost_usd") is not None:
            total["cost_usd"] = (total["cost_usd"] or 0.0) + p["cost_usd"]
            total["cost_estimated"] = total["cost_estimated"] or bool(p.get("cost_estimated")) or unpriced(p) > 0
        elif (p.get("weighted") or 0) > 0:
            unknown = True
        for k, fn in (("first", min), ("last", max)):
            if p.get(k) is not None:
                total[k] = p[k] if total[k] is None else fn(total[k], p[k])
    if unknown and total["cost_usd"] is not None:
        total["cost_estimated"] = True
    merged = merge_intervals(intervals)
    total["intervals"] = merged
    total["elapsed_active_s"] = sum(e - s for s, e in merged)
    total["calendar_s"] = (total["last"] - total["first"]) if total["first"] is not None else 0.0
    total["agent_s"] = total["active_s"]
    if total.get("segments"):
        # Built in segments: the subagents' own time beside the orchestrator's; how many worked at once
        seg_s = sum(v["agent_s"] for v in total["segments"].values())
        total["segment_agent_s"] = seg_s
        total["parallelism"] = round(seg_s / total["elapsed_active_s"], 2) if total["elapsed_active_s"] > 0 else None
    return total


def brief_line(m: dict) -> str:
    if not m.get("sessions"):
        return "no sessions recorded yet"
    models = ", ".join(sorted(m["models"], key=lambda k: -m["models"][k])[:2]) or "—"
    efforts = "/".join(sorted(m["efforts"], key=lambda k: -m["efforts"][k])[:2]) or "—"
    cost = cost_text(m.get("cost_usd"), m.get("cost_estimated"))
    return (f"agent {duration(m['agent_s'])} (model {duration(m['model_s'])}, tools {duration(m['tool_s'])}) · "
            f"{m['gates']} human gate{'' if m['gates'] == 1 else 's'} (waited {duration(m['wait_s'])}) · calendar {duration(m['calendar_s'], True)} · "
            f"{cost} · {fmt_tokens(sum(m['tokens'].values()))} tokens · {models} · effort {efforts}")


def cost_text(usd: float | None, estimated: bool = False) -> str:
    return "cost unknown" if usd is None else fmt_cost(usd) + ("+" if estimated else "")


def collect(root: Path) -> dict:
    """key -> list of feature aggregates across every recorded session. Work no cost record covers (a
    session still running, or what a session did after it was resumed) is priced at the rate, dollars per
    weighted token, of the work that has one; with no priced work at all its cost stays unknown."""
    sessions = load_sessions(root)
    parts = [(key, a) for s in sessions for key, a in (s.get("features") or {}).items()]
    cost = weight = 0.0
    for _, a in parts:
        if a.get("cost_usd") is not None:
            cost += a["cost_usd"]
            weight += (a.get("weighted") or 0.0) - unpriced(a)
    rate = cost / weight if weight > 0 else None
    out: dict = {}
    for key, a in parts:
        if rate is not None and unpriced(a) > 0:
            a = {**a, "cost_usd": round((a.get("cost_usd") or 0.0) + unpriced(a) * rate, 4), "cost_estimated": True}
        out.setdefault(key, []).append(a)
    return out


def epic_metrics(root: Path, cfg: dict, bcfg: dict, bp, epic: dict | None = None) -> dict:
    parts = collect(root)
    bid = bcfg["id"]
    if epic is not None:
        m = combine(parts.get(f"{bid}:{epic['id']}", []))
        m.pop("ticks", None)
        m.pop("intervals", None)
        trace = None
        from .compiler import find_feature_dir

        d = find_feature_dir(root, bcfg, epic["id"])
        if d:
            trace = read_trace(d)
        est = (trace or {}).get("estimate")
        brief = brief_line(m)
        if est and m.get("sessions"):
            brief += f" · estimate was agent {duration(est['agent_s'][0])} – {duration(est['agent_s'][1])}"
        return {"blueprint_id": bid, "epic": epic["id"], "metrics": m, "estimate_at_compile": est, "brief": brief}
    epics = {}
    for e in bp.epics:
        m = combine(parts.get(f"{bid}:{e['id']}", []))
        if m["sessions"]:
            m.pop("ticks", None)
            m.pop("intervals", None)
            epics[e["id"]] = {"metrics": m, "brief": brief_line(m)}
    other = {k: combine(v) for k, v in parts.items() if k in (f"{bid}:discover", f"{bid}:amend")}
    for m in other.values():
        m.pop("ticks", None)
        m.pop("intervals", None)
    return {"blueprint_id": bid, "epics": epics, "discovery_and_amendments": other,
            "sessions": len(load_sessions(root))}


def write_epic_summary(root: Path, bcfg: dict, epic: dict, data: dict) -> None:
    from .compiler import find_feature_dir

    d = find_feature_dir(root, bcfg, epic["id"])
    if d is None:
        raise SystemExit(f"error: {epic['id']} is not compiled")
    m = data["metrics"]
    keep = {k: m.get(k) for k in ("agent_s", "model_s", "tool_s", "wait_s", "gates", "calendar_s", "cost_usd",
                                  "cost_estimated", "tokens", "models", "efforts", "phases", "repos", "stories", "sessions")}
    (d / "metrics.json").write_text(json.dumps({"epic": epic["id"], **keep, "brief": data["brief"]}, indent=2,
                                               ensure_ascii=False) + "\n", encoding="utf-8")


def worktree_paths(root: Path) -> list:
    rc, out = git(root, "worktree", "list", "--porcelain")
    paths = [Path(ln.split(" ", 1)[1]) for ln in out.splitlines() if rc == 0 and ln.startswith("worktree ")]
    return paths or [root.resolve()]


def backfill(root: Path, cfg: dict, dirs: list | None = None) -> dict:
    """Summarise every transcript Claude Code still keeps for the hub and its worktrees."""
    if not dirs:
        dirs = [transcript_dir(p) for p in worktree_paths(main_root(root))]
    ctx = Ctx(root, cfg)
    seen, with_work = 0, 0
    for d in dirs:
        for t in sorted(Path(d).glob("*.jsonl")):
            s = summarise_session(root, cfg, t, ctx)
            if s:
                seen += 1
                with_work += any(k and ":" in k for k in s["features"])
    return {"dirs": [str(d) for d in dirs], "sessions": seen, "sessions_with_blueprint_work": with_work,
            "store": str(metrics_dir(root))}


def tail_entries(path: Path, size: int = 96_000) -> list:
    """The JSON entries in the last `size` bytes of a transcript."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            n = fh.tell()
            fh.seek(max(0, n - size))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for ln in data.split("\n")[1:] if n > size else data.split("\n"):
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def ended_with_cost(transcript: Path) -> bool:
    """Whether the session has ended: after its conversation, the transcript's last record is the cost."""
    last = None
    for e in tail_entries(transcript, 65_536):
        if e.get("type") == "cost-state":
            last = "cost"
        elif e.get("type") in ("user", "assistant"):
            last = "conversation"
    return last == "cost"


def refresh_finished(root: Path, cfg: dict, ctx: Ctx | None = None, skip: str | None = None,
                     limit: int = LOOK_AGAIN, days: int = 7) -> int:
    """Summarise again the sessions of the last `days` that ended after their last summary. Claude Code
    writes a session's cost when the session ends, after its last Stop event: the hook never sees it."""
    d = metrics_dir(root) / "sessions"
    files = []
    for p in d.glob("*.json") if d.is_dir() else []:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if p.stem != skip and mtime > time.time() - days * 86400:
            files.append((mtime, p.stem))
    done = 0
    for summarised, sid in sorted(files, reverse=True):
        try:  # the transcript's path is near the start of the summary: no need to parse all of it
            with open(d / f"{sid}.json", encoding="utf-8", errors="replace") as fh:
                m = re.search(r"\"transcript\":\s*(\"(?:[^\"\\]|\\.)*\")", fh.read(4096))
            t = Path(json.loads(m.group(1))) if m else None
            changed = t is not None and t.is_file() and t.stat().st_mtime > summarised + 1
        except (OSError, ValueError):
            continue
        if changed and ended_with_cost(t):
            ctx = ctx or Ctx(root, cfg)
            summarise_session(root, cfg, t, ctx)
            done += 1
            if done >= limit:
                break
    return done
