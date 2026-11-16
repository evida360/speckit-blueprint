"""Compile one epic into a Spec Kit feature folder; staleness and accepted changes."""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path

from .core import HEADING_RE, TRACE_FILE, VERIFY_RE, id_type, read_trace, sha
from .config import bp_config, branch_prefix_for, trace_blueprint_id
from .model import Blueprint, Unit, demote, extract_ids, gen_header, label_block, render_units, slugify, strip_label_block
from .repos import base_branch, branch_name, epic_repos, git, hub_entry, registry, remote_branch, unknown_repos
from .board import claim, epic_key, load as load_board


# Epic header fields that are not epic context: the repository table becomes sections of its own
# (`**Target repositories**` is its schema-1 predecessor, converted by `blueprint.py migrate`).
OWN_SECTION_FIELDS = ("Repositories", "Target repositories")


def repo_rows(cfg: dict, root: Path, epic: dict, folder: str) -> list[dict]:
    """The epic's repositories in order, with the base and the branch each one works on."""
    rows = []
    for r in epic_repos(cfg, root, epic):
        rows.append({
            "name": r["name"], "order": r["order"], "role": r.get("role", ""), "contracts": r.get("contracts", ""),
            "base": base_branch(cfg, root, r["name"], r.get("base", "")),
            "branch": branch_name(cfg, root, r["name"], folder, r.get("branch", "")),
        })
    return rows


def repo_sections(epic: dict, rows: list[dict]) -> tuple[list[str], list[str]]:
    """spec.md "Target Repositories" and plan.md "Repositories", for an epic with a repository table."""
    if not epic.get("repos"):
        return [], []
    spec = ["## Target Repositories", "", "| Repository | Role in this epic |", "|---|---|"]
    spec += [f"| `{r['name']}` | {r['role'] or '—'} |" for r in rows] + [""]
    plan = [
        "## Repositories",
        "",
        "Change, gate and merge the repositories in this order (providers before consumers); merge the hub's "
        "feature branch last. Tasks name their repository in brackets, for example "
        f"`[{rows[0]['name']}]`, and give paths relative to that repository's root.",
        "",
        "| Order | Repository | Base | Branch | Contracts |",
        "|---|---|---|---|---|",
    ]
    plan += [f"| {r['order']} | `{r['name']}` | `{r['base']}` | `{r['branch']}` | {r['contracts'] or '—'} |" for r in rows] + [""]
    if epic.get("repo_notes"):
        plan += [epic["repo_notes"], ""]
    return spec, plan


_TRACES: dict = {}


def feature_traces(specs: Path) -> list[tuple[Path, dict]]:
    """Every compiled feature folder with its trace, read once per process while the folder list is the
    same (status and the dashboard look up every epic of every blueprint)."""
    try:
        key = (str(specs.resolve()), specs.stat().st_mtime_ns)
    except OSError:
        return []
    if key not in _TRACES:
        _TRACES.clear()
        _TRACES[key] = [(d, t) for d in sorted(specs.iterdir()) for t in [read_trace(d)] if t]
    return _TRACES[key]


def find_feature_dir(root: Path, bcfg: dict, epic_id: str) -> Path | None:
    """The feature folder compiled from this epic of this blueprint (epic IDs repeat across blueprints)."""
    specs = root / bcfg["specs_dir"]
    if not specs.is_dir():
        return None
    for d, trace in feature_traces(specs):
        if trace.get("epic") == epic_id and trace_blueprint_id(bcfg, trace) == bcfg["id"]:
            return d
    return None


