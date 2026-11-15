"""The extension config: a stdlib YAML subset, defaults, per-blueprint settings, blueprint selection.

Schema 2 (DESIGN.md "Config, schema 2"). Every key is optional.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .core import BLUEPRINT_ID_RE, ConfigError, EXT_ID, IMPLICIT_ID, active_feature, read_trace

SCHEMA = 2

DEFAULTS = {
    "schema": SCHEMA,
    "blueprint_path": "docs/BLUEPRINT.md",
    "specs_dir": "specs",
    # Where bootstrap writes the decisions log and the review checklist.
    "decisions_path": "docs/DECISIONS.md",
    "review_checklist_path": "docs/REVIEW-CHECKLIST.md",
    # false: bootstrap does not touch CLAUDE.md (for projects whose CLAUDE.md only imports other files).
    "claude_md": True,
    # Sections copied into .specify/memory/constitution.md (read by tasks and implement).
    "constitution_sections": ["13.2", "13.5", "13.6", "13.7", "13.9", "13.10", "14.2", "14.3", "14.8", "12.10"],
    # Sections copied into every epic's plan.md "Technical Context".
    "plan_sections": ["12.2", "13.1", "13.3", "13.4"],
    # Second-level resolution: IDs of these types found inside resolved definitions are
    # resolved too (a flow names its copy lines, an edge case names its copy lines).
    "second_level_types": ["CP", "EC"],
    # own: bootstrap writes the whole constitution. append: it keeps the project's constitution and
    # maintains one managed block in it. auto: own for a missing or template constitution, else append.
    "constitution_mode": "auto",
    # append mode: when the managed block is added or its rules change, bump the project's constitution
    # version (minor | patch | none) and set Last Amended, as Spec Kit's constitution governance expects.
    "constitution_version_bump": "minor",
    "guard": True,
    # Repositories the epics change: name -> path (relative to the hub), gate, base, branch_prefix, remote.
    # Empty: the hub is the only repository.
    "repositories": {},
    # Epic branch names are <prefix><feature folder>. None: Spec Kit's git extension's branch_prefix.
    "branch_prefix": None,
    # Epic worktrees go to <repository parent>/<worktrees_dir>/.
    "worktrees_dir": ".worktrees",
    # Steps that run their reasoning in a subagent at a guaranteed level; the rest use the session's.
    "effort": {"discover": "max", "amend": "max", "review": "max"},
    # Model for those subagents: inherit (the session's), or an alias such as opus or sonnet.
    "effort_model": "inherit",
    # local: metrics from Claude Code transcripts, kept in the hub's .git; commit: also a summary per epic
    # in its feature folder; off: none.
    "metrics": "local",
}

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
EFFORT_STEPS = ("discover", "amend", "review")
METRICS_MODES = ("local", "commit", "off")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REPO_KEYS = ("path", "remote", "gate", "base", "branch_prefix")

# The `next.command` values status reports that run at a guaranteed effort, and their `effort` key.
COMMAND_STEP = {
    "speckit-blueprint-discover": "discover",
    "speckit-blueprint-amend": "amend",
    "speckit-blueprint-review": "review",
}


def _split_top(s: str, sep: str = ",") -> list[str]:
    """Split on `sep` outside quotes and brackets."""
    out, depth, quote, cur = [], 0, None, ""
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    if cur.strip():
        out.append(cur)
    return out


def _parse_scalar(v: str):
    v = v.strip()
    if v in ("true", "True", "yes"):
        return True
    if v in ("false", "False", "no"):
        return False
    if v in ("null", "~"):
        return None
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_parse_scalar(x) for x in _split_top(inner)] if inner else []
    if v.startswith("{") and v.endswith("}"):
        out = {}
        for part in _split_top(v[1:-1].strip()):
            k, sep, val = part.partition(":")
            if sep:
                out[k.strip().strip("'\"")] = _parse_scalar(val)
        return out
    return v


KEY_RE = re.compile(r"^([^:#][^:]*):(?:\s+(.*)|)$")


def load_simple_yaml(path: Path) -> dict:
    """The subset this extension's config uses: `key: value`, `key:` + `- item` lists, inline `[a, b]`
    lists and `{k: v}` maps, and nested maps by indentation (`blueprints:` → id → settings)."""
    return parse_simple_yaml(path.read_text(encoding="utf-8")) if path.is_file() else {}


def parse_simple_yaml(text: str) -> dict:
    rows = []
    for raw in text.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        line = raw.split(" #", 1)[0].rstrip()
        if line.strip():
            rows.append((len(line) - len(line.lstrip()), line.strip()))
    out, _ = _yaml_map(rows, 0, rows[0][0]) if rows else ({}, 0)
    return out


def _yaml_list(rows: list, i: int, indent: int) -> tuple[list, int]:
    out = []
    while i < len(rows) and rows[i][0] == indent and rows[i][1].startswith("- "):
        out.append(_parse_scalar(rows[i][1][2:]))
        i += 1
    return out, i


def _yaml_map(rows: list, i: int, indent: int) -> tuple[dict, int]:
    out: dict = {}
    while i < len(rows) and rows[i][0] >= indent:
        ind, text = rows[i]
        m = KEY_RE.match(text)
        if ind > indent or not m:  # stray line: skip it rather than misread it
            i += 1
            continue
        key, val = m.group(1).strip(), (m.group(2) or "").strip()
        i += 1
        if val:
            out[key] = _parse_scalar(val)
        elif i < len(rows) and rows[i][1].startswith("- ") and rows[i][0] >= indent:
            out[key], i = _yaml_list(rows, i, rows[i][0])
        elif i < len(rows) and rows[i][0] > indent:
            out[key], i = _yaml_map(rows, i, rows[i][0])
        else:
            out[key] = []
    return out, i


# Paths that belong to one blueprint: top-level keys in a single-blueprint config, `path`, `decisions`,
# `checklist` per id under `blueprints:`.
PATH_KEYS = ("blueprint_path", "decisions_path", "review_checklist_path")
# Settings a `blueprints:` entry may override; the top-level value (or the default) applies otherwise.
PER_BLUEPRINT_KEYS = ("constitution_sections", "plan_sections", "second_level_types")


def load_config(root: Path) -> dict:
    """The extension config. `blueprints` maps each blueprint id to its own settings; a config without
    that map has one blueprint, IMPLICIT_ID, built from the top-level keys (and `implicit` is true)."""
    ext_dir = root / ".specify" / "extensions" / EXT_ID
    return config_from([load_simple_yaml(ext_dir / f"{EXT_ID}-config.yml"), load_simple_yaml(ext_dir / f"{EXT_ID}-config.local.yml")])


def config_from(raws: list) -> dict:
    """The config from parsed files, later ones on top (the `.local.yml` file overrides)."""
    cfg = dict(DEFAULTS)
    blueprints: dict = {}
    top: set = set()
    for raw in raws:
        for k, v in raw.items():
            if v == []:
                continue
            if k == "blueprints":
                if not isinstance(v, dict):
                    raise ConfigError("`blueprints` must map each blueprint id to its settings (path, decisions, …)")
                for bid, spec in v.items():
                    spec = spec if isinstance(spec, dict) else {"path": spec}
                    blueprints.setdefault(str(bid), {}).update({sk: sv for sk, sv in spec.items() if sv != []})
            elif k == "repositories" and isinstance(v, dict) and isinstance(cfg.get("repositories"), dict) and k in top:
                cfg[k] = merge_repositories(cfg[k], v)
            else:
                cfg[k] = v
                top.add(k)
    try:
        schema = int(cfg.get("schema") or SCHEMA)
    except (TypeError, ValueError):
        raise ConfigError("`schema` must be a number")
    if schema > SCHEMA:
        raise ConfigError(f"the config is schema {schema}; this extension reads schema {SCHEMA}. Update the extension.")
    if schema < SCHEMA:
        raise ConfigError(f"the config is schema {schema}; run `blueprint.py migrate` to upgrade it to schema {SCHEMA}")
    cfg["schema"] = schema
    if blueprints:
        clash = sorted(top & set(PATH_KEYS))
        if clash:
            raise ConfigError(
                f"{', '.join(clash)} at the top level of a config with `blueprints:` is ambiguous; "
                "set `path`, `decisions` and `checklist` under each blueprint id instead"
            )
        for bid in blueprints:
            if not BLUEPRINT_ID_RE.match(bid):
                raise ConfigError(f"blueprint id '{bid}' must be lowercase letters, digits and hyphens")
        if cfg.get("default_blueprint") and cfg["default_blueprint"] not in blueprints:
            raise ConfigError(f"default_blueprint '{cfg['default_blueprint']}' is not under `blueprints:`")
        cfg["implicit"] = False
    else:
        env_path = os.environ.get("SPECKIT_BLUEPRINT_BLUEPRINT_PATH")
        if env_path:
            cfg["blueprint_path"] = env_path
        blueprints = {IMPLICIT_ID: {}}
        cfg["implicit"] = True
    cfg["blueprints"] = blueprints
    cfg["effort"] = effort_config(cfg.get("effort"))
    cfg["repositories"] = repo_entries(cfg)
    metrics = str(cfg.get("metrics") if cfg.get("metrics") is not False else "off").strip().lower()
    if metrics not in METRICS_MODES:
        raise ConfigError(f"metrics: '{metrics}' is not one of {', '.join(METRICS_MODES)}")
    cfg["metrics"] = metrics
    seen: dict = {}
    for bid in blueprints:
        b = bp_config(cfg, bid)
        if b["prefix"] and not re.match(r"^[A-Za-z][A-Za-z0-9]*$", b["prefix"]):
            raise ConfigError(f"prefix '{b['prefix']}' of blueprint '{bid}' must be letters and digits")
        for key in PATH_KEYS:
            p = os.path.normpath(b[key])
            if p in seen:
                raise ConfigError(f"blueprints '{seen[p]}' and '{bid}' both use {p}; give each its own file")
            seen[p] = bid
    return cfg


def effort_config(value) -> dict:
    """The steps that run at a guaranteed level: the defaults, with the config's `effort:` on top. A
    level of `session` (or `none`) takes the step off the list."""
    out = dict(DEFAULTS["effort"])
    if value in (None, [], out):
        return out
    if not isinstance(value, dict):
        raise ConfigError("`effort` must map steps (" + ", ".join(EFFORT_STEPS) + ") to a level")
    for step, level in value.items():
        if step not in EFFORT_STEPS:
            raise ConfigError(
                f"effort: '{step}' cannot run at a guaranteed level (only {', '.join(EFFORT_STEPS)} do; "
                "every other step runs at the session's level)"
            )
        level = str(level).strip().lower()
        if level in ("session", "none", "off", "false"):
            out.pop(step, None)
            continue
        if level not in EFFORT_LEVELS:
            raise ConfigError(f"effort: '{level}' for {step} is not one of {', '.join(EFFORT_LEVELS)} or session")
        out[step] = level
    return out


def effort_rank(level: str | None) -> int:
    return EFFORT_LEVELS.index(level) if level in EFFORT_LEVELS else -1


def merge_repositories(shared: dict, local: dict) -> dict:
    """The local file's `repositories:` on top of the shared one, setting by setting: `core: {remote: …}` there
    changes core's remote and keeps its path and gate."""
    def as_map(spec) -> dict:
        if isinstance(spec, dict):
            return spec
        return {} if spec in (None, "", [], {}) else {"path": spec}

    out = dict(shared)
    for name, spec in local.items():
        out[name] = {**as_map(out.get(name)), **as_map(spec)}
    return out


