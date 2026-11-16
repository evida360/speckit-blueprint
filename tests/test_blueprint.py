"""Tests for scripts/blueprint.py against tests/fixtures/mini-blueprint.md.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "blueprint.py"
FIXTURE = REPO / "tests" / "fixtures" / "mini-blueprint.md"
sys.path.insert(0, str(REPO / "scripts"))
sys.dont_write_bytecode = True

EPIC_02 = """## Epic 02 — Daily totals

**Goal** — Reza sees the day's total.
**Personas served** P2
**Depends on** E00
**Preconditions for the session** — E00 done.
**Flows and screens implemented** SCR-02.

### E02-S01 — See the day's total

**Story** — As **P2 (Reza)**, I want the day's total, so that I can close the day.
**Acceptance Criteria**
- AC-1 Given two sales, When SCR-02 opens, Then CP-SCR02-1 shows their sum.
**Edge cases** — none.
**Tests**
- Unit: `src/total.test.ts` — covers AC-1.
**Tasks**
- E02-S01-T1 Total query.
**Story DoD** — Default.

**Epic-level tests** — Totals end to end.
**Definition of Done**
- All stories DONE.
**Session handoff checklist** — summary.

"""

EPIC_03 = """## Epic 03 — Monthly export

**Goal** — Reza exports the month.
**Personas served** P2
**Depends on** E00
**Preconditions for the session** — E00 done.

### E03-S01 — Export the month

**Story** — As **P2 (Reza)**, I want the month's sales as a file, so that I can file the taxes.
**Acceptance Criteria**
- AC-1 Given a month of sales, When the export runs, Then a CSV lists them.
**Edge cases** — none.
**Tests**
- Unit: `src/export.test.ts` — covers AC-1.
**Tasks**
- E03-S01-T1 Export query.
**Story DoD** — Default.

**Epic-level tests** — Export end to end.
**Definition of Done**
- All stories DONE.
**Session handoff checklist** — summary.

