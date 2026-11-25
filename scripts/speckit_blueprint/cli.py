"""speckit-blueprint helper: turns blueprints into spec-kit artifacts, one epic at a time.

Standard library only, so it runs under any Python 3.9+ (including the interpreter spec-kit's
event dispatcher uses for Claude Code hooks).

Usage (from the project root; the extension installs this file under
.specify/extensions/blueprint/scripts/):

  State and flow
    blueprint.py status [--json | --brief]   where the project stands and the next step (every blueprint)
    blueprint.py compile E03 [--force] [--take]
                                             write specs/NNN-e03-<slug>/, claim the epic, make it active
    blueprint.py bootstrap [--skip-lint]     constitution rules, CLAUDE.md block, REVIEW-CHECKLIST, DECISIONS
    blueprint.py activate E03|<folder>       make a compiled epic the active feature
    blueprint.py context                     facts for the before_implement hook (JSON)
    blueprint.py review-facts                facts for the review loop: ACs, checklist, passes, diffs, gates (JSON)
    blueprint.py answers [--step S]          where a question's default comes from (the answers hook, JSON)
    blueprint.py migrate [--apply]           upgrade a schema-1 project (dry run without --apply)
  Blueprint
    blueprint.py section 14.4 [13.4 ...]     print blueprint sections by number (read less, not all)
    blueprint.py find ENT-Order [SCR-03 ...] print the blueprint definitions of IDs
    blueprint.py search <words…> [--limit N] blueprint lines holding every word, with the section and ID to cite
    blueprint.py rules                       the constitution rules that govern this blueprint's features
    blueprint.py lint                        the structural linter, then refs
    blueprint.py refs                        per epic: IDs compile can or cannot resolve (exit 1 if any cannot)
    blueprint.py stale                       compiled epics whose blueprint text changed since compile
    blueprint.py accept E01 --note "…"       a built epic stays as built after a blueprint change
    blueprint.py epics                       epic and story IDs in blueprint order
  Repositories and parallel epics
    blueprint.py repos [E03] [--json]        git state of the epic's repositories (branch, uncommitted, merged)
    blueprint.py gate [E03] [--repo NAME]    run the gate of each repository the epic changes
    blueprint.py workspace E03 [--create] [--take]
                                             the epic's worktrees in the hub and every repository it changes
                                             (a branch pushed from another machine is continued)
    blueprint.py workspace setup [E03] [--no-fetch] [--dry-run]
                                             clone the repositories missing on this machine from their `remote`
                                             and fetch the others (every repository, or the epic's)
    blueprint.py workspace link [--dry-run]  a block in each code repository's CLAUDE.md that points to the hub
    blueprint.py claims [--release KEY]      which worktree builds which epic
  Building an epic in segments (build.mode: segments; references/segments.md)
    blueprint.py tasks-graph [--json] [--check]
                                             the active feature's task graph: findings and the stage plan
    blueprint.py segments start [--resume]   plan the open tasks into stages and start a run (orchestrator)
    blueprint.py segments next [--retry S2b] the segments to start now, each with its prompt and effort
    blueprint.py segments status             every segment's state, the questions waiting, what to do next
    blueprint.py segments answer S2b         record the user's answers (stdin) and print what to send back
    blueprint.py segments join               check the stage's ticks, run the gates, open the next stage
    blueprint.py segments stop               end the run (ticked tasks stay done)
    blueprint.py segment start|ask|done S2b  a segment's subagent: its brief, its questions, its handoff (stdin)
    blueprint.py tick T012 [T013 ...]        tick tasks in tasks.md under a lock
    blueprint.py decision add                a DR-E03-n record with its index row, numbered under a lock (stdin)
    blueprint.py exclusive <name> -- <command ...>
                                             run a command that shares ports or a database, one at a time
  Effort, metrics, dashboard
    blueprint.py require-effort <step> [--pass N]
                                             first command of a guaranteed-effort subagent (checked by the hook)
    blueprint.py metrics [E03] [--json] [--write] [--backfill [--transcripts DIR ...]]
    blueprint.py estimate [E05] [--json]     time, cost and calendar ranges for one epic or what remains
    blueprint.py dashboard [--port N] [--no-open] [--export FILE]
    blueprint.py statusline                  one line for Claude Code's status line (reads its JSON on stdin)
  Claude Code events
    blueprint.py hook                        SessionStart, PreToolUse and Stop, from the event's JSON on stdin

A project with several blueprints lists them under `blueprints:` in the config. Every command then works
on one of them: `--blueprint <id>` (or SPECKIT_BLUEPRINT), else the active feature's blueprint, else the
default. Arguments can name it inline: `compile desk:E01`, `find DESK:DR-03 one:SCR-02`.

Why a script and not the model: every build session used to search a 100-page blueprint for the
few sections its epic needs, and that search was paid for again on every call of the session.
Resolving the references once, deterministically, at compile time is cheaper and cannot miss."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .core import SCRIPTS_DIR, ConfigError, REFS_HINT, find_root, read_trace, task_counts
from .config import bp_config, load_config, resolve_id, select_id, split_qualified
from .model import Blueprint, render_units
from .blocks import rules_text
from .compiler import accept_change, compile_epic, epic_refs, find_feature_dir, qualify, stale_units
from .bootstrap import bootstrap
from .status import brief, full_status

EPIC_COMMANDS = ("compile", "accept", "activate", "repos", "gate", "workspace", "metrics", "estimate")


def pop_option(args: list[str], name: str) -> tuple[str | None, list[str]]:
    """`--name value` or `--name=value` → (value, the other args)."""
    out, value, i = [], None, 0
    while i < len(args):
        a = args[i]
        if a == name and i + 1 < len(args):
            value, i = args[i + 1], i + 2
            continue
        if a.startswith(name + "="):
            value = a.split("=", 1)[1]
        else:
            out.append(a)
        i += 1
    return value, out


def dump(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def usage(cmd: str) -> str:
    """The usage lines of one command (every form of it, with the lines that continue them); the whole
    usage when the command is unknown."""
    out, keep = [], False
    for line in (__doc__ or "").split("\n"):
        head = re.match(r" {4}blueprint\.py (\S+)", line)
        if head:
            keep = head.group(1) == cmd
        elif not (line.startswith(" " * 6) and line.strip()):
            keep = False
        if keep:
            out.append("  " + line[4:])
    if not out:
        return __doc__ or ""
    return "Usage:\n" + "\n".join(out) + "\n\n`blueprint.py --help` lists every command."


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "exclusive":  # everything after `--` is the command, its own flags included
        from .segments import exclusive

        rest = argv[1:]
        cut = rest.index("--") if "--" in rest else len(rest)
        return exclusive(find_root(Path.cwd()), rest[0] if cut > 0 else "", rest[cut + 1:])
    if "-h" in argv[1:] or "--help" in argv[1:]:  # print the usage and run nothing: `dashboard --help` used to serve
        print(usage(argv[0]))
        return 0
    root = find_root(Path.cwd())
    cmd, args = argv[0], argv[1:]
    wanted, args = pop_option(args, "--blueprint")
    if cmd == "migrate":  # reads the config itself: it must work on a config the runtime refuses
        from .migrate import migrate

        return migrate(root, apply="--apply" in args)
    if cmd == "statusline":
        from .dashboard import statusline

        return statusline(root)
    try:
        cfg = load_config(root)
        if cmd == "hook":
            from .hooks import dispatch

            return dispatch(root, cfg, args[0] if args else None)
        return run(root, cfg, cmd, args, wanted)
    except ConfigError as exc:
        if cmd == "hook":  # a broken config must never block a session or an edit
            print(f"[speckit-blueprint] config error: {exc}")
            return 0
        print(f"error: {exc}", file=sys.stderr)
        return 1


def workspace_command(root: Path, cfg: dict, verb: str, args: list[str], wanted: str | None) -> int:
    """`workspace setup [E03]` and `workspace link`: the workspace on this machine, not one epic's worktrees."""
    from .workspace import link, setup

    flags = {a for a in args if a.startswith("--")}
    if verb == "link":
        dump(link(root, cfg, dry_run="--dry-run" in flags))
        return 0
    names = None
    keys = [a for a in args if not a.startswith("--")]
    if keys:
        from .repos import epic_repos

        inline, key = split_qualified(cfg, keys[0])
        bcfg = bp_config(cfg, inline or select_id(root, cfg, wanted))
        path = root / bcfg["blueprint_path"]
        epic = Blueprint(path).epic(key) if path.is_file() else None
        if epic is None:
            raise SystemExit(f"error: no epic {keys[0]} in {bcfg['blueprint_path']}")
        names = [r["name"] for r in epic_repos(bcfg, root, epic)]
    out = setup(root, cfg, names, fetch="--no-fetch" not in flags, dry_run="--dry-run" in flags)
    dump(out)
    return 0 if all(r["ok"] for r in out["repositories"]) else 1


