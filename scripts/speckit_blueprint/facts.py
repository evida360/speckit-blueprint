"""Facts the context and review hooks used to work out by themselves, now computed: which repositories an
epic changes and their git state, the gate results, preconditions, VERIFY-AT-BUILD lines, the next
decision ID, the ACs a review must give evidence for.
"""

from __future__ import annotations

import re
from pathlib import Path

from .core import TRACE_FILE, active_feature, read_trace, task_counts
from .config import bp_config
from .model import Blueprint, label_block
from .repos import base_branch, branch_name, facts, git, registry, run_gate
from .board import epic_key, load as load_board, record_gate
from .compiler import find_feature_dir, qualify
from .status import epic_acs, project_status, review_check


def epic_repo_rows(root: Path, cfg: dict, epic: dict, trace: dict | None = None) -> list[dict]:
    """The epic's repositories with the path to work in: the epic's own worktree when the board has one
    (parallel epics), else the repository's checkout."""
    reg = registry(cfg, root)
    held = load_board(root).get("epics", {}).get(epic_key(cfg["id"], epic["id"]), {})
    rows = (trace or {}).get("repositories") or []
    if not rows:
        from .compiler import repo_rows

        d = find_feature_dir(root, cfg, epic["id"])
        rows = repo_rows(cfg, root, epic, d.name if d else "")
    out = []
    for r in rows:
        entry = reg.get(r["name"], {})
        wt = ((held.get("repositories") or {}).get(r["name"]) or {}).get("worktree")
        path = wt if wt and Path(wt).is_dir() else entry.get("path", "")
        out.append({**r, "path": path, "gate": entry.get("gate", ""), "hub": entry.get("hub", False),
                    "configured": bool(entry)})
    return out


def repos_facts(root: Path, cfg: dict, bp: Blueprint, epic: dict) -> dict:
    d = find_feature_dir(root, cfg, epic["id"])
    trace = read_trace(d) if d else None
    gates = load_board(root).get("gates", {}).get(epic_key(cfg["id"], epic["id"]), {})
    out = []
    for r in epic_repo_rows(root, cfg, epic, trace):
        f = facts(Path(r["path"]), r.get("branch", ""), r.get("base", "")) if r["path"] else {"exists": False}
        out.append({**r, **f, "last_gate": gates.get(r["name"])})
    return {"blueprint_id": cfg["id"], "epic": epic["id"], "repositories": out}


def gate(root: Path, cfg: dict, bp: Blueprint, epic: dict, only: str | None = None) -> dict:
    """Run every target repository's gate in order (the config's `gate` command for each)."""
    d = find_feature_dir(root, cfg, epic["id"])
    rows = epic_repo_rows(root, cfg, epic, read_trace(d) if d else None)
    results, missing = [], []
    for r in rows:
        if only and r["name"] != only:
            continue
        if not r["gate"]:
            missing.append(r["name"])
            continue
        res = run_gate(r["gate"], Path(r["path"]))
        res["repository"] = r["name"]
        record_gate(root, epic_key(cfg["id"], epic["id"]), r["name"], res)
        results.append(res)
    return {"epic": epic["id"], "results": results, "no_gate_configured": missing,
            "note": ("Repositories without a `gate` in the config: run the gate the plan's Technical Context "
                     "names for them.") if missing else None}


def next_decision_id(root: Path, cfg: dict, eid: str) -> str:
    """The next epic-scoped decision record, e.g. DR-E08-3 (DESK:DR-E08-3 in shared files)."""
    p = root / cfg["decisions_path"]
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    nums = [int(n) for n in re.findall(r"DR-" + re.escape(eid) + r"-(\d+)", text)]
    return qualify(cfg, f"DR-{eid}-{max(nums, default=0) + 1}")