"""

# §16.1 of the template: the rows need not be in Order, and E03 is left out
EPIC_ORDER_TABLE = (
    "| Order | Epic | Goal | Depends on | Stories | Size | Why now, not earlier/later |\n"
    "|---|---|---|---|---|---|---|\n"
    "| 1 | E00 Foundation | Repo and `check` | — | 1 | S | Everything builds on it |\n"
    "| 3 | E01 Record sales | Sara records a sale | E00 | 2 | M | After the totals Reza asked for |\n"
    "| 2 | **E02** — Daily totals | Reza sees the day's total | E00 | 1 | S | Reza closes the day first |\n"
)

REPO_TABLE = (
    "**Repositories**\n\n"
    "| Repository | Order | Role | Base | Branch | Contracts |\n|---|---|---|---|---|---|\n"
    "| `core` | 1 | Provides the sale API | | | provides API-01 |\n"
    "| `app` | 2 | Consumes it | | | consumes API-01 |\n\n"
    "Both repositories deploy together.\n"
)

# E01's repository fields as schema 1 (0.4) wrote them
SCHEMA_1_REPOS = (
    "**Target repositories**\n\n| Repository | Role |\n|---|---|\n| `core` | Provides the sale API |\n| `app` | Consumes it |\n\n"
    "**Repositories** — Order: `core` first, then `app`. `core`: base `master`, working branch `feat/sales`, "
    "contracts: provides API-01. `app`: base `main`, branch `feat/sales-ui`, contracts: none.\n"
)

DONE_REVIEW = {
    "E00": "Status: DONE\n# Handoff — E00\nAC evidence: E00-S01 AC-1 src/smoke.test.ts::passes\n",
    "E01": "Status: DONE\n# Handoff — E01\nAC evidence: E01-S01 AC-1 test/sale.int.test.ts::stores · AC-2 test/sale.int.test.ts::rejects\n"
           "E01-S02 AC-1 src/undo.test.ts::deletes\n",
    "E02": "Status: DONE\n# Handoff — E02\nAC evidence: E02-S01 AC-1 src/total.test.ts::sums\n",
}


def git(path, *args):
    res = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise AssertionError(f"git {args}: {res.stderr}")
    return res.stdout.strip()


def git_init(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "t")
    (path / "README.md").write_text("x\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")


class Transcript:
    """A synthetic Claude Code transcript with the fields the metrics parser reads."""

    def __init__(self, root, session_id, start_offset=-7200, model="claude-sonnet-5-5", effort="medium"):
        import time as _time

        self.root, self.sid, self.model, self.effort = Path(root), session_id, model, effort
        self.t0 = _time.time() + start_offset
        self.dir = self.root / ".transcripts"
        self.lines, self.n, self.sessions, self.last = [], 0, [], self.t0

    def ts(self, at):
        import datetime as _dt

        self.last = max(self.last, self.t0 + at)
        return _dt.datetime.fromtimestamp(self.t0 + at, _dt.timezone.utc).isoformat().replace("+00:00", "Z")

    def base(self, at):
        return {"timestamp": self.ts(at), "sessionId": self.sid, "cwd": str(self.root), "version": "2.1.306"}

    def prompt(self, at, text):
        self.lines.append({**self.base(at), "type": "user", "message": {"role": "user", "content": text}})

    def assistant(self, at, content, effort=None, model=None):
        self.lines.append({**self.base(at), "type": "assistant", "effort": effort or self.effort,
                           "message": {"model": model or self.model, "content": content,
                                       "usage": {"input_tokens": 2, "output_tokens": 100, "cache_read_input_tokens": 20000,
                                                 "cache_creation_input_tokens": 500}}})

    def tool(self, at, name, inp, done_at, agent=None, output="ok", error=False):
        tid = f"toolu_{self.sid}_{self.n}"
        self.n += 1
        self.assistant(at, [{"type": "tool_use", "id": tid, "name": name, "input": inp}])
        if done_at is None:
            return
        res = {"type": "tool_result", "tool_use_id": tid, "content": output, **({"is_error": True} if error else {})}
        result = {**self.base(done_at), "type": "user", "message": {"role": "user", "content": [res]}}
        if agent:
            agent_id, agent_type, level = agent
            result["toolUseResult"] = {"agentId": agent_id, "agentType": agent_type, "totalDurationMs": int((done_at - at) * 1000), "status": "completed"}
            sub = self.dir / self.sid / "subagents"
            sub.mkdir(parents=True, exist_ok=True)
            entry = {**self.base(at + 1), "type": "assistant", "effort": level, "message": {"model": self.model, "content": [{"type": "text", "text": "ok"}],
                     "usage": {"input_tokens": 10, "output_tokens": 4000, "cache_read_input_tokens": 50000, "cache_creation_input_tokens": 9000}}}
            (sub / f"agent-{agent_id}.jsonl").write_text(json.dumps(entry) + "\n")
        self.lines.append(result)

    def say(self, at, text="done"):
        self.assistant(at, [{"type": "text", "text": text}])

    def cost(self, usd):
        self.lines.append({"type": "cost-state", "sessionId": self.sid, "totalCostUSD": usd})

    def write(self, live=False):
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{self.sid}.jsonl"
        path.write_text("".join(json.dumps(x) + "\n" for x in self.lines))
        if not live:
            os.utime(path, (self.last, self.last))
        self.sessions.append({"id": self.sid, "path": str(path)})
        return path


class ProjectTest(unittest.TestCase):
    root_name = None  # a subfolder name to put the project next to sibling repositories

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.tmp = self.base / self.root_name if self.root_name else self.base
        (self.tmp / ".specify" / "memory").mkdir(parents=True)
        (self.tmp / "docs").mkdir()
        shutil.copy(FIXTURE, self.tmp / "docs" / "BLUEPRINT.md")
        self.cfg_dir = self.tmp / ".specify" / "extensions" / "blueprint"
        self.env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_EFFORT_LEVEL", "SPECKIT_BLUEPRINT")}

    def tearDown(self):
        shutil.rmtree(self.base)

    def configure(self, text):
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        (self.cfg_dir / "blueprint-config.yml").write_text(text, encoding="utf-8")

    def run_cli(self, *args, stdin="", check=True, cwd=None, env=None):
        res = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=cwd or self.tmp,
            input=stdin,
            capture_output=True,
            text=True,
            env={**self.env, **(env or {})},
        )
        if check and res.returncode != 0:
            self.fail(f"{args} exited {res.returncode}: {res.stderr}")
        return res

    def json_cli(self, *args, **kw):
        return json.loads(self.run_cli(*args, **kw).stdout)

    def feature(self, epic="E01"):
        return self.tmp / "specs" / {"E00": "001-e00-foundation", "E01": "002-e01-record-sales", "E02": "003-e02-daily-totals"}[epic]

    def blueprint(self):
        return self.tmp / "docs" / "BLUEPRINT.md"

    def edit_blueprint(self, old, new):
        bp = self.blueprint()
        text = bp.read_text(encoding="utf-8")
        self.assertIn(old, text)
        bp.write_text(text.replace(old, new, 1), encoding="utf-8")

    def add_epic_02(self):
        self.edit_blueprint("## 18. Session protocol", EPIC_02 + "## 18. Session protocol")

    def add_epic_03(self):
        self.edit_blueprint("## 18. Session protocol", EPIC_03 + "## 18. Session protocol")

    def finish(self, epic):
        (self.feature(epic) / "tasks.md").write_text("- [x] T001 [US1] done\n")
        (self.feature(epic) / "review.md").write_text(DONE_REVIEW[epic])

    def state(self, *args):
        return self.json_cli("status", *args)


class CompileTest(ProjectTest):
    def test_compile_resolves_every_reference(self):
        out = self.json_cli("compile", "E01")
        self.assertEqual(out["feature_directory"], "specs/002-e01-record-sales")
        self.assertEqual(out["unresolved_ids"], [])
        f = self.feature()
        spec = (f / "spec.md").read_text(encoding="utf-8")
        # stories become prioritised user stories, tasks move to the plan
        self.assertIn("### User Story 1 - Enter and save a sale (Priority: P1)", spec)
        self.assertIn("### User Story 2 - Undo the last sale (Priority: P2)", spec)
        self.assertNotIn("E01-S01-T1", spec)
        # ranges expand: SCR-01 to SCR-02 brings both copy tables; EC-INPUT-01 to 02 both rows
        self.assertIn("CP-SCR02-1", spec)
        self.assertIn("EC-INPUT-01", spec)
        self.assertIn("EC-INPUT-02 (new)", spec)
        self.assertIn("EC-NET-01", spec)
        # personas, flows, open questions resolved
        self.assertIn("P1 — Sara, shop owner", spec)
        self.assertIn("F-01 — Record a sale", spec)
        self.assertIn("OQ-01", spec)
        self.assertNotIn("Reza", spec)  # P2 is not referenced by this epic
        self.assertIn("**Definition of Done**", spec)

        plan = (f / "plan.md").read_text(encoding="utf-8")
        self.assertIn("E01-S01-T2 API-01 handler.", plan)
        self.assertIn("### US2 — E01-S02 Undo the last sale", plan)
        self.assertIn("12.2 Module map", plan)
        self.assertIn("[VERIFY-AT-BUILD]", plan)
        self.assertIn("R-01", plan)
        self.assertIn("`DR-E01-1`", plan)  # decisions taken while building are numbered within the epic

        dm = (f / "data-model.md").read_text(encoding="utf-8")
        self.assertIn("**ENT-Sale**", dm)
        self.assertIn("amount_rial", dm)
        self.assertIn("integer rials", dm)  # §12.4 conventions
        self.assertNotIn("ENT-Shop", dm)

        api = (f / "contracts" / "api.md").read_text(encoding="utf-8")
        self.assertIn("API-01", api)
        self.assertNotIn("API-02", api)
        self.assertIn("error envelope", api)
        self.assertIn("Kavenegar", (f / "contracts" / "integrations.md").read_text(encoding="utf-8"))
        self.assertIn("AD-1", (f / "research.md").read_text(encoding="utf-8"))

        quick = (f / "quickstart.md").read_text(encoding="utf-8")
        self.assertIn("Record and undo end to end", quick)

        self.assertEqual(
            json.loads((self.tmp / ".specify" / "feature.json").read_text())["feature_directory"],
            "specs/002-e01-record-sales",
        )
        trace = json.loads((f / ".blueprint.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["blueprint_id"], "default")
        self.assertEqual(trace["depends"], ["E00"])
        self.assertEqual(trace["acs"], {"E01-S01": ["AC-1", "AC-2"], "E01-S02": ["AC-1"]})
        self.assertEqual(trace["branch"], "002-e01-record-sales")

    def test_second_level_copy_from_flow(self):
        self.json_cli("compile", "E00")
        spec = (self.feature("E00") / "spec.md").read_text(encoding="utf-8")
        self.assertIn("DR-01", (self.feature("E00") / "research.md").read_text(encoding="utf-8"))
        self.assertIn("FF-01", (self.feature("E00") / "research.md").read_text(encoding="utf-8"))
        self.assertIn("Manual", (self.feature("E00") / "quickstart.md").read_text(encoding="utf-8"))
        self.assertNotIn("data-model.md", json.loads((self.feature("E00") / ".blueprint.json").read_text())["files"])
        self.assertIn("Sara", spec)

    def test_recompile_refuses_over_finished_tasks(self):
        self.json_cli("compile", "E01")
        (self.feature() / "tasks.md").write_text("- [X] T001 done\n- [ ] T002 todo\n")
        res = self.run_cli("compile", "E01", check=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("completed tasks", res.stderr)
        self.json_cli("compile", "E01", "--force")

    def test_refs_and_unresolved(self):
        self.assertEqual(self.run_cli("refs").returncode, 0)
        self.edit_blueprint("ENT-Sale migration", "ENT-Refund migration")
        res = self.run_cli("refs", check=False)
        self.assertEqual(res.returncode, 1)
        self.assertIn("ENT-Refund", json.loads(res.stdout)["E01"]["unresolved"])

    def test_activate(self):
        self.json_cli("compile", "E00")
        self.json_cli("compile", "E01")
        self.assertEqual(self.json_cli("activate", "E00")["feature_directory"], "specs/001-e00-foundation")
        self.assertEqual(json.loads((self.tmp / ".specify" / "feature.json").read_text())["feature_directory"], "specs/001-e00-foundation")
        self.assertNotEqual(self.run_cli("activate", "E07", check=False).returncode, 0)


class StatusTest(ProjectTest):
    def test_walks_the_lifecycle(self):
        st = self.state()
        self.assertEqual((st["next"]["command"], st["next"]["run"].split()[-1]), ("bootstrap", "bootstrap"))
        self.json_cli("bootstrap")
        st = self.state()
        self.assertEqual(st["constitution"], "compiled")
        self.assertEqual((st["next"]["command"], st["next"]["epic"]), ("speckit-blueprint-compile", "E00"))

        self.json_cli("compile", "E00")
        self.assertEqual(self.state()["next"]["command"], "speckit-tasks")
        tasks = self.feature("E00") / "tasks.md"
        tasks.write_text("- [ ] T001 [US1] E00-S01-T1 init\n- [ ] T002 [US1] test\n")
        self.assertEqual(self.state()["epics"][0]["state"], "tasked")
        tasks.write_text("- [x] T001 [US1] E00-S01-T1 init\n- [ ] T002 [US1] test\n")
        st = self.state()
        self.assertEqual((st["epics"][0]["state"], st["next"]["command"]), ("in-progress", "speckit-implement"))
        tasks.write_text("- [x] T001 a\n- [X] T002 b\n")
        st = self.state()
        self.assertEqual((st["next"]["command"], st["next"]["effort"]), ("speckit-blueprint-review", "max"))
        (self.feature("E00") / "review.md").write_text("Status: IN PROGRESS\n")
        self.assertEqual(self.state()["epics"][0]["state"], "implemented")
        # DONE without evidence for every AC is not done
        (self.feature("E00") / "review.md").write_text("Status: DONE\n# Handoff\n")
        st = self.state()
        self.assertEqual((st["epics"][0]["state"], st["epics"][0]["review_missing"]), ("implemented", ["E00-S01 AC-1"]))
        self.assertIn("no evidence for E00-S01 AC-1", st["next"]["why"])
        (self.feature("E00") / "review.md").write_text(DONE_REVIEW["E00"])
        st = self.state()
        self.assertEqual(st["epics"][0]["state"], "done")
        self.assertEqual((st["next"]["command"], st["next"]["epic"]), ("speckit-blueprint-compile", "E01"))
        self.assertNotIn("effort", st["next"])  # compile runs at the session's level

    def test_review_evidence_spans_lines(self):
        self.json_cli("bootstrap")
        self.json_cli("compile", "E01")
        (self.feature() / "tasks.md").write_text("- [x] T001 a\n")
        review = self.feature() / "review.md"
        review.write_text("Status: DONE\nAC evidence:\n- E01-S01\n  - AC-1 sale.int::stores\n  - AC-2 sale.int::rejects\n- E01-S02\n  - AC-1 undo::deletes\n")
        self.assertEqual(next(e for e in self.state()["epics"] if e["epic"] == "E01")["state"], "done")
        review.write_text("Status: DONE\nAC evidence: E01-S01 AC-1 sale.int::stores\nE01-S02 AC-1 undo::deletes\n")
        e01 = next(e for e in self.state()["epics"] if e["epic"] == "E01")
        self.assertEqual((e01["state"], e01["review_missing"]), ("implemented", ["E01-S01 AC-2"]))

    def test_review_evidence_ranges(self):
        self.json_cli("bootstrap")
        self.json_cli("compile", "E01")
        (self.feature() / "tasks.md").write_text("- [x] T001 a\n")
        review = self.feature() / "review.md"
        review.write_text("Status: DONE\nAC evidence: E01-S01 AC-1 to 2 sale.int\nE01-S02 AC-1 undo::deletes\n")
        self.assertEqual(next(e for e in self.state()["epics"] if e["epic"] == "E01")["state"], "done")
        # a range counts in its own story's evidence only
        review.write_text("Status: DONE\nAC evidence: E01-S01 AC-1 sale.int::stores\nE01-S02 AC-1/2 undo::deletes\n")
        e01 = next(e for e in self.state()["epics"] if e["epic"] == "E01")
        self.assertEqual((e01["state"], e01["review_missing"]), ("implemented", ["E01-S01 AC-2"]))

    def test_review_evidence_range_forms(self):
        from speckit_blueprint.status import review_check

        review = self.tmp / "review.md"
        acs = {"E05-S01": [f"AC-{n}" for n in range(1, 13)]}

        def missing(evidence):
            review.write_text(f"Status: DONE\nAC evidence: E05-S01 {evidence}\n", encoding="utf-8")
            return [m.split()[1] for m in review_check(review, acs)[1]]

        for ev in ("AC-1 to AC-12 x_test.dart", "AC-1 to 12 x_test.dart", "AC-1 through AC-6 a · AC-7 through 12 b",
                   "AC-1–AC-4 a · AC-5–8 b · AC-9…10 c · AC-11...12 d", "AC-1 to 10 a\n  AC-11/12 n/a",
                   "AC-1/2/3 a · AC-4/AC-5 b · AC-6 to AC-12 c"):
            self.assertEqual(missing(ev), [], ev)
        self.assertEqual(missing("AC-1 to AC-5 a · AC-7/8 b"), ["AC-6", "AC-9", "AC-10", "AC-11", "AC-12"])
        self.assertEqual(missing("AC-1 — 12 cases a · AC-2 to AC-11 b"), ["AC-12"])  # an em dash is no range
        # a number ends where its digits do: AC-10 to AC-12 never name AC-1, nor AC-1 AC-12
        self.assertEqual(missing(" · ".join(f"AC-{n} t" for n in range(2, 13))), ["AC-1"])
        self.assertEqual(missing("AC-2 to AC-12 a"), ["AC-1"])
        self.assertEqual(missing("AC-1/2 a · AC-3 to 11 b"), ["AC-12"])
        self.assertEqual(missing("AC-1–2 a · AC-3…11 b"), ["AC-12"])

    def test_dependencies_decide_the_next_epic(self):
        self.add_epic_02()
        self.json_cli("bootstrap")
        st = self.state()
        e02 = next(e for e in st["epics"] if e["epic"] == "E02")
        self.assertEqual((e02["depends"], e02["blocked_by"]), (["E00"], ["E00"]))
        self.json_cli("compile", "E00")
        self.finish("E00")
        st = self.state()
        self.assertNotIn("blocked_by", next(e for e in st["epics"] if e["epic"] == "E02"))
        self.assertEqual(st["next"]["epic"], "E01")
        self.assertEqual(st["epic_order"], {"source": "blueprint", "epics": ["E00", "E01", "E02"]})

    def test_next_follows_the_epic_order_table(self):
        self.add_epic_02()
        self.add_epic_03()
        self.edit_blueprint("E00, then E01.\n", EPIC_ORDER_TABLE)
        self.json_cli("bootstrap")
        self.json_cli("compile", "E00")
        self.finish("E00")
        st = self.state()
        # by the Order number, not the row; the epic the table leaves out comes last
        self.assertEqual(st["epic_order"], {"source": "table", "epics": ["E00", "E02", "E01", "E03"]})
        self.assertEqual((st["next"]["command"], st["next"]["epic"]), ("speckit-blueprint-compile", "E02"))
        self.assertIn("Current: E02 Daily totals — not-compiled.", self.run_cli("status", "--brief").stdout)

    def test_an_epic_waited_for_comes_first(self):
        """An epic added at the end that an earlier one depends on is next, not the first epic that is free."""
        self.add_epic_02()
        self.add_epic_03()
        self.edit_blueprint("**Depends on** E00\n**Preconditions for the session** — E00 done. SMS",
                            "**Depends on** E00, E03\n**Preconditions for the session** — E00 done. SMS")
        self.json_cli("bootstrap")
        self.json_cli("compile", "E00")
        self.finish("E00")
        st = self.state()
        self.assertEqual(st["epic_order"]["epics"], ["E00", "E03", "E01", "E02"])
        self.assertEqual(st["next"]["epic"], "E03")
        self.assertEqual(st["next"]["why"], "Next epic: E03 — Monthly export. E01 waits for it.")
        # the table's order holds, and an epic waited for still comes before the one waiting
        self.edit_blueprint("E00, then E01.\n", EPIC_ORDER_TABLE.replace("| 3 | E01", "| 1 | E01"))
        self.assertEqual(self.state()["epic_order"], {"source": "table", "epics": ["E00", "E03", "E01", "E02"]})

    def test_an_epic_compiled_here_is_finished_first(self):
        self.add_epic_02()
        self.json_cli("bootstrap")
        self.json_cli("compile", "E00")
        self.finish("E00")
        self.json_cli("compile", "E02")
        (self.feature("E02") / "tasks.md").write_text("- [x] T001 [US1] total query\n- [ ] T002 [US1] screen\n")
        st = self.state()
        self.assertEqual(st["epic_order"]["epics"], ["E00", "E01", "E02"])
        self.assertEqual((st["next"]["command"], st["next"]["epic"]), ("speckit-implement", "E02"))
        self.assertIn("Current: E02 Daily totals — in-progress, tasks 1/2.", self.run_cli("status", "--brief").stdout)

    def test_constitution_outdated(self):
        self.json_cli("bootstrap")
        self.edit_blueprint("No ORMs.", "No ORMs. No GraphQL.")
        st = self.state()
        self.assertEqual((st["constitution"], st["next"]["command"]), ("outdated", "bootstrap"))
        self.json_cli("bootstrap")
        self.assertEqual(self.state()["constitution"], "compiled")

    def test_stale_after_blueprint_change(self):
        self.json_cli("bootstrap")
        self.json_cli("compile", "E01")
        self.edit_blueprint("| CP-SCR01-2 | Button | ثبت |", "| CP-SCR01-2 | Button | ذخیره |")
        stale = self.json_cli("stale")
        self.assertEqual([s["epic"] for s in stale], ["E01"])
        self.assertIn("SCR-01", stale[0]["changed"])
        e01 = next(e for e in self.state()["epics"] if e["epic"] == "E01")
        self.assertEqual(e01["state"], "stale")

    def test_accept_keeps_a_built_epic(self):
        self.json_cli("bootstrap")
        self.json_cli("compile", "E01")
        self.finish("E01")
        self.edit_blueprint("| CP-SCR01-2 | Button | ثبت |", "| CP-SCR01-2 | Button | ذخیره |")
        self.assertEqual([s["epic"] for s in self.json_cli("stale")], ["E01"])
        self.assertNotEqual(self.run_cli("accept", "E01", check=False).returncode, 0)  # a note is required
        out = self.json_cli("accept", "E01", "--note", "covered by E02 (A-E02-1)")
        self.assertIn("SCR-01", out["accepted"])
        self.assertEqual(self.json_cli("stale"), [])
        e01 = next(e for e in self.state()["epics"] if e["epic"] == "E01")
        self.assertNotIn("changed_since_compile", e01)
        trace = json.loads((self.feature() / ".blueprint.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["accepted"][0]["note"], "covered by E02 (A-E02-1)")
        self.assertIn("ثبت", (self.feature() / "spec.md").read_text(encoding="utf-8"))  # compiled files untouched

    def test_no_blueprint(self):
        self.blueprint().unlink()
        st = self.state()
        self.assertEqual((st["state"], st["next"]["command"], st["next"]["effort"]), ("no-blueprint", "speckit-blueprint-discover", "max"))


class BootstrapTest(ProjectTest):
    def test_outputs_and_idempotence(self):
        (self.tmp / "CLAUDE.md").write_text("# My rules\n\nKeep me.\n")
        out = self.json_cli("bootstrap")
        self.assertEqual(out["constitution"]["missing_sections"], [])
        c = (self.tmp / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8")
        self.assertIn("# Tally Constitution", c)
        self.assertIn("No ORMs.", c)
        self.assertIn("**Version**: 1.0.0", c)
        self.assertIn("blueprint-id default", c.split("\n", 1)[0])
        claude = (self.tmp / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("Keep me.", claude)
        self.assertEqual(claude.count("speckit-blueprint:start id=default"), 1)
        self.assertNotIn("AGENTS.md", claude)  # the block names no file the project may not have
        self.assertIn("Every AC has a test", (self.tmp / "docs" / "REVIEW-CHECKLIST.md").read_text(encoding="utf-8"))
        self.assertIn("| DR-01 | Integer rials | blueprint | accepted |", (self.tmp / "docs" / "DECISIONS.md").read_text(encoding="utf-8"))

        # unchanged blueprint → same version, one managed block
        self.json_cli("bootstrap")
        c2 = (self.tmp / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8")
        self.assertIn("**Version**: 1.0.0", c2)
        self.assertEqual((self.tmp / "CLAUDE.md").read_text(encoding="utf-8").count("speckit-blueprint:start"), 1)

        # changed constitution section → patch bump
        self.edit_blueprint("No ORMs.", "No ORMs. No GraphQL.")
        self.json_cli("bootstrap")
        self.assertIn("**Version**: 1.0.1", (self.tmp / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8"))

    def test_lint_gate(self):
        self.edit_blueprint("ENT-Sale migration", "ENT-Refund migration")
        res = self.run_cli("bootstrap", check=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("E01: unresolved IDs ENT-Refund", res.stderr)
        self.assertFalse((self.tmp / ".specify" / "memory" / "constitution.md").exists())
        self.json_cli("bootstrap", "--skip-lint")


class HookTest(ProjectTest):
    def hook(self, payload, *args, env=None):
        return self.run_cli("hook", *args, stdin=json.dumps(payload), check=False, env=env)

    def guard(self, tool, path, **extra):
        return self.hook({"tool_name": tool, "tool_input": {"file_path": str(self.tmp / path), **extra}}, "guard")

    def test_guard(self):
        self.json_cli("bootstrap")
        self.json_cli("compile", "E01")
        f = "specs/002-e01-record-sales"
        self.assertEqual(self.guard("Edit", f"{f}/spec.md").returncode, 2)
        self.assertEqual(self.guard("Write", f"{f}/contracts/api.md").returncode, 2)
        self.assertEqual(self.guard("Edit", ".specify/memory/constitution.md").returncode, 2)
        self.assertEqual(self.guard("Edit", f"{f}/tasks.md").returncode, 0)
        self.assertEqual(self.guard("Write", f"{f}/review.md").returncode, 0)
        self.assertEqual(self.guard("Edit", "docs/BLUEPRINT.md").returncode, 0)
        self.assertEqual(self.guard("Read", f"{f}/spec.md").returncode, 0)
        self.configure("guard: false\n")
        self.assertEqual(self.guard("Edit", f"{f}/spec.md").returncode, 0)

    def test_dispatch_by_event_name(self):
        out = self.hook({"hook_event_name": "SessionStart", "session_id": "s1"}).stdout
        self.assertIn("Tally: 0/2 epics done", out)
        self.assertIn("Next: `blueprint.py bootstrap`", out)
        self.json_cli("bootstrap")
        self.json_cli("compile", "E01")
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": str(self.feature() / "spec.md")}}
        self.assertEqual(self.hook(payload).returncode, 2)
        self.assertEqual(self.hook({"hook_event_name": "Stop", "session_id": "s1"}).returncode, 0)
        self.assertEqual(self.hook({"hook_event_name": "Nope"}).returncode, 0)

    def test_effort_config(self):
        st = self.state()
        self.assertEqual(st["effort"], {"discover": "max", "amend": "max", "review": "max"})
        self.configure("effort:\n  review: XHigh\n  amend: session\n")
        self.assertEqual(self.state()["effort"], {"discover": "max", "review": "xhigh"})
        for bad, words in (("effort:\n  implement: max\n", "cannot run at a guaranteed level"),
                           ("effort:\n  review: huge\n", "not one of"), ("effort: max\n", "must map")):
            self.configure(bad)
            res = self.run_cli("status", "--json", check=False)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn(words, res.stderr)

    def test_require_effort(self):
        cmd = "python3 .specify/extensions/blueprint/scripts/blueprint.py require-effort review"

        def check(level, agent="general-purpose", env=None):
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}}
            if level:
                payload["effort"] = {"level": level}
            if agent:
                payload["agent_type"] = agent
            return self.hook(payload, env=env)

        res = check("medium")
        self.assertEqual(res.returncode, 2)
        self.assertIn("needs effort max (review)", res.stderr)
        self.assertIn("general-purpose subagent runs at medium", res.stderr)
        self.assertEqual(check("max").returncode, 0)
        self.assertEqual(check(None).returncode, 0)  # older Claude Code: nothing to check, nothing blocked
        res = check("high", env={"CLAUDE_CODE_EFFORT_LEVEL": "high"})
        self.assertIn("CLAUDE_CODE_EFFORT_LEVEL=high", res.stderr)
        res = check("low", agent=None)
        self.assertIn("/effort max", res.stderr)
        self.assertIn("runs at effort max", self.run_cli("require-effort", "review").stdout)
        self.configure("effort:\n  review: session\n")
        self.assertEqual(check("low").returncode, 0)

    def test_effort_guards_blueprint_and_done_reviews(self):
        self.json_cli("bootstrap")
        self.json_cli("compile", "E01")

        def write(path, content, level):
            return self.hook({"hook_event_name": "PreToolUse", "tool_name": "Write", "effort": {"level": level},
                              "tool_input": {"file_path": str(self.tmp / path), "content": content}}).returncode

        self.assertEqual(write("docs/BLUEPRINT.md", "x", "high"), 2)
        self.assertEqual(write("docs/BLUEPRINT.md", "x", "max"), 0)
        self.assertEqual(write("docs/discovery/brief.md", "x", "medium"), 2)
        review = "specs/002-e01-record-sales/review.md"
        self.assertEqual(write(review, "Status: DONE\n", "medium"), 2)
        self.assertEqual(write(review, "Status: IN PROGRESS\n", "medium"), 0)
        self.assertEqual(write(review, "Status: DONE\n", "max"), 0)
        self.assertEqual(write("src/app.ts", "Status: DONE", "low"), 0)

    def test_one_epic_per_worktree(self):
        self.json_cli("bootstrap")
        self.json_cli("compile", "E00")
        transcript = self.tmp / "t1.jsonl"
        transcript.write_text("{}\n")
        self.hook({"hook_event_name": "SessionStart", "session_id": "session-a", "transcript_path": str(transcript)})
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "session-b",
                   "tool_input": {"command": "python3 .specify/extensions/blueprint/scripts/blueprint.py compile E01"}}
        res = self.hook(payload)
        self.assertEqual(res.returncode, 2)
        self.assertIn("workspace <epic> --create", res.stderr)
        self.assertEqual(self.hook({**payload, "session_id": "session-a"}).returncode, 0)  # the same session may switch
        same = {**payload, "tool_input": {"command": "python3 .specify/extensions/blueprint/scripts/blueprint.py compile E00 --force"}}
        self.assertEqual(self.hook(same).returncode, 0)  # recompiling the active epic switches nothing
        os.utime(transcript, (0, 0))  # session-a is no longer live
        self.assertEqual(self.hook(payload).returncode, 0)


PROJECT_CONSTITUTION = """<!--
Sync Impact Report
- Version change: 2.2.0 → 2.3.0 (principle I reworded)
-->
# Evida Constitution

