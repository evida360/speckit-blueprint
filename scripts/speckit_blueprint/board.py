"""The board: which worktree builds which epic, the sessions working in the hub, gate results.

It lives in the hub's git directory (`<git-common-dir>/speckit-blueprint/`), so every worktree of the hub
sees the same board and nothing of it is ever committed. Outside git it falls back to
`.specify/blueprint-state/`.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import time
from pathlib import Path

from .repos import git_common_dir

DIRNAME = "speckit-blueprint"
LIVE_MINUTES = 15


def state_dir(root: Path) -> Path:
    common = git_common_dir(root)
    return (common / DIRNAME) if common is not None else (root / ".specify" / "blueprint-state")


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _empty() -> dict:
    return {"epics": {}, "sessions": {}, "gates": {}, "reviewers": {}, "builds": {}, "builders": {}, "build_pending": {}}


def load(root: Path) -> dict:
    p = state_dir(root) / "board.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    for k, v in _empty().items():
        data.setdefault(k, v)
    return data


@contextlib.contextmanager
def file_lock(lock: Path, wait_s: float = 5, stale_s: float = 30, what: str = "the board"):
    """An exclusive lock file: several worktrees, sessions and subagents may write at the same time."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + wait_s
    fd = None
    while fd is None:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_s:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise SystemExit(f"error: {what} is locked ({lock}); remove the file if no blueprint command is running")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def update(root: Path, fn) -> dict:
    """Read, change and write the board under a lock file (worktrees may write at the same time)."""
    d = state_dir(root)
    with file_lock(d / "board.lock"):
        board = load(root)
        fn(board)
        tmp = d / f"board.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(board, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, d / "board.json")
        return board


def epic_key(bid: str, eid: str) -> str:
    return f"{bid}:{eid}"


def same_path(a: str | Path, b: str | Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


def elsewhere(board: dict, root: Path) -> dict:
    """Claims held by other worktrees that still exist and whose epic is not done."""
    out = {}
    for key, e in board.get("epics", {}).items():
        wt = e.get("worktree")
        if not wt or same_path(wt, root) or not Path(wt).is_dir() or e.get("state") == "done":
            continue
        out[key] = e
    return out


def claim(root: Path, key: str, entry: dict, take: bool = False) -> dict | None:
    """Claim an epic for this worktree. Returns the other worktree's entry when it holds the claim."""
    conflict: dict = {}

    def fn(board: dict) -> None:
        cur = board["epics"].get(key)
        if cur and not take and cur.get("worktree") and not same_path(cur["worktree"], root) \
                and Path(cur["worktree"]).is_dir() and cur.get("state") != "done":
            conflict.update(cur)
            return
        repos = {name: {**((cur or {}).get("repositories") or {}).get(name, {}), **fields}
                 for name, fields in (entry.get("repositories") or {}).items()}
        board["epics"][key] = {**(cur or {}), **entry, **({"repositories": repos} if repos else {}), "worktree": str(root.resolve()),
                               "since": (cur or {}).get("since") if cur and same_path(cur.get("worktree", ""), root) else now_iso(),
                               "updated": now_iso()}

    update(root, fn)
    return conflict or None


def refresh(root: Path, rows: dict) -> None:
    """Record this worktree's view of the epics it holds (state, tasks, review). `rows`: key -> fields."""
    if not rows:
        return

    def fn(board: dict) -> None:
        for key, fields in rows.items():
            cur = board["epics"].get(key)
            if cur and same_path(cur.get("worktree", ""), root):
                cur.update(fields, updated=now_iso())

    try:
        update(root, fn)
    except SystemExit:
        pass  # a locked board must not break status


def register_session(root: Path, session_id: str, info: dict) -> None:
    if not session_id:
        return

    def fn(board: dict) -> None:
        cur = board["sessions"].get(session_id, {})
        cur.update({k: v for k, v in info.items() if v is not None}, last_seen=now_iso())
        board["sessions"][session_id] = cur
        cutoff = time.time() - 30 * 86400
        for sid in [s for s, v in board["sessions"].items() if _ts(v.get("last_seen")) < cutoff]:
            del board["sessions"][sid]

    try:
        update(root, fn)
    except SystemExit:
        pass


def _ts(iso: str | None) -> float:
    try:
        return _dt.datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def live(session: dict, minutes: int = LIVE_MINUTES) -> bool:
    """A session is live while its transcript keeps being written."""
    t = session.get("transcript")
    try:
        return bool(t) and time.time() - Path(t).stat().st_mtime < minutes * 60
    except OSError:
        return False


def record_gate(root: Path, key: str, repo: str, result: dict) -> None:
    def fn(board: dict) -> None:
        board["gates"].setdefault(key, {})[repo] = {k: result[k] for k in ("ok", "exit", "at", "duration_s", "command")}

    update(root, fn)


def register_reviewer(root: Path, agent_id: str, entry: dict) -> dict | None:
    """Record the subagent that runs a review pass; returns the earlier entry for the same agent, if any.
    Entries older than two days are dropped."""
    earlier: dict = {}

    def change(board: dict) -> None:
        cutoff = time.time() - 2 * 86400
        rv = {k: v for k, v in board.get("reviewers", {}).items() if v.get("at", 0) >= cutoff}
        if agent_id in rv:
            earlier.update(rv[agent_id])
        rv[agent_id] = {**entry, "at": time.time()} if not earlier else rv[agent_id]
        board["reviewers"] = rv

    update(root, change)
    return earlier or None


def reviewer(root: Path, agent_id: str | None) -> dict | None:
    if not agent_id:
        return None
    return load(root).get("reviewers", {}).get(agent_id)
