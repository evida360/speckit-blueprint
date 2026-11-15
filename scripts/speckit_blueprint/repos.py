"""Repositories: the registry from the config, git facts per repository, branch names, the gate.

A hub without `repositories:` has one repository: itself. Paths in the config are relative to the hub's
main checkout, so they resolve the same from any of its worktrees.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import time
from pathlib import Path

from .config import branch_prefix_for, repo_entries


def git(path: Path, *args: str, timeout: float = 20) -> tuple[int, str]:
    try:
        res = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return res.returncode, (res.stdout if res.returncode == 0 else res.stderr).strip()


def git_network(args: list[str], cwd: Path | None = None, timeout: float = 600) -> tuple[int, str]:
    """A clone or a fetch. It never waits for a password prompt: without credentials it fails and says so."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        res = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                             timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return res.returncode, (res.stdout if res.returncode == 0 else res.stderr).strip()


def remote_branch(path: Path, branch: str) -> str:
    """`<remote>/<branch>` when a remote has the branch (origin first), else ''."""
    if not branch:
        return ""
    rc, out = git(path, "remote")
    for r in sorted(out.split() if rc == 0 else [], key=lambda x: x != "origin"):
        if git(path, "rev-parse", "--verify", "-q", f"refs/remotes/{r}/{branch}")[0] == 0:
            return f"{r}/{branch}"
    return ""


def resolve_remote(remote: str, base_url: str) -> str:
    """A repository's clone URL. One written relative to the hub's own (`../core.git`) resolves the way git
    resolves relative submodule URLs: each `../` drops one component of the hub's URL. '' when it cannot."""
    if not remote.startswith(("./", "../")):
        return remote
    if not base_url:
        return ""
    url, rel, sep = base_url.rstrip("/"), remote, "/"
    scheme = url.find("://")
    while rel.startswith(("./", "../")):
        if rel.startswith("./"):
            rel = rel[2:]
            continue
        rel = rel[3:]
        cut = max(url.rfind("/"), url.rfind(":"))
        if cut <= 0 or (scheme >= 0 and cut <= scheme + 2):
            return ""
        sep, url = url[cut], url[:cut]
    return f"{url}{sep}{rel}" if rel else url


def is_inside(path: str | Path, parent: Path) -> bool:
    """A folder of `parent` (a repository that is a folder of the hub, in a monorepo)."""
    p, q = Path(path).resolve(), parent.resolve()
    return p != q and q in p.parents


def git_common_dir(root: Path) -> Path | None:
    rc, out = git(root, "rev-parse", "--git-common-dir")
    if rc != 0 or not out:
        return None
    p = Path(out)
    return (p if p.is_absolute() else (root / p)).resolve()


def main_root(root: Path) -> Path:
    """The hub's main checkout, which its worktrees hang off; `root` itself outside git."""
    common = git_common_dir(root)
    if common is not None and common.name == ".git":
        return common.parent
    return root.resolve()


def registry(cfg: dict, root: Path) -> dict:
    """name -> {name, path (this checkout's view), main_path, remote, gate, base, branch_prefix, hub}."""
    cfg = cfg.get("_cfg", cfg)
    base = main_root(root)
    here = root.resolve()
    entries = repo_entries(cfg)
    if not entries:
        name = base.name
        return {name: {"name": name, "path": str(here), "main_path": str(base), "remote": "", "gate": "", "base": "",
                       "branch_prefix": None, "hub": True}}
    out = {}
    for name, e in entries.items():
        p = Path(e["path"])
        p = (p if p.is_absolute() else base / p).resolve()
        hub = p == base
        out[name] = {**e, "main_path": str(p), "path": str(here if hub else p), "hub": hub}
    return out


def hub_entry(reg: dict) -> dict | None:
    return next((r for r in reg.values() if r["hub"]), None)


def hub_url(cfg: dict, root: Path) -> str:
    """The hub's own clone URL: its `remote` in the config, else its `origin`."""
    hub = hub_entry(registry(cfg, root))
    if hub and hub.get("remote") and not hub["remote"].startswith(("./", "../")):
        return hub["remote"]
    rc, out = git(main_root(root), "remote", "get-url", "origin")
    return out if rc == 0 else ""