def repo_entries(cfg: dict) -> dict:
    """`repositories:` normalised: name -> {name, path, remote, gate, base, branch_prefix}. Paths are as
    written (relative to the hub's main checkout); repos.registry() resolves them."""
    raw = cfg.get("repositories") or {}
    if isinstance(raw, dict) and raw and all(isinstance(v, dict) and "name" in v for v in raw.values()):
        return raw  # already normalised
    if not isinstance(raw, dict):
        raise ConfigError("`repositories` must map each repository name to its settings (path, gate, …)")
    out = {}
    for name, spec in raw.items():
        name = str(name)
        if not REPO_NAME_RE.match(name):
            raise ConfigError(f"repository name '{name}' must be letters, digits, '.', '_' and '-'")
        spec = spec if isinstance(spec, dict) else {"path": spec}
        unknown = sorted(set(spec) - set(REPO_KEYS))
        if unknown:
            raise ConfigError(f"repository '{name}': unknown key(s) {', '.join(unknown)} (known: {', '.join(REPO_KEYS)})")
        out[name] = {
            "name": name,
            "path": str(spec.get("path") or f"../{name}"),
            "remote": str(spec.get("remote") or ""),
            "gate": str(spec.get("gate") or ""),
            "base": str(spec.get("base") or ""),
            "branch_prefix": None if spec.get("branch_prefix") in (None, []) else str(spec.get("branch_prefix")),
        }
    return out


