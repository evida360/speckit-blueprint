"""An epic's workspace (one git worktree per repository it changes, and the hub, all on its branch) and the
workspace itself on a machine.

`blueprint.py workspace <epic>` prints the plan; `--create` makes the worktrees and claims the epic for
the hub worktree. Paths: `<repository parent>/<worktrees_dir>/<repository>-<short>`, where `<short>` is
`<blueprint id>-eNN` (or `eNN` in a project with one blueprint). A branch pushed from another machine is
continued, never started again from the base.

`blueprint.py workspace setup` clones the repositories missing on this machine from their `remote` and
fetches the others. `blueprint.py workspace link` writes a block into each code repository's CLAUDE.md that
points to the hub: a session opened in a repository cloned on its own then knows where its specs are.
Building a feature without the hub is not supported; getting the hub is one clone.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from .blocks import managed_blocks, marker
from .model import Blueprint, slugify
from .repos import (base_branch, branch_name, epic_repos, git, git_network, hub_entry, hub_url, is_inside, main_root,
                    registry, remote_branch, resolve_remote)
from .board import claim, epic_key
from .compiler import feature_number, find_feature_dir, started_folders

SCRIPT = "python3 .specify/extensions/blueprint/scripts/blueprint.py"


def short_name(cfg: dict, eid: str) -> str:
    return eid.lower() if cfg["implicit"] else f"{cfg['id']}-{eid.lower()}"


def feature_folder(root: Path, cfg: dict, bp: Blueprint, epic: dict) -> str:
    """The feature folder's name: the one in this checkout, else the one on the epic's branch (here or pushed
    from another machine), else the one compile will create."""
    d = find_feature_dir(root, cfg, epic["id"])
    if d is not None:
        return d.name
    started = started_folders(root, cfg).get(epic["id"])
    if started:
        return started["folder"]
    tag = "" if cfg["implicit"] else f"{cfg['id']}-"
    return f"{feature_number(root, cfg, epic['num']):03d}-{tag}{epic['id'].lower()}-{slugify(epic['title'])}"


def start_point(main: Path, branch: str, base: str) -> dict:
    """Where a worktree starts: the local branch; else the same branch on a remote (pushed from another
    machine, continued with its commits and ticked tasks); else a new branch from the base."""
    if not main.is_dir():
        return {"from": "missing"}
    if git(main, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")[0] == 0:
        return {"from": "local", "ref": branch}
    pushed = remote_branch(main, branch)
    if pushed:
        return {"from": "remote", "ref": pushed}
    if git(main, "rev-parse", "--verify", "-q", f"refs/heads/{base}")[0] != 0:
        base = remote_branch(main, base) or base  # a fresh clone may know the base only as origin/<base>
    return {"from": "base", "ref": base}


def plan(root: Path, cfg: dict, bp: Blueprint, epic: dict) -> dict:
    """Where the epic's worktrees go, on which branches, from which start points."""
    reg = registry(cfg, root)
    hub = hub_entry(reg)
    folder = feature_folder(root, cfg, bp, epic)
    short = short_name(cfg, epic["id"])
    wt_dir = str(cfg.get("worktrees_dir") or ".worktrees")
    hub_main = main_root(root)
    hub_name = hub["name"] if hub else hub_main.name
    items = [{
        "repository": hub_name, "hub": True, "main_path": str(hub_main),
        "worktree": str(hub_main.parent / wt_dir / f"{hub_main.name}-{short}"),
        "branch": branch_name(cfg, root, hub_name if hub else None, folder),
        "base": base_branch(cfg, root, hub_name) if hub else _current_or_default(hub_main),
    }]
    for r in epic_repos(cfg, root, epic):
        entry = reg.get(r["name"])
        if entry is None or entry["hub"]:
            continue
        main = Path(entry["main_path"])
        items.append({
            "repository": r["name"], "hub": False, "main_path": str(main),
            "worktree": str(main.parent / wt_dir / f"{main.name}-{short}"),
            "branch": branch_name(cfg, root, r["name"], folder, r.get("branch", "")),
            "base": base_branch(cfg, root, r["name"], r.get("base", "")),
            "order": r["order"],
        })
    for i in items:
        i["start"] = start_point(Path(i["main_path"]), i["branch"], i["base"])
    hub_wt = items[0]["worktree"]
    add_dirs = " ".join(f"--add-dir {shlex.quote(i['worktree'])}" for i in items[1:])
    return {
        "epic": epic["id"], "blueprint_id": cfg["id"], "feature_folder": folder, "worktrees": items,
        "open": f"cd {shlex.quote(hub_wt)} && claude {add_dirs}".strip(),
    }


