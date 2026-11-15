"""Constitution, review checklist, decisions log and CLAUDE.md block from a blueprint."""

from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path

from .core import EXT_ROOT, SCRIPTS_DIR, GENERATED_MARK, TEMPLATE_MARK, TRACE_FILE, id_type, sha
from .model import Blueprint, demote, gen_header
from .blocks import block_content_sha, block_id, find_block, managed_blocks, marker, own_owner, owns_block, owns_constitution
from .compiler import id_note, qualify


FIXED_PRINCIPLES = """### {n}I. The blueprint is the source of truth
`{bp}` decides what is built. {scope} are compiled from it and are never edited by hand.
When reality disagrees with the blueprint, the blueprint changes first, through `/speckit-blueprint-amend`
(an `A-nn` entry in its Amendments log), and affected epics are recompiled. No silent deviation.

### {n}II. Evidence or silence
Every claim about the outside world (library, version, service, regulation) is verified or tagged
`[ASSUMED]` / `[UNKNOWN → OQ-nn]`. Items tagged `[VERIFY-AT-BUILD]` are re-checked before code depends on them.

### {n}III. Tests first, gate always green
Each story's tests are written from its acceptance criteria and fail before the implementation exists.
`check` stays green after every task. The gate is never weakened to make a change pass; if it must change,
that is a decision record.

### {n}IV. Copy by ID, decisions by record
User-facing text comes from the blueprint's copy tables by `CP-` ID, never improvised. Any decision the
blueprint does not cover is recorded in `{decisions}` (`DR-E03-1`: numbered within the epic it was
taken in) in the same commit.

### {n}V. Review before done
An epic is done only after `/speckit-blueprint-review` walked `{checklist}` over its diff and
wrote `review.md` with `Status: DONE`."""


def bump_version(old: str | None) -> str:
    if not old:
        return "1.0.0"
    parts = (old.split(".") + ["0", "0", "0"])[:3]
    try:
        return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    except ValueError:
        return "1.0.0"


def rules_body(bp: Blueprint, cfg: dict) -> tuple[list[str], list[str], str]:
    """The configured constitution sections of a blueprint, the ones it lacks, and their content hash
    (status compares it with the recorded one to report an outdated constitution)."""
    body, missing = [], []
    for key in cfg.get("constitution_sections") or []:
        t = bp.section_text(str(key))
        if t:
            body += [demote(t, 3), ""]
        else:
            missing.append(str(key))
    return body, missing, sha("\n".join(body))


def lint_gate(bp: Blueprint, cfg: dict) -> list[str]:
    """Errors that must be fixed before bootstrap writes anything: the structural linter's errors and
    references compile cannot resolve."""
    import subprocess
    import sys

    from .compiler import epic_refs

    problems = []
    res = subprocess.run([sys.executable, str(SCRIPTS_DIR / "lint_blueprint.py"), str(bp.path)], capture_output=True, text=True)
    if res.returncode != 0:
        errors = [ln.strip() for ln in res.stdout.splitlines() if "ERROR" in ln.upper()]
        problems += errors[:20] or [f"the linter exited {res.returncode}"]
    for e in bp.epics:
        unresolved = epic_refs(bp, e, cfg)[2]
        if unresolved:
            problems.append(f"{e['id']}: unresolved IDs {', '.join(unresolved[:10])}")
    return problems


def feature_scope(cfg: dict) -> str:
    """The feature folders a blueprint's rules govern."""
    if cfg["implicit"]:
        return f"Feature folders under `{cfg['specs_dir']}/` that contain `{TRACE_FILE}`"
    return f"Feature folders under `{cfg['specs_dir']}/` whose `{TRACE_FILE}` has `\"blueprint_id\": \"{cfg['id']}\"`"