def run(root: Path, cfg: dict, cmd: str, args: list[str], wanted: str | None) -> int:
    if cmd == "workspace" and args and args[0] in ("setup", "link"):
        return workspace_command(root, cfg, args[0], args[1:], wanted)
    if cmd in ("tasks-graph", "segments", "segment", "tick", "decision"):
        from .segments import command

        return command(root, cfg, cmd, args)
    # `desk:E01` names the blueprint inline; it must agree with --blueprint when both are given.
    note, args = pop_option(args, "--note")
    keys = [a for a in args if not a.startswith("--")]
    inline = None
    if cmd in EPIC_COMMANDS and keys and not keys[0].startswith(("specs/", "./")) and "/" not in keys[0]:
        inline, key = split_qualified(cfg, keys[0])
        keys[0] = key
    if inline and wanted and resolve_id(cfg, wanted) != inline:
        raise ConfigError(f"{keys[0]} and --blueprint {wanted} name different blueprints")
    bid = inline or select_id(root, cfg, wanted)
    bcfg = bp_config(cfg, bid)
    flags = {a for a in args if a.startswith("--")}

    if cmd == "status":
        st = full_status(root, cfg, bid, write_board=True)
        print(brief(st)) if "--brief" in flags else dump(st)
        return 0

    if cmd == "rules":
        t = rules_text(root, bcfg)
        if t is None:
            print(f"error: the constitution has no rules from blueprint '{bid}'; run `blueprint.py bootstrap`", file=sys.stderr)
            return 1
        print(t)
        return 0

    if cmd == "claims":
        from .board import load as load_board, update

        release, _ = pop_option(args, "--release")
        if release:
            update(root, lambda b: b["epics"].pop(release, None))
        dump(load_board(root).get("epics", {}))
        return 0

    if cmd == "require-effort":
        from .effort import require_effort

        return require_effort(cfg, keys[0] if keys else "")

    cache: dict = {}

    def load(b: str) -> Blueprint:
        if b not in cache:
            path = root / bp_config(cfg, b)["blueprint_path"]
            if not path.is_file():
                raise ConfigError(f"blueprint '{b}' not found at {bp_config(cfg, b)['blueprint_path']} (set its path in the extension config)")
            cache[b] = Blueprint(path)
        return cache[b]

    if cmd in ("context", "review-facts", "answers"):
        # Hooks run these before every Spec Kit command: a project without its blueprint file gets
        # `applies: false`, never an error.
        from .facts import answers_facts, context_facts, review_facts

        path = root / bcfg["blueprint_path"]
        bp0 = load(bid) if path.is_file() else None
        if cmd == "answers":
            step, _ = pop_option(args, "--step")
            dump(answers_facts(root, bcfg, bp0, (step or "clarify").strip().lower()))
        else:
            dump((context_facts if cmd == "context" else review_facts)(root, bcfg, bp0))
        return 0

    if cmd == "search":
        limit, rest = pop_option(args, "--limit")
        words = [a for a in rest if not a.startswith("--")]
        if not words:
            print("usage: blueprint.py search <words…> [--limit N]", file=sys.stderr)
            return 1
        bp = load(bid)
        hits = bp.search(words, int(limit) if limit and limit.isdigit() else 40)
        dump({"blueprint_id": bid, "blueprint": bcfg["blueprint_path"], "terms": words, "hits": hits, "silent": not hits})
        return 0

    if cmd in ("section", "find"):
        # Each argument may name its blueprint: `desk:DR-03`, `one:14.4`.
        rc = 0
        for arg in keys:
            other, key = split_qualified(cfg, arg)
            bp = load(other or bid)
            if cmd == "section":
                t = bp.section_text(key)
                if t is None:
                    print(f"error: no section '{key}' in {bp.path.name}", file=sys.stderr)
                    rc = 1
                else:
                    print(t + "\n")
                continue
            units = bp.defs.get(key)
            if not units:
                print(f"error: {arg} is not defined outside the epics", file=sys.stderr)
                rc = 1
                continue
            print(f"<!-- {qualify(bp_config(cfg, other or bid), key)} -->\n" + render_units(bp, [(key, u) for u in units]) + "\n")
        return rc

    if cmd == "dashboard":
        from .dashboard import serve_or_export

        port, _ = pop_option(args, "--port")
        export, _ = pop_option(args, "--export")
        return serve_or_export(root, cfg, port=int(port) if port else None, export=export, open_browser="--no-open" not in flags)

    if cmd == "metrics" and "--backfill" in flags:
        from .metrics import backfill

        dirs = [a for a in keys if a and Path(a).is_dir()]
        dump(backfill(root, cfg, dirs or None))
        return 0

    bp = load(bid)

    def epic_of(key: str | None) -> dict:
        if not key:
            fd = (read_trace(root / (active_feature_dir(root) or "")) or {}) if active_feature_dir(root) else {}
            key = fd.get("epic")
            if not key:
                raise SystemExit("error: name an epic (E03), or activate one first")
        e = bp.epic(key)
        if e is None:
            raise SystemExit(f"error: no '## Epic {key}' in {bp.path}")
        return e

    if cmd == "compile":
        if not keys:
            print("usage: blueprint.py compile E03 [--force] [--take] [--blueprint <id>]", file=sys.stderr)
            return 1
        dump(compile_epic(root, bcfg, bp, keys[0], force="--force" in flags, take="--take" in flags))
        return 0
    if cmd == "bootstrap":
        dump(bootstrap(root, bcfg, bp, skip_lint="--skip-lint" in flags))
        return 0
    if cmd == "activate":
        if not keys:
            print("usage: blueprint.py activate E03|specs/<folder>", file=sys.stderr)
            return 1
        folder = root / keys[0] if "/" in keys[0] else find_feature_dir(root, bcfg, epic_of(keys[0])["id"])
        if folder is None or not folder.is_dir():
            raise SystemExit(f"error: {keys[0]} is not compiled yet")
        rel = folder.resolve().relative_to(root.resolve()).as_posix()
        (root / ".specify").mkdir(exist_ok=True)
        (root / ".specify" / "feature.json").write_text(json.dumps({"feature_directory": rel}) + "\n", encoding="utf-8")
        dump({"feature_directory": rel})
        return 0
    if cmd == "accept":
        if not keys or not note:
            print('usage: blueprint.py accept E01 --note "why the built epic stays as it is"', file=sys.stderr)
            return 1
        dump(accept_change(root, bcfg, bp, keys[0], note))
        return 0
    if cmd == "stale":
        out = []
        for e in bp.epics:
            if e["retired"]:  # never recompiled: compile refuses it
                continue
            d = find_feature_dir(root, bcfg, e["id"])
            if d:
                changed = stale_units(bp, read_trace(d) or {})
                if changed:
                    total, done = task_counts(d / "tasks.md")
                    out.append({"blueprint_id": bid, "epic": e["id"], "feature_directory": d.relative_to(root).as_posix(), "changed": changed, "tasks_done": done, "tasks_total": total})
        dump(out)
        return 0
    if cmd in ("refs", "lint"):
        rc = 0
        if cmd == "lint":
            linter = SCRIPTS_DIR / "lint_blueprint.py"
            import subprocess

            rc = subprocess.run([sys.executable, str(linter), str(bp.path), *[a for a in args if a == "--strict"]]).returncode
            print("\nReference resolution (blueprint.py refs):")
        from .repos import unknown_repos

        out = {}
        for e in bp.epics:
            _, pairs, unresolved = epic_refs(bp, e, bcfg)
            row = {"resolved": len({i for i, _ in pairs}), "unresolved": unresolved}
            problems = list(e["repo_errors"]) + [f"unknown repository: {n}" for n in unknown_repos(bcfg, root, e)]
            if problems:
                row["repositories"] = problems
            out[e["id"]] = row
        if any(v["unresolved"] for v in out.values()):
            out["how_to_define"] = REFS_HINT
            rc = rc or 1
        if any(v.get("repositories") for v in out.values() if isinstance(v, dict)):
            rc = rc or 1
        dump(out)
        return rc
    if cmd == "epics":
        dump([{"epic": e["id"], "title": e["title"], **({"retired": True} if e["retired"] else {}),
               "depends": [f"{b}:{x}" if b else x for b, x in e["depends"]],
               "repositories": [r["name"] for r in e["repos"]], "stories": [s["id"] for s in e["stories"]]} for e in bp.epics])
        return 0
    if cmd in ("repos", "gate"):
        from .facts import gate, repos_facts

        e = epic_of(keys[0] if keys else None)
        if cmd == "repos":
            dump(repos_facts(root, bcfg, bp, e))
            return 0
        repo, _ = pop_option(args, "--repo")
        res = gate(root, bcfg, bp, e, repo)
        dump(res)
        return 0 if all(r.get("ok") for r in res["results"]) else 1
    if cmd == "workspace":
        from .workspace import create, git_commands, plan

        p = plan(root, bcfg, bp, epic_of(keys[0] if keys else None))
        if "--create" in flags:
            out = create(root, bcfg, p, take="--take" in flags)
            dump(out)
            return 0 if all(w["result"] in ("created", "exists") for w in out["worktrees"]) and not out.get("claim_conflict") else 1
        dump({**p, "git": git_commands(p)})
        return 0
    if cmd == "metrics":
        from .metrics import epic_metrics, write_epic_summary

        e = epic_of(keys[0]) if keys else None
        data = epic_metrics(root, cfg, bcfg, bp, e)
        if "--write" in flags and e is not None:
            write_epic_summary(root, bcfg, e, data)
        if "--brief" in flags and e is not None:
            print(data["brief"])
        else:
            dump(data)
        return 0
    if cmd == "estimate":
        from .estimate import estimate

        dump(estimate(root, cfg, bcfg, bp, epic_of(keys[0]) if keys else None))
        return 0
    print(f"unknown command: {cmd}\n\n{__doc__}", file=sys.stderr)
    return 1


def active_feature_dir(root: Path) -> str | None:
    from .core import active_feature

    return active_feature(root)
