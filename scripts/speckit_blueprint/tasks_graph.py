"""The task graph of a feature's tasks.md, and the segments it can be built in (DESIGN.md "Building an
epic in segments").

tasks.md keeps Spec Kit's line format. A dependency is written the way Spec Kit's own tasks template
writes it, as a suffix naming earlier tasks: `- [ ] T013 [US2] Session API in `src/api.ts` (depends on T012)`.
Without one, a task depends on the task before it; a `[P]` task on the task before its `[P]` group, and
the task after a `[P]` group on the whole group. Every Setup and Foundational task comes before every
story and Polish task, and every story task before Polish. Two tasks that name the same file never run
at the same time.

The plan: a topological order is cut wherever every task before the cut is an ancestor of every task
after it. A block between two cuts whose tasks fall apart into independent groups (sharing no file) is a
parallel stage, one chain of tasks per group; the rest are serial. Every segment, serial or chain, is
built by one subagent, task after task. Pure and deterministic: the same tasks.md gives the same plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .core import COMPILED_FILES, sha

TASK_RE = re.compile(r"^\s*-\s*\[( |x|X)\]\s*(T\d+)\b(.*)$")
PHASE_RE = re.compile(r"^#{2,4}\s+Phase\s+(\d+)\s*[:.\-—–]?\s*(.*?)\s*$", re.I)
DEPENDS_RE = re.compile(r"\(\s*(?:depends\s+on|depends:|after)\s*:?\s*([^)]*)\)", re.I)
TID_RE = re.compile(r"\bT\d+\b")
LEAD_TAG_RE = re.compile(r"^\s*\[([^\]]+)\]")
BACKTICK_RE = re.compile(r"`([^`\s]+)`")
BARE_PATH_RE = re.compile(r"(?<![`\w/.-])((?:\.{0,2}/)?[\w@.-]+(?:/[\w@.-]+)+\.[A-Za-z0-9]{1,8}|[\w@.-]+(?:/[\w@.-]+)*/)(?![\w/`])")
EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")

DEFAULTS = {"max_parallel": 3, "min_chain_tasks": 2}


def phase_kind(title: str) -> str:
    t = title.lower()
    if "setup" in t:
        return "setup"
    if "foundation" in t or "blocking prerequisite" in t:
        return "foundational"
    if "polish" in t or "cross-cutting" in t:
        return "polish"
    if re.search(r"user story|\bus\d+\b|story", t):
        return "story"
    return "other"


def looks_like_path(s: str) -> bool:
    s = s.strip().rstrip(",.;:")
    if not s or " " in s or s.startswith(("http://", "https://")) or "(" in s or "=" in s:
        return False
    if s.endswith("/") and len(s) > 1:
        return True
    return bool(EXT_RE.search(s)) and ("/" in s or bool(re.match(r"^[\w@.-]+\.[A-Za-z0-9]{1,8}$", s)))


def files_of(text: str) -> list:
    """Paths a task names: backticked spans that look like paths, and bare tokens with a folder."""
    text = DEPENDS_RE.sub(" ", text)
    out = []
    for m in BACKTICK_RE.finditer(text):
        s = m.group(1).strip().rstrip(",.;:")
        if looks_like_path(s):
            out.append(s)
    for m in BARE_PATH_RE.finditer(BACKTICK_RE.sub(" ", text)):
        s = m.group(1).rstrip(",.;:")
        if looks_like_path(s):
            out.append(s)
    seen, uniq = set(), []
    for p in out:
        p = re.sub(r"^\./", "", p)
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def parse(text: str, repos: list | None = None) -> dict:
    """Phases and tasks, in file order. `repos`: the configured repository names a `[name]` tag may give."""
    repos = set(repos or [])
    tasks, phases, errors = [], [], []
    phase = {"n": 0, "title": "", "kind": "other"}
    ids: dict = {}
    for lineno, ln in enumerate(text.split("\n"), 1):
        pm = PHASE_RE.match(ln)
        if pm:
            phase = {"n": int(pm.group(1)), "title": pm.group(2), "kind": phase_kind(pm.group(2))}
            phases.append(phase)
            continue
        m = TASK_RE.match(ln)
        if not m:
            continue
        tid, rest = m.group(2), m.group(3)
        tags, body = [], rest
        while True:
            tm = LEAD_TAG_RE.match(body)
            if not tm:
                break
            tags.append(tm.group(1).strip())
            body = body[tm.end():]
        parallel = any(t.upper() == "P" for t in tags)
        story = next((t.upper() for t in tags if re.match(r"^US\d+$", t, re.I)), None)
        repo = next((t for t in tags if t in repos), None)
        deps = None
        dm = DEPENDS_RE.search(body)
        if dm:
            deps = TID_RE.findall(dm.group(1))
        kind = phase["kind"]
        if kind == "other" and story:
            kind = "story"
        task = {"id": tid, "index": len(tasks), "line": lineno, "done": m.group(1) in "xX", "parallel": parallel,
                "story": story, "repo": repo, "phase": phase["n"], "kind": kind, "deps": deps,
                "files": files_of(body), "text": body.strip()}
        if tid in ids:
            errors.append(f"{tid} appears twice (lines {tasks[ids[tid]]['line']} and {lineno})")
            continue
        ids[tid] = len(tasks)
        tasks.append(task)
    return {"tasks": tasks, "phases": phases, "errors": errors}


def file_keys(task: dict) -> list:
    return [(task["repo"] or "", f) for f in task["files"]]


def overlap(a: tuple, b: tuple) -> bool:
    if a[0] != b[0]:
        return False
    x, y = a[1], b[1]
    return x == y or (x.endswith("/") and y.startswith(x)) or (y.endswith("/") and x.startswith(y))


def share_file(a: dict, b: dict) -> bool:
    return any(overlap(x, y) for x in file_keys(a) for y in file_keys(b))


def edges(parsed: dict) -> tuple[dict, list, list]:
    """Direct predecessors per task index, with the errors and warnings found on the way."""
    tasks = parsed["tasks"]
    by_id = {t["id"]: t["index"] for t in tasks}
    preds: dict = {t["index"]: set() for t in tasks}
    errors, warnings = list(parsed["errors"]), []
    group: list = []  # the [P] group the previous task belongs to
    anchor: list = []  # what the current [P] group depends on
    prev = None
    for t in tasks:
        i = t["index"]
        if group and t["parallel"] and t["deps"] is None and tasks[prev]["phase"] != t["phase"]:
            anchor, group = list(group), [i]  # a [P] group never spans two phases: the new one waits for the old
            preds[i].update(anchor)
            prev = i
            continue
        if t["deps"] is not None:
            for d in t["deps"]:
                j = by_id.get(d)
                if j is None:
                    errors.append(f"{t['id']} depends on {d}, which is not in tasks.md")
                elif j == i:
                    errors.append(f"{t['id']} depends on itself")
                elif j > i:
                    errors.append(f"{t['id']} depends on {d}, which comes after it (name earlier tasks only)")
                else:
                    preds[i].add(j)
        elif t["parallel"]:
            if not group:
                anchor = [prev] if prev is not None else []
            preds[i].update(anchor)
        elif group:
            preds[i].update(group)
        elif prev is not None:
            preds[i].add(prev)
        if t["parallel"] and t["deps"] is None:
            group = group + [i] if group else [i]
        else:
            group = []
        prev = i
    early = [t["index"] for t in tasks if t["kind"] in ("setup", "foundational")]
    stories = [t["index"] for t in tasks if t["kind"] == "story"]
    for t in tasks:
        if t["kind"] in ("story", "polish", "other"):
            preds[t["index"]].update(j for j in early if j != t["index"])
        if t["kind"] == "polish":
            preds[t["index"]].update(stories)
    for t in tasks:
        for f in t["files"]:
            if Path(f).name in COMPILED_FILES and re.search(r"(^|/)specs?/", f):
                errors.append(f"{t['id']} names {f}, a compiled file; change the blueprint through /speckit-blueprint-amend")
    return preds, errors, warnings


def closure(n: int, preds: dict, order: list) -> list:
    """anc[i]: bitset of every ancestor of i, over a topological order."""
    anc = [0] * n
    for i in order:
        a = 0
        for j in preds[i]:
            a |= anc[j] | (1 << j)
        anc[i] = a
    return anc


def topo(n: int, preds: dict) -> list | None:
    """Kahn's algorithm, ties broken by file order; None on a cycle."""
    indeg = {i: len(preds[i]) for i in range(n)}
    succ: dict = {i: [] for i in range(n)}
    for i in range(n):
        for j in preds[i]:
            succ[j].append(i)
    import heapq

    ready = [i for i in range(n) if indeg[i] == 0]
    heapq.heapify(ready)
    out = []
    while ready:
        i = heapq.heappop(ready)
        out.append(i)
        for k in succ[i]:
            indeg[k] -= 1
            if indeg[k] == 0:
                heapq.heappush(ready, k)
    return out if len(out) == n else None