def own_constitution(old: str, rel_bp: str, bp: "Blueprint", content_sha: str, body: list[str], cfg: dict, today: str) -> str:
    """The whole constitution, compiled from the blueprint (a new project). Managed blocks that other
    blueprints added to it are kept, before Governance."""
    ver_m = re.search(r"\*\*Version\*\*:\s*([0-9.]+)", old) if GENERATED_MARK in old else None
    rat_m = re.search(r"\*\*Ratified\*\*:\s*([0-9-]+)", old) if GENERATED_MARK in old else None
    old_sha = re.search(r"content-sha ([0-9a-f]+)", old.split("\n", 1)[0])
    if ver_m and old_sha and old_sha.group(1) == content_sha:
        version = ver_m.group(1)
    else:
        version = bump_version(ver_m.group(1) if ver_m else None)
    scope = "Specs under `{}/`".format(cfg["specs_dir"]) if cfg["implicit"] else feature_scope(cfg)
    others = [old[s:e] for i, s, e in managed_blocks(old, "rules:") if not owns_block(cfg, i)] if GENERATED_MARK in old else []
    id_part = f"blueprint-id {cfg['id']}, "
    return "\n".join([
        f"<!-- {GENERATED_MARK} — constitution compiled from {rel_bp} ({id_part}sha {bp.sha}, content-sha {content_sha}). "
        "Do not edit by hand: change the blueprint, then run `blueprint.py bootstrap`. -->",
        f"# {bp.title()} Constitution",
        "",
        "## Core Principles",
        "",
        FIXED_PRINCIPLES.format(n="", bp=rel_bp, scope=scope, decisions=cfg["decisions_path"], checklist=cfg["review_checklist_path"]),
        "",
        *([id_note(cfg), ""] if cfg["prefix"] else []),
        "## Engineering Rules (from the blueprint)",
        "",
        *body,
        *[x for b in others for x in (b, "")],
        "## Governance",
        "",
        f"This constitution supersedes other practice in this repository. It is regenerated from {rel_bp}; "
        "amend the blueprint, never this file. Plans and tasks must comply; a justified exception is a DR in "
        f"`{cfg['decisions_path']}`.",
        "",
        f"**Version**: {version} | **Ratified**: {rat_m.group(1) if rat_m else today} | **Last Amended**: {today}",
        "",
    ])


def append_rules(old: str, rel_bp: str, bp_sha: str, content_sha: str, body: list[str], cfg: dict) -> str:
    """A constitution with one managed block of blueprint rules per blueprint; the rest is left alone."""
    scope = feature_scope(cfg)
    bid = block_id(cfg)
    if cfg["implicit"]:
        title, n, applies = "## Blueprint Rules (speckit-blueprint)", "B-", f"(their folder contains `{TRACE_FILE}`)"
    else:
        tag = cfg["prefix"] or cfg["id"].upper()
        title, n, applies = f"## Blueprint Rules: {tag} (speckit-blueprint, id={bid})", f"{tag}-", f"({scope[0].lower() + scope[1:]})"
    block = "\n".join([
        marker("rules:", "start", bid),
        f"<!-- Compiled from {rel_bp} (sha {bp_sha}, content-sha {content_sha}) by `blueprint.py bootstrap`. "
        "Edit the blueprint, not this block; the rest of this file belongs to the project. -->",
        title,
        "",
        f"These rules apply only to features compiled from `{rel_bp}` {applies}. "
        "Every other feature follows this project's own workflow"
        + ("" if cfg["implicit"] else ", and the blocks of other blueprints do not apply to it")
        + ". They add to the principles above and never override them: where the two conflict, the principles above win.",
        "",
        *([id_note(cfg), ""] if cfg["prefix"] else []),
        FIXED_PRINCIPLES.format(n=n, bp=rel_bp, scope=scope, decisions=cfg["decisions_path"], checklist=cfg["review_checklist_path"]),
        "",
        "### Engineering rules from the blueprint",
        "",
        *body,
        marker("rules:", "end", bid),
    ])
    span = find_block(old, "rules:", cfg)
    if span:
        return old[: span[0]] + block + old[span[1]:]
    m = re.search(r"^## Governance\b", old, re.M)
    if m:
        return old[: m.start()] + block + "\n\n" + old[m.start():]
    return old.rstrip() + "\n\n" + block + "\n"


VERSION_RE = re.compile(r"(\*\*Version\*\*:\s*)(\d+)\.(\d+)\.(\d+)")