## Core Principles

### I. One planning home
Every change, however small, is recorded as a Spec Kit task first.

## Governance

Amendments need two approvals.

**Version**: 2.3.0 | **Ratified**: 2025-01-01 | **Last Amended**: 2026-09-01
"""


class AdoptTest(ProjectTest):
    """An existing Spec Kit project: its constitution, checklist and features stay its own."""

    def setUp(self):
        super().setUp()
        self.cpath = self.tmp / ".specify" / "memory" / "constitution.md"
        self.cpath.write_text(PROJECT_CONSTITUTION, encoding="utf-8")
        (self.tmp / "docs" / "REVIEW-CHECKLIST.md").write_text("# Our checklist\n- [ ] Two reviewers\n")
        (self.tmp / "specs" / "001-existing-work").mkdir(parents=True)

    def test_bootstrap_appends_a_managed_block(self):
        out = self.json_cli("bootstrap")
        self.assertEqual(out["constitution"]["mode"], "append")
        self.assertEqual((out["constitution"]["block"], out["constitution"]["version"]), ("added", "2.3.0 → 2.4.0"))
        c = self.cpath.read_text(encoding="utf-8")
        before, rest = c.split("<!-- speckit-blueprint:rules:start id=default -->")
        block, after = rest.split("<!-- speckit-blueprint:rules:end id=default -->")
        # the project's text is kept; only the report gains a line
        self.assertIn("Sync Impact Report\n- Version change: 2.3.0 → 2.4.0", before)
        self.assertIn("Blueprint Rules block added by speckit-blueprint", before)
        self.assertIn("- Version change: 2.2.0 → 2.3.0 (principle I reworded)", before)
        self.assertIn(PROJECT_CONSTITUTION.split("-->\n")[1].split("## Governance")[0], before)
        self.assertTrue(after.lstrip().startswith("## Governance"))
        self.assertRegex(after, r"\*\*Version\*\*: 2\.4\.0 \| \*\*Ratified\*\*: 2025-01-01 \| \*\*Last Amended\*\*: \d{4}-\d{2}-\d{2}")
        self.assertNotIn("2026-09-01", after)
        self.assertIn("### B-I. The blueprint is the source of truth", block)
        self.assertIn("the principles above win", block)
        self.assertIn("No ORMs.", block)
        self.assertEqual((self.tmp / "docs" / "REVIEW-CHECKLIST.md").read_text(), "# Our checklist\n- [ ] Two reviewers\n")
        self.assertEqual(self.state()["constitution"], "appended")
        self.assertEqual(self.state()["next"]["command"], "speckit-blueprint-compile")
        # compile continues the project's numbering
        self.assertEqual(self.json_cli("compile", "E00")["feature_directory"], "specs/002-e00-foundation")

        # idempotent (no second bump); a project edit outside the block survives a re-run
        self.cpath.write_text(c.replace("however small", "however small (agreed 2026)"), encoding="utf-8")
        self.assertEqual(self.json_cli("bootstrap")["constitution"]["version"], "unchanged")
        c2 = self.cpath.read_text(encoding="utf-8")
        self.assertIn("however small (agreed 2026)", c2)
        self.assertIn("**Version**: 2.4.0", c2)
        self.assertEqual(c2.count("speckit-blueprint:rules:start"), 1)
        # a blueprint change rewrites only the block, after status reports it outdated
        self.edit_blueprint("No ORMs.", "No ORMs. No GraphQL.")
        self.assertEqual(self.state()["constitution"], "outdated")
        self.assertEqual(self.json_cli("bootstrap")["constitution"]["block"], "updated")
        c3 = self.cpath.read_text(encoding="utf-8")
        self.assertIn("No GraphQL.", c3)
        self.assertIn("**Version**: 2.5.0", c3)
        self.assertIn("2.4.0 → 2.5.0", c3)
        self.assertIn("Blueprint Rules block updated", c3)

    def test_no_sync_impact_report_is_created(self):
        # Spec Kit removes the report before committing an amendment; bootstrap must not leave a new one behind
        self.cpath.write_text(PROJECT_CONSTITUTION.split("-->\n", 1)[1], encoding="utf-8")
        out = self.json_cli("bootstrap")
        self.assertEqual(out["constitution"]["version"], "2.3.0 → 2.4.0")
        self.assertIn("Blueprint Rules block added", out["constitution"]["amendment"])
        c = self.cpath.read_text(encoding="utf-8")
        self.assertNotIn("Sync Impact Report", c)
        self.assertTrue(c.startswith("# Evida Constitution"))
        self.assertIn("**Version**: 2.4.0", c)

    def test_version_bump_can_be_switched_off(self):
        self.configure("constitution_version_bump: none\n")
        self.assertEqual(self.json_cli("bootstrap")["constitution"]["version"], "unchanged")
        c = self.cpath.read_text(encoding="utf-8")
        self.assertIn("**Version**: 2.3.0 | **Ratified**: 2025-01-01 | **Last Amended**: 2026-09-01", c)
        self.assertIn("speckit-blueprint:rules:start", c)

    def test_guard_protects_only_the_block(self):
        self.json_cli("bootstrap")
        text = self.cpath.read_text(encoding="utf-8")

        def guard(tool, ti):
            ti = {"file_path": str(self.cpath), **ti}
            return self.run_cli("hook", "guard", stdin=json.dumps({"tool_name": tool, "tool_input": ti}), check=False).returncode

        self.assertEqual(guard("Edit", {"old_string": "however small", "new_string": "x"}), 0)
        self.assertEqual(guard("Edit", {"old_string": "No ORMs.", "new_string": "ORMs ok."}), 2)
        self.assertEqual(guard("MultiEdit", {"edits": [{"old_string": "however small"}, {"old_string": "B-II."}]}), 2)
        self.assertEqual(guard("Write", {"content": text.replace("however small", "x")}), 0)
        self.assertEqual(guard("Write", {"content": text.replace("No ORMs.", "")}), 2)
        self.assertEqual(guard("Write", {"content": PROJECT_CONSTITUTION}), 2)

    def test_template_constitution_is_replaced(self):
        self.cpath.write_text("# [PROJECT_NAME] Constitution\n", encoding="utf-8")
        self.assertEqual(self.json_cli("bootstrap")["constitution"]["mode"], "own")
        self.assertEqual(self.state()["constitution"], "compiled")

    def test_session_start_is_silent_without_a_blueprint(self):
        self.blueprint().unlink()
        self.cfg_dir.mkdir(parents=True)
        self.assertEqual(self.run_cli("hook", "session-start", stdin="{}").stdout, "")
        # a fresh `specify init` project still gets the hint
        shutil.rmtree(self.tmp / "specs")
        self.cpath.write_text("# [PROJECT_NAME] Constitution\n", encoding="utf-8")
        self.assertIn("/speckit-blueprint-discover", self.run_cli("hook", "session-start", stdin="{}").stdout)


class WorkspaceTest(ProjectTest):
    """A hub with two code repositories next to it: tables, branches, facts, gates, worktrees, claims."""

    root_name = "hub"

    def setUp(self):
        super().setUp()
        self.add_epic_02()
        self.edit_blueprint("**Risks specific to this epic** R-01.\n", "**Risks specific to this epic** R-01.\n" + REPO_TABLE)
        self.configure("repositories:\n  hub: {path: .}\n  core:\n    path: ../core\n    gate: \"true\"\n"
                       "  app: {path: ../app, gate: \"exit 3\", branch_prefix: feat/}\n")
        git_init(self.base / "core")
        git_init(self.base / "app")
        (self.tmp / ".gitignore").write_text(".specify/feature.json\n")
        git(self.tmp, "init", "-q", "-b", "main")
        git(self.tmp, "config", "user.email", "t@example.com")
        git(self.tmp, "config", "user.name", "t")
        self.json_cli("bootstrap")
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "hub")

    def test_repository_table_compiles_into_sections(self):
        out = self.json_cli("compile", "E01")
        self.assertEqual([(r["name"], r["base"], r["branch"]) for r in out["repositories"]],
                         [("core", "main", "002-e01-record-sales"), ("app", "main", "feat/002-e01-record-sales")])
        spec = (self.feature() / "spec.md").read_text(encoding="utf-8")
        plan = (self.feature() / "plan.md").read_text(encoding="utf-8")
        self.assertIn("## Target Repositories\n\n| Repository | Role in this epic |\n|---|---|\n| `core` | Provides the sale API |", spec)
        self.assertNotIn("**Repositories**", spec.split("## Epic Context")[1].split("## Target Repositories")[0])
        self.assertIn("| 2 | `app` | `main` | `feat/002-e01-record-sales` | consumes API-01 |", plan)
        self.assertIn("Both repositories deploy together.", plan)
        self.assertLess(plan.index("## Constitution Check"), plan.index("## Repositories"))
        self.assertLess(plan.index("## Repositories"), plan.index("## Project Structure"))
        self.assertEqual(json.loads(self.run_cli("epics").stdout)[1]["repositories"], ["core", "app"])

    def test_unknown_and_prose_repositories_are_errors(self):
        self.edit_blueprint("| `app` | 2 |", "| `web` | 2 |")
        res = self.run_cli("compile", "E01", check=False)
        self.assertIn("names web", res.stderr)
        self.assertEqual(json.loads(self.run_cli("refs", check=False).stdout)["E01"]["repositories"], ["unknown repository: web"])
        bp = self.blueprint()
        bp.write_text(bp.read_text(encoding="utf-8").replace(REPO_TABLE.replace("`app` | 2", "`web` | 2"), "**Repositories** Order: `core`, then `app`.\n"), encoding="utf-8")
        self.assertIn("not a table", self.run_cli("compile", "E01", check=False).stderr)

    def test_facts_gates_and_context(self):
        self.json_cli("compile", "E00")
        self.finish("E00")
        self.json_cli("compile", "E01")
        facts = self.json_cli("repos", "E01")["repositories"]
        self.assertEqual([(r["name"], r["exists"], r["current_branch"], r["branch_exists"]) for r in facts],
                         [("core", True, "main", False), ("app", True, "main", False)])
        res = self.run_cli("gate", "E01", check=False)
        self.assertEqual(res.returncode, 1)
        gates = json.loads(res.stdout)["results"]
        self.assertEqual([(g["repository"], g["ok"]) for g in gates], [("core", True), ("app", False)])
        self.assertEqual(self.json_cli("repos", "E01")["repositories"][0]["last_gate"]["ok"], True)
        ctx = self.json_cli("context")
        self.assertTrue(ctx["applies"])
        self.assertEqual(ctx["next_decision_id"], "DR-E01-1")
        self.assertIn("core: on branch main, expected 002-e01-record-sales", " ".join(ctx["problems"]))
        (self.tmp / "docs" / "DECISIONS.md").write_text("## DR-E01-1 — x\n## DR-E01-2 — y\n")
        self.assertEqual(self.json_cli("context")["next_decision_id"], "DR-E01-3")
        facts = self.json_cli("review-facts")
        self.assertEqual(facts["acs_needing_evidence"]["E01-S01"], ["AC-1", "AC-2"])
        self.assertEqual(facts["decisions_this_epic"], ["DR-E01-1", "DR-E01-2"])
        self.assertEqual(facts["effort"], "max")
        (self.tmp / ".specify" / "feature.json").write_text(json.dumps({"feature_directory": "specs/001-existing"}))
        self.assertFalse(self.json_cli("context")["applies"])

    def test_parallel_epics_in_worktrees(self):
        self.json_cli("compile", "E00")
        self.finish("E00")
        plan = self.json_cli("workspace", "E01")
        wts = {w["repository"]: w for w in plan["worktrees"]}
        self.assertEqual(Path(wts["hub"]["worktree"]), (self.base / ".worktrees" / "hub-e01").resolve())
        self.assertEqual(wts["app"]["branch"], "feat/002-e01-record-sales")
        self.assertIn("--add-dir", plan["open"])
        out = self.json_cli("workspace", "E01", "--create")
        self.assertEqual({w["result"] for w in out["worktrees"]}, {"created"})
        self.assertEqual(git(self.base / ".worktrees" / "app-e01", "branch", "--show-current"), "feat/002-e01-record-sales")
        # the main checkout sees E01 elsewhere and moves on to E02, which does not depend on E01
        st = self.state()
        e01 = next(e for e in st["epics"] if e["epic"] == "E01")
        self.assertEqual(Path(e01["elsewhere"]["worktree"]), (self.base / ".worktrees" / "hub-e01").resolve())
        self.assertEqual((st["next"]["command"], st["next"]["epic"]), ("speckit-blueprint-compile", "E02"))
        self.assertIn("Elsewhere: E01 Record sales", self.run_cli("status", "--brief").stdout)
        res = self.run_cli("compile", "E01", check=False)
        self.assertIn("is being built in", res.stderr)
        # E02 compiled here takes the next number even though E01's folder lives in the other worktree
        self.assertEqual(self.json_cli("compile", "E02")["feature_directory"], "specs/003-e02-daily-totals")
        # inside the worktree the epic is its own
        wt = self.base / ".worktrees" / "hub-e01"
        self.assertEqual(self.json_cli("compile", "E01", cwd=wt)["feature_directory"], "specs/002-e01-record-sales")
        facts = self.json_cli("repos", "E01", cwd=wt)["repositories"]
        self.assertEqual([Path(r["path"]).name for r in facts], ["core-e01", "app-e01"])
        self.assertTrue(all(r["on_branch"] for r in facts))
        # --take moves the claim
        self.json_cli("compile", "E01", "--take", "--force")
        self.assertNotIn("elsewhere", next(e for e in self.state()["epics"] if e["epic"] == "E01"))

    def test_missing_repository_is_reported(self):
        self.json_cli("compile", "E00")
        self.finish("E00")
        shutil.rmtree(self.base / "app")
        st = self.state()
        self.assertEqual(st["next"]["epic"], "E01")
        self.assertEqual(st["next"]["missing_repositories"], [{"name": "app", "path": "../app", "remote": False}])
        self.assertTrue(st["next"]["setup"].endswith("workspace setup E01"))
        self.assertIn("Not on this machine: app (../app).", self.run_cli("status", "--brief").stdout)
        res = self.run_cli("workspace", "setup", "E01", "--dry-run", check=False)
        self.assertEqual(res.returncode, 1)
        out = json.loads(res.stdout)
        self.assertEqual({r["repository"]: r["result"] for r in out["repositories"]},
                         {"hub": "would fetch", "core": "would fetch", "app": "missing: the config gives it no `remote`"})
        self.assertIn("relative to the hub's own", out["how_to_fix"])
        res = self.run_cli("workspace", "E01", "--create", check=False)
        self.assertEqual(res.returncode, 1)
        results = {w["repository"]: w["result"] for w in json.loads(res.stdout)["worktrees"]}
        self.assertEqual((results["hub"], results["core"]), ("created", "created"))
        self.assertTrue(results["app"].startswith("missing:"))


class RemoteWorkspaceTest(ProjectTest):
    """The workspace on another machine: setup clones and fetches, --create continues pushed branches, a
    status names epics started elsewhere, and link points the code repositories to the hub."""

    root_name = "hub"

    def setUp(self):
        super().setUp()
        self.add_epic_02()
        self.edit_blueprint("**Risks specific to this epic** R-01.\n", "**Risks specific to this epic** R-01.\n" + REPO_TABLE)
        self.configure("repositories:\n  hub: {path: .}\n  core: {path: ../core, remote: ../core.git}\n"
                       "  app: {path: ../app, remote: ../app.git, branch_prefix: feat/}\n")
        self.remotes = self.base / "remotes"
        for name in ("core", "app"):
            git_init(self.base / name)
            self.publish(self.base / name, name)
        (self.tmp / ".gitignore").write_text(".specify/feature.json\n")
        git(self.tmp, "init", "-q", "-b", "main")
        git(self.tmp, "config", "user.email", "t@example.com")
        git(self.tmp, "config", "user.name", "t")
        self.json_cli("bootstrap")
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "hub")
        self.publish(self.tmp, "hub")

    def publish(self, path, name):
        bare = self.remotes / f"{name}.git"
        bare.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        git(path, "remote", "add", "origin", str(bare))
        git(path, "push", "-q", "-u", "origin", "main")

    def clone(self, name, machine):
        dest = machine / name
        subprocess.run(["git", "clone", "-q", str(self.remotes / f"{name}.git"), str(dest)], check=True, capture_output=True)
        git(dest, "config", "user.email", "t@example.com")
        git(dest, "config", "user.name", "t")
        return dest

    def commit_and_push(self, path, message):
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", message)
        git(path, "push", "-q", "-u", "origin", "HEAD")

    def test_another_machine_continues_the_pushed_epic(self):
        # machine 1: E00 is done on main; E01 is started in its worktrees and its hub and core branches pushed
        self.json_cli("compile", "E00")
        self.finish("E00")
        self.commit_and_push(self.tmp, "E00")
        out = self.json_cli("workspace", "E01", "--create")
        self.assertEqual({w["start"]["from"] for w in out["worktrees"]}, {"base"})
        hub_wt, core_wt = self.base / ".worktrees" / "hub-e01", self.base / ".worktrees" / "core-e01"
        self.json_cli("compile", "E01", cwd=hub_wt)
        (hub_wt / "specs" / "002-e01-record-sales" / "tasks.md").write_text("- [x] T001 [core] sale API\n- [ ] T002 [app] sale screen\n")
        self.commit_and_push(hub_wt, "E01: sale API")
        (core_wt / "sale.py").write_text("x = 1\n")
        self.commit_and_push(core_wt, "sale API")

        # machine 2 has core, cloned on its own, and clones the hub next to it
        m2 = self.base / "m2"
        self.clone("core", m2)
        hub2 = self.clone("hub", m2)
        st = self.json_cli("status", cwd=hub2)
        e01 = next(e for e in st["epics"] if e["epic"] == "E01")
        self.assertEqual(e01["started"], {"branch": "002-e01-record-sales", "local": False,
                                          "remote": "origin/002-e01-record-sales", "folder": "002-e01-record-sales"})
        self.assertEqual((st["next"]["command"], st["next"]["epic"]), ("speckit-blueprint-compile", "E02"))
        self.assertIn("Pushed: E01 Record sales is on origin/002-e01-record-sales, started on another machine",
                      self.run_cli("status", "--brief", cwd=hub2).stdout)

        out = self.json_cli("workspace", "setup", cwd=hub2)
        self.assertEqual({r["repository"]: r["result"] for r in out["repositories"]},
                         {"hub": "fetched", "core": "fetched", "app": "cloned"})
        self.assertEqual(next(r for r in out["repositories"] if r["repository"] == "app")["remote"], str(self.remotes / "app.git"))
        self.assertTrue((m2 / "app" / "README.md").is_file())

        plan = self.json_cli("workspace", "E01", cwd=hub2)
        self.assertEqual({w["repository"]: w["start"] for w in plan["worktrees"]}, {
            "hub": {"from": "remote", "ref": "origin/002-e01-record-sales"},
            "core": {"from": "remote", "ref": "origin/002-e01-record-sales"},
            "app": {"from": "base", "ref": "main"},
        })
        self.assertIn("worktree add --track -b 002-e01-record-sales", plan["git"][0])
        out = self.json_cli("workspace", "E01", "--create", cwd=hub2)
        self.assertEqual({w["result"] for w in out["worktrees"]}, {"created"})
        wt2 = m2 / ".worktrees"
        self.assertEqual((wt2 / "core-e01" / "sale.py").read_text(), "x = 1\n")
        self.assertIn("- [x] T001", (wt2 / "hub-e01" / "specs" / "002-e01-record-sales" / "tasks.md").read_text())
        self.assertEqual(git(wt2 / "core-e01", "rev-parse", "--abbrev-ref", "@{u}"), "origin/002-e01-record-sales")
        e01 = next(e for e in self.json_cli("status", cwd=hub2)["epics"] if e["epic"] == "E01")
        self.assertIn("elsewhere", e01)
        self.assertNotIn("started", e01)

    def test_epic_merged_upstream_asks_for_a_pull(self):
        self.json_cli("compile", "E00")
        self.finish("E00")
        self.commit_and_push(self.tmp, "E00")
        hub2 = self.clone("hub", self.base / "m2")  # cloned before E01 is merged
        git(self.tmp, "checkout", "-q", "-b", "002-e01-record-sales")
        self.json_cli("compile", "E01")
        self.commit_and_push(self.tmp, "E01")
        git(self.tmp, "checkout", "-q", "main")
        git(self.tmp, "merge", "-q", "--no-ff", "-m", "merge E01", "002-e01-record-sales")
        git(self.tmp, "push", "-q")
        self.assertEqual({r["repository"]: r["result"] for r in self.json_cli("workspace", "setup", cwd=hub2)["repositories"]},
                         {"hub": "fetched", "core": "cloned", "app": "cloned"})
        e01 = next(e for e in self.json_cli("status", cwd=hub2)["epics"] if e["epic"] == "E01")
        self.assertEqual(e01["started"]["merged_into"], "origin/main")
        self.assertIn("Merged: E01 Record sales is in origin/main but not in this checkout; bring it in (pull or merge).",
                      self.run_cli("status", "--brief", cwd=hub2).stdout)
        git(hub2, "pull", "-q")
        e01 = next(e for e in self.json_cli("status", cwd=hub2)["epics"] if e["epic"] == "E01")
        self.assertNotIn("started", e01)
        self.assertEqual(e01["state"], "compiled")

    def test_link_points_code_repositories_to_the_hub(self):
        (self.base / "app" / "CLAUDE.md").write_text("# App rules\n\nUse tabs.\n")
        out = self.json_cli("workspace", "link")
        self.assertEqual({r["repository"]: r["result"] for r in out["repositories"]}, {"core": "created", "app": "added"})
        core = (self.base / "core" / "CLAUDE.md").read_text()
        self.assertIn("<!-- speckit-blueprint:hub:start id=hub -->", core)
        self.assertIn(f"`git clone {self.remotes / 'hub.git'} ../hub`", core)
        app = (self.base / "app" / "CLAUDE.md").read_text()
        self.assertTrue(app.startswith("# App rules\n\nUse tabs.\n\n<!-- speckit-blueprint:hub:start id=hub -->"))
        hub_md = self.tmp / "CLAUDE.md"
        self.assertNotIn("speckit-blueprint:hub:", hub_md.read_text() if hub_md.is_file() else "")
        self.assertEqual({r["result"] for r in self.json_cli("workspace", "link")["repositories"]}, {"unchanged"})
        git(self.tmp, "remote", "set-url", "origin", "git@example.com:org/hub.git")
        dry = self.json_cli("workspace", "link", "--dry-run")
        self.assertEqual({r["result"] for r in dry["repositories"]}, {"would be updated"})
        self.assertEqual((self.base / "core" / "CLAUDE.md").read_text(), core)
        self.json_cli("workspace", "link")
        core = (self.base / "core" / "CLAUDE.md").read_text()
        self.assertIn("`git clone git@example.com:org/hub.git ../hub`", core)
        self.assertEqual(core.count("speckit-blueprint:hub:start"), 1)


class SingleRepoGitTest(ProjectTest):
    """The common case: code and specs in one git repository, a branch per feature, no `repositories:`."""

    root_name = "app"  # the bare remote goes next to the project, not inside it

    def setUp(self):
        super().setUp()
        self.add_epic_02()
        (self.tmp / ".gitignore").write_text(".specify/feature.json\n")
        git(self.tmp, "init", "-q", "-b", "main")
        git(self.tmp, "config", "user.email", "t@example.com")
        git(self.tmp, "config", "user.name", "t")
        self.json_cli("bootstrap")
        self.json_cli("compile", "E00")
        self.finish("E00")
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "E00")
        origin = self.base / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        git(self.tmp, "remote", "add", "origin", str(origin))
        git(self.tmp, "push", "-q", "-u", "origin", "main")

    def started(self):
        return next(e for e in self.state()["epics"] if e["epic"] == "E01").get("started")

    def test_branch_per_feature(self):
        name = self.tmp.name
        # on the feature branch before compiling: the epic is compiled here, not "in progress elsewhere"
        git(self.tmp, "checkout", "-q", "-b", "002-e01-record-sales")
        self.assertIsNone(self.started())
        self.assertEqual(self.state()["next"]["epic"], "E01")
        self.assertEqual(self.json_cli("compile", "E01")["repositories"], [
            {"name": name, "order": 1, "role": "", "contracts": "", "base": "main", "branch": "002-e01-record-sales"}])
        self.assertEqual(self.json_cli("context")["problems"], [])
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "E01 compiled")
        # back on main, unpushed: E01 is on a local branch, never compiled again; E02 is next
        git(self.tmp, "checkout", "-q", "main")
        self.assertEqual(self.started(), {"branch": "002-e01-record-sales", "local": True, "remote": None,
                                          "folder": "002-e01-record-sales"})
        st = self.state()
        self.assertEqual((st["next"]["epic"], "missing_repositories" in st["next"]), ("E02", False))
        self.assertIn("On a branch: E01 Record sales is in progress on 002-e01-record-sales, not merged here",
                      self.run_cli("status", "--brief").stdout)
        git(self.tmp, "push", "-q", "origin", "002-e01-record-sales")
        self.assertEqual(self.started()["remote"], "origin/002-e01-record-sales")
        # the workspace commands have nothing else to reach
        self.assertEqual(self.json_cli("workspace", "setup")["repositories"],
                         [{"repository": name, "path": str(self.tmp.resolve()), "hub": True, "result": "fetched", "ok": True}])
        self.assertEqual(self.json_cli("workspace", "link")["repositories"], [])
        self.assertEqual([w["start"] for w in self.json_cli("workspace", "E01")["worktrees"]],
                         [{"from": "local", "ref": "002-e01-record-sales"}])
        # merged into main: the folder is here and the overlay is gone
        git(self.tmp, "merge", "-q", "--no-ff", "-m", "merge E01", "002-e01-record-sales")
        self.assertIsNone(self.started())
        self.assertEqual(next(e for e in self.state()["epics"] if e["epic"] == "E01")["state"], "compiled")


class RemoteUrlTest(unittest.TestCase):
    def test_relative_remotes_resolve_like_submodule_urls(self):
        from speckit_blueprint.repos import resolve_remote

        cases = [
            ("../core.git", "https://github.com/org/hub.git", "https://github.com/org/core.git"),
            ("../core.git", "git@github.com:org/hub.git", "git@github.com:org/core.git"),
            ("../../other/core.git", "git@github.com:org/hub.git", "git@github.com:other/core.git"),
            ("../core", "/srv/git/hub", "/srv/git/core"),
            ("./core.git", "https://host/org/hub", "https://host/org/hub/core.git"),
            ("git@github.com:org/core.git", "", "git@github.com:org/core.git"),
            ("../core.git", "", ""),
            ("../../../core.git", "https://github.com/hub.git", ""),
        ]
        for remote, base, want in cases:
            self.assertEqual(resolve_remote(remote, base), want, (remote, base))


MULTI_CONFIG = """# two products in one planning repository
default_blueprint: one
blueprints:
  one:
    path: docs/BLUEPRINT.md
  desk:
    path: docs/desk/BLUEPRINT.md
    number_base: 500
    constitution_sections: ["13.2"]
    sources:
      - ../evida-desk  # the product's code lives next door
      - ../desk-prototype
