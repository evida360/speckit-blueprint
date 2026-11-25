"""0.7: the task graph, and building an epic in segments (one subagent per segment, the serial ones too).

Run from the repository root:  python3 -m unittest discover -s tests -v
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_blueprint import DONE_REVIEW, REPO, ProjectTest, Transcript  # noqa: E402

from speckit_blueprint.core import task_counts  # noqa: E402
from speckit_blueprint.tasks_graph import files_of, parse, plan, render  # noqa: E402

SCRIPT_CMD = "python3 .specify/extensions/blueprint/scripts/blueprint.py"

TASKS = """# Tasks: Record sales

## Phase 1: Setup
- [x] T001 Init the project in `package.json`
- [ ] T002 [P] Lint config in `.eslintrc.json`
- [ ] T003 [P] Test config in `vitest.config.ts`

## Phase 2: Foundational
- [ ] T004 DB schema in `src/db/schema.ts`
- [ ] T005 Shared types in `src/types.ts`

## Phase 3: User Story 1 - Record a sale (Priority: P1)
- [ ] T006 [US1] E01-S01-T1 Sale model test in `src/sale/model.test.ts` (depends on T005)
- [ ] T007 [US1] E01-S01-T2 Sale model in `src/sale/model.ts`
- [ ] T008 [US1] E01-S01-T3 Sale API in `src/sale/api.ts`

## Phase 4: User Story 2 - Undo a sale (Priority: P2)
- [ ] T009 [US2] E01-S02-T1 Undo query in `src/undo/query.ts` (depends on T005)
- [ ] T010 [US2] E01-S02-T2 Undo button in `src/undo/button.tsx`