LAST_AMENDED_RE = re.compile(r"(\*\*Last Amended\*\*:\s*)([0-9-]+)")


def record_amendment(text: str, change: str, bump: str, rel_bp: str, today: str) -> tuple[str, str | None]:
    """Follow the project's governance for a change to the managed block: bump the version and set Last
    Amended. Returns the text and the version change ("1.1.0 → 1.2.0").

    Spec Kit treats the Sync Impact Report comment as scratch for reviewing one amendment, removed before
    the commit, so none is created here; a line is added only to a report that is already open (an
    amendment in progress), which is removed together with it."""
    m = VERSION_RE.search(text)
    if bump == "none" or not m:
        return text, None
    major, minor, patch = int(m.group(2)), int(m.group(3)), int(m.group(4))
    new = f"{major}.{minor + 1}.0" if bump == "minor" else f"{major}.{minor}.{patch + 1}"
    old = f"{major}.{minor}.{patch}"
    text = text[: m.start()] + m.group(1) + new + text[m.end():]
    text = LAST_AMENDED_RE.sub(lambda x: x.group(1) + today, text, count=1)
    line = f"- Version change: {old} → {new} ({today}): Blueprint Rules block {change} by speckit-blueprint from {rel_bp}"
    rep_m = re.search(r"<!--\s*\n?\s*Sync Impact Report[^\n]*\n", text)
    if rep_m:
        text = text[: rep_m.end()] + line + "\n" + text[rep_m.end():]
    return text, f"{old} → {new}"