def git_extension_prefix(root: Path) -> str:
    """`branch_prefix` from Spec Kit's git extension config, if the project uses it."""
    data = load_simple_yaml(root / ".specify" / "extensions" / "git" / "git-config.yml")
    v = data.get("branch_prefix")
    return str(v) if isinstance(v, str) else ""


def branch_prefix_for(cfg: dict, root: Path, repo: dict | None) -> str:
    """A repository's own `branch_prefix`, else the config's, else Spec Kit's git extension's, else ''."""
    cfg = cfg.get("_cfg", cfg)
    if repo and repo.get("branch_prefix") is not None:
        return repo["branch_prefix"]
    if cfg.get("branch_prefix") is not None and cfg.get("branch_prefix") != []:
        return str(cfg["branch_prefix"])
    return git_extension_prefix(root)


def sibling_path(path: str, name: str) -> str:
    """docs/desk/BLUEPRINT.md → docs/desk/DECISIONS.md; docs/DESK-BLUEPRINT.md → docs/DESK-DECISIONS.md."""
    head, tail = os.path.split(path)
    stem = os.path.splitext(tail)[0]
    new = stem.replace("BLUEPRINT", name) if "BLUEPRINT" in stem else f"{stem}-{name}"
    return f"{head}/{new}.md" if head else f"{new}.md"