def graph(text: str, repos: list | None = None, repo_order: list | None = None) -> dict:
    """The parsed tasks with their full dependency sets (explicit, default, barriers, same file)."""
    parsed = parse(text, repos)
    tasks = parsed["tasks"]
    n = len(tasks)
    preds, errors, warnings = edges(parsed)
    order = topo(n, preds)
    if order is None:
        errors.append("the dependencies form a cycle")
        return {"tasks": tasks, "preds": preds, "anc": None, "order": None, "errors": errors, "warnings": warnings}
    anc = closure(n, preds, order)
    # Two tasks that name the same file and are not ordered yet: the earlier runs first
    for a in range(n):
        for b in range(a + 1, n):
            if (anc[b] >> a) & 1 or (anc[a] >> b) & 1 or not share_file(tasks[a], tasks[b]):
                continue
            preds[b].add(a)
            add = anc[a] | (1 << a)
            for k in range(n):
                if k == b or (anc[k] >> b) & 1:
                    anc[k] |= add
    order = topo(n, preds)
    for t in tasks:
        if not t["files"] and not t["done"]:
            warnings.append(f"{t['id']} names no file: it cannot run beside other tasks")
    if repo_order and len(repo_order) > 1:
        rank = {r: k for k, r in enumerate(repo_order)}
        for t in tasks:
            if t["done"] or not t["repo"] or rank.get(t["repo"], 0) == 0:
                continue
            providers = {r for r in repo_order[:rank[t["repo"]]]}
            has_provider_tasks = any(x["repo"] in providers for x in tasks)
            if has_provider_tasks and not any(tasks[j]["repo"] in providers for j in range(n) if (anc[t["index"]] >> j) & 1):
                warnings.append(f"{t['id']} ({t['repo']}) depends on no task of the repositories that provide for it "
                                f"({', '.join(sorted(providers))})")
    return {"tasks": tasks, "preds": preds, "anc": anc, "order": order, "errors": errors, "warnings": warnings}