def bootstrap(root: Path, cfg: dict, bp: Blueprint, skip_lint: bool = False) -> dict:
    if not skip_lint:
        problems = lint_gate(bp, cfg)
        if problems:
            raise SystemExit("error: fix the blueprint before bootstrap (`blueprint.py lint` shows everything):\n- " + "\n- ".join(problems))
    rel_bp = os.path.relpath(bp.path, root)
    today = _dt.date.today().isoformat()
    report: dict = {"blueprint_id": cfg["id"]}

    # constitution
    cpath = root / ".specify" / "memory" / "constitution.md"
    old = cpath.read_text(encoding="utf-8") if cpath.is_file() else ""
    body, missing, content_sha = rules_body(bp, cfg)
    mode = cfg.get("constitution_mode") or "auto"
    if mode == "auto":
        owned = not old.strip() or TEMPLATE_MARK in old or owns_constitution(cfg, old)
        mode = "own" if owned else "append"
    if mode == "own" and own_owner(old) not in (False, None) and not owns_constitution(cfg, old):
        raise SystemExit(
            f"error: the constitution is compiled whole from blueprint '{own_owner(old)}'; "
            f"constitution_mode: own would replace it. Use auto or append for '{cfg['id']}'."
        )
    if mode == "append":
        before = block_content_sha(old, cfg)
        change = "added" if before is None else ("updated" if before != content_sha else None)
        text = append_rules(old, rel_bp, bp.sha, content_sha, body, cfg)
        version = None
        if change:
            text, version = record_amendment(text, change, str(cfg.get("constitution_version_bump") or "minor"), rel_bp, today)
        report["constitution"] = {
            "path": ".specify/memory/constitution.md", "mode": "append", "bytes": len(text.encode()),
            "block": change or "unchanged",
            "version": version or "unchanged",
            "amendment": (f"Blueprint Rules block {change}; version {version}" if change and version else None),
            "missing_sections": missing,
        }
    else:
        text = own_constitution(old, rel_bp, bp, content_sha, body, cfg, today)
        version = re.search(r"\*\*Version\*\*:\s*([0-9.]+)", text).group(1)
        report["constitution"] = {"path": ".specify/memory/constitution.md", "mode": "own", "bytes": len(text.encode()), "version": version, "missing_sections": missing}
    cpath.parent.mkdir(parents=True, exist_ok=True)
    cpath.write_text(text, encoding="utf-8")

    # review checklist
    rc = bp.section_text("14.4") or bp.section_text("Review checklist")
    rc_rel = cfg["review_checklist_path"]
    p = root / rc_rel
    if p.is_file() and GENERATED_MARK not in p.read_text(encoding="utf-8"):
        report["review_checklist"] = f"{rc_rel} exists and is not generated; left unchanged"
    elif rc:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(gen_header(rel_bp, bp.sha, "REVIEW-CHECKLIST.md") + "\n" + demote(rc, 1) + "\n", encoding="utf-8")
        report["review_checklist"] = rc_rel
    else:
        report["review_checklist"] = "missing §14.4 in blueprint"

    # decisions log (created once; sessions append to it)
    d_rel = cfg["decisions_path"]
    dpath = root / d_rel
    if not dpath.is_file():
        rows = []
        for ident in sorted((k for k in bp.defs if id_type(k) == "DR"), key=lambda x: [int(n) for n in re.findall(r"\d+", x)]):
            u = bp.defs[ident][0]
            first = bp.lines[u.start]
            title = re.sub(r"^#+\s*|^\*\*|\*\*$", "", first).strip()
            title = re.sub(r"^" + re.escape(ident) + r"\s*[—–:-]*\s*", "", title)
            status = "active"
            m = re.search(r"\*\*Status\*\*\s*([A-Za-z]+)", u.text(bp.lines))
            if m:
                status = m.group(1).lower()
            rows.append(f"| {qualify(cfg, ident)} | {title[:90]} | blueprint | {status} |")
        tpl = (EXT_ROOT / "templates" / "DECISIONS.md").read_text(encoding="utf-8")
        dpath.parent.mkdir(parents=True, exist_ok=True)
        dpath.write_text(
            tpl.replace("{{PRODUCT}}", bp.title()).replace("{{BLUEPRINT}}", rel_bp).replace("{{ROWS}}", "\n".join(rows))
            .replace("{{FIND}}", f"blueprint.py find {qualify(cfg, 'DR-07')}")
            .replace("{{ID_NOTE}}", ("\n" + id_note(cfg) + "\n") if cfg["prefix"] else ""),
            encoding="utf-8",
        )
        report["decisions"] = f"created {d_rel} with {len(rows)} blueprint DRs indexed"
    else:
        report["decisions"] = f"{d_rel} exists; left unchanged"

    # CLAUDE.md managed block
    if not cfg.get("claude_md", True):
        report["claude_md"] = "skipped (claude_md: false)"
        return report
    block_tpl = (EXT_ROOT / "templates" / "claude-block.md").read_text(encoding="utf-8")
    flag = "" if cfg["implicit"] or not cfg["multi"] else f" --blueprint {cfg['id']}"
    values = {
        "PRODUCT": bp.title() + ("" if cfg["implicit"] else f" ({cfg['prefix'] or cfg['id']})"),
        "SCOPE": feature_scope(cfg),
        "BLUEPRINT": rel_bp,
        "DECISIONS": cfg["decisions_path"],
        "EPIC": f"{cfg['id']}:Enn" if cfg["multi"] else "Enn",
        "FLAG": flag,
        "ID_NOTE": ("\n" + id_note(cfg) + "\n") if cfg["prefix"] else "",
    }
    for k, v in values.items():
        block_tpl = block_tpl.replace("{{" + k + "}}", v)
    bid = block_id(cfg)
    block = f"{marker('', 'start', bid)}\n{block_tpl.strip()}\n{marker('', 'end', bid)}"
    cm = root / "CLAUDE.md"
    cur = cm.read_text(encoding="utf-8") if cm.is_file() else ""
    span = find_block(cur, "", cfg)
    if span:
        new = cur[: span[0]] + block + cur[span[1]:]
    else:
        blocks = managed_blocks(cur, "")
        if blocks:  # next to the other blueprints' blocks
            end = blocks[-1][2]
            new = cur[:end] + "\n\n" + block + cur[end:]
        else:
            new = (cur.rstrip() + "\n\n" if cur.strip() else "") + block + "\n"
    cm.write_text(new, encoding="utf-8")
    report["claude_md"] = "CLAUDE.md managed block updated" + ("" if cfg["implicit"] else f" (id={cfg['id']})")
    return report