def default_id(cfg: dict) -> str:
    cfg = cfg.get("_cfg", cfg)
    return cfg.get("default_blueprint") or next(iter(cfg["blueprints"]))


def bp_config(cfg: dict, bid: str | None = None) -> dict:
    """The config with one blueprint's settings filled in: blueprint_path, decisions_path,
    review_checklist_path, the section lists, prefix, sources, number_base. `_cfg` keeps the whole
    config for lookups across blueprints."""
    cfg = cfg.get("_cfg", cfg)
    bid = bid or default_id(cfg)
    spec = cfg["blueprints"][bid]
    b = dict(cfg)
    if cfg["implicit"]:
        path, decisions, checklist, prefix = cfg["blueprint_path"], cfg["decisions_path"], cfg["review_checklist_path"], ""
    else:
        path = str(spec.get("path") or f"docs/{bid}/BLUEPRINT.md")
        decisions = str(spec.get("decisions") or sibling_path(path, "DECISIONS"))
        checklist = str(spec.get("checklist") or sibling_path(path, "REVIEW-CHECKLIST"))
        prefix = spec.get("prefix")
        if prefix is None:
            prefix = bid.upper().replace("-", "") if len(cfg["blueprints"]) > 1 else ""
    sources = spec.get("sources") or []
    try:
        number_base = int(spec.get("number_base") or 0)
    except ValueError:
        raise ConfigError(f"number_base of blueprint '{bid}' must be a number")
    b.update(
        {k: spec.get(k, cfg.get(k)) for k in PER_BLUEPRINT_KEYS},
        _cfg=cfg,
        id=bid,
        blueprint_path=path,
        decisions_path=decisions,
        review_checklist_path=checklist,
        prefix=str(prefix or ""),
        sources=[str(s) for s in (sources if isinstance(sources, list) else [sources])],
        number_base=number_base,
        is_default=(bid == default_id(cfg)),
        multi=len(cfg["blueprints"]) > 1,
    )
    return b


def resolve_id(cfg: dict, key: str) -> str:
    """A blueprint id from an id or a prefix, case-insensitive."""
    cfg = cfg.get("_cfg", cfg)
    for bid in cfg["blueprints"]:
        prefix = bp_config(cfg, bid)["prefix"]
        if key.lower() == bid or (prefix and key.upper() == prefix.upper()):
            return bid
    raise ConfigError(f"no blueprint '{key}' in the config (configured: {', '.join(cfg['blueprints'])})")


def split_qualified(cfg: dict, token: str) -> tuple[str | None, str]:
    """`desk:E01` or `DESK:DR-03` → ('desk', 'E01'); an unqualified token → (None, token)."""
    left, sep, right = token.partition(":")
    if sep and left and right:
        return resolve_id(cfg, left), right
    return None, token


def trace_blueprint_id(cfg: dict, trace: dict) -> str | None:
    """The blueprint a compiled feature belongs to: its `blueprint_id` (schema 2 writes it always). A
    trace without one is a legacy folder: `blueprint.py migrate` adds it; until then it belongs to the
    blueprint whose path it was compiled from."""
    cfg = cfg.get("_cfg", cfg)
    bid = trace.get("blueprint_id")
    if bid in cfg["blueprints"]:
        return bid
    if bid == IMPLICIT_ID and cfg["implicit"]:
        return IMPLICIT_ID
    rel = trace.get("blueprint")
    if (not bid or bid == IMPLICIT_ID) and rel:
        for other in cfg["blueprints"]:
            if os.path.normpath(bp_config(cfg, other)["blueprint_path"]) == os.path.normpath(rel):
                return other
    return None


def active_blueprint_id(root: Path, cfg: dict) -> str | None:
    fd = active_feature(root)
    trace = read_trace(root / fd) if fd else None
    return trace_blueprint_id(cfg, trace) if trace else None


def select_id(root: Path, cfg: dict, wanted: str | None = None) -> str:
    """--blueprint, else SPECKIT_BLUEPRINT, else the active feature's blueprint, else the default."""
    wanted = wanted or os.environ.get("SPECKIT_BLUEPRINT")
    if wanted:
        return resolve_id(cfg, wanted)
    return active_blueprint_id(root, cfg) or default_id(cfg)
