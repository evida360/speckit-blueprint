"""Metrics from Claude Code transcripts (DESIGN.md "Metrics").

The Stop hook summarises a session's transcript into `<git-common-dir>/speckit-blueprint/metrics/
sessions/<session>.json`: per epic (and per blueprint for discovery and amendments) the agent's active
time split into model and tool time, the time it waited for the user, tokens, cost, models, effort
levels, phases, repositories touched, task ticks and story times. Summaries are idempotent: the whole
transcript is read again each time.

Transcripts are Claude Code's own record, not a public API: every field is read defensively and the
Claude Code version is kept with each summary.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shlex
from pathlib import Path

from .core import TRACE_FILE, read_trace
from .config import bp_config, default_id, split_qualified, trace_blueprint_id
from .repos import git, main_root, registry
from .board import load as load_board, state_dir
from .units import cost as fmt_cost, duration, tokens as fmt_tokens

WAIT_CAP_S = 4 * 3600
TICK_BATCH_S = 60
WEIGHTS = {"input": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0}
PHASE_RE = re.compile(r"speckit-(?:blueprint-)?(discover|amend|compile|tasks|analyze|implement|review)\b")
PHASE_OF = {"discover": "discover", "amend": "amend", "compile": "plan", "tasks": "plan", "analyze": "plan",
            "implement": "build", "review": "review"}
TICK_RE = re.compile(r"-\s*\[[xX]\]\s*(T\d+)([^\n]*)")
OPEN_RE = re.compile(r"-\s*\[ \]\s*(T\d+)")
SEED_STORY_RE = re.compile(r"\b(?:[A-Za-z][A-Za-z0-9]*:)?(E\d{2}-S\d{2})-T\d+")
US_RE = re.compile(r"\[US(\d+)\]")
COMPILE_RE = re.compile(r"blueprint\.py[\"']?\s+(?:compile|activate)\s+([^\s;&|]+)")
FOLDER_NAME_RE = re.compile(r"^\d{3,}-(?:([a-z0-9][a-z0-9-]*?)-)?e(\d{2})-")
PATH_KEYS = ("file_path", "path", "notebook_path")


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
        for key, e in load_board(root).get("epics", {}).items():
            if e.get("feature"):
                self.folders.setdefault(Path(e["feature"]).name, key)
        reg = registry(cfg, root)
        paths = []
        for name, r in reg.items():
            paths.append((str(Path(r["main_path"])), name))
            paths.append((str(Path(r["path"])), name))
        for e in load_board(root).get("epics", {}).values():
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

    def key_of_folder(self, name: str) -> str:
        if name in self.folders:
            return self.folders[name]
        m = FOLDER_NAME_RE.match(name)
        if not m:
            return ""
        bid = m.group(1) if m.group(1) in self.cfg["blueprints"] else self.default
        return f"{bid}:E{m.group(2)}"

    def feature_in(self, text: str) -> str | None:
        m = self.folder_re.search(text)
        if m:
            return self.key_of_folder(m.group(1)) or None
        m = COMPILE_RE.search(text)
        if m and "/" not in m.group(1):
            try:
                bid, ref = split_qualified(self.cfg, m.group(1))
            except Exception:  # noqa: BLE001
                return None
            num = re.sub(r"\D", "", ref)
            return f"{bid or self.default}:E{int(num):02d}" if num else None
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


def is_prompt(e: dict) -> bool:
    if e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain"):
        return False
    content = (e.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and not any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content)


def text_of(e: dict) -> str:
    content = (e.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
    return ""


def subagent_usage(transcript: Path, session_id: str, agent_id: str) -> dict:
    """Tokens, models and effort levels of one subagent run."""
    path = transcript.parent / session_id / "subagents" / f"agent-{agent_id}.jsonl"
    out = {"tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}, "models": {}, "efforts": {}}
    for e in read_jsonl(path):
        if e.get("type") != "assistant":
            continue
        msg = e.get("message") or {}
        for k, v in usage_of(msg).items():
            out["tokens"][k] += v
        bump(out["models"], msg.get("model"))
        bump(out["efforts"], e.get("effort"))
    return out


def summarise_session(root: Path, cfg: dict, transcript: Path, ctx: Ctx | None = None, write: bool = True) -> dict | None:
    entries = [e for e in read_jsonl(transcript) if parse_ts(e.get("timestamp")) is not None or e.get("type") == "cost-state"]
    timed = [e for e in entries if parse_ts(e.get("timestamp")) is not None]
    if not timed:
        return None
    ctx = ctx or Ctx(root, cfg)
    session_id = next((e.get("sessionId") for e in entries if e.get("sessionId")), transcript.stem)
    cwd = next((e.get("cwd") for e in timed if e.get("cwd")), str(root))
    version = next((e.get("version") for e in reversed(timed) if e.get("version")), None)
    per: dict = {}
    feature: str | None = None
    phase = "other"
    pending: list = []  # (kind, payload) waiting for the turn's first feature
    tools: dict = {}  # tool_use id -> (key, start, repo)
    session_cost = None
    in_turn = False
    prev_t = None

    def agg(key: str) -> dict:
        return per.setdefault(key, new_agg())

    def credit(key: str | None, kind: str, payload) -> None:
        if key is None:
            pending.append((kind, payload))
            return
        a = agg(key)
        if kind == "active":
            s, e_, ph = payload
            a["active_s"] += e_ - s
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
            a["tool_s"] += secs
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

    def flush(key: str) -> None:
        while pending:
            kind, payload = pending.pop(0)
            credit(key, kind, payload)

    def set_feature(key: str) -> None:
        nonlocal feature
        feature = key
        flush(key)

    for e in entries:
        if e.get("type") == "cost-state":
            try:
                session_cost = float(e.get("totalCostUSD"))
            except (TypeError, ValueError):
                pass
            continue
        t = parse_ts(e.get("timestamp"))
        if t is None:
            continue
        if is_prompt(e):
            if prev_t is not None and in_turn:
                gap = max(0.0, t - prev_t)
                key = feature if feature is not None else ""
                a = agg(key)
                a["wait_s"] += min(gap, WAIT_CAP_S)
                a["gates"] += 1
            m = PHASE_RE.search(text_of(e))
            if m:
                phase = PHASE_OF[m.group(1)]
            hit = ctx.feature_in(text_of(e))
            if hit:
                set_feature(hit)
            if feature is not None:
                agg(feature)["turns"] += 1
            in_turn, prev_t = True, t
            continue
        if prev_t is not None and in_turn and t > prev_t:
            credit(feature, "active", (prev_t, t, phase))
        prev_t = max(prev_t or t, t)
        if e.get("type") == "assistant":
            msg = e.get("message") or {}
            for c in msg.get("content") or []:
                if not isinstance(c, dict) or c.get("type") != "tool_use":
                    continue
                inp = c.get("input") or {}
                blob = json.dumps(inp, ensure_ascii=False)
                m = PHASE_RE.search(blob)
                if m and c.get("name") in ("Skill", "SlashCommand"):
                    phase = PHASE_OF[m.group(1)]
                path = next((str(inp.get(k)) for k in PATH_KEYS if inp.get(k)), "")
                bid = ctx.blueprint_written(path) if c.get("name") in ("Write", "Edit", "MultiEdit") and path else None
                if bid and feature is None and phase in ("discover", "amend"):
                    set_feature(f"{bid}:{phase}")
                hit = ctx.feature_in(blob)
                if hit and hit != feature:
                    set_feature(hit)
                repo = ctx.repo_of(path or str(inp.get("command") or ""), cwd) or (ctx.hub if c.get("name") in ("Bash", "Edit", "Write", "MultiEdit", "Read") else None)
                tools[c.get("id")] = (feature, t, repo, c.get("name"))
                if path.endswith("/tasks.md") or path.endswith("tasks.md"):
                    pairs = [(inp.get("old_string") or "", inp.get("new_string") or "")] + [
                        (x.get("old_string") or "", x.get("new_string") or "") for x in inp.get("edits") or [] if isinstance(x, dict)]
                    folder = ctx.folder_re.search(path)
                    key = ctx.key_of_folder(folder.group(1)) if folder else feature
                    for old, new in pairs:
                        opened = set(OPEN_RE.findall(old))
                        for tid, rest in TICK_RE.findall(new):
                            if tid in opened and key:
                                agg(key)["ticks"].append({"task": tid, "story": story_of(ctx, key, rest), "t": t})
            credit(feature, "tokens", (usage_of(msg), msg.get("model"), e.get("effort")))
        elif e.get("type") == "user":
            content = (e.get("message") or {}).get("content")
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in tools:
                    key, start, repo, name = tools.pop(c["tool_use_id"])
                    credit(key, "tool", (repo, max(0.0, t - start)))
                    res = e.get("toolUseResult")
                    if name in ("Agent", "Task") and isinstance(res, dict) and res.get("agentId"):
                        credit(key, "sub", (res.get("agentType"), subagent_usage(transcript, session_id, res["agentId"])))
    if pending:
        flush("")
    for a in per.values():
        a["tool_s"] = min(a["tool_s"], a["active_s"])
        a["model_s"] = a["active_s"] - a["tool_s"]
        a["stories"] = story_times(a)
    total_w = sum(a["weighted"] for a in per.values()) or 0.0
    for a in per.values():
        a["cost_usd"] = (session_cost * a["weighted"] / total_w) if (session_cost is not None and total_w) else None
    out = {
        "session_id": session_id, "transcript": str(transcript), "cwd": cwd, "version": version,
        "start": timed[0].get("timestamp"), "end": timed[-1].get("timestamp"), "cost_usd": session_cost,
        "summarised_at": iso(_dt.datetime.now().timestamp()), "features": per,
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


def combine(parts: list) -> dict:
    """Sum feature aggregates from several sessions."""
    total = new_agg()
    total.update(model_s=0.0, cost_usd=0.0, stories={}, sessions=0, cost_estimated=False)
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
        total["ticks"] += p.get("ticks") or []
        intervals += p.get("intervals") or []
        if p.get("cost_usd") is None or p.get("cost_estimated"):
            total["cost_estimated"] = True
        total["cost_usd"] += p.get("cost_usd") or 0.0
        for k, fn in (("first", min), ("last", max)):
            if p.get(k) is not None:
                total[k] = p[k] if total[k] is None else fn(total[k], p[k])
    merged = merge_intervals(intervals)
    total["intervals"] = merged
    total["elapsed_active_s"] = sum(e - s for s, e in merged)
    total["calendar_s"] = (total["last"] - total["first"]) if total["first"] is not None else 0.0
    total["agent_s"] = total["active_s"]
    return total


def brief_line(m: dict) -> str:
    if not m.get("sessions"):
        return "no sessions recorded yet"
    models = ", ".join(sorted(m["models"], key=lambda k: -m["models"][k])[:2]) or "—"
    efforts = "/".join(sorted(m["efforts"], key=lambda k: -m["efforts"][k])[:2]) or "—"
    cost = fmt_cost(m["cost_usd"]) + ("+" if m.get("cost_estimated") else "")
    return (f"agent {duration(m['agent_s'])} (model {duration(m['model_s'])}, tools {duration(m['tool_s'])}) · "
            f"{m['gates']} human gate{'' if m['gates'] == 1 else 's'} (waited {duration(m['wait_s'])}) · calendar {duration(m['calendar_s'], True)} · "
            f"{cost} · {fmt_tokens(sum(m['tokens'].values()))} tokens · {models} · effort {efforts}")


def collect(root: Path) -> dict:
    """key -> list of feature aggregates across every recorded session. A session still running has no
    cost total yet: its parts are priced at the rate (dollars per weighted token) of the finished ones."""
    sessions = load_sessions(root)
    priced = [a for s in sessions for a in (s.get("features") or {}).values() if a.get("cost_usd") is not None]
    weight = sum(a.get("weighted") or 0 for a in priced)
    rate = (sum(a["cost_usd"] for a in priced) / weight) if weight else None
    out: dict = {}
    for s in sessions:
        for key, a in (s.get("features") or {}).items():
            if a.get("cost_usd") is None and rate is not None:
                a = {**a, "cost_usd": round((a.get("weighted") or 0) * rate, 4), "cost_estimated": True}
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
                                  "tokens", "models", "efforts", "phases", "repos", "stories", "sessions")}
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