"""


class MultiBlueprintTest(ProjectTest):
    """Two blueprints (ONE and DESK) in one project: nothing of one touches the other."""

    def setUp(self):
        super().setUp()
        (self.tmp / "docs" / "desk").mkdir()
        desk = FIXTURE.read_text(encoding="utf-8").replace("# Tally", "# Desk", 1).replace("No ORMs.", "Desk rules: Dart only.")
        (self.tmp / "docs" / "desk" / "BLUEPRINT.md").write_text(desk, encoding="utf-8")

    def single_then_multi(self):
        """ONE built with a one-blueprint config, then the project gains DESK and migrates."""
        self.json_cli("bootstrap")
        self.json_cli("compile", "E01")
        (self.feature() / "tasks.md").write_text("- [x] T001 a\n- [ ] T002 b\n")
        self.configure(MULTI_CONFIG)
        st = self.state()
        self.assertEqual(st["next"]["command"], "migrate")
        self.assertTrue(st["migration_needed"])
        out = json.loads(self.run_cli("migrate", "--apply").stdout)
        self.assertTrue(out["applied"])
        self.assertNotIn("migration_needed", self.state())

    def test_compile_never_reaches_the_other_blueprints_folder(self):
        self.single_then_multi()
        spec_before = (self.feature() / "spec.md").read_text(encoding="utf-8")
        out = self.json_cli("compile", "desk:E01")
        self.assertEqual(out["feature_directory"], "specs/502-desk-e01-record-sales")
        self.assertEqual(out["blueprint_id"], "desk")
        self.assertEqual((self.feature() / "spec.md").read_text(encoding="utf-8"), spec_before)
        desk = self.tmp / out["feature_directory"]
        self.assertEqual(json.loads((desk / ".blueprint.json").read_text())["blueprint_id"], "desk")
        self.assertIn("(blueprint `desk`)", (desk / "spec.md").read_text(encoding="utf-8"))
        self.assertIn("DESK:E01-S02-T3", (desk / "plan.md").read_text(encoding="utf-8"))
        self.assertIn("`DESK:DR-E01-1`", (desk / "plan.md").read_text(encoding="utf-8"))
        # ONE's folder: recompiling it hits the finished-task check
        res = self.run_cli("compile", "E01", "--blueprint", "one", check=False)
        self.assertIn("completed tasks", res.stderr)
        # the active feature decides the default: desk's folder is active now
        self.assertEqual(self.json_cli("compile", "E00")["feature_directory"], "specs/501-desk-e00-foundation")
        self.assertEqual(self.json_cli("compile", "E00", "--blueprint", "ONE")["feature_directory"], "specs/001-one-e00-foundation")
        res = self.run_cli("compile", "desk:E00", "--blueprint", "one", check=False)
        self.assertNotEqual(res.returncode, 0)

    def test_status_reads_each_blueprint_from_its_own_folders(self):
        self.single_then_multi()
        st = self.state()  # active feature is ONE's E01
        self.assertEqual(st["blueprint_id"], "one")
        e01 = next(e for e in st["epics"] if e["epic"] == "E01")
        self.assertEqual((e01["state"], e01["feature_directory"]), ("in-progress", "specs/002-e01-record-sales"))
        self.assertEqual([b["blueprint_id"] for b in st["blueprints"]], ["one", "desk"])
        desk = st["blueprints"][1]
        self.assertEqual((desk["epics_done"], desk["constitution"]), (0, "not-from-blueprint"))
        self.assertEqual(desk["sources"], ["../evida-desk", "../desk-prototype"])
        self.assertEqual(desk["decisions"], "docs/desk/DECISIONS.md")
        st = self.state("--blueprint", "desk")
        self.assertTrue(all(e["state"] == "not-compiled" for e in st["epics"]))
        self.assertEqual(st["next"]["command"], "bootstrap")
        brief = self.run_cli("status", "--brief").stdout
        self.assertIn("Tally (one)", brief)
        self.assertIn("Also: Desk (desk)", brief)
        self.assertIn("Tally (one)", self.run_cli("hook", "session-start", stdin="{}").stdout)

    def test_bootstrap_keeps_one_block_per_blueprint(self):
        cpath = self.tmp / ".specify" / "memory" / "constitution.md"
        cpath.write_text(PROJECT_CONSTITUTION, encoding="utf-8")
        (self.tmp / "CLAUDE.md").write_text("# Wiki rules\n")
        self.single_then_multi()
        c = cpath.read_text(encoding="utf-8")
        self.assertIn("<!-- speckit-blueprint:rules:start id=one -->", c)  # migrate renamed the block
        self.assertNotIn("id=default", c)
        out = self.json_cli("bootstrap", "--blueprint", "desk")
        self.assertEqual((out["constitution"]["mode"], out["constitution"]["block"]), ("append", "added"))
        c = cpath.read_text(encoding="utf-8")
        self.assertIn("No ORMs.", c)  # ONE's rules survive
        self.assertIn("<!-- speckit-blueprint:rules:start id=desk -->", c)
        self.assertIn("Desk rules: Dart only.", c)
        self.assertIn('"blueprint_id": "desk"', c)
        self.assertIn("### DESK-I. The blueprint is the source of truth", c)
        self.assertLess(c.index("rules:end id=one -->"), c.index("rules:start id=desk"))
        self.json_cli("bootstrap", "--blueprint", "one")
        c = cpath.read_text(encoding="utf-8")
        self.assertEqual(c.count("speckit-blueprint:rules:start id=one"), 1)
        self.assertEqual(c.count("speckit-blueprint:rules:start id=desk"), 1)
        self.assertEqual(self.state("--blueprint", "desk")["constitution"], "appended")
        # rules prints only the active blueprint's block
        rules = self.run_cli("rules", "--blueprint", "desk").stdout
        self.assertIn("Dart only", rules)
        self.assertNotIn("No ORMs", rules)
        # CLAUDE.md: one block each, the project's text kept
        claude = (self.tmp / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("# Wiki rules", claude)
        self.assertEqual(claude.count("speckit-blueprint:start id=one"), 1)
        self.assertEqual(claude.count("speckit-blueprint:start id=desk"), 1)
        self.assertIn("--blueprint desk", claude)
        # checklists and decisions are separate files; DESK's DR rows carry its prefix
        self.assertTrue((self.tmp / "docs" / "desk" / "REVIEW-CHECKLIST.md").is_file())
        self.assertIn("| DESK:DR-01 | Integer rials |", (self.tmp / "docs" / "desk" / "DECISIONS.md").read_text(encoding="utf-8"))
        self.assertIn("| DR-01 | Integer rials |", (self.tmp / "docs" / "DECISIONS.md").read_text(encoding="utf-8"))

        def guard(old):
            payload = {"tool_name": "Edit", "tool_input": {"file_path": str(cpath), "old_string": old}}
            return self.run_cli("hook", "guard", stdin=json.dumps(payload), check=False)

        self.assertEqual(guard("No ORMs.").returncode, 2)
        res = guard("Dart only.")
        self.assertEqual(res.returncode, 2)
        self.assertIn("docs/desk/BLUEPRINT.md", res.stderr)
        self.assertEqual(guard("however small").returncode, 0)

    def test_own_constitution_keeps_other_blocks(self):
        self.configure(MULTI_CONFIG)
        self.assertEqual(self.json_cli("bootstrap", "--blueprint", "one")["constitution"]["mode"], "own")
        self.assertEqual(self.json_cli("bootstrap", "--blueprint", "desk")["constitution"]["mode"], "append")
        self.assertEqual(self.json_cli("bootstrap", "--blueprint", "one")["constitution"]["mode"], "own")
        c = (self.tmp / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8")
        self.assertIn("blueprint-id one", c.split("\n", 1)[0])
        self.assertIn("Desk rules: Dart only.", c)
        self.assertLess(c.index("rules:end id=desk"), c.index("## Governance"))
        self.assertEqual(self.state("--blueprint", "one")["constitution"], "compiled")
        self.assertEqual(self.state("--blueprint", "desk")["constitution"], "appended")
        self.configure(MULTI_CONFIG + "constitution_mode: own\n")
        self.assertIn("compiled whole from blueprint 'one'", self.run_cli("bootstrap", "--blueprint", "desk", check=False).stderr)

    def test_lookups_by_prefix(self):
        self.configure(MULTI_CONFIG)
        out = self.run_cli("find", "DESK:DR-01", "one:ENT-Shop").stdout
        self.assertIn("<!-- DESK:DR-01 -->", out)
        self.assertIn("<!-- ONE:ENT-Shop -->", out)
        self.assertIn("Dart only", self.run_cli("section", "desk:13.2").stdout)
        self.assertIn("No ORMs", self.run_cli("section", "13.2").stdout)  # the default blueprint
        self.assertNotEqual(self.run_cli("find", "nope:DR-01", check=False).returncode, 0)

    def test_discover_mode_per_blueprint(self):
        self.configure(MULTI_CONFIG)
        (self.tmp / "docs" / "desk" / "BLUEPRINT.md").unlink()
        st = self.state("--blueprint", "desk")
        self.assertEqual((st["state"], st["next"]["command"]), ("no-blueprint", "speckit-blueprint-discover"))
        self.assertEqual(st["blueprints"][0]["state"], "building")  # ONE is unaffected
        self.assertEqual(self.state()["blueprint_id"], "one")

    def test_cross_blueprint_dependency(self):
        self.configure(MULTI_CONFIG)
        desk = self.tmp / "docs" / "desk" / "BLUEPRINT.md"
        desk.write_text(desk.read_text(encoding="utf-8").replace("**Depends on** E00", "**Depends on** E00, one:E00"), encoding="utf-8")
        self.json_cli("bootstrap", "--blueprint", "one")
        self.json_cli("bootstrap", "--blueprint", "desk")
        e01 = next(e for e in self.state("--blueprint", "desk")["epics"] if e["epic"] == "E01")
        self.assertEqual(e01["blocked_by"], ["E00", "one:E00"])
        self.json_cli("compile", "one:E00")
        f = self.tmp / "specs" / "001-one-e00-foundation"
        (f / "review.md").write_text(DONE_REVIEW["E00"])
        e01 = next(e for e in self.state("--blueprint", "desk")["epics"] if e["epic"] == "E01")
        self.assertEqual(e01["blocked_by"], ["E00"])

    def test_config_errors(self):
        self.configure("blueprints:\n  one:\n    path: docs/BLUEPRINT.md\n  desk:\n    path: docs/x.md\n    decisions: docs/DECISIONS.md\n")
        res = self.run_cli("status", check=False)
        self.assertIn("both use docs/DECISIONS.md", res.stderr)
        self.assertEqual(self.run_cli("hook", "guard", stdin="{}", check=False).returncode, 0)
        self.configure("blueprint_path: docs/BLUEPRINT.md\nblueprints:\n  desk:\n    path: docs/desk/BLUEPRINT.md\n")
        self.assertIn("ambiguous", self.run_cli("status", check=False).stderr)
        self.configure("blueprints:\n  Desk!:\n    path: x.md\n")
        self.assertIn("lowercase", self.run_cli("status", check=False).stderr)
        self.configure("schema: 1\n")
        self.assertIn("blueprint.py migrate", self.run_cli("status", check=False).stderr)
        self.configure("schema: 3\n")
        self.assertIn("Update the extension", self.run_cli("status", check=False).stderr)
        self.configure("repositories:\n  core: {path: ../core, colour: blue}\n")
        self.assertIn("unknown key(s) colour", self.run_cli("status", check=False).stderr)
        self.configure("metrics: sometimes\n")
        self.assertIn("metrics", self.run_cli("status", check=False).stderr)

    def test_single_blueprint_map_keeps_ids_out_of_ids(self):
        # one entry under `blueprints:`: folder names carry the id, IDs need no prefix
        self.configure("blueprints:\n  one:\n    path: docs/BLUEPRINT.md\n")
        out = self.json_cli("compile", "E01")
        self.assertEqual(out["feature_directory"], "specs/002-one-e01-record-sales")
        self.assertIn("E01-S02-T3", (self.tmp / out["feature_directory"] / "plan.md").read_text(encoding="utf-8"))
        self.assertNotIn("ONE:", (self.tmp / out["feature_directory"] / "plan.md").read_text(encoding="utf-8"))


class MigrateTest(ProjectTest):
    """A schema-1 project: prose repository fields, traces without blueprint_id, unmarked blocks."""

    root_name = "wiki"

    def setUp(self):
        super().setUp()
        for name in ("core", "app"):
            (self.base / name).mkdir()
        self.json_cli("bootstrap")
        self.json_cli("compile", "E00")
        trace = self.feature("E00") / ".blueprint.json"
        t = json.loads(trace.read_text(encoding="utf-8"))
        del t["blueprint_id"]
        trace.write_text(json.dumps(t), encoding="utf-8")
        for rel in (".specify/memory/constitution.md", "CLAUDE.md"):
            p = self.tmp / rel
            if p.is_file():
                p.write_text(p.read_text(encoding="utf-8").replace(" id=default -->", " -->").replace("blueprint-id default, ", ""), encoding="utf-8")
        self.edit_blueprint("**Risks specific to this epic** R-01.\n", "**Risks specific to this epic** R-01.\n" + SCHEMA_1_REPOS)
        (self.feature("E00") / "tasks.md").write_text("- [ ] T001 [US1] [core] E00-S01-T1 init\n- [ ] T002 [app] wire\n")
        self.configure("# old config\nguard: true\neffort_check: false\n")

    def test_dry_run_then_apply(self):
        st = self.state()
        self.assertEqual(st["next"]["command"], "migrate")
        self.assertEqual(len(st["migration_needed"]), 3)
        res = self.run_cli("compile", "E01", check=False)
        self.assertIn("blueprint.py migrate", res.stderr)
        before = self.blueprint().read_text(encoding="utf-8")
        dry = json.loads(self.run_cli("migrate").stdout)
        self.assertFalse(dry["applied"])
        self.assertEqual(self.blueprint().read_text(encoding="utf-8"), before)
        changes = " | ".join(c["change"] for c in dry["changes"])
        for words in ("schema: 2", "removed effort_check", "repositories: core, app", "E01: repository fields → table",
                      "blueprint_id: default", "managed block marked id=default", "blueprint-id default"):
            self.assertIn(words, changes)
        self.assertEqual(dry["warnings"], [])
        out = json.loads(self.run_cli("migrate", "--apply").stdout)
        self.assertTrue(out["applied"])
        st = self.state()
        self.assertNotIn("migration_needed", st)
        cfg = (self.cfg_dir / "blueprint-config.yml").read_text(encoding="utf-8")
        self.assertTrue(cfg.startswith("# old config\nschema: 2\n"))
        self.assertNotIn("effort_check", cfg)
        self.assertIn("  core:\n    path: ../core", cfg)
        text = self.blueprint().read_text(encoding="utf-8")
        self.assertNotIn("**Target repositories**", text)
        self.assertIn("| `core` | 1 | Provides the sale API | master | feat/sales | provides API-01 |", text)
        self.assertIn("| `app` | 2 | Consumes it | main | feat/sales-ui |  |", text)
        out = self.json_cli("compile", "E01")
        self.assertEqual([(r["name"], r["branch"]) for r in out["repositories"]], [("core", "feat/sales"), ("app", "feat/sales-ui")])
        self.assertIn("Notes (schema 1, kept by `blueprint.py migrate`)", (self.feature() / "plan.md").read_text(encoding="utf-8"))
        again = json.loads(self.run_cli("migrate").stdout)
        self.assertEqual((again["changes"], again["next"]), ([], "Nothing to migrate."))


class MigrateConfigTest(unittest.TestCase):
    def test_schema_line(self):
        from speckit_blueprint.config import parse_simple_yaml
        from speckit_blueprint.migrate import migrate_config

        for before, after in (
            # 0.4's template: a header, a blank line, the first key's comment
            ("# speckit-blueprint configuration.\n\n# Where spec-kit feature folders live.\nspecs_dir: specs\n",
             "# speckit-blueprint configuration.\nschema: 2\n\n# Where spec-kit feature folders live.\nspecs_dir: specs\n"),
            ("# old config\nguard: true\n", "# old config\nschema: 2\n\nguard: true\n"),
            ("guard: true\n", "schema: 2\n\nguard: true\n"),
            ("schema: 1\n\nguard: true\n", "schema: 2\n\nguard: true\n"),
            ("# only a comment\n", "# only a comment\nschema: 2\n"),
        ):
            self.assertEqual(migrate_config(before, parse_simple_yaml(before), []), after, before)


class UpgradeFrom04Test(ProjectTest):
    """Epics built and reviewed under 0.4, then migrated: they stay as built and as done."""

    root_name = "wiki"

    def setUp(self):
        super().setUp()
        from speckit_blueprint.core import sha
        from speckit_blueprint.model import Blueprint

        for name in ("core", "app"):
            (self.base / name).mkdir()
        self.add_epic_02()
        self.json_cli("bootstrap")
        for epic in ("E00", "E01", "E02"):
            self.json_cli("compile", epic)
        # 0.4 counted any DONE review: E00's names no AC; E01's leaves AC-2 out and a task open by decision
        (self.feature("E00") / "tasks.md").write_text("- [x] T001 a\n")
        (self.feature("E00") / "review.md").write_text("Status: DONE\n# Handoff — E00\ncheck: green\n")
        (self.feature("E01") / "tasks.md").write_text("- [x] T001 a\n- [ ] T002 moved to E02 by decision\n")
        (self.feature("E01") / "review.md").write_text(
            "Status: DONE\n# Handoff — E01\nAC evidence: E01-S01 AC-1 test/sale.int.test.ts · E01-S02 AC-1 src/undo.test.ts\n")
        (self.feature("E02") / "tasks.md").write_text("- [x] T001 a\n- [ ] T002 b\n")
        self.edit_blueprint("**Risks specific to this epic** R-01.\n", "**Risks specific to this epic** R-01.\n" + SCHEMA_1_REPOS)
        bp = Blueprint(self.blueprint())
        for epic in ("E00", "E01", "E02"):  # the traces 0.4 wrote, hashing the schema-1 text
            path = self.feature(epic) / ".blueprint.json"
            t = json.loads(path.read_text(encoding="utf-8"))
            t = {k: v for k, v in t.items() if k not in ("branch", "depends", "repositories", "acs", "estimate")}
            t["epic_sha"] = sha(bp.epic_text(bp.epic(epic[1:])))
            path.write_text(json.dumps(t, indent=2), encoding="utf-8")
        self.configure("guard: true\neffort_check: false\n")

    def row(self, st, epic):
        return next(e for e in st["epics"] if e["epic"] == epic)

    def trace(self, epic):
        return json.loads((self.feature(epic) / ".blueprint.json").read_text(encoding="utf-8"))

    def test_migrate_keeps_them_built_and_done(self):
        st = self.state()
        self.assertEqual(st["next"]["command"], "migrate")
        self.assertIn("3 feature folder(s) compiled before 0.5 without their review recorded", " ".join(st["migration_needed"]))
        e00, e02 = self.row(st, "E00"), self.row(st, "E02")
        self.assertEqual((e00["state"], e00["review_missing"], e02["blocked_by"]), ("implemented", ["E00-S01 AC-1"], ["E00"]))
        dry = json.loads(self.run_cli("migrate").stdout)
        self.assertEqual(dry["warnings"], [])
        changes = {(c["file"], c["change"]) for c in dry["changes"]}
        e01 = "specs/002-e01-record-sales/.blueprint.json"
        self.assertIn((e01, "epic_sha: E01 as converted (recorded in accepted)"), changes)
        self.assertIn((e01, "review_before_0_5: DONE, counted as done while review.md is unchanged"), changes)
        self.assertIn(("specs/003-e02-daily-totals/.blueprint.json",
                       "review_before_0_5: no review.md; a DONE review written later needs evidence for every AC"), changes)
        self.assertNotIn("review_before_0_5", self.trace("E01"))  # the dry run wrote nothing
        self.json_cli("migrate", "--apply")
        st = self.state()
        self.assertNotIn("migration_needed", st)
        e00, e01, e02 = (self.row(st, e) for e in ("E00", "E01", "E02"))
        self.assertEqual((e00["state"], e00.get("review_before_0_5")), ("done", True))
        self.assertEqual((e01["state"], e01.get("review_before_0_5")), ("done", True))
        self.assertNotIn("changed_since_compile", e01)  # converting its repository fields changed nothing it was built from
        self.assertNotIn("review_missing", e01)
        self.assertNotIn("blocked_by", e02)
        self.assertEqual((st["next"]["command"], st["next"]["epic"]), ("speckit-implement", "E02"))
        self.assertEqual(self.json_cli("stale"), [])
        t = self.trace("E01")
        self.assertEqual(t["accepted"][-1]["changed"], ["E01"])
        self.assertTrue(t["accepted"][-1]["note"].startswith("blueprint.py migrate:"))
        self.assertEqual(sorted(t["review_before_0_5"]), ["date", "sha", "status"])
        self.assertEqual(json.loads(self.run_cli("migrate").stdout)["next"], "Nothing to migrate.")

    def test_the_old_rule_holds_while_the_review_is_unchanged(self):
        self.json_cli("migrate", "--apply")
        review = self.feature("E01") / "review.md"
        review.write_bytes(review.read_bytes().replace(b"\n", b"  \r\n"))  # another machine's line endings
        self.assertEqual(self.row(self.state(), "E01")["state"], "done")
        review.write_text(review.read_text(encoding="utf-8") + "Manual checks for you: none\n", encoding="utf-8")
        e01 = self.row(self.state(), "E01")
        self.assertEqual((e01["state"], e01["review_missing"]), ("in-progress", ["E01-S01 AC-2"]))
        self.assertNotIn("review_before_0_5", e01)
        # E02 was not DONE before the migration: a DONE review written now needs evidence for every AC
        (self.feature("E02") / "review.md").write_text("Status: DONE\n# Handoff — E02\ncheck: green\n")
        st = self.state()
        self.assertEqual(self.row(st, "E02")["review_missing"], ["E02-S01 AC-1"])
        self.assertNotIn("migration_needed", st)

    def test_a_change_made_before_the_migration_stays_reported(self):
        self.edit_blueprint("When undo is tapped", "When undo is pressed")
        dry = json.loads(self.run_cli("migrate").stdout)
        self.assertEqual(dry["warnings"], ["specs/002-e01-record-sales/.blueprint.json: E01 had changed since compile "
                                           "before the migration; review the change, then `blueprint.py accept` or recompile"])
        self.json_cli("migrate", "--apply")
        self.assertEqual(self.row(self.state(), "E01")["changed_since_compile"], ["E01"])
        self.assertNotIn("accepted", self.trace("E01"))

    def test_a_project_migrated_by_0_5_0(self):
        """0.5.0's migrate recorded no review; running migrate again records them and changes nothing else."""
        self.json_cli("migrate", "--apply")
        for epic in ("E00", "E01", "E02"):
            path = self.feature(epic) / ".blueprint.json"
            t = json.loads(path.read_text(encoding="utf-8"))
            del t["review_before_0_5"]
            path.write_text(json.dumps(t, indent=2), encoding="utf-8")
        st = self.state()
        self.assertEqual(st["migration_needed"], ["3 feature folder(s) compiled before 0.5 without their review recorded "
                                                  "(001-e00-foundation, 002-e01-record-sales, 003-e02-daily-totals…)"])
        self.assertEqual(self.row(st, "E00")["state"], "implemented")
        dry = json.loads(self.run_cli("migrate").stdout)
        self.assertEqual({c["change"].split(":")[0] for c in dry["changes"]}, {"review_before_0_5"})
        self.assertEqual(self.json_cli("migrate", "--apply")["next"], "Review the diff, then run `blueprint.py status`.")
        self.assertEqual([self.row(self.state(), e)["state"] for e in ("E00", "E01", "E02")], ["done", "done", "in-progress"])