def started_folders(root: Path, bcfg: dict) -> dict:
    """Epics of this blueprint whose hub branch exists, here or on a remote, other than the branch this
    checkout is on: epic id -> {branch, local, remote, folder, merged_into?}. Status uses it for epics whose
    folder is not in this checkout: they are in progress on that branch (beside this one, on another machine,
    by someone else), or merged into the base (`merged_into`) without this checkout having it yet. The
    folder's name wins over the one compile would pick here, so the epic is continued, not compiled again."""
    rc, out = git(root, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes")
    if rc != 0 or not out:
        return {}
    hub = hub_entry(registry(bcfg, root))
    prefix = branch_prefix_for(bcfg, root, hub)
    tag = "" if bcfg["implicit"] else f"{bcfg['id']}-"
    pat = re.compile(r"^refs/(?:heads/|remotes/([^/]+)/)" + re.escape(prefix)
                     + r"(\d{3,}-" + re.escape(tag) + r"(e\d+)-[a-z0-9-]*)$")
    current = git(root, "branch", "--show-current")[1]
    found: dict = {}
    for ref in out.split():  # refs/heads/ sort before refs/remotes/
        m = pat.match(ref)
        if not m or prefix + m.group(2) == current:
            continue  # the branch this checkout is on: the epic is compiled here
        remote, folder = m.group(1), m.group(2)
        e = found.setdefault(m.group(3).upper(), {"branch": prefix + folder, "local": False, "remote": None, "folder": folder})
        if folder != e["folder"]:
            continue
        if remote is None:
            e["local"] = True
        elif e["remote"] is None or remote == "origin":
            e["remote"] = f"{remote}/{prefix}{folder}"
    if found:
        base = base_branch(bcfg, root, hub["name"]) if hub else ""
        bases = [b for b in (base, remote_branch(root, base) if base else "") if b]
        if not bases:
            rc, head = git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
            bases = [head] if rc == 0 and head else []
        for v in found.values():
            for b in bases:
                if git(root, "cat-file", "-e", f"{b}:{bcfg['specs_dir']}/{v['folder']}/{TRACE_FILE}")[0] == 0:
                    v["merged_into"] = b
                    break
    return found


def feature_number(root: Path, bcfg: dict, epic_num: int) -> int:
    """number_base + epic + 1 (E00 → 001 …) when free, so folders sort like the blueprint; otherwise one
    above the highest number in this blueprint's range, which ends at the next blueprint's number_base.
    Separate ranges keep two blueprints compiled on parallel branches from taking the same number."""
    specs = root / bcfg["specs_dir"]
    names = [d.name for d in specs.iterdir()] if specs.is_dir() else []
    # folders compiled in other worktrees of the hub are not on this branch yet: the board knows them
    own = epic_key(bcfg["id"], f"E{epic_num:02d}")
    names += [Path(e.get("feature", "")).name for k, e in load_board(root).get("epics", {}).items() if k != own]
    nums = set()
    for name in names:
        m = re.match(r"^(\d{3,})-", name)
        if m and not re.match(r"^\d{8}-\d{6}-", name):
            nums.add(int(m.group(1)))
    base = bcfg["number_base"]
    if base + epic_num + 1 not in nums:
        return base + epic_num + 1
    upper = min((b for b in (bp_config(bcfg, i)["number_base"] for i in bcfg["_cfg"]["blueprints"]) if b > base), default=None)
    return max((n for n in nums if n >= base and (upper is None or n < upper)), default=base) + 1


def group(pairs: list[tuple[str, Unit]], types: set[str]) -> list[tuple[str, Unit]]:
    return [(i, u) for i, u in pairs if id_type(i) in types]


def epic_refs(bp: Blueprint, epic: dict, cfg: dict) -> tuple[list[str], list[tuple[str, Unit]], list[str]]:
    """IDs an epic uses, their definition units (two levels deep for some types), and the unresolved ones."""
    ids = [i for i in extract_ids(bp.epic_text(epic), bp.defs) if id_type(i)]
    level1 = bp.resolve(ids)
    second_types = set(cfg.get("second_level_types") or [])
    extra: list[str] = []
    if second_types:
        for ident, u in level1:
            if id_type(ident) in {"F", "SCR", "EC"}:
                for x in extract_ids(u.text(bp.lines), bp.defs):
                    if id_type(x) in second_types and x not in ids and x not in extra:
                        extra.append(x)
    pairs = level1 + bp.resolve(extra)
    # Goals (G1…) are often defined in prose; their absence is not worth a defect.
    unresolved = sorted({i for i in ids if i not in bp.defs and id_type(i) != "G"})
    return ids, pairs, unresolved


def qualify(cfg: dict, ident: str) -> str:
    return f"{cfg['prefix']}:{ident}" if cfg["prefix"] else ident


def id_note(cfg: dict) -> str:
    """For a blueprint with a prefix: how its IDs are written outside its own files."""
    p = cfg["prefix"]
    return (
        f"IDs here (`E01`, `DR-03`, `FF-02`, `CE-1`, …) are blueprint `{cfg['id']}`'s; in files shared with "
        f"other blueprints (constitution, tasks, commits) write them as `{p}:DR-03`."
    ) if p else ""


def compile_epic(root: Path, cfg: dict, bp: Blueprint, epic_ref: str, force: bool = False, take: bool = False) -> dict:
    epic = bp.epic(epic_ref)
    if not epic:
        raise SystemExit(f"error: no '## Epic {epic_ref}' in {bp.path}")
    if epic["retired"]:
        raise SystemExit(f"error: {epic['id']} is retired ('## Epic {epic['num']:02d} — {epic['title']}'); there is nothing to compile")
    rel_bp = os.path.relpath(bp.path, root)
    eid, etitle = epic["id"], epic["title"]
    lines = bp.lines
    bid, prefix = cfg["id"], cfg["prefix"]
    if epic["repo_errors"]:
        raise SystemExit(f"error: {eid}: " + "; ".join(epic["repo_errors"]))
    if epic["legacy_target_repos"] and not epic["repos"]:
        raise SystemExit(f"error: {eid} has a **Target repositories** field from schema 1; run `blueprint.py migrate`")
    unknown = unknown_repos(cfg, root, epic)
    if unknown:
        raise SystemExit(
            f"error: {eid}'s **Repositories** table names {', '.join(unknown)}, which `repositories:` in the "
            "extension config does not list"
        )

    feature = find_feature_dir(root, cfg, eid)
    if feature is None:
        num = feature_number(root, cfg, epic["num"])
        tag = "" if cfg["implicit"] else f"{bid}-"
        feature = root / cfg["specs_dir"] / f"{num:03d}-{tag}{eid.lower()}-{slugify(etitle)}"
    rows = repo_rows(cfg, root, epic, feature.name)
    hub = hub_entry(registry(cfg, root))
    branch = branch_name(cfg, root, hub["name"] if hub else None, feature.name)
    key = epic_key(bid, eid)
    conflict = claim(root, key, {
        "blueprint_id": bid, "epic": eid, "title": etitle, "feature": feature.relative_to(root).as_posix(),
        "branch": branch, "repositories": {r["name"]: {"branch": r["branch"], "base": r["base"]} for r in rows},
    }, take=take)
    if conflict:
        raise SystemExit(
            f"error: {key} is being built in {conflict.get('worktree')} (branch {conflict.get('branch')}). "
            "Continue it there, or pass --take to move it to this worktree."
        )
    trace_path = feature / TRACE_FILE
    if trace_path.is_file() and (feature / "tasks.md").is_file() and not force:
        tasks = (feature / "tasks.md").read_text(encoding="utf-8")
        if re.search(r"^\s*-\s*\[[xX]\]", tasks, re.M):
            raise SystemExit(
                f"error: {feature.relative_to(root)} already has completed tasks. Recompiling would "
                "change the spec under finished work. Pass --force only if that is intended "
                "(then regenerate tasks.md with /speckit-tasks)."
            )

    epic_text = bp.epic_text(epic)
    ids, pairs, unresolved = epic_refs(bp, epic, cfg)

    header_text = "\n".join(lines[epic["header"][0] : epic["header"][1]]).strip()
    footer_text = "\n".join(lines[epic["footer"][0] : epic["footer"][1]]).strip()
    today = _dt.date.today().isoformat()
    spec_repos, plan_repos = repo_sections(epic, rows)
    epic_context = header_text
    for label in OWN_SECTION_FIELDS:
        epic_context = strip_label_block(epic_context, label).strip()

    # ---- spec.md
    spec = [
        gen_header(rel_bp, bp.sha, "spec.md"),
        f"# Feature Specification: {eid} — {etitle}",
        "",
        f"**Feature Branch**: `{branch}`",
        "",
        f"**Created**: {today}",
        "",
        f"**Status**: Compiled from blueprint ({eid})",
        "",
        f"**Input**: {rel_bp} — Epic {eid[1:]}"
        + ("" if cfg["implicit"] else f" (blueprint `{bid}`)")
        + ". Acceptance criteria, copy IDs and edge cases below are binding verbatim.",
        "",
        *([id_note(cfg), ""] if prefix else []),
        "## Epic Context",
        "",
        epic_context,
        "",
        *spec_repos,
        "## User Scenarios & Testing *(mandatory)*",
        "",
        "Priorities follow the blueprint's story order, which is its dependency order: P1 is built first.",
        "",
    ]
    for n, st in enumerate(epic["stories"], 1):
        body = "\n".join(lines[st["start"] + 1 : st["end"]]).strip()
        body = strip_label_block(body, "Tasks")
        spec += [
            f"### User Story {n} - {st['title']} (Priority: P{n})",
            "",
            f"**Blueprint ID**: {st['id']}",
            "",
            body.replace("**Acceptance Criteria**", "**Acceptance Scenarios** (Acceptance Criteria)", 1),
            "",
            f"**Independent Test**: the **Tests** block of {st['id']} above.",
            "",
        ]
    ec = group(pairs, {"EC"})
    spec += ["### Edge Cases", "", render_units(bp, ec) if ec else "Story-level edge cases above; no register entries referenced.", ""]
    spec += [
        "## Requirements *(mandatory)*",
        "",
        "### Functional Requirements",
        "",
        "Every acceptance criterion above (`<story> AC-n`) is a functional requirement. Copy is used by ID "
        "(`CP-…`) exactly as defined under Product Context; no improvised strings.",
        "",
    ]
    ents = [i for i in ids if id_type(i) == "ENT"]
    if ents:
        spec += ["### Key Entities", ""] + [f"- **{e}** — see data-model.md" for e in ents] + [""]
    spec += ["## Success Criteria *(mandatory)*", "", footer_text or "See each story's **Story DoD**.", ""]
    ctx_groups = [
        ("Personas", {"P", "AP"}),
        ("Flows", {"F"}),
        ("Screens and copy", {"SCR", "CP"}),
        ("Goals and metrics", {"G", "M", "CE"}),
    ]
    ctx = [(title, group(pairs, t)) for title, t in ctx_groups]
    if any(g for _, g in ctx):
        spec += ["## Product Context (resolved from the blueprint)", ""]
        for title, g in ctx:
            if g:
                spec += [f"### {title}", "", render_units(bp, g), ""]
    oq = group(pairs, {"OQ"})
    spec += ["## Assumptions", "", "Open questions that touch this epic, with the default to use if still unanswered:", ""]
    spec += [render_units(bp, oq) if oq else "None referenced.", ""]

    # ---- plan.md
    plan = [
        gen_header(rel_bp, bp.sha, "plan.md"),
        f"# Implementation Plan: {eid} — {etitle}",
        "",
        f"**Branch**: `{branch}` | **Date**: {today} | **Spec**: [spec.md](spec.md)",
        "",
        f"**Input**: compiled from {rel_bp}. Architecture, stack and conventions are already decided there; "
        "this plan does not reopen them.",
        "",
        "## Summary",
        "",
        label_block(header_text, "Goal") or etitle,
        "",
        "## Technical Context",
        "",
    ]
    for key in cfg.get("plan_sections") or []:
        t = bp.section_text(str(key))
        if t:
            plan += [demote(t, 3), ""]
    plan += [
        "## Constitution Check",
        "",
        "The constitution (`.specify/memory/constitution.md`) holds the project's principles and the rules "
        "bootstrap compiled from the same blueprint"
        + ("" if cfg["implicit"] else f" (the Blueprint Rules block marked `id={bid}`; blocks of other blueprints do not apply here)")
        + "; this plan complies with both. No violations are planned; "
        f"any deviation needs a decision record (`{qualify(cfg, f'DR-{eid}-1')}`, numbered within this epic) in `{cfg['decisions_path']}` and, if it changes "
        "behaviour, an amendment through `/speckit-blueprint-amend`.",
        "",
        *plan_repos,
        "## Project Structure",
        "",
        "### Documentation (this feature)",
        "",
        "```text",
        f"{cfg['specs_dir']}/{branch}/",
        "├── spec.md          # stories, ACs, edge cases, copy (compiled)",
        "├── plan.md          # this file (compiled)",
        "├── research.md      # decisions, drivers, sources, open questions (compiled)",
        "├── data-model.md    # entities this epic touches (compiled)",
        "├── quickstart.md    # how to verify the epic (compiled)",
        "├── contracts/       # API rows and integration cards (compiled)",
        "├── tasks.md         # /speckit-tasks output",
        "└── review.md        # /speckit-blueprint-review output (handoff)",
        "```",
        "",
        "### Source Code (repository root)",
        "",
        "Use the repository layout and module map in Technical Context above; do not invent new top-level folders.",
        "",
        "## Blueprint Task Seeds",
        "",
        "Instructions for `/speckit-tasks`: build one phase per user story in the order below. Keep each "
        "blueprint task ID in its task description (for example "
        f"`- [ ] T007 [US2] {prefix + ':' if prefix else ''}E01-S02-T3 …`), keep their "
        "order, and put the story's tests (its **Tests** block in spec.md) before its implementation tasks. "
        "Add setup or foundational tasks only when the epic's preconditions need them.",
        "",
    ]
    for n, st in enumerate(epic["stories"], 1):
        body = "\n".join(lines[st["start"] + 1 : st["end"]])
        tasks = label_block(body, "Tasks")
        tasks = "\n".join(tasks.split("\n")[1:]).strip() if tasks else "- (no blueprint tasks; derive from the ACs)"
        plan += [f"### US{n} — {st['id']} {st['title']}", "", tasks, ""]
    verify = sorted({ln.strip() for ln in epic_text.split("\n") if VERIFY_RE.search(ln)})
    plan += [
        "## Preconditions and VERIFY-AT-BUILD",
        "",
        label_block(header_text, "Preconditions for the session") or "None stated.",
        "",
    ]
    if verify:
        plan += ["Lines in this epic that carry `[VERIFY-AT-BUILD]` (re-check before building on them):", ""]
        plan += [f"- {v.lstrip('- ')[:600]}" for v in verify] + [""]
    risks = group(pairs, {"R"})
    if risks:
        plan += ["## Risks", "", render_units(bp, risks), ""]
    plan += ["## Complexity Tracking", "", "None — complexity decisions are recorded as DRs in research.md.", ""]

    # ---- research.md
    research = [gen_header(rel_bp, bp.sha, "research.md"), f"# Research: {eid} — {etitle}", ""]
    for title, types in [
        ("Decisions", {"DR"}),
        ("Architecture drivers", {"AD"}),
        ("Fitness functions", {"FF"}),
        ("Sources", {"S"}),
    ]:
        g = group(pairs, types)
        if g:
            research += [f"## {title}", "", render_units(bp, g), ""]
    if len(research) == 3:
        research += ["No decisions, drivers or sources are referenced by this epic.", ""]

    # ---- data-model.md
    ent = group(pairs, {"ENT"})
    data_model = None
    if ent:
        data_model = [gen_header(rel_bp, bp.sha, "data-model.md"), f"# Data Model: {eid} — {etitle}", ""]
        dm = bp.section("12.4")
        if dm:
            pre = []
            for ln in lines[dm.idx + 1 : dm.end]:
                if ln.startswith("**ENT-") or ln.startswith("|") or HEADING_RE.match(ln):
                    break
                pre.append(ln)
            if "".join(pre).strip():
                data_model += ["## Conventions", "", "\n".join(pre).strip(), ""]
        data_model += ["## Entities", "", render_units(bp, ent), ""]

    # ---- contracts/
    contracts = {}
    api = group(pairs, {"API"})
    if api:
        body = [gen_header(rel_bp, bp.sha, "contracts/api.md"), f"# API Contracts: {eid}", ""]
        sec = bp.section("12.5")
        if sec:
            pre = []
            for ln in lines[sec.idx + 1 : sec.end]:
                if ln.startswith("|") or HEADING_RE.match(ln):
                    break
                pre.append(ln)
            if "".join(pre).strip():
                body += ["\n".join(pre).strip(), ""]
        body += [render_units(bp, api), ""]
        contracts["api.md"] = body
    ints = group(pairs, {"INT"})
    if ints:
        contracts["integrations.md"] = [
            gen_header(rel_bp, bp.sha, "contracts/integrations.md"),
            f"# Integrations: {eid}",
            "",
            render_units(bp, ints),
            "",
        ]

    # ---- quickstart.md
    manual = []
    for st in epic["stories"]:
        for ln in lines[st["start"] : st["end"]]:
            if re.match(r"^\s*-\s*Manual\b", ln):
                manual.append(f"- {st['id']}: {ln.strip().lstrip('- ').strip()}")
    quick = [
        gen_header(rel_bp, bp.sha, "quickstart.md"),
        f"# Quickstart: verify {eid} — {etitle}",
        "",
        "## Epic-level verification",
        "",
        label_block(footer_text, "Epic-level tests") or "Run the full `check` and every story's tests.",
        "",
        "## Manual checks",
        "",
        "\n".join(manual) if manual else "None beyond the Definition of Done in spec.md.",
        "",
    ]

    # ---- write
    feature.mkdir(parents=True, exist_ok=True)
    written = {}

    def write(rel: str, parts: list[str]) -> None:
        p = feature / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(parts).rstrip() + "\n"
        p.write_text(text, encoding="utf-8")
        written[rel] = len(text.encode("utf-8"))

    write("spec.md", spec)
    write("plan.md", plan)
    write("research.md", research)
    write("quickstart.md", quick)
    if data_model:
        write("data-model.md", data_model)
    elif (feature / "data-model.md").is_file():
        (feature / "data-model.md").unlink()
    for name, parts in contracts.items():
        write(f"contracts/{name}", parts)

    unit_hashes = {}
    for ident, u in pairs:
        unit_hashes.setdefault(ident, sha(u.text(lines)))
    trace = {
        "blueprint_id": bid,
        "epic": eid,
        "title": etitle,
        "blueprint": rel_bp,
        "blueprint_sha": bp.sha,
        "epic_sha": sha(epic_text),
        "branch": branch,
        "depends": [f"{b}:{e}" if b else e for b, e in epic["depends"]],
        "repositories": rows,
        "acs": {st["id"]: st["acs"] for st in epic["stories"]},
        "units": unit_hashes,
        "unresolved": unresolved,
        "compiled_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "files": written,
    }
    if cfg.get("metrics", "local") != "off":
        try:  # the estimate made before building, so the review can compare it with what happened
            from .estimate import estimate as estimate_epic

            est = estimate_epic(root, cfg["_cfg"], cfg, bp, epic)
            if est.get("agent_s"):
                trace["estimate"] = {k: est[k] for k in ("agent_s", "cost_usd", "tokens", "calendar_s", "confidence", "history")}
                trace["estimate"]["at"] = today
        except Exception:  # noqa: BLE001 - an estimate must never stop a compile
            pass
    trace_path.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _TRACES.clear()

    rel_feature = feature.relative_to(root).as_posix()
    (root / ".specify").mkdir(exist_ok=True)
    (root / ".specify" / "feature.json").write_text(
        json.dumps({"feature_directory": rel_feature}) + "\n", encoding="utf-8"
    )
    return {
        "blueprint_id": bid,
        "epic": eid,
        "title": etitle,
        "feature_directory": rel_feature,
        "files": written,
        "total_bytes": sum(written.values()),
        "blueprint_bytes": len(bp.raw.encode("utf-8")),
        "resolved_ids": len(unit_hashes),
        "unresolved_ids": unresolved,
        "branch": branch,
        "repositories": rows,
    }


def accept_change(root: Path, cfg: dict, bp: "Blueprint", epic_key: str, note: str) -> dict:
    """Mark the blueprint changes under a compiled epic as reviewed: the epic stays as built.

    The trace's hashes move to the current blueprint text, so status stops reporting the change; the
    compiled files are left as they were, and the decision is kept in the trace's `accepted` list.
    """
    eid = epic_key.upper() if epic_key.upper().startswith("E") else f"E{int(epic_key):02d}"
    d = find_feature_dir(root, cfg, eid)
    if d is None:
        raise SystemExit(f"error: {eid} has no compiled feature folder")
    tpath = d / TRACE_FILE
    trace = json.loads(tpath.read_text(encoding="utf-8"))
    changed = stale_units(bp, trace)
    if not changed:
        return {"epic": eid, "accepted": [], "note": "nothing changed since compile"}
    epic = bp.epic(eid[1:])
    if epic is None:
        raise SystemExit(f"error: {eid} is no longer in the blueprint")
    trace["epic_sha"] = sha(bp.epic_text(epic))
    for ident in list((trace.get("units") or {}).keys()):
        units = bp.defs.get(ident)
        if units:
            trace["units"][ident] = sha(units[0].text(bp.lines))
    trace.setdefault("accepted", []).append({
        "date": _dt.date.today().isoformat(), "blueprint_sha": bp.sha, "changed": changed, "note": note,
    })
    tpath.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _TRACES.clear()
    return {"epic": eid, "feature_directory": d.relative_to(root).as_posix(), "accepted": changed, "note": note}


def stale_units(bp: Blueprint, trace: dict) -> list[str]:
    changed = []
    epic = bp.epic(trace.get("epic", "")[1:] if trace.get("epic") else "")
    if epic is None:
        return ["<epic removed>"]
    if sha(bp.epic_text(epic)) != trace.get("epic_sha"):
        changed.append(trace["epic"])
    for ident, h in (trace.get("units") or {}).items():
        units = bp.defs.get(ident)
        if not units or sha(units[0].text(bp.lines)) != h:
            changed.append(ident)
    return changed