## Phase 5: Polish
- [ ] T011 Docs in `README.md`
"""


def stage_tasks(p):
    return [[s["tasks"] for s in st["segments"]] for st in p["stages"]]


class TasksGraphTest(unittest.TestCase):
    def test_defaults_are_todays_sequence(self):
        text = "\n".join(f"- [ ] T00{i} Step {i} in `src/f{i}.ts`" for i in range(1, 5))
        p = plan(text)
        self.assertEqual(stage_tasks(p), [[["T001", "T002", "T003", "T004"]]])
        self.assertTrue(any("no stage runs in parallel" in w for w in p["warnings"]))

    def test_explicit_dependencies_open_chains(self):
        p = plan(TASKS)
        self.assertEqual(p["errors"], [])
        self.assertEqual(stage_tasks(p), [[["T002", "T003", "T004", "T005"]],
                                          [["T006", "T007", "T008"], ["T009", "T010"]],
                                          [["T011"]]])
        self.assertEqual(render(p), "S1(4) → S2a(3) ∥ S2b(2) → S3(1)")
        self.assertEqual([st["kind"] for st in p["stages"]], ["serial", "parallel", "serial"])
        self.assertEqual(p["stats"]["widest"], 2)

    def test_a_p_group_opens_and_joins(self):
        text = ("- [ ] T001 Base in `src/base.ts`\n"
                "- [ ] T002 [P] A in `src/a.ts`\n- [ ] T003 [P] A2 in `src/a2.ts`\n"
                "- [ ] T004 [P] B in `src/b.ts`\n- [ ] T005 [P] B2 in `src/b2.ts`\n"
                "- [ ] T006 Join in `src/join.ts`\n")
        p = plan(text, min_chain_tasks=1, max_parallel=8)
        self.assertEqual(stage_tasks(p), [[["T001"]], [["T002"], ["T003"], ["T004"], ["T005"]], [["T006"]]])
        packed = plan(text, min_chain_tasks=1, max_parallel=2)
        self.assertEqual(len(packed["stages"][1]["segments"]), 2)
        merged = plan(text, min_chain_tasks=2, max_parallel=8)  # single tasks join each other
        self.assertEqual(stage_tasks(merged)[1], [["T002", "T003"], ["T004", "T005"]])

    def test_a_shared_file_keeps_tasks_in_one_chain(self):
        text = TASKS.replace("Undo button in `src/undo/button.tsx`", "Undo button in `src/undo/button.tsx` and `src/sale/api.ts`")
        p = plan(text)
        # stories 1 and 2 share a file: nothing runs in parallel, and neighbouring serial blocks are one segment
        self.assertEqual([st["kind"] for st in p["stages"]], ["serial"])

    def test_a_task_without_files_stays_serial(self):
        p = plan(TASKS.replace("Undo query in `src/undo/query.ts`", "Undo query"))
        self.assertEqual([st["kind"] for st in p["stages"]], ["serial"])
        self.assertTrue(any("T009 names no file" in w for w in p["warnings"]))

    def test_errors(self):
        self.assertIn("comes after it", " ".join(plan(TASKS.replace("(depends on T005)", "(depends on T010)", 1))["errors"]))
        self.assertIn("not in tasks.md", " ".join(plan(TASKS.replace("(depends on T005)", "(depends on T099)", 1))["errors"]))
        self.assertIn("appears twice", " ".join(plan(TASKS + "- [ ] T011 again `x.ts`\n")["errors"]))
        self.assertIn("compiled file", " ".join(plan(TASKS + "- [ ] T012 Edit `specs/002-e01/spec.md`\n")["errors"]))

    def test_ticked_tasks_drop_out(self):
        done = TASKS.replace("- [ ] T002", "- [x] T002").replace("- [ ] T003", "- [x] T003") \
                    .replace("- [ ] T004", "- [x] T004").replace("- [ ] T005", "- [x] T005")
        p = plan(done)
        self.assertEqual(stage_tasks(p)[0], [["T006", "T007", "T008"], ["T009", "T010"]])
        self.assertEqual(p["stats"]["open"], 6)

    def test_the_suffix_keeps_every_parser(self):
        t = parse(TASKS)["tasks"]
        self.assertEqual(t[5]["deps"], ["T005"])
        self.assertEqual(t[5]["story"], "US1")
        self.assertEqual(files_of("Sale model in `src/sale/model.ts` (depends on T005)"), ["src/sale/model.ts"])
        self.assertEqual(files_of("Wire src/app/routes.ts and lib/ into it"), ["src/app/routes.ts", "lib/"])
        tmp = Path(__file__).resolve().parent / "_tmp_tasks.md"
        try:
            tmp.write_text(TASKS)
            self.assertEqual(task_counts(tmp), (11, 1))
        finally:
            tmp.unlink()
        from speckit_blueprint.dashboard import read_tasks

        tmp.write_text(TASKS)
        try:
            row = next(r for r in read_tasks(tmp, ["E01-S01", "E01-S02"], {}) if r["id"] == "T006")
            self.assertEqual(row["title"], "Sale model test in `src/sale/model.test.ts`")
        finally:
            tmp.unlink()

    def test_repositories(self):
        text = ("- [ ] T001 [core] API in `src/api.ts`\n- [ ] T002 [app] Client in `src/client.ts` (depends on T001)\n"
                "- [ ] T003 [app] Screen in `src/screen.ts`\n- [ ] T004 [core] Docs in `src/api.ts` (depends on T001)\n")
        g = plan(text, ["core", "app"], ["core", "app"], min_chain_tasks=1)
        self.assertEqual(g["errors"], [])
        # the same path in two repositories is two files
        self.assertEqual(stage_tasks(g), [[["T001"]], [["T002", "T003"], ["T004"]]])
        bad = plan(text.replace(" (depends on T001)", "", 1).replace("- [ ] T002 [app]", "- [ ] T000 [x] Pre `a.ts`\n- [ ] T002 [app]"),
                   ["core", "app"], ["core", "app"])
        self.assertEqual(bad["errors"], [])


class SegmentsTest(ProjectTest):
    """The orchestrator's commands, the segments' commands and the hook's rules, end to end."""

    def setUp(self):
        super().setUp()
        self.json_cli("bootstrap")
        self.json_cli("compile", "E00")
        self.finish("E00")
        self.json_cli("compile", "E01")
        self.f = self.feature()
        (self.f / "tasks.md").write_text(TASKS)
        self.configure("build:\n  mode: segments\n")

    def hook(self, payload):
        return self.run_cli("hook", stdin=json.dumps({"hook_event_name": "PreToolUse", **payload}), check=False)

    def bash(self, cmd, agent=None, level="high", session="s1", mode="acceptEdits"):
        p = {"tool_name": "Bash", "tool_input": {"command": cmd}, "effort": {"level": level}, "session_id": session,
             "permission_mode": mode}
        if agent:
            p["agent_id"], p["agent_type"] = agent, "general-purpose"
        return self.hook(p)

    def write(self, path, agent=None):
        p = {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "x"}, "effort": {"level": "high"}}
        if agent:
            p["agent_id"] = agent
        return self.hook(p)

    def start(self):
        self.assertEqual(self.bash(f"{SCRIPT_CMD} segments start").returncode, 0)
        return self.json_cli("segments", "start")

    def begin(self, sid, agent):
        res = self.bash(f"{SCRIPT_CMD} segment start {sid}", agent=agent)
        self.assertEqual(res.returncode, 0, res.stderr)
        return self.run_cli("segment", "start", sid).stdout

    def finish_segment(self, sid, tasks):
        self.json_cli("tick", *tasks)
        self.assertIn(f"DONE {sid}", self.run_cli("segment", "done", sid, stdin=f"built {sid}").stdout)

    def test_mode_sequential_refuses(self):
        self.configure("")
        res = self.run_cli("segments", "start", check=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("build.mode is sequential", res.stderr)

    def test_a_whole_build(self):
        out = self.start()
        self.assertEqual(out["plan"], "S1(4) → S2a(3) ∥ S2b(2) → S3(1)")
        self.assertEqual(out["effort"], "high")
        nxt = self.json_cli("segments", "next")
        self.assertEqual([x["segment"] for x in nxt["start"]], ["S1"])  # the trunk is one subagent too
        self.assertEqual(nxt["start"][0]["effort"], "high")
        self.assertIn("segment start S1", nxt["start"][0]["prompt"])
        self.assertIn("T002, T003, T004, T005", nxt["start"][0]["prompt"])

        # a subagent below the session's effort is refused; the right one registers
        res = self.bash(f"{SCRIPT_CMD} segment start S1", agent="a1", level="medium")
        self.assertEqual(res.returncode, 2)
        self.assertIn("below the session's high", res.stderr)
        brief = self.begin("S1", "a1")
        self.assertIn("T004 DB schema", brief)
        self.assertIn("run the repository gate after each task", brief)
        # one subagent per segment
        self.assertIn("held by another subagent", self.bash(f"{SCRIPT_CMD} segment start S1", agent="a9").stderr)
        # while S1 builds, the orchestrator writes nothing in the project
        self.assertEqual(self.write(self.tmp / "src" / "x.ts").returncode, 2)
        self.assertEqual(self.write(self.tmp / "src" / "db" / "schema.ts", agent="a1").returncode, 0)
        self.assertEqual(self.write(self.f / "tasks.md", agent="a1").returncode, 2)
        self.assertEqual(self.write(self.f / "spec.md", agent="a1").returncode, 2)
        self.assertEqual(self.write(self.tmp / "docs" / "DECISIONS.md", agent="a1").returncode, 2)
        self.assertEqual(self.bash("git commit -am wip", agent="a1").returncode, 2)
        self.assertEqual(self.bash("npm install left-pad", agent="a1").returncode, 0)  # a serial segment may
        self.assertEqual(self.bash(f"{SCRIPT_CMD} tick T007", agent="a1").returncode, 2)  # not its task
        self.assertEqual(self.bash(f"{SCRIPT_CMD} segments next", agent="a1").returncode, 2)
        # done needs every task ticked
        self.assertNotEqual(self.run_cli("segment", "done", "S1", stdin="x", check=False).returncode, 0)
        self.finish_segment("S1", ["T002", "T003", "T004", "T005"])
        self.assertTrue(self.json_cli("segments", "next")["join"])
        j = self.json_cli("segments", "join")
        self.assertTrue(j["ok"])

        # stage 2: two chains at once, each its own subagent
        nxt = self.json_cli("segments", "next")
        self.assertEqual([x["segment"] for x in nxt["start"]], ["S2a", "S2b"])
        self.assertIn("at the same time", nxt["start"][0]["prompt"])
        self.begin("S2a", "a2")
        brief = self.begin("S2b", "a3")
        self.assertIn("Do not write files of S2a", brief)
        self.assertIn("only the tests and lint of your own files", brief)
        self.assertIn("this subagent builds S2a", self.bash(f"{SCRIPT_CMD} segment start S2b", agent="a2").stderr)
        self.assertEqual(self.write(self.tmp / "src" / "sale" / "api.ts", agent="a3").returncode, 2)  # S2a's
        self.assertEqual(self.write(self.tmp / "src" / "undo" / "button.tsx", agent="a3").returncode, 0)
        self.assertEqual(self.write(self.tmp / "src" / "undo" / "helper.ts", agent="a3").returncode, 0)  # leased
        self.assertEqual(self.write(self.tmp / "src" / "undo" / "helper.ts", agent="a2").returncode, 2)
        self.assertEqual(self.bash("npm install left-pad", agent="a3").returncode, 2)  # parallel: no installs
        self.assertEqual(self.bash("npm test -- src/undo", agent="a3").returncode, 0)

        # a question goes to the orchestrator and the answer back to the same subagent
        ask = self.run_cli("segment", "ask", "S2b", stdin="Q1 | T009 | closed | Undo window? | A 5 min · B 1 h | A [BLUEPRINT — E01-S02 AC-1]\nDone: none · Next: T009\n").stdout
        self.assertIn("CHECKPOINT S2b: Q-S2b-1", ask)
        st = self.json_cli("segments", "status")
        waiting = [s for s in st["segments"] if s["status"] == "waiting"]
        self.assertEqual(waiting[0]["questions"][0]["default"], "A [BLUEPRINT — E01-S02 AC-1]")
        self.assertIn("Ask the user every question", st["next"])
        ans = self.json_cli("segments", "answer", "S2b", stdin="Q-S2b-1: A, five minutes")
        self.assertEqual(ans["agent"], "a3")
        self.assertIn("Continue from T009", ans["send"])
        self.assertIn("Q-S2b-1: A, five minutes", self.run_cli("segment", "start", "S2b").stdout)

        self.finish_segment("S2a", ["T006", "T007", "T008"])
        self.assertIn("still has unticked", self.run_cli("segment", "done", "S2b", stdin="x", check=False).stderr)
        self.finish_segment("S2b", ["T009", "T010"])
        self.assertTrue(self.json_cli("segments", "join")["ok"])
        nxt = self.json_cli("segments", "next")
        self.assertEqual([x["segment"] for x in nxt["start"]], ["S3"])
        brief = self.begin("S3", "a4")
        self.assertIn("built S2a", brief)  # notes of earlier segments
        self.finish_segment("S3", ["T011"])
        self.json_cli("segments", "join")
        self.assertTrue(self.json_cli("segments", "next")["done"])
        self.assertEqual(task_counts(self.f / "tasks.md"), (11, 11))
        log = (self.f / "build.md").read_text()
        for line in ("## Run r", "### S1 done", "### Questions S2b", "### Answers S2b", "### Join stage 2"):
            self.assertIn(line, log)
        # the run is over: the session writes again
        self.assertEqual(self.write(self.tmp / "src" / "x.ts").returncode, 0)

    def test_a_red_join_adds_a_fix_segment(self):
        self.configure("build:\n  mode: segments\nrepositories:\n  hub:\n    path: .\n    gate: exit 3\n")
        self.start()
        self.begin("S1", "a1")
        self.finish_segment("S1", ["T002", "T003", "T004", "T005"])
        j = self.json_cli("segments", "join")
        self.assertFalse(j["ok"])
        self.assertEqual(j["fix_segment"], "S1-fix")
        nxt = self.json_cli("segments", "next")
        self.assertEqual(nxt["start"][0]["segment"], "S1-fix")
        self.assertIn("gate is red", nxt["start"][0]["prompt"])

    def test_retry_and_resume(self):
        self.start()
        self.begin("S1", "a1")
        self.json_cli("tick", "T002")
        nxt = self.json_cli("segments", "next", "--retry", "S1")
        self.assertEqual(nxt["start"][0]["open_tasks"], ["T003", "T004", "T005"])
        brief = self.begin("S1", "a2")
        self.assertIn("an earlier agent worked here", brief)
        self.assertEqual(self.write(self.tmp / "src" / "types.ts", agent="a1").returncode, 0)  # a1 is no builder any more
        # a new session: the run is planned again from the ticks
        self.assertEqual(self.bash(f"{SCRIPT_CMD} segments start --resume", session="s2").returncode, 0)
        out = self.json_cli("segments", "start", "--resume")
        self.assertTrue(out["resumed"])
        self.assertEqual(out["lost"], ["S1"])
        self.assertTrue(out["plan"].startswith("S1(3)"))

    def test_skills_gate_the_first_edit(self):
        self.configure("build:\n  mode: segments\n  skills: [frontend-design]\n")
        self.start()
        brief = self.begin("S1", "a1")
        self.assertIn("Load these skills with the Skill tool before your first edit", brief)
        res = self.write(self.tmp / "src" / "db" / "schema.ts", agent="a1")
        self.assertEqual(res.returncode, 2)
        self.assertIn("frontend-design", res.stderr)
        self.hook({"tool_name": "Skill", "tool_input": {"skill": "frontend-design"}, "agent_id": "a1"})
        self.assertEqual(self.write(self.tmp / "src" / "db" / "schema.ts", agent="a1").returncode, 0)

    def test_permission_mode_warning(self):
        self.assertEqual(self.bash(f"{SCRIPT_CMD} segments start", mode="default").returncode, 0)
        self.assertIn("asks before every edit", self.json_cli("segments", "start")["warning"])

    def test_stop_lets_the_session_write(self):
        self.start()
        self.begin("S1", "a1")
        self.assertEqual(self.write(self.tmp / "src" / "x.ts").returncode, 2)
        self.json_cli("segments", "stop")
        self.assertEqual(self.write(self.tmp / "src" / "x.ts").returncode, 0)

    def test_tick_and_decisions(self):
        out = json.loads(self.run_cli("tick", "T002", "T001", "T099", check=False).stdout)
        self.assertEqual((out["ticked"], out["already"], out["missing"]), (["T002"], ["T001"], ["T099"]))
        a = self.json_cli("decision", "add", stdin="Money as integer minor units\n**Context** [NOT IN BLUEPRINT — searched: x]\n**Decision** int")
        b = self.json_cli("decision", "add", stdin="# Second one\n**Decision** y")
        self.assertEqual((a["id"], b["id"]), ("DR-E01-1", "DR-E01-2"))
        log = (self.tmp / "docs" / "DECISIONS.md").read_text()
        self.assertIn("| DR-E01-1 | Money as integer minor units | E01 build | Accepted |", log)
        self.assertIn("## DR-E01-2 — Second one", log)
        self.assertLess(log.index("| DR-E01-2 |"), log.index("## Session decisions"))

    def test_exclusive_runs_the_command(self):
        res = self.run_cli("exclusive", "db", "--", "python3", "-c", "import sys; print('ran'); sys.exit(3)", check=False)
        self.assertEqual(res.returncode, 3)
        self.assertIn("ran", res.stdout)

    def test_graph_and_context_facts(self):
        g = self.json_cli("tasks-graph", "--json")
        self.assertTrue(g["ok"])
        self.assertEqual(g["plan"], "S1(4) → S2a(3) ∥ S2b(2) → S3(1)")
        (self.f / "tasks.md").write_text(TASKS.replace("(depends on T005)", "(depends on T099)", 1))
        self.assertEqual(self.run_cli("tasks-graph", "--check", check=False).returncode, 1)
        (self.f / "tasks.md").write_text(TASKS)
        b = self.json_cli("context")["build"]
        self.assertEqual((b["mode"], b["plan"], b["running"]), ("segments", "S1(4) → S2a(3) ∥ S2b(2) → S3(1)", None))

    def test_config(self):
        for bad, words in (("build:\n  mode: fast\n", "build.mode"), ("build:\n  max_parallel: 20\n", "1 to 8"),
                           ("build:\n  colour: red\n", "unknown key")):
            self.configure(bad)
            res = self.run_cli("status", "--json", check=False)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn(words, res.stderr)

    def test_metrics_count_the_segments_ticks(self):
        self.start()
        tr = Transcript(self.tmp, "s9", start_offset=-3600)
        tr.prompt(0, "/speckit-implement")
        tr.tool(10, "Bash", {"command": f"{SCRIPT_CMD} activate E01"}, 11, output='{"feature_directory": "specs/002-e01-record-sales"}')
        tr.tool(20, "Agent", {"description": "E01 S1"}, 600, agent=("seg1", "general-purpose", "high"), agent_times=[30, 300, 590])
        tr.say(610, end=True)
        tr.write()
        sub = self.tmp / ".transcripts" / "s9" / "subagents" / "agent-seg1.jsonl"
        lines = [json.loads(x) for x in sub.read_text().splitlines()]
        lines[0]["message"]["content"] = [{"type": "tool_use", "id": "x1", "name": "Bash", "input": {"command": f"{SCRIPT_CMD} segment start S1"}}]
        lines[1]["message"]["content"] = [{"type": "tool_use", "id": "x2", "name": "Bash", "input": {"command": f"{SCRIPT_CMD} tick T006 T007"}}]
        sub.write_text("".join(json.dumps(x) + "\n" for x in lines))
        self.run_cli("hook", stdin=json.dumps({"hook_event_name": "Stop", "session_id": "s9", "transcript_path": str(tr.dir / "s9.jsonl")}), check=False)
        m = self.json_cli("metrics", "E01")["metrics"]
        self.assertIn("E01-S01", m["stories"])
        self.assertEqual(m["segments"]["S1"]["runs"], 1)
        self.assertGreater(m["segment_agent_s"], 0)


class ManifestTest(unittest.TestCase):
    def test_after_tasks_and_skill_matcher(self):
        manifest = (REPO / "extension.yml").read_text()
        self.assertRegex(manifest, r"after_tasks:\n\s+command: speckit\.blueprint\.graph\n\s+optional: false")
        self.assertIn("Bash|Skill", manifest)
        guide = (REPO / "references" / "segments.md").read_text()
        for rule in ("exactly one\nsubagent", "in one message", "segment start <S>", "never give one segment"):
            self.assertIn(rule.replace("\n", " ") if "\n" in rule else rule, guide.replace("\n", " ") if "\n" in rule else guide)
        self.assertIn("references/segments.md", (REPO / "commands" / "context.md").read_text())


if __name__ == "__main__":
    unittest.main()