class LookupTest(ProjectTest):
    def test_section_and_find(self):
        self.assertIn("`npm run check`", self.run_cli("section", "13.4").stdout)
        out = self.run_cli("find", "ENT-Shop", "CP-SCR01-3").stdout
        self.assertIn("`name` text", out)
        self.assertIn("مبلغ نامعتبر است", out)
        self.assertNotEqual(self.run_cli("find", "ENT-Nope", check=False).returncode, 0)


class ConfigTest(ProjectTest):
    def test_config_overrides(self):
        self.configure("# comment\nspecs_dir: features\nplan_sections:\n  - \"13.4\"\nconstitution_sections: [\"13.2\"]\n")
        out = self.json_cli("compile", "E01")
        self.assertTrue(out["feature_directory"].startswith("features/"))
        plan = (self.tmp / out["feature_directory"] / "plan.md").read_text(encoding="utf-8")
        self.assertIn("npm run check", plan)
        self.assertNotIn("12.2 Module map", plan)

    def test_paths_and_claude_md_switch(self):
        self.configure(
            "blueprint_path: specs/blueprint/BLUEPRINT.md\ndecisions_path: specs/blueprint/DECISIONS.md\n"
            "review_checklist_path: specs/blueprint/REVIEW-CHECKLIST.md\nclaude_md: false\n"
        )
        (self.tmp / "specs" / "blueprint").mkdir(parents=True)
        shutil.move(str(self.blueprint()), self.tmp / "specs" / "blueprint" / "BLUEPRINT.md")
        (self.tmp / "CLAUDE.md").write_text("@AGENTS.md\n")
        out = self.json_cli("bootstrap")
        self.assertEqual(out["claude_md"], "skipped (claude_md: false)")
        self.assertEqual((self.tmp / "CLAUDE.md").read_text(), "@AGENTS.md\n")
        self.assertTrue((self.tmp / "specs" / "blueprint" / "DECISIONS.md").is_file())
        self.assertTrue((self.tmp / "specs" / "blueprint" / "REVIEW-CHECKLIST.md").is_file())
        self.assertFalse((self.tmp / "docs" / "DECISIONS.md").exists())
        c = (self.tmp / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8")
        self.assertIn("`specs/blueprint/DECISIONS.md`", c)
        self.assertNotIn("docs/DECISIONS.md", c)
        st = self.state()
        self.assertEqual((st["decisions"], st["review_checklist"]), ("specs/blueprint/DECISIONS.md", "specs/blueprint/REVIEW-CHECKLIST.md"))
        # the blueprint folder under specs/ is not mistaken for a feature
        out = self.json_cli("compile", "E00")
        self.assertTrue(out["feature_directory"].startswith("specs/001-"))
        plan = (self.tmp / out["feature_directory"] / "plan.md").read_text(encoding="utf-8")
        self.assertIn("`specs/blueprint/DECISIONS.md`", plan)

    def test_local_file_overrides_repositories_setting_by_setting(self):
        from speckit_blueprint.config import config_from, parse_simple_yaml

        shared = parse_simple_yaml("repositories:\n  core:\n    path: ../core\n    gate: make check\n  app: ../app\n")
        local = parse_simple_yaml("repositories:\n  core: {remote: git@example.com:org/core.git}\n"
                                  "  app: {gate: npm test}\n  web: ../web\n")
        repos = config_from([shared, local])["repositories"]
        self.assertEqual((repos["core"]["path"], repos["core"]["gate"], repos["core"]["remote"]),
                         ("../core", "make check", "git@example.com:org/core.git"))
        self.assertEqual((repos["app"]["path"], repos["app"]["gate"]), ("../app", "npm test"))
        self.assertEqual(repos["web"]["path"], "../web")


class YamlTest(unittest.TestCase):
    def test_nested_maps_and_lists(self):
        from speckit_blueprint.config import parse_simple_yaml

        data = parse_simple_yaml(MULTI_CONFIG + "plan_sections:\n- \"13.4\"\nguard: false\n"
                                 "repositories:\n  core: {path: ../core, gate: \"make lint, test\"}\n  app: ../app\n")
        self.assertEqual(data["default_blueprint"], "one")
        self.assertEqual(data["blueprints"]["one"], {"path": "docs/BLUEPRINT.md"})
        self.assertEqual(data["blueprints"]["desk"]["constitution_sections"], ["13.2"])
        self.assertEqual(data["blueprints"]["desk"]["sources"], ["../evida-desk", "../desk-prototype"])
        self.assertEqual(data["blueprints"]["desk"]["number_base"], "500")
        self.assertEqual((data["plan_sections"], data["guard"]), (["13.4"], False))
        self.assertEqual(data["repositories"]["core"], {"path": "../core", "gate": "make lint, test"})
        self.assertEqual(data["repositories"]["app"], "../app")


class ModelTest(unittest.TestCase):
    def test_epic_fields(self):
        from speckit_blueprint.model import parse_depends, parse_repo_table

        self.assertEqual(parse_depends("**Depends on** E15 (external entities), E18 (Release 1), admin APIs in evida-id, which E19 waits for"),
                         [(None, "E15"), (None, "E18")])
        self.assertEqual(parse_depends("**Depends on** — (design runs first, beside E00)"), [])
        self.assertEqual(parse_depends("**Depends on** E10; `evida-asr` (INT-asr), one:E18"), [(None, "E10"), ("one", "E18")])
        # ranges and joined IDs count for every epic they name
        self.assertEqual(parse_depends("**Depends on** E07 (pages and panels), E20 and E21 (branches) (amended A-44), E00"),
                         [(None, "E07"), (None, "E20"), (None, "E21"), (None, "E00")])
        self.assertEqual(parse_depends("**Depends on** E02 to E07"), [(None, f"E0{n}") for n in range(2, 8)])
        self.assertEqual(parse_depends("**Depends on** E00–E02, E04–E06 and E16 (released; amended A-13)"),
                         [(None, e) for e in ("E00", "E01", "E02", "E04", "E05", "E06", "E16")])
        self.assertEqual(parse_depends("**Depends on** one:E01 to E03 & desk:E05, E08-E09, E10 through E11/E14 + E15"),
                         [("one", "E01"), ("one", "E02"), ("one", "E03"), ("desk", "E05"), (None, "E08"), (None, "E09"),
                          (None, "E10"), (None, "E11"), (None, "E14"), (None, "E15")])
        # a story ID, a dash before prose, "to" in a sentence: no range
        self.assertEqual(parse_depends("**Depends on** E01-S02 (the API), E05 - the shell, E06 to be confirmed"),
                         [(None, "E01"), (None, "E05"), (None, "E06")])
        rows, errors = parse_repo_table(REPO_TABLE.replace("| 1 |", "| 2 |", 1).replace("| `app` | 2 |", "| `app` | 1 |"))
        self.assertEqual(([r["name"] for r in rows], errors), (["app", "core"], []))
        self.assertEqual(parse_repo_table("**Repositories** Order: `core`.")[1], ["the **Repositories** field is not a table (run `blueprint.py migrate`)"])

    def test_epic_order_table(self):
        from speckit_blueprint.model import Blueprint

        def order(text):
            return Blueprint(Path("BLUEPRINT.md"), text).epic_order()

        head = "| Order | Epic | Goal |\n|---|---|---|\n"
        self.assertEqual(order("## 16. Delivery plan\n\n" + head + "| 2 | E01 Sales | x |\n| 1 | `E00` Foundation | x |\n"
                               "| — | E09 Later | x |\n| 3 | Epic 05 — Shell | x |\n| 3 | E06, E07 Two at once | x |\n"
                               "| 4 | E17 Fixes (after E16) | x |\n| 5 | E01 again | x |\n\nAfter the table.\n| 6 | E08 | x |\n"),
                         [0, 1, 5, 6, 7, 17, 9])
        self.assertIsNone(order("| Rank | Epic |\n|---|---|\n| 1 | E00 |\n"))  # no Order column
        self.assertIsNone(order("| Order | Repository |\n|---|---|\n| 1 | core |\n"))  # an epic's Repositories table
        self.assertIsNone(order("```\n" + head + "| 1 | E00 | x |\n```\n"))  # a format example
        self.assertIsNone(order("E00, then E01.\n"))


class MetricsTest(ProjectTest):
    """Transcripts → time, tokens, cost, stories per epic; estimates; status line; export."""

    def setUp(self):
        super().setUp()
        self.json_cli("bootstrap")
        self.json_cli("compile", "E00")
        self.f0 = self.feature("E00")
        (self.f0 / "tasks.md").write_text("- [ ] T001 [US1] E00-S01-T1 Init the repo\n- [ ] T002 [US1] Add the check\n")

    def stop(self, tr):
        for s in tr.sessions:
            self.hook_event({"hook_event_name": "Stop", "session_id": s["id"], "transcript_path": s["path"]})

    def hook_event(self, payload):
        return self.run_cli("hook", stdin=json.dumps(payload), check=False)

    def e00_session(self, cost=1.42, sid="s1"):
        tr = Transcript(self.tmp, sid, start_offset=-2 * 86400)
        tr.prompt(0, "/speckit-implement")
        tr.tool(10, "Read", {"file_path": str(self.f0 / "spec.md")}, 12)
        tr.tool(20, "Bash", {"command": "npm run check"}, 140)
        tr.tool(150, "Edit", {"file_path": str(self.f0 / "tasks.md"), "old_string": "- [ ] T001 [US1] E00-S01-T1 Init the repo",
                              "new_string": "- [x] T001 [US1] E00-S01-T1 Init the repo"}, 151)
        tr.tool(155, "Edit", {"file_path": str(self.f0 / "tasks.md"), "old_string": "- [ ] T002 [US1] Add the check",
                              "new_string": "- [x] T002 [US1] Add the check"}, 156)
        tr.say(170)
        tr.prompt(170 + 3600, "/speckit-blueprint-review")
        tr.tool(3800, "Agent", {"description": "Review E00"}, 4400, agent=("ag1", "general-purpose", "max"))
        tr.say(4410)
        if cost is not None:
            tr.cost(cost)
        tr.write()
        self.stop(tr)
        return tr

    def test_session_metrics(self):
        self.e00_session()
        out = self.json_cli("metrics", "E00")
        m = out["metrics"]
        self.assertEqual((m["agent_s"], m["tool_s"], m["model_s"]), (810.0, 724.0, 86.0))
        self.assertEqual((m["wait_s"], m["gates"], m["sessions"]), (3600.0, 1, 1))
        self.assertAlmostEqual(m["cost_usd"], 1.42)
        self.assertEqual(m["phases"], {"build": 170.0, "review": 640.0})
        self.assertEqual(m["efforts"], {"medium": 7})
        self.assertEqual(m["subagents"], {"general-purpose": {"runs": 1, "efforts": {"max": 1}}})
        self.assertEqual(m["stories"], {"E00-S01": 155.0})
        self.assertGreater(m["tokens"]["cache_read"], 100000)  # the subagent's tokens count too
        brief = self.run_cli("metrics", "E00", "--brief").stdout
        self.assertIn("agent 13m (model 1m 26s, tools 12m) · 1 human gate (waited 1h)", brief)
        self.assertIn("$1.42", brief)

    def summary(self, sid):
        path = self.tmp / ".specify" / "blueprint-state" / "metrics" / "sessions" / f"{sid}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def stop_now(self, tr):
        """The Stop event at the end of the turn just written; the session stays open."""
        path = tr.write(live=True)
        self.hook_event({"hook_event_name": "Stop", "session_id": tr.sid, "transcript_path": str(path)})
        return path

    def bash(self, *args):
        return {"command": "python3 .specify/extensions/blueprint/scripts/blueprint.py " + " ".join(args)}

    def test_reading_an_epic_does_not_make_the_work_its(self):
        # A migration session reads E00's files while a feature of no epic is active: no epic gets its work.
        chore = self.tmp / "specs" / "004-chore-migrate"
        chore.mkdir(parents=True)
        (chore / "spec.md").write_text("# Migrate\n")
        self.json_cli("activate", "specs/004-chore-migrate")
        tr = Transcript(self.tmp, "m1", start_offset=-3600)
        tr.prompt(0, "Migrate the extension; E00 is in specs/001-e00-foundation")
        tr.tool(10, "Read", {"file_path": str(self.f0 / "spec.md")}, 11)
        tr.tool(20, "Bash", {"command": f"grep -n AC {self.f0}/tasks.md"}, 21)
        tr.tool(30, "Edit", {"file_path": str(chore / "plan.md"), "old_string": "a", "new_string": "b"}, 31)
        tr.say(40)
        tr.write()
        self.stop(tr)
        self.assertEqual(self.json_cli("metrics", "E00")["metrics"]["sessions"], 0)
        features = self.summary("m1")["features"]
        self.assertEqual((list(features), features[""]["active_s"]), ([""], 40.0))

    def test_a_compile_in_the_session_moves_the_work(self):
        tr = Transcript(self.tmp, "c1", start_offset=-3600)
        tr.prompt(0, "continue")
        tr.tool(5, "Bash", self.bash("status", "--json"), 8)
        tr.tool(10, "Bash", self.bash("compile", "E01"), 14, output=self.run_cli("compile", "E01").stdout)
        tr.tool(20, "Read", {"file_path": str(self.feature() / "spec.md")}, 21)
        tr.say(30)
        self.stop_now(tr)
        # the work before the compile too: a session that starts by compiling an epic is getting it ready
        self.assertEqual(list(self.summary("c1")["features"]), ["default:E01"])
        tr.prompt(100, "back to E00")
        tr.tool(110, "Bash", self.bash("activate", "E00"), 112, output=self.run_cli("activate", "E00").stdout)
        tr.tool(115, "Bash", self.bash("activate", "E09"), 116, output="error: no '## Epic E09'", error=True)
        tr.say(130)
        self.stop_now(tr)
        features = self.summary("c1")["features"]
        self.assertEqual(features["default:E01"]["active_s"], 42.0)  # the first turn stays E01's
        self.assertEqual(features["default:E00"]["active_s"], 18.0)  # from the activate on; the failed one changes nothing

    def test_a_change_the_session_does_not_show_moves_only_later_turns(self):
        tr = Transcript(self.tmp, "o1", start_offset=-3600)
        tr.prompt(0, "/speckit-implement")
        tr.tool(10, "Read", {"file_path": str(self.f0 / "spec.md")}, 11)
        tr.say(20)
        self.stop_now(tr)
        chore = self.tmp / "specs" / "004-chore"
        chore.mkdir(parents=True)
        self.json_cli("activate", "specs/004-chore")  # as /speckit.specify or another terminal would
        tr.prompt(100, "now the chore")
        tr.tool(110, "Read", {"file_path": str(chore / "spec.md")}, 111)
        tr.say(150)
        self.stop_now(tr)
        features = self.summary("o1")["features"]
        self.assertEqual((features["default:E00"]["active_s"], features[""]["active_s"]), (20.0, 50.0))

    def test_an_amendment_belongs_to_its_blueprint_while_no_epic_is_active(self):
        (self.tmp / ".specify" / "feature.json").unlink()
        tr = Transcript(self.tmp, "a1", start_offset=-3600)
        tr.prompt(0, "/speckit-blueprint-amend Sara wants to undo a sale")
        tr.tool(10, "Edit", {"file_path": str(self.blueprint()), "old_string": "x", "new_string": "y"}, 12)
        tr.tool(30, "Bash", self.bash("compile", "E01"), 34, output=self.run_cli("compile", "E01").stdout)
        tr.say(40)
        tr.write()
        self.stop(tr)
        out = self.json_cli("metrics")
        self.assertEqual(out["discovery_and_amendments"]["default:amend"]["agent_s"], 34.0)  # up to the compile
        self.assertEqual(out["epics"]["E01"]["metrics"]["agent_s"], 6.0)
        # while an epic is active, an amendment is part of its work (and so is what follows while the phase reads "amend")
        tr = Transcript(self.tmp, "a2", start_offset=-1800)
        tr.prompt(0, "/speckit-blueprint-amend one more change")
        tr.tool(10, "Edit", {"file_path": str(self.blueprint()), "old_string": "y", "new_string": "z"}, 12)
        tr.say(20)
        tr.write()
        self.stop(tr)
        out = self.json_cli("metrics")
        self.assertEqual(out["discovery_and_amendments"]["default:amend"]["agent_s"], 34.0)
        self.assertEqual(out["epics"]["E01"]["metrics"]["agent_s"], 26.0)

    def test_sessions_the_hook_never_saw(self):
        # backfill without a record of the active feature: compiles, activates, Spec Kit's report of the
        # active feature and writes into a feature folder count; reading one does not
        implementer = Transcript(self.tmp, "h3", start_offset=-1800)
        implementer.prompt(0, "continue")
        implementer.tool(10, "Bash", {"command": ".specify/scripts/bash/check-prerequisites.sh --json --require-tasks"}, 11,
                         output=json.dumps({"FEATURE_DIR": str(self.f0), "AVAILABLE_DOCS": ["tasks.md"]}))
        implementer.tool(20, "Edit", {"file_path": str(self.tmp / "src" / "app.ts"), "old_string": "a", "new_string": "b"}, 21)
        implementer.say(40)
        implementer.write()
        reader = Transcript(self.tmp, "h1", start_offset=-7200)
        reader.prompt(0, "what does specs/001-e00-foundation say?")
        reader.tool(10, "Read", {"file_path": str(self.f0 / "spec.md")}, 11)
        reader.say(20)
        reader.write()
        builder = Transcript(self.tmp, "h2", start_offset=-3600)
        builder.prompt(0, "/speckit-implement")
        builder.tool(10, "Read", {"file_path": str(self.f0 / "spec.md")}, 11)
        builder.tool(20, "Edit", {"file_path": str(self.f0 / "tasks.md"), "old_string": "- [ ] T001 [US1] E00-S01-T1 Init the repo",
                                  "new_string": "- [x] T001 [US1] E00-S01-T1 Init the repo"}, 21)
        builder.say(30)
        builder.write()
        out = self.json_cli("metrics", "--backfill", str(reader.dir))
        self.assertEqual((out["sessions"], out["sessions_with_blueprint_work"]), (3, 2))
        m = self.json_cli("metrics", "E00")["metrics"]
        self.assertEqual((m["sessions"], m["agent_s"]), (2, 70.0))

    def test_an_unknown_cost_is_not_zero(self):
        self.add_epic_02()
        self.e00_session(cost=None)
        self.finish("E00")
        self.assertIsNone(self.json_cli("metrics", "E00")["metrics"]["cost_usd"])
        self.assertIn("· cost unknown ·", self.run_cli("metrics", "E00", "--brief").stdout)
        e01 = self.json_cli("estimate", "E01")
        self.assertEqual((e01["agent_s"], e01["cost_usd"]), ([2430.0, 3645.0], None))
        rest = self.json_cli("estimate")
        self.assertIsNone(rest["remaining"]["cost_usd"])
        for brief in (e01["brief"], rest["brief"]):
            self.assertIn("cost unknown", brief)
            self.assertNotIn("$", brief)
        out = self.tmp / "dashboard.html"
        self.run_cli("dashboard", "--export", str(out))
        state = json.loads(out.read_text(encoding="utf-8").split("window.__STATE__ = ", 1)[1].split(";</script>", 1)[0])
        self.assertEqual((state["totals"]["cost_usd"], state["blueprints"][0]["epics"][0]["actual"]["cost_usd"]), (None, None))

    def test_a_session_that_ended_gets_its_cost(self):
        tr = Transcript(self.tmp, "f1", start_offset=-600)
        tr.prompt(0, "/speckit-implement")
        tr.tool(10, "Read", {"file_path": str(self.f0 / "spec.md")}, 12)
        tr.say(20)
        self.stop_now(tr)
        self.assertIsNone(self.summary("f1")["cost_usd"])  # Claude Code writes the cost when the session ends
        self.json_cli("compile", "E01")  # another epic is active by the time it arrives
        tr.cost(0.9)
        path = tr.write(live=True)
        later = time.time() + 30
        os.utime(path, (later, later))
        other = Transcript(self.tmp, "f2", start_offset=-60)
        other.prompt(0, "/speckit-implement")
        other.say(5)
        self.stop_now(other)  # any later Stop event reads the ended session again
        s = self.summary("f1")
        self.assertEqual((s["cost_usd"], list(s["features"])), (0.9, ["default:E00"]))
        m = self.json_cli("metrics", "E00")["metrics"]
        self.assertAlmostEqual(m["cost_usd"], 0.9)
        self.assertFalse(m["cost_estimated"])

    def test_work_after_the_cost_record_is_priced_at_the_rate(self):
        tr = Transcript(self.tmp, "r1", start_offset=-3600)
        tr.prompt(0, "/speckit-implement")
        tr.tool(10, "Read", {"file_path": str(self.f0 / "spec.md")}, 12)
        tr.say(20)
        tr.cost(0.5)  # the session ended here and was resumed: the record covers the work before it
        tr.prompt(100, "go on")
        tr.tool(110, "Read", {"file_path": str(self.f0 / "plan.md")}, 112)
        tr.say(120)
        tr.write()
        self.stop(tr)
        part = self.summary("r1")["features"]["default:E00"]
        self.assertAlmostEqual(part["cost_usd"], 0.5)
        self.assertGreater(part["weighted_unpriced"], 0)
        m = self.json_cli("metrics", "E00")["metrics"]
        self.assertEqual((round(m["cost_usd"], 4), m["cost_estimated"]), (1.0, True))
        self.assertIn("$1.00+", self.run_cli("metrics", "E00", "--brief").stdout)

    def test_running_session_is_priced_at_the_learned_rate(self):
        self.e00_session()
        self.json_cli("compile", "E01")
        tr = Transcript(self.tmp, "s2", start_offset=-600)
        tr.prompt(0, "/speckit-implement")
        tr.tool(30, "Read", {"file_path": str(self.feature() / "spec.md")}, 31)
        tr.write(live=True)
        self.stop(tr)
        m = self.json_cli("metrics", "E01")["metrics"]
        self.assertTrue(m["cost_estimated"])
        self.assertGreater(m["cost_usd"], 0)

    def test_estimates(self):
        self.add_epic_02()
        self.e00_session()
        self.finish("E00")
        e01 = self.json_cli("estimate", "E01")
        self.assertEqual((e01["history"], e01["confidence"], e01["acs"]), (1, "low", 3))
        self.assertEqual(e01["agent_s"], [2430.0, 3645.0])  # 810 s per AC × 3 ACs; one epic: P90 widened
        everything = self.json_cli("estimate")
        self.assertEqual(sorted(everything["epics"]), ["E01", "E02"])
        rem = everything["remaining"]
        self.assertEqual((rem["epics"], rem["parallelism"]), (2, 1))
        self.assertLessEqual(rem["agent_s"][0], rem["agent_s"][1])
        # compile records the estimate made before building
        self.json_cli("compile", "E01")
        trace = json.loads((self.feature() / ".blueprint.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["estimate"]["agent_s"], [2430.0, 3645.0])

    def test_backfill_and_status_line(self):
        tr = self.e00_session(sid="s3")
        store = self.tmp / ".specify" / "blueprint-state" / "metrics" / "sessions"
        shutil.rmtree(store)
        out = self.json_cli("metrics", "--backfill", str(tr.dir))
        self.assertEqual((out["sessions"], out["sessions_with_blueprint_work"]), (1, 1))
        self.assertTrue((store / "s3.json").is_file())
        line = self.run_cli("statusline", stdin=json.dumps({"workspace": {"current_dir": str(self.tmp)}, "cost": {"total_cost_usd": 0.84},
                                                            "model": {"display_name": "Sonnet 5.5"}})).stdout.strip()
        self.assertEqual(line, "E00·S01 · 0/2 tasks · $0.84 · Sonnet 5.5")

    def test_dashboard_export(self):
        self.e00_session()
        out = self.tmp / "dashboard.html"
        res = json.loads(self.run_cli("dashboard", "--export", str(out)).stdout)
        html = out.read_text(encoding="utf-8")
        self.assertEqual(res["exported"], str(out.resolve()))
        self.assertIn("window.__STATE__ = ", html)
        self.assertNotIn("<!--STATE-->", html)
        state = json.loads(html.split("window.__STATE__ = ", 1)[1].split(";</script>", 1)[0])
        e00 = state["blueprints"][0]["epics"][0]
        self.assertEqual((e00["id"], e00["tasks_total"], e00["actual"]["agent_s"]), ("E00", 2, 810.0))
        self.assertEqual(e00["stories"][0]["tasks"][0]["seed"], "E00-S01-T1")


    def test_dashboard_server(self):
        import urllib.request

        self.e00_session()
        proc = subprocess.Popen([sys.executable, str(SCRIPT), "dashboard", "--port", "0", "--no-open"], cwd=self.tmp,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        local = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 127.0.0.1 never goes through a proxy
        try:
            line = proc.stdout.readline()
            url = line.split("dashboard: ", 1)[1].split(" ", 1)[0]
            self.assertTrue(url.startswith("http://127.0.0.1:"))
            state = json.loads(local.open(url + "api/state", timeout=20).read())
            self.assertEqual(state["blueprints"][0]["epics"][0]["actual"]["agent_s"], 810.0)
            page = local.open(url, timeout=20).read().decode("utf-8")
            self.assertIn("<!--STATE-->", page)
        finally:
            proc.terminate()
            proc.wait(timeout=10)


class UnitsTest(unittest.TestCase):
    def test_formats(self):
        from speckit_blueprint import units

        self.assertEqual([units.duration(s) for s in (5, 125, 600, 7500, 7200)], ["5s", "2m 5s", "10m", "2h 5m", "2h"])
        self.assertEqual(units.duration(90000, calendar=True), "1d 1h")
        self.assertEqual((units.cost(3.1), units.cost(0.84), units.cost(1234.5)), ("$3.10", "$0.84", "$1,234.50"))
        self.assertEqual((units.tokens(2_400_000), units.tokens(310_000), units.tokens(512)), ("2.4M", "310k", "512"))
        self.assertEqual(units.span(2700, 4800), "45m – 1h 20m")
        self.assertEqual(units.span(60, 60), "1m")


if __name__ == "__main__":
    unittest.main()