def _current_or_default(path: Path) -> str:
    rc, cur = git(path, "branch", "--show-current")
    return cur if rc == 0 and cur else "main"


def worktree_args(i: dict, start: dict) -> list[str]:
    if start["from"] == "local":
        return ["worktree", "add", i["worktree"], i["branch"]]
    if start["from"] == "remote":
        return ["worktree", "add", "--track", "-b", i["branch"], i["worktree"], start["ref"]]
    return ["worktree", "add", "-b", i["branch"], i["worktree"], start["ref"]]


def git_commands(p: dict) -> list[str]:
    out = []
    for i in p["worktrees"]:
        s = i.get("start") or {"from": "base", "ref": i["base"]}
        if s["from"] == "missing":
            out.append(f"# {i['repository']}: {i['main_path']} is not on this machine; run `blueprint.py workspace setup`")
            continue
        out.append(f"git -C {shlex.quote(i['main_path'])} " + " ".join(shlex.quote(a) for a in worktree_args(i, s)))
    return out


def create(root: Path, cfg: dict, p: dict, take: bool = False) -> dict:
    """Make every worktree (an existing worktree is kept; an existing or pushed branch is checked out) and
    claim the epic for the hub worktree."""
    results = []
    for i in p["worktrees"]:
        wt = Path(i["worktree"])
        main = Path(i["main_path"])
        if wt.is_dir():
            results.append({**i, "result": "exists"})
            continue
        s = start_point(main, i["branch"], i["base"])
        if s["from"] == "missing":
            results.append({**i, "start": s, "result": f"missing: {main} is not on this machine; run `blueprint.py workspace setup`"})
            continue
        wt.parent.mkdir(parents=True, exist_ok=True)
        rc, out = git(main, *worktree_args(i, s), timeout=120)
        results.append({**i, "start": s, "result": "created" if rc == 0 else f"failed: {last_line(out) or rc}"})
    hub_wt = Path(p["worktrees"][0]["worktree"])
    conflict = None
    if hub_wt.is_dir():
        conflict = claim(hub_wt, epic_key(cfg["id"], p["epic"]), {
            "blueprint_id": cfg["id"], "epic": p["epic"], "branch": p["worktrees"][0]["branch"],
            "feature": f"{cfg['specs_dir']}/{p['feature_folder']}", "state": "claimed",
            "repositories": {i["repository"]: {"branch": i["branch"], "base": i["base"], "worktree": i["worktree"]}
                             for i in p["worktrees"][1:]},
        }, take=take)
    return {**p, "worktrees": results, "claim_conflict": conflict}


def last_line(text: str) -> str:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def setup(root: Path, cfg: dict, names: list[str] | None = None, fetch: bool = True, dry_run: bool = False) -> dict:
    """Make the workspace whole on this machine: clone each repository that is missing from its `remote` (one
    written relative to the hub's own URL resolves against it) and fetch the hub and the repositories that are
    here, so branches pushed from another machine can be continued. `names` limits it to those repositories."""
    reg = registry(cfg, root)
    hub_main = main_root(root)
    base_url = hub_url(cfg, root)
    entries = list(reg.values())
    if not any(e["hub"] for e in entries):
        entries.insert(0, {"name": hub_main.name, "main_path": str(hub_main), "remote": "", "hub": True})
    rows = []
    for e in entries:
        if names is not None and not e["hub"] and e["name"] not in names:
            continue
        main = Path(e["main_path"])
        row = {"repository": e["name"], "path": str(main), "hub": e["hub"]}
        if not e["hub"] and is_inside(main, hub_main):
            rows.append({**row, "result": "part of the hub", "ok": True})
            continue
        if main.is_dir() and any(main.iterdir()):
            rc, top = git(main, "rev-parse", "--show-toplevel")
            if rc != 0 or Path(top).resolve() != main.resolve():
                rows.append({**row, "result": "not a git repository; left as it is", "ok": False})
            elif not fetch:
                rows.append({**row, "result": "present", "ok": True})
            elif dry_run:
                rows.append({**row, "result": "would fetch", "ok": True})
            else:
                rc, out = git_network(["-C", str(main), "fetch", "--all", "--prune", "--quiet"])
                rows.append({**row, "result": "fetched" if rc == 0 else f"fetch failed: {last_line(out)}", "ok": rc == 0})
            continue
        url = resolve_remote(e.get("remote") or "", base_url)
        if not url:
            why = ("its `remote` is relative and the hub has no `origin` to resolve it against" if e.get("remote")
                   else "the config gives it no `remote`")
            rows.append({**row, "result": f"missing: {why}", "ok": False})
            continue
        if dry_run:
            rows.append({**row, "remote": url, "result": "would clone", "ok": True})
            continue
        main.parent.mkdir(parents=True, exist_ok=True)
        rc, out = git_network(["clone", "--quiet", url, str(main)], timeout=1800)
        rows.append({**row, "remote": url, "result": "cloned" if rc == 0 else f"clone failed: {last_line(out)}", "ok": rc == 0})
    out: dict = {"repositories": rows}
    if any(r["result"].startswith("missing") for r in rows):
        out["how_to_fix"] = ("Give each missing repository a `remote` under `repositories:` in the config (a URL, or one "
                             "relative to the hub's own, such as `../<name>.git`), or clone it to its path yourself.")
    if all(r["ok"] for r in rows) and not dry_run:
        out["next"] = (f"Continue or start an epic in its own worktrees: `{SCRIPT} workspace <epic> --create` "
                       "(a branch pushed from another machine is continued).")
    return out