def epic_repos(cfg: dict, root: Path, epic: dict) -> list[dict]:
    """The repositories an epic changes, in order: its **Repositories** table, or the hub alone."""
    reg = registry(cfg, root)
    rows = epic.get("repos") or []
    if not rows:
        hub = hub_entry(reg) or next(iter(reg.values()))
        return [{"name": hub["name"], "order": 1, "role": "", "base": "", "branch": "", "contracts": ""}]
    return rows


def unknown_repos(cfg: dict, root: Path, epic: dict) -> list[str]:
    """Names in an epic's table that the config does not list (only when the config lists some)."""
    if not repo_entries(cfg.get("_cfg", cfg)):
        return []
    reg = registry(cfg, root)
    return [r["name"] for r in epic.get("repos") or [] if r["name"] not in reg]


def branch_name(cfg: dict, root: Path, repo_name: str | None, folder: str, explicit: str = "") -> str:
    """An epic's branch in one repository: the table's Branch cell, else <prefix><feature folder>."""
    if explicit:
        return explicit
    reg = registry(cfg, root)
    return branch_prefix_for(cfg, root, reg.get(repo_name or "")) + folder


def base_branch(cfg: dict, root: Path, repo_name: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    entry = registry(cfg, root).get(repo_name) or {}
    if entry.get("base"):
        return entry["base"]
    path = Path(entry.get("main_path") or root)
    rc, out = git(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if rc == 0 and out:
        return out.split("/", 1)[-1]
    for cand in ("main", "master"):
        if git(path, "rev-parse", "--verify", "-q", f"refs/heads/{cand}")[0] == 0:
            return cand
    return "main"


def facts(path: Path, branch: str = "", base: str = "") -> dict:
    """Git state of one checkout: current branch, uncommitted files, the epic branch against its base."""
    out: dict = {"path": str(path), "exists": path.is_dir()}
    if not out["exists"]:
        return out
    rc, cur = git(path, "branch", "--show-current")
    if rc != 0:
        out["git"] = False
        return out
    out.update(git=True, current_branch=cur)
    rc, st = git(path, "status", "--porcelain")
    out["dirty"] = len([ln for ln in st.splitlines() if ln.strip()]) if rc == 0 else None
    rc, head = git(path, "rev-parse", "--short", "HEAD")
    out["head"] = head if rc == 0 else None
    if branch:
        out["branch"] = branch
        out["on_branch"] = cur == branch
        out["branch_exists"] = git(path, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")[0] == 0
        if not out["branch_exists"]:
            rb = remote_branch(path, branch)
            if rb:
                out["remote_branch"] = rb  # pushed from another machine: `workspace <epic> --create` continues it
        if base and out["branch_exists"]:
            out["base"] = base
            rc, counts = git(path, "rev-list", "--left-right", "--count", f"{base}...{branch}")
            if rc == 0 and len(counts.split()) == 2:
                behind, ahead = (int(x) for x in counts.split())
                out.update(ahead=ahead, behind=behind)
            out["merged"] = git(path, "merge-base", "--is-ancestor", branch, base)[0] == 0
    return out


def run_gate(command: str, cwd: Path, timeout: float | None = None) -> dict:
    """Run one repository's gate command; the result keeps only the tail of the output."""
    started = time.time()
    try:
        res = subprocess.run(command, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        rc, output = res.returncode, (res.stdout + res.stderr)
    except subprocess.TimeoutExpired as exc:
        rc, output = 124, f"timed out after {exc.timeout}s"
    except OSError as exc:
        rc, output = 127, str(exc)
    return {
        "command": command,
        "cwd": str(cwd),
        "ok": rc == 0,
        "exit": rc,
        "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_s": round(time.time() - started, 1),
        "tail": "\n".join(output.strip().splitlines()[-30:]),
    }


def rel_or_abs(path: str, root: Path) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path