def plan_section(plan: Path, heading: str) -> str:
    if not plan.is_file():
        return ""
    text = plan.read_text(encoding="utf-8")
    m = re.search(r"^## " + re.escape(heading) + r"\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def feature_context(root: Path, cfg: dict) -> tuple[Path | None, dict | None]:
    fd = active_feature(root)
    if not fd:
        return None, None
    d = root / fd
    trace = read_trace(d)
    return (d, trace) if trace else (d, None)


def context_facts(root: Path, cfg: dict, bp: Blueprint) -> dict:
    """For the before_implement hook: does it apply, and what must hold before the first task."""
    d, trace = feature_context(root, cfg)
    if trace is None:
        return {"applies": False, "why": "the active feature was not compiled from a blueprint"}
    bcfg = bp_config(cfg, trace.get("blueprint_id") or cfg["id"])
    if bp is None or bcfg["id"] != cfg["id"]:
        bp = Blueprint(root / bcfg["blueprint_path"])
    epic = bp.epic(trace["epic"][1:])
    st = project_status(root, bcfg)
    row = next((r for r in st.get("epics", []) if r["epic"] == trace["epic"]), {})
    repos = repos_facts(root, bcfg, bp, epic)["repositories"] if epic else []
    total, done = task_counts(d / "tasks.md")
    problems = []
    if row.get("blocked_by"):
        problems.append(f"dependencies not done in this checkout: {', '.join(row['blocked_by'])}")
    if row.get("changed_since_compile"):
        problems.append("the blueprint changed since this epic was compiled: " + ", ".join(row["changed_since_compile"][:10]))
    for r in repos:
        if not r.get("exists"):
            problems.append(f"{r['name']}: {r.get('path') or 'no path'} does not exist "
                            "(`blueprint.py workspace setup` clones it from its `remote`)")
        elif r.get("git") and r.get("branch") and not r.get("on_branch") and not r.get("hub"):
            pushed = f"; it continues {r['remote_branch']}, pushed from another machine" if r.get("remote_branch") else ""
            problems.append(f"{r['name']}: on branch {r.get('current_branch')}, expected {r['branch']} "
                            f"(`blueprint.py workspace {trace['epic']} --create` sets up the worktrees{pushed})")
    verify = plan_section(d / "plan.md", "Preconditions and VERIFY-AT-BUILD")
    return {
        "build": build_facts(root, bcfg, d),
        "applies": True,
        "blueprint_id": bcfg["id"],
        "epic": trace["epic"],
        "title": trace.get("title"),
        "feature_directory": d.relative_to(root).as_posix(),
        "spec": (d / "spec.md").relative_to(root).as_posix(),
        "rules_command": f"python3 .specify/extensions/blueprint/scripts/blueprint.py rules --blueprint {bcfg['id']}",
        "tasks": {"done": done, "total": total},
        "repositories": [{k: r.get(k) for k in ("name", "order", "role", "path", "branch", "base", "current_branch",
                                                 "on_branch", "dirty", "gate", "last_gate")} for r in repos],
        "problems": problems,
        "preconditions_and_verify_at_build": verify,
        "decisions_log": bcfg["decisions_path"],
        "next_decision_id": next_decision_id(root, bcfg, trace["epic"]),
        "amendment_id_format": f"A-{trace['epic']}-<n> on this epic's branch",
    }


def build_facts(root: Path, cfg: dict, d: Path) -> dict:
    """How this epic is built: the mode, and in segments mode the plan over the open tasks."""
    mode = cfg.get("build", {}).get("mode", "sequential")
    out: dict = {"mode": mode}
    if mode != "segments" or not (d / "tasks.md").is_file():
        return out
    from .segments import active_run
    from .tasks_graph import plan, render

    b = cfg["build"]
    trace = read_trace(d) or {}
    order = [r["name"] for r in sorted(trace.get("repositories") or [], key=lambda r: int(r.get("order") or 0))]
    p = plan((d / "tasks.md").read_text(encoding="utf-8"), sorted(set(cfg.get("repositories", {})) | set(order)),
             order, b["max_parallel"], b["min_chain_tasks"])
    run = active_run(load_board(root), root, d.relative_to(root).as_posix())
    out.update({"plan": render(p), "errors": p["errors"], "warnings": p["warnings"], "stats": p["stats"],
                "running": run["run"] if run else None, "guide": ".specify/extensions/blueprint/references/segments.md"})
    return out


def diffstat(path: Path, base: str, branch: str) -> dict:
    out = {}
    if base and branch:
        rc, s = git(path, "diff", "--shortstat", f"{base}...{branch}")
        out["committed"] = s if rc == 0 else None
    rc, s = git(path, "diff", "--shortstat", "HEAD")
    out["uncommitted"] = s if rc == 0 else None
    return out


def review_facts(root: Path, cfg: dict, bp: Blueprint) -> dict:
    """For the review: the ACs that need evidence, the repositories and their diffs, the last gates."""
    d, trace = feature_context(root, cfg)
    if trace is None:
        return {"applies": False, "why": "the active feature was not compiled from a blueprint"}
    bcfg = bp_config(cfg, trace.get("blueprint_id") or cfg["id"])
    if bp is None or bcfg["id"] != cfg["id"]:
        bp = Blueprint(root / bcfg["blueprint_path"])
    epic = bp.epic(trace["epic"][1:])
    acs = trace.get("acs") or (epic_acs(epic) if epic else {})
    status, missing = review_check(d / "review.md", acs)
    repos = repos_facts(root, bcfg, bp, epic)["repositories"] if epic else []
    for r in repos:
        if r.get("exists") and r.get("git"):
            r["diff"] = diffstat(Path(r["path"]), r.get("base", ""), r.get("branch", ""))
    total, done = task_counts(d / "tasks.md")
    return {
        "applies": True,
        "blueprint_id": bcfg["id"],
        "epic": trace["epic"],
        "feature_directory": d.relative_to(root).as_posix(),
        "review": (d / "review.md").relative_to(root).as_posix(),
        "review_status": status,
        "acs_needing_evidence": acs,
        "acs_missing_evidence": missing,
        "tasks": {"done": done, "total": total},
        "review_checklist": bcfg["review_checklist_path"],
        "decisions_log": bcfg["decisions_path"],
        "decisions_this_epic": sorted(set(re.findall(r"(?:[A-Z][A-Z0-9]*:)?DR-" + re.escape(trace["epic"]) + r"-\d+",
                                                     (root / bcfg["decisions_path"]).read_text(encoding="utf-8")
                                                     if (root / bcfg["decisions_path"]).is_file() else ""))),
        "repositories": [{k: r.get(k) for k in ("name", "order", "role", "path", "branch", "base", "current_branch",
                                                 "dirty", "merged", "ahead", "behind", "diff", "gate", "last_gate", "contracts")}
                         for r in repos],
        "effort": bcfg["effort"].get("review"),
        "effort_model": bcfg.get("effort_model", "inherit"),
        "review_loop": review_loop_facts(root, bcfg, d, trace, acs),
    }


def review_loop_facts(root: Path, cfg: dict, d: Path, trace: dict, acs: dict) -> dict:
    """What every pass must cover, and where the loop stands."""
    from .review import checklist_items, loop_state

    n_items = checklist_items(root / cfg["review_checklist_path"])
    dod = trace.get("dod") or []
    state = loop_state(d / "review.md", acs, n_items, len(dod))
    return {"max_passes": cfg.get("review_passes", 5), **state,
            "scope": {"stories": acs, "checklist_items": n_items, "dod": dod}}


def answers_facts(root: Path, cfg: dict, bp: Blueprint | None, step: str) -> dict:
    """For the answers hook before a Spec Kit command that asks questions: where this feature's defaults
    come from. A compiled feature always; otherwise, before specify or plan in a project with a blueprint,
    the products the description may be about."""
    from .answers import RULE, TAGS, open_questions

    d, trace = feature_context(root, cfg)
    if trace is not None and step != "specify":
        bcfg = bp_config(cfg, trace.get("blueprint_id") or cfg["id"])
        path = root / bcfg["blueprint_path"]
        if bp is None or bcfg["id"] != cfg["id"]:
            bp = Blueprint(path) if path.is_file() else None
        epic = bp.epic(trace["epic"][1:]) if bp else None
        oqs = []
        if epic is not None:
            from .compiler import epic_refs

            _, pairs, _ = epic_refs(bp, epic, bcfg)
            rows = [(bp.lines[u.header[0]], bp.lines[u.start]) for i, u in pairs
                    if i.startswith("OQ-") and u.kind == "row" and u.header]
            oqs = open_questions(rows)
        flag = f" --blueprint {bcfg['id']}" if cfg.get("multi") else ""
        script = "python3 .specify/extensions/blueprint/scripts/blueprint.py"
        rel = d.relative_to(root).as_posix()
        return {
            "applies": True, "scope": "feature", "step": step, "blueprint_id": bcfg["id"],
            "epic": trace["epic"], "feature_directory": rel, "rule": RULE, "tags": TAGS,
            "look_in": [
                {"where": f"{rel}/spec.md", "holds": "stories, ACs, edge cases, copy, success criteria; open questions with their defaults under Assumptions"},
                {"where": f"{rel}/plan.md", "holds": "architecture, stack, conventions, repositories, task seeds"},
                {"where": f"{rel}/research.md, data-model.md, contracts/", "holds": "decisions, drivers, entities, API rows, integrations"},
                {"where": bcfg["decisions_path"], "holds": "decisions recorded since the blueprint"},
                {"where": f"{script} search <words>{flag}", "holds": "any blueprint line, with the section and ID to cite"},
                {"where": f"{script} find <ID>{flag} / section <n>{flag}", "holds": "a definition or a whole section"},
            ],
            "open_questions": oqs,
            "record": {"product": "/speckit-blueprint-amend",
                       "technical": f"{next_decision_id(root, bcfg, trace['epic'])} in {bcfg['decisions_path']}"},
            "next_decision_id": next_decision_id(root, bcfg, trace["epic"]),
            "decisions_log": bcfg["decisions_path"],
        }
    if step in ("specify", "plan") and (trace is None or step == "specify"):
        products = []
        for bid in cfg["blueprints"]:
            b = bp_config(cfg, bid)
            path = root / b["blueprint_path"]
            if not path.is_file():
                continue
            other = bp if bp is not None and bp.path.resolve() == path.resolve() else Blueprint(path)
            products.append({"id": bid, "blueprint": b["blueprint_path"], "product": other.title(),
                             "decisions": b["decisions_path"],
                             "epics": [{"id": e["id"], "title": e["title"]} for e in other.epics if not e["retired"]]})
        if products:
            return {"applies": True, "scope": "project", "step": step, "rule": RULE, "tags": TAGS,
                    "blueprints": products}
        return {"applies": False, "why": "no blueprint in this project"}
    return {"applies": False, "why": "the active feature was not compiled from a blueprint"}