def block_id(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "hub"


def hub_block(hub_name: str, url: str, rel_hub: str, specs_dir: str) -> str:
    """The CLAUDE.md block of a code repository: where its features are specified and how to get there."""
    bid = block_id(hub_name)
    clone = (f"clone it there: `git clone {url} {rel_hub}`" if url else
             f"clone the `{hub_name}` repository to `{rel_hub}` (ask your team for its URL)")
    return "\n".join([
        marker("hub:", "start", bid),
        f"## Features of this repository are specified in `{hub_name}` (speckit-blueprint)",
        "",
        f"This repository is one of the repositories of the `{hub_name}` workspace. The features that change it are",
        f"planned, specified and tracked in `{hub_name}`: its blueprint, `{specs_dir}/<feature>/` (spec, plan, tasks,",
        "review) and its decisions log. This repository holds their code.",
        "",
        f"- Build or continue such a feature from a session opened in `{hub_name}`, not here. If it is not at",
        f"  `{rel_hub}`, {clone}. Then, in it, `{SCRIPT} workspace setup` brings the other repositories.",
        f"- A feature's branch here is named after its folder in `{hub_name}/{specs_dir}/` (with a branch prefix, if",
        "  the project uses one).",
        "- Do not write specs, plans or tasks for those features in this repository.",
        marker("hub:", "end", bid),
    ])


def link(root: Path, cfg: dict, dry_run: bool = False) -> dict:
    """Write or refresh the hub block in the CLAUDE.md of every code repository on this machine. It never
    commits: each repository's owner commits the file."""
    reg = registry(cfg, root)
    hub_main = main_root(root)
    hub = hub_entry(reg)
    hub_name = hub["name"] if hub else hub_main.name
    url = hub_url(cfg, root)
    specs_dir = str(cfg.get("specs_dir") or "specs")
    bid = block_id(hub_name)
    rows = []
    for e in reg.values():
        if e["hub"]:
            continue
        main = Path(e["main_path"])
        row = {"repository": e["name"], "path": str(main)}
        if is_inside(main, hub_main):
            rows.append({**row, "result": "part of the hub"})
            continue
        if not main.is_dir():
            rows.append({**row, "result": f"missing: run `{SCRIPT} workspace setup` first"})
            continue
        block = hub_block(hub_name, url, Path(os.path.relpath(hub_main, main)).as_posix(), specs_dir)
        path = main / "CLAUDE.md"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        span = next(((s, end) for i, s, end in managed_blocks(text, "hub:") if i == bid), None)
        if span:
            new, result = text[:span[0]] + block + text[span[1]:], "updated"
        elif text:
            new, result = text.rstrip("\n") + "\n\n" + block + "\n", "added"
        else:
            new, result = block + "\n", "created"
        if new == text:
            result = "unchanged"
        elif dry_run:
            result = f"would be {result}"
        else:
            path.write_text(new, encoding="utf-8")
        rc, cur = git(main, "branch", "--show-current")
        rows.append({**row, "file": str(path), "branch": cur if rc == 0 and cur else None, "result": result})
    out: dict = {"hub": hub_name, "hub_url": url or None, "repositories": rows}
    if not url:
        out["note"] = "The hub has no `origin`: the block cannot say where to clone it from."
    if any(r["result"] in ("created", "added", "updated") for r in rows):
        out["next"] = "Commit CLAUDE.md in each changed repository, on the branch shown."
    return out
