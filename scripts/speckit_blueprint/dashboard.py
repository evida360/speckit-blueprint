"""The dashboard (DESIGN.md "Dashboard"): one JSON state built from status, the board, metrics and
estimates; a local page that polls it; an exported single file for people who cannot reach this machine;
and one line for Claude Code's status line.

`blueprint.py dashboard` serves `dashboard.html` and `/api/state` on 127.0.0.1 only (stdlib http.server).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys
import threading
import time
from pathlib import Path

from .core import SCRIPTS_DIR, active_feature, read_trace
from .config import bp_config, load_config
from .model import Blueprint
from .repos import facts, main_root, registry
from .board import live, load as load_board
from .status import project_status
from .metrics import Ctx, collect, combine, parse_ts, tail_entries, transcript_dir
from .estimate import epic_acs, estimate, history
from .units import cost as fmt_cost, duration

HTML = Path(__file__).resolve().parent / "dashboard.html"
TASK_RE = re.compile(r"^\s*-\s*\[( |x|X)\]\s*(T\d+)\s*(.*)$")
REPO_PREFIX_RE = re.compile(r"\[([a-z][a-z0-9._-]*)\]")
SEED_RE = re.compile(r"\b(?:([A-Za-z][A-Za-z0-9]*):)?(E\d{2}-S\d{2})-T\d+")
US_RE = re.compile(r"\[US(\d+)\]")
ACTIVE_STATES = ("compiled", "stale", "tasked", "in-progress", "implemented")
_FACTS: dict = {}


def iso(t: float | None) -> str | None:
    return _dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds") if t else None


def cached_facts(path: Path, branch: str, base: str) -> dict:
    key = (str(path), branch, base)
    hit = _FACTS.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    f = facts(path, branch, base)
    _FACTS[key] = (time.time(), f)
    return f


def read_tasks(tasks: Path, stories: list, ticks: dict) -> list:
    out = []
    if not tasks.is_file():
        return out
    for ln in tasks.read_text(encoding="utf-8").split("\n"):
        m = TASK_RE.match(ln)
        if not m:
            continue
        text = m.group(3).strip()
        seed = SEED_RE.search(text)
        story = seed.group(2) if seed else None
        us = US_RE.search(text)
        if not story and us and 0 < int(us.group(1)) <= len(stories):
            story = stories[int(us.group(1)) - 1]
        repos = [r for r in REPO_PREFIX_RE.findall(text) if not re.match(r"^(us\d+|p)$", r, re.I)]
        title = re.sub(r"^(\[[^\]]+\]\s*)+", "", text)
        title = SEED_RE.sub("", title, count=1).strip(" —-")
        out.append({"id": m.group(2), "done": m.group(1) in "xX", "story": story, "repository": repos[0] if repos else None,
                    "seed": seed.group(0) if seed else None, "title": title[:160], "ticked_at": iso(ticks.get(m.group(2)))})
    return out


def epic_entry(root: Path, cfg: dict, bcfg: dict, bp: Blueprint, e: dict, row: dict, board: dict,
               parts: dict, est: dict, reg: dict) -> dict:
    key = f"{bcfg['id']}:{e['id']}"
    held = board.get("epics", {}).get(key, {})
    where = Path(held["worktree"]) if row.get("elsewhere") and held.get("worktree") else root
    folder = (where / held["feature"]) if row.get("elsewhere") and held.get("feature") else (
        root / row["feature_directory"] if row.get("feature_directory") else None)
    m = combine(parts.get(key, []))
    ticks = {}
    for t in m.pop("ticks", []):
        ticks[t["task"]] = max(ticks.get(t["task"], 0), t["t"])
    story_ids = [s["id"] for s in e["stories"]]
    tasks = read_tasks(folder / "tasks.md", story_ids, ticks) if folder else []
    stories = []
    for s in e["stories"]:
        st_tasks = [t for t in tasks if t["story"] == s["id"]]
        done = sum(t["done"] for t in st_tasks)
        stories.append({"id": s["id"], "title": s["title"], "acs": len(s["acs"]), "tasks": st_tasks,
                        "done": done, "total": len(st_tasks), "active_s": round(m["stories"].get(s["id"], 0.0), 1),
                        "state": "done" if st_tasks and done == len(st_tasks) else "in-progress" if done else "open"})
    loose = [t for t in tasks if t["story"] not in story_ids]
    trace = read_trace(folder) if folder else None
    rows = (trace or {}).get("repositories") or [{"name": r["name"], "order": r["order"], "role": r.get("role", ""),
                                                   "branch": r.get("branch", ""), "base": r.get("base", "")} for r in e["repos"]]
    gates = board.get("gates", {}).get(key, {})
    repos = []
    for r in rows:
        entry = reg.get(r["name"], {})
        wt = ((held.get("repositories") or {}).get(r["name"]) or {}).get("worktree")
        path = Path(wt) if wt and Path(wt).is_dir() else Path(entry.get("path") or "")
        f = cached_facts(path, r.get("branch", ""), r.get("base", "")) if row["state"] in ACTIVE_STATES and entry else {}
        repo_tasks = [t for t in tasks if t["repository"] == r["name"]]
        repos.append({"name": r["name"], "order": r.get("order"), "role": r.get("role", ""), "branch": r.get("branch"),
                      "base": r.get("base"), "tasks_done": sum(t["done"] for t in repo_tasks), "tasks_total": len(repo_tasks),
                      "gate": gates.get(r["name"]), **{k: f.get(k) for k in ("current_branch", "on_branch", "branch_exists", "head", "dirty",
                                                                    "ahead", "behind", "merged", "exists")}})
    intervals = m.pop("intervals", [])
    return {
        "id": e["id"], "key": key, "title": e["title"], "state": row["state"], "depends": row.get("depends", []),
        "blocked_by": row.get("blocked_by", []), "elsewhere": row.get("elsewhere"), "started": row.get("started"),
        "feature": row.get("feature_directory"),
        "branch": (trace or {}).get("branch") or row.get("branch"), "acs": epic_acs(e),
        "tasks_done": sum(t["done"] for t in tasks), "tasks_total": len(tasks), "review": row.get("review"),
        "review_missing": row.get("review_missing", []), "stories": stories, "other_tasks": loose, "repositories": repos,
        "actual": {k: m.get(k) for k in ("agent_s", "model_s", "tool_s", "wait_s", "gates", "cost_usd", "cost_estimated",
                                         "calendar_s", "sessions", "models", "efforts", "phases", "subagents", "repos", "stories")}
                  | {"tokens": sum(m["tokens"].values()), "first": iso(m["first"]), "last": iso(m["last"]),
                     "spans": [[iso(s), iso(x)] for s, x in intervals[-200:]]},
        "estimate": (est.get("epics") or {}).get(e["id"]) or (trace or {}).get("estimate"),
    }


def activity(transcript: Path) -> dict:
    """What a live session is doing now, from the end of its transcript."""
    entries = [e for e in tail_entries(transcript) if parse_ts(e.get("timestamp"))]
    if not entries:
        return {}
    last = entries[-1]
    now = time.time()
    model = next(((e.get("message") or {}).get("model") for e in reversed(entries) if e.get("type") == "assistant"), None)
    effort = next((e.get("effort") for e in reversed(entries) if e.get("type") == "assistant" and e.get("effort")), None)
    open_tools = {}
    for e in entries:
        content = (e.get("message") or {}).get("content")
        for c in content if isinstance(content, list) else []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                open_tools[c.get("id")] = (c, parse_ts(e["timestamp"]))
            elif c.get("type") == "tool_result":
                open_tools.pop(c.get("tool_use_id"), None)
    out = {"model": model, "effort": effort, "last_at": iso(parse_ts(last["timestamp"]))}
    if open_tools:
        c, t = list(open_tools.values())[-1]
        inp = c.get("input") or {}
        what = inp.get("command") or inp.get("file_path") or inp.get("description") or inp.get("pattern") or ""
        out.update(kind="tool", tool=c.get("name"), detail=str(what)[:120], since=iso(t), for_s=round(now - t))
    elif last.get("type") == "assistant" or (last.get("type") == "system" and last.get("subtype") == "stop_hook_summary"):
        out.update(kind="waiting", since=iso(parse_ts(last["timestamp"])), for_s=round(now - parse_ts(last["timestamp"])))
    else:
        out.update(kind="thinking", since=iso(parse_ts(last["timestamp"])), for_s=round(now - parse_ts(last["timestamp"])))
    return out


def session_rows(root: Path, cfg: dict, board: dict, ctx: Ctx) -> list:
    out = []
    for sid, s in board.get("sessions", {}).items():
        if not live(s):
            continue
        t = Path(s["transcript"])
        feature = s.get("feature")
        key = ctx.active_key(feature, s.get("worktree")) or None
        story = None
        if feature and s.get("worktree"):
            rows = read_tasks(Path(s["worktree"]) / feature / "tasks.md", ctx.stories.get(key or "", []), {})
            story = next((r["story"] for r in rows if not r["done"] and r["story"]), None)
        out.append({"session_id": sid, "worktree": s.get("worktree"), "feature": feature, "epic": key, "story": story,
                    **activity(t)})
    return sorted(out, key=lambda r: r.get("worktree") or "")


def build_state(root: Path, cfg: dict) -> dict:
    board = load_board(root)
    parts = collect(root)
    reg = registry(cfg, root)
    ctx = Ctx(root, cfg)
    blueprints, rates = [], None
    for bid in cfg["blueprints"]:
        bcfg = bp_config(cfg, bid)
        path = root / bcfg["blueprint_path"]
        if not path.is_file():
            blueprints.append({"id": bid, "state": "no-blueprint", "blueprint": bcfg["blueprint_path"], "epics": []})
            continue
        bp = Blueprint(path)
        if rates is None:
            rates = history(root, cfg, bcfg, bp, parts)
        st = project_status(root, bcfg, board)
        est = estimate(root, cfg, bcfg, bp, parts=parts, rates=rates, st=st)
        rows = {r["epic"]: r for r in st.get("epics", [])}
        epics = [epic_entry(root, cfg, bcfg, bp, e, rows[e["id"]], board, parts, est, reg) for e in bp.epics]
        disc = {k.split(":")[1]: combine(v) for k, v in parts.items() if k in (f"{bid}:discover", f"{bid}:amend")}
        blueprints.append({
            "id": bid, "product": st.get("product"), "prefix": bcfg["prefix"], "state": st.get("state"),
            "blueprint": bcfg["blueprint_path"], "constitution": st.get("constitution"), "next": st.get("next"),
            "migration_needed": st.get("migration_needed"), "epics": epics,
            "acs_total": sum(e["acs"] for e in epics), "acs_done": sum(e["acs"] for e in epics if e["state"] == "done"),
            "remaining": est.get("remaining"), "confidence": est.get("confidence"), "history": est.get("history"),
            "discovery": {k: {f: v.get(f) for f in ("agent_s", "cost_usd", "cost_estimated", "wait_s", "sessions")} | {"tokens": sum(v["tokens"].values())}
                          for k, v in disc.items()},
        })
    repos = []
    for name, r in reg.items():
        touching = [f"{b['id']}:{e['id']}" for b in blueprints for e in b["epics"]
                    if e["state"] in ACTIVE_STATES and any(x["name"] == name for x in e["repositories"])]
        repos.append({"name": name, "path": r["main_path"], "hub": r["hub"], "gate": r.get("gate"), "epics_in_progress": touching})
    every = [e for b in blueprints for e in b["epics"]]
    worked = [e["actual"] for e in every if e["actual"].get("sessions")] \
        + [d for b in blueprints for d in (b.get("discovery") or {}).values() if d.get("sessions")]
    known = [x["cost_usd"] for x in worked if x.get("cost_usd") is not None]
    totals = {
        "epics": len(every), "epics_done": sum(e["state"] == "done" for e in every),
        "acs": sum(e["acs"] for e in every), "acs_done": sum(e["acs"] for e in every if e["state"] == "done"),
        "agent_s": sum(e["actual"]["agent_s"] or 0 for e in every)
                   + sum(d.get("agent_s") or 0 for b in blueprints for d in (b.get("discovery") or {}).values()),
        "cost_usd": sum(known) if known else None,  # None: no recorded cost yet (unknown, not zero)
        "tokens": sum(e["actual"]["tokens"] or 0 for e in every)
                  + sum(d.get("tokens") or 0 for b in blueprints for d in (b.get("discovery") or {}).values()),
        "cost_estimated": bool(known) and (len(known) < len(worked) or any(x.get("cost_estimated") for x in worked)),
        "in_progress": sum(e["state"] in ACTIVE_STATES for e in every),
    }
    firsts = [e["actual"]["first"] for e in every if e["actual"]["first"]]
    totals["started"] = min(firsts) if firsts else None
    return {"generated": iso(time.time()), "project": main_root(root).name, "blueprints": blueprints,
            "sessions": session_rows(root, cfg, board, ctx), "repositories": repos, "totals": totals,
            "metrics": cfg.get("metrics", "local")}


def page(state: dict | None = None) -> str:
    html = HTML.read_text(encoding="utf-8")
    if state is None:
        return html
    data = json.dumps(state, ensure_ascii=False).replace("</", "<\\/")
    return html.replace("<!--STATE-->", f"<script>window.__STATE__ = {data};</script>", 1)


def serve_or_export(root: Path, cfg: dict, port: int | None = None, export: str | None = None, open_browser: bool = True) -> int:
    if export:
        out = Path(export)
        out.write_text(page(build_state(root, cfg)), encoding="utf-8")
        print(json.dumps({"exported": str(out.resolve()), "bytes": out.stat().st_size}))
        return 0
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    cache: dict = {"t": 0.0, "body": b""}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                body, ctype = page().encode("utf-8"), "text/html; charset=utf-8"
            elif self.path.startswith("/api/state"):
                with lock:
                    if time.time() - cache["t"] > 2:
                        try:
                            cache["body"] = json.dumps(build_state(root, load_config(root)), ensure_ascii=False).encode("utf-8")
                        except Exception as exc:  # noqa: BLE001 - keep serving; show the error on the page
                            cache["body"] = json.dumps({"error": str(exc)}).encode("utf-8")
                        cache["t"] = time.time()
                    body = cache["body"]
                ctype = "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port if port is not None else 8765), Handler)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"speckit-blueprint dashboard: {url} (Ctrl+C to stop)", flush=True)
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def statusline(root: Path) -> int:
    """One line for Claude Code's status line: epic, story, tasks, session cost, model, estimate."""
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        data = {}
    cwd = Path(((data.get("workspace") or {}).get("current_dir")) or data.get("cwd") or root)
    from .core import find_root

    root = find_root(cwd)
    parts = []
    fd = active_feature(root)
    trace = read_trace(root / fd) if fd else None
    if trace:
        tasks = root / fd / "tasks.md"
        rows = read_tasks(tasks, list((trace.get("acs") or {}).keys()), {})
        open_story = next((t["story"] for t in rows if not t["done"] and t["story"]), None)
        label = trace.get("epic", "")
        if trace.get("blueprint_id") not in (None, "default"):
            label = f"{trace['blueprint_id']}:{label}"
        parts.append(label + (f"·{open_story.split('-')[-1]}" if open_story else ""))
        if rows:
            parts.append(f"{sum(t['done'] for t in rows)}/{len(rows)} tasks")
        est = trace.get("estimate")
        if est:
            parts.append(f"est {duration(est['agent_s'][0])}–{duration(est['agent_s'][1])}")
    usd = (data.get("cost") or {}).get("total_cost_usd")
    if usd is not None:
        parts.append(fmt_cost(float(usd)))
    model = (data.get("model") or {}).get("display_name")
    if model:
        parts.append(model)
    print(" · ".join(parts) if parts else "speckit-blueprint")
    return 0