def plan(text: str, repos: list | None = None, repo_order: list | None = None, max_parallel: int = 3,
         min_chain_tasks: int = 2) -> dict:
    """Stages of segments over the open tasks."""
    g = graph(text, repos, repo_order)
    tasks = g["tasks"]
    base = {"errors": g["errors"], "warnings": g["warnings"], "stages": [], "stats": {}}
    if g["errors"]:
        return base
    anc, order = g["anc"], g["order"]
    open_ = [i for i in order if not tasks[i]["done"]]
    if not open_:
        base["stats"] = {"open": 0, "stages": 0}
        base["hash"] = sha("[]")
        return base
    pos = {i: k for k, i in enumerate(open_)}
    n_open = len(open_)
    # desc over open tasks, as bitsets of positions in open_
    desc = [0] * n_open
    for k, i in enumerate(open_):
        for k2 in range(k + 1, n_open):
            if (anc[open_[k2]] >> i) & 1:
                desc[k] |= 1 << k2
    full = (1 << n_open) - 1
    cuts = []
    inter = full
    for k in range(n_open - 1):
        inter &= desc[k]
        suffix = full & ~((1 << (k + 1)) - 1)
        if inter & suffix == suffix:
            cuts.append(k + 1)
    bounds = [0] + cuts + [n_open]
    blocks = [open_[a:b] for a, b in zip(bounds, bounds[1:])]

    def related(a: int, b: int) -> bool:
        return bool((anc[a] >> b) & 1 or (anc[b] >> a) & 1)

    raw = []  # ("serial", [tasks]) or ("parallel", [[tasks], …])
    for block in blocks:
        if len(block) == 1:
            raw.append(("serial", block))
            continue
        parent = {i: i for i in block}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for x in range(len(block)):
            for y in range(x + 1, len(block)):
                a, b = block[x], block[y]
                if related(a, b) or share_file(tasks[a], tasks[b]):
                    parent[find(a)] = find(b)
        comps: dict = {}
        for i in block:
            comps.setdefault(find(i), []).append(i)
        chains = [sorted(c, key=lambda i: pos[i]) for c in comps.values()]
        fileless = any(not tasks[i]["files"] for i in block)
        if len(chains) < 2 or fileless:
            raw.append(("serial", block))
            continue
        chains.sort(key=lambda c: pos[c[0]])
        # Small chains join the smallest other chain
        while len(chains) > 1 and min(len(c) for c in chains) < min_chain_tasks:
            small = min(chains, key=lambda c: (len(c), pos[c[0]]))
            chains.remove(small)
            target = min(chains, key=lambda c: (len(c), pos[c[0]]))
            target.extend(small)
            target.sort(key=lambda i: pos[i])
        if len(chains) < 2:
            raw.append(("serial", block))
            continue
        # At most max_parallel segments: largest chains first, each to the lightest segment
        if len(chains) > max_parallel:
            bins = [[] for _ in range(max_parallel)]
            for c in sorted(chains, key=lambda c: (-len(c), pos[c[0]])):
                min(bins, key=len).extend(c)
            chains = [sorted(b, key=lambda i: pos[i]) for b in bins if b]
            chains.sort(key=lambda c: pos[c[0]])
        raw.append(("parallel", chains))
    merged = []
    for kind, val in raw:
        if kind == "serial" and merged and merged[-1][0] == "serial":
            merged[-1] = ("serial", merged[-1][1] + val)
        else:
            merged.append((kind, val))

    def seg(sid: str, idx: list, stage: int) -> dict:
        ts = [tasks[i] for i in idx]
        return {"id": sid, "stage": stage, "tasks": [t["id"] for t in ts],
                "files": sorted({f"{t['repo'] + ':' if t['repo'] else ''}{f}" for t in ts for f in t["files"]}),
                "repositories": sorted({t["repo"] for t in ts if t["repo"]}),
                "stories": sorted({t["story"] for t in ts if t["story"]})}

    stages = []
    for n, (kind, val) in enumerate(merged, 1):
        if kind == "serial":
            stages.append({"n": n, "kind": "serial", "segments": [seg(f"S{n}", val, n)]})
        else:
            stages.append({"n": n, "kind": "parallel",
                           "segments": [seg(f"S{n}{chr(ord('a') + k)}", c, n) for k, c in enumerate(val)]})
    longest = sum(max(len(s["tasks"]) for s in st["segments"]) for st in stages)
    # critical path over open tasks
    depth: dict = {}
    for i in open_:
        depth[i] = 1 + max((depth[j] for j in open_ if (anc[i] >> j) & 1 and j in depth), default=0)
    base["stages"] = stages
    base["stats"] = {"open": n_open, "stages": len(stages),
                     "widest": max(len(st["segments"]) for st in stages),
                     "critical_path": max(depth.values()),
                     "speedup": round(n_open / longest, 2) if longest else 1.0}
    if not any(st["kind"] == "parallel" for st in stages):
        base["warnings"].append("no stage runs in parallel: name each story's real dependencies with "
                                "`(depends on …)` and give every task its file paths")
    base["hash"] = sha(json.dumps([[s["tasks"] for s in st["segments"]] for st in stages]))
    return base


def render(p: dict) -> str:
    """The plan in a few lines: S1 → S2a ∥ S2b → S3."""
    if not p["stages"]:
        return "nothing open" if not p["errors"] else "no plan: " + "; ".join(p["errors"])
    parts = []
    for st in p["stages"]:
        parts.append(" ∥ ".join(f"{s['id']}({len(s['tasks'])})" for s in st["segments"]))
    return " → ".join(parts)
