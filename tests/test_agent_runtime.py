import tempfile
import unittest
from pathlib import Path

from backend.agent_runtime import AgentRuntime, command_access_scope, command_allowed_without_prompt, continues_existing_task, response_misstates_command_access, response_needs_web_retry
from backend.storage import Storage


TOOL = {"type": "function", "function": {"name": "read_file_range", "description": "read", "parameters": {"type": "object", "properties": {}, "required": []}}}
COMMAND_TOOL = {"type": "function", "function": {"name": "run_terminal_command", "description": "run", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
WEB_TOOL = {"type": "function", "function": {"name": "web_search", "description": "search", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}


class AgentRuntimeTests(unittest.TestCase):
    def test_follow_up_detection_keeps_continuations_and_resets_new_tasks(self):
        self.assertTrue(continues_existing_task("continue and finish the remaining tests"))
        self.assertTrue(continues_existing_task("why did that fail?"))
        self.assertFalse(continues_existing_task("New task: build a weather dashboard"))
        self.assertFalse(continues_existing_task("Explain dependency injection in Python"))

    def test_history_keeps_recent_messages_within_token_and_turn_limits(self):
        messages = [{"role": "user" if index % 2 == 0 else "assistant", "content": f"message-{index} " * 30} for index in range(30)]
        bounded = AgentRuntime._bounded_history(messages, max_tokens=800, max_messages=12)
        self.assertLessEqual(len(bounded), 12)
        self.assertIn("message-29", bounded[-1]["content"])
        self.assertNotIn("message-0", " ".join(item["content"] for item in bounded))

    def test_web_retry_detection_targets_capability_and_freshness_failures(self):
        self.assertTrue(response_needs_web_retry("I cannot perform live web searches in this environment."))
        self.assertTrue(response_needs_web_retry("I have zero internet access and cannot verify your claim."))
        self.assertTrue(response_needs_web_retry("I don't have access to real-time information."))
        self.assertFalse(response_needs_web_retry("The implementation is complete."))

    def test_command_capability_misstatement_depends_on_access_policy(self):
        refusal = "I cannot download it because I run in a restricted sandbox."
        self.assertTrue(response_misstates_command_access(refusal, "ask"))
        self.assertTrue(response_misstates_command_access(refusal, "whole_device"))
        self.assertFalse(response_misstates_command_access(refusal, "workspace"))

    def test_command_approval_policies_are_bounded(self):
        self.assertTrue(command_allowed_without_prompt("python3 script.py", "workspace"))
        self.assertTrue(command_allowed_without_prompt("python3 script.py", "whole_device"))
        self.assertFalse(command_allowed_without_prompt("git status", "ask"))
        self.assertEqual(command_access_scope("workspace"), "workspace")
        self.assertEqual(command_access_scope("ask"), "whole_device")

    def make_runtime(self, provider_stream, executor=lambda name, args, workspace: {"status": "success", "summary": "read", "content": "ok", "artifacts": [], "next_actions": []}, tools=None, web_tools=None):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.workspace.mkdir()
        store = Storage(str(Path(self.temp.name) / "state.db"))
        runtime = AgentRuntime(provider_stream=provider_stream, tool_executor=executor, tools=tools or [TOOL], web_tools=web_tools, storage=store, system_intro="System", agent_name="Agent")
        return runtime, store

    def tearDown(self):
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def test_continues_beyond_three_tool_turns_and_preserves_call_ids(self):
        calls = []

        def provider(provider, model, messages, tools, config, **kwargs):
            calls.append(messages)
            if len(calls) <= 4:
                tool_call = {"id": f"call_{len(calls)}", "type": "function", "function": {"name": "read_file_range", "arguments": {"turn": len(calls)}}}
                yield "tool_call", tool_call
                yield "done", {"content": "", "tool_calls": [tool_call]}
            else:
                yield "token", "Finished"
                yield "done", {"content": "Finished", "tool_calls": []}

        runtime, store = self.make_runtime(provider, lambda name, args, workspace: {"status": "success", "summary": f"read turn {args.get('turn')}", "content": "ok", "artifacts": [], "next_actions": []})
        events = list(runtime.run(session_id="s1", prompt="work", workspace=str(self.workspace), provider="fixture", model="fixture", config={}))
        self.assertEqual(len(calls), 5)
        tool_results = [message for message in calls[-1] if message.get("role") == "tool"]
        self.assertEqual([item["tool_call_id"] for item in tool_results], ["call_1", "call_2", "call_3", "call_4"])
        self.assertEqual(events[-1]["status"], "completed")
        self.assertEqual(store.get_session("s1")["messages"][-1]["content"], "Finished")

    def test_action_limit_reserves_a_tool_free_final_answer_turn(self):
        turns = []

        def provider(provider, model, messages, tools, config, **kwargs):
            turns.append([item["function"]["name"] for item in tools])
            if tools:
                call = {"id": f"call_{len(turns)}", "type": "function", "function": {"name": "read_file_range", "arguments": {"turn": len(turns)}}}
                yield "done", {"content": "", "tool_calls": [call]}
            else:
                yield "done", {"content": "Completed what I could and listed remaining work.", "tool_calls": []}

        runtime, store = self.make_runtime(provider)
        events = list(runtime.run(session_id="reserved", prompt="complex task", workspace=str(self.workspace), provider="fixture", model="fixture", config={"agent": {"max_turns": 2}}))
        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[-1], [])
        self.assertEqual(events[-1]["status"], "completed")
        self.assertFalse(any(item.get("code") == "turn_budget_exhausted" for item in events))
        self.assertIn("remaining work", store.get_session("reserved")["messages"][-1]["content"])

    def test_tool_limit_rejects_extra_call_then_finalizes(self):
        turns = 0
        executed = []

        def provider(provider, model, messages, tools, config, **kwargs):
            nonlocal turns
            turns += 1
            if turns == 1:
                calls = [
                    {"id": "one", "type": "function", "function": {"name": "read_file_range", "arguments": {"path": "one"}}},
                    {"id": "two", "type": "function", "function": {"name": "read_file_range", "arguments": {"path": "two"}}},
                ]
                yield "done", {"content": "", "tool_calls": calls}
            else:
                self.assertEqual(tools, [])
                self.assertTrue(any("tool_budget_exhausted" in str(item.get("content")) for item in messages if item.get("role") == "tool"))
                yield "done", {"content": "Final bounded report.", "tool_calls": []}

        runtime, _ = self.make_runtime(provider, lambda *args: executed.append(args) or {"status": "success", "summary": "observed", "artifacts": [], "next_actions": []})
        events = list(runtime.run(session_id="tools", prompt="inspect", workspace=str(self.workspace), provider="fixture", model="fixture", config={"agent": {"max_tool_calls": 1}}))
        self.assertEqual(len(executed), 1)
        self.assertEqual(events[-1]["status"], "completed")

    def test_prior_session_messages_are_given_to_provider(self):
        captured = []

        def provider(provider, model, messages, tools, config, **kwargs):
            captured.extend(messages)
            yield "done", {"content": "answer", "tool_calls": []}

        runtime, store = self.make_runtime(provider)
        store.append_message("s1", "user", "first", str(self.workspace))
        store.append_message("s1", "assistant", "prior answer", str(self.workspace))
        list(runtime.run(session_id="s1", prompt="continue", workspace=str(self.workspace), provider="fixture", model="fixture", config={}))
        contents = [item["content"] for item in captured]
        self.assertIn("first", contents)
        self.assertIn("prior answer", contents)

    def test_free_pool_route_is_pinned_after_first_turn(self):
        invocations = []

        def provider(provider, model, messages, tools, config, **kwargs):
            invocations.append((provider, model))
            if len(invocations) == 1:
                self.assertEqual(provider, "free_pool")
                yield "route_selected", {"provider": "kilo", "model": "stable-model"}
                call = {"id": "inspect", "type": "function", "function": {"name": "read_file_range", "arguments": {"path": "file"}}}
                yield "done", {"content": "", "tool_calls": [call]}
            else:
                yield "done", {"content": "finished", "tool_calls": []}

        runtime, _ = self.make_runtime(provider)
        events = list(runtime.run(session_id="route", prompt="work", workspace=str(self.workspace), provider="free_pool", model="complex-auto", config={}))
        self.assertEqual(invocations, [("free_pool", "complex-auto"), ("kilo", "stable-model")])
        self.assertTrue(any(item.get("type") == "model_route" and item.get("pinned") for item in events))

    def test_incomplete_run_checkpoint_is_injected_into_next_turn(self):
        turns = 0
        second_run_messages = []

        def provider(provider, model, messages, tools, config, **kwargs):
            nonlocal turns
            turns += 1
            if turns == 1:
                call = {"id": "inspect", "type": "function", "function": {"name": "read_file_range", "arguments": {"path": "important.py"}}}
                yield "done", {"content": "", "tool_calls": [call]}
            elif turns == 2:
                yield "error", "provider disconnected"
            else:
                second_run_messages.extend(messages)
                yield "done", {"content": "resumed", "tool_calls": []}

        runtime, store = self.make_runtime(provider, lambda *args: {"status": "success", "summary": "Read important.py", "artifacts": [], "next_actions": []})
        first = list(runtime.run(session_id="recover", prompt="analyze important.py", workspace=str(self.workspace), provider="fixture", model="fixture", config={}))
        self.assertTrue(any(item.get("code") == "provider_error" for item in first))
        with store._connect() as database:
            failed = database.execute("SELECT status,error FROM runs WHERE session_id=? ORDER BY created_at DESC,id DESC LIMIT 1", ("recover",)).fetchone()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "provider disconnected")
        list(runtime.run(session_id="recover", prompt="continue", workspace=str(self.workspace), provider="fixture", model="fixture", config={}))
        current = second_run_messages[-1]["content"]
        self.assertIn("Active task ledger", current)
        self.assertIn("Primary objective: analyze important.py", current)
        self.assertIn("Read important.py", current)

    def test_cancelled_run_starts_no_tools(self):
        executed = []

        def provider(provider, model, messages, tools, config, **kwargs):
            tool_call = {"id": "call", "type": "function", "function": {"name": "read_file_range", "arguments": {}}}
            yield "tool_call", tool_call
            yield "done", {"content": "", "tool_calls": [tool_call]}

        runtime, _ = self.make_runtime(provider, lambda *args: executed.append(args))
        iterator = runtime.run(session_id="s1", prompt="work", workspace=str(self.workspace), provider="fixture", model="fixture", config={}, run_id="run_cancel")
        next(iterator)
        runtime.cancel("run_cancel")
        events = list(iterator)
        self.assertEqual(executed, [])
        self.assertTrue(any(item.get("status") == "cancelled" for item in events))

    def test_terminal_command_waits_for_explicit_approval(self):
        turns = 0
        executed = []

        def provider(provider, model, messages, tools, config, **kwargs):
            nonlocal turns
            turns += 1
            if turns == 1:
                call = {"id": "command_1", "type": "function", "function": {"name": "run_terminal_command", "arguments": {"command": "python -m unittest"}}}
                yield "tool_call", call
                yield "done", {"content": "", "tool_calls": [call]}
            else:
                yield "done", {"content": "complete", "tool_calls": []}

        runtime, _ = self.make_runtime(provider, lambda *args: executed.append(args) or {"status": "success", "summary": "passed", "artifacts": [], "next_actions": []}, [COMMAND_TOOL])
        iterator = runtime.run(session_id="s1", prompt="test", workspace=str(self.workspace), provider="fixture", model="fixture", config={}, run_id="approval_run")
        events = []
        for event in iterator:
            events.append(event)
            if event.get("type") == "approval_required":
                self.assertEqual(executed, [])
                self.assertTrue(runtime.registry.resolve_approval("approval_run", "command_1", True))
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0][1]["_access_scope"], "whole_device")
        self.assertEqual(events[-1]["status"], "completed")

    def test_workspace_policy_runs_command_without_approval_prompt(self):
        turns = 0
        executed = []

        def provider(provider, model, messages, tools, config, **kwargs):
            nonlocal turns
            turns += 1
            if turns == 1:
                call = {"id": "command_1", "type": "function", "function": {"name": "run_terminal_command", "arguments": {"command": "python -m unittest"}}}
                yield "tool_call", call
                yield "done", {"content": "", "tool_calls": [call]}
            else:
                yield "done", {"content": "complete", "tool_calls": []}

        runtime, _ = self.make_runtime(provider, lambda *args: executed.append(args) or {"status": "success", "summary": "passed", "artifacts": [], "next_actions": []}, [COMMAND_TOOL])
        events = list(runtime.run(session_id="s1", prompt="test", workspace=str(self.workspace), provider="fixture", model="fixture", approval_policy="workspace", config={}))
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0][1]["_access_scope"], "workspace")
        self.assertFalse(any(item.get("type") == "approval_required" for item in events))

    def test_whole_device_policy_exposes_terminal_without_workspace(self):
        received = []

        def provider(provider, model, messages, tools, config, **kwargs):
            received.extend(item["function"]["name"] for item in tools)
            yield "done", {"content": "ready", "tool_calls": []}

        runtime, _ = self.make_runtime(provider, tools=[TOOL, COMMAND_TOOL])
        list(runtime.run(session_id="device", prompt="install a tool", workspace="", provider="fixture", model="fixture", mode="ask", approval_policy="whole_device", config={}))
        self.assertEqual(received, ["run_terminal_command"])

    def test_web_tools_are_available_only_when_enabled(self):
        received = []

        def provider(provider, model, messages, tools, config, **kwargs):
            received.append([item["function"]["name"] for item in tools])
            yield "done", {"content": "answer", "tool_calls": []}

        runtime, _ = self.make_runtime(provider, web_tools=[WEB_TOOL])
        list(runtime.run(session_id="off", prompt="answer", workspace="", provider="fixture", model="fixture", mode="ask", config={}))
        list(runtime.run(session_id="on", prompt="research", workspace="", provider="fixture", model="fixture", mode="ask", web_search=True, config={}))
        self.assertEqual(received, [[], ["web_search"]])
        self.assertIn("cite source URLs", runtime._system_prompt("", [], "ask", "fast", True))

    def test_web_tool_call_executes_in_ask_mode_and_returns_to_model(self):
        turns = 0
        executed = []

        def provider(provider, model, messages, tools, config, **kwargs):
            nonlocal turns
            turns += 1
            if turns == 1:
                call = {"id": "web_1", "type": "function", "function": {"name": "web_search", "arguments": {"query": "current docs"}}}
                yield "tool_call", call
                yield "done", {"content": "", "tool_calls": [call]}
            else:
                tool_message = next(item for item in messages if item.get("role") == "tool")
                self.assertEqual(tool_message["tool_call_id"], "web_1")
                self.assertIn("https://example.test/docs", tool_message["content"])
                yield "done", {"content": "Answer with citation https://example.test/docs", "tool_calls": []}

        def executor(name, arguments, workspace):
            executed.append((name, arguments, workspace))
            return {"status": "success", "summary": "Found one result", "results": [{"url": "https://example.test/docs"}], "artifacts": [], "next_actions": []}

        runtime, _ = self.make_runtime(provider, executor, web_tools=[WEB_TOOL])
        events = list(runtime.run(session_id="web", prompt="research this", workspace="", provider="fixture", model="fixture", mode="ask", web_search=True, config={}))
        self.assertEqual(executed, [
            ("web_search", {"query": "research this", "max_results": 5}, ""),
            ("web_search", {"query": "current docs"}, ""),
        ])
        self.assertEqual(events[-1]["status"], "completed")

    def test_enabled_web_search_gives_live_evidence_to_non_tool_calling_model(self):
        captured = []

        def provider(provider, model, messages, tools, config, **kwargs):
            captured.extend(messages)
            yield "done", {"content": "Used the supplied live result.", "tool_calls": []}

        def executor(name, arguments, workspace):
            return {"status": "success", "summary": "Found one live result", "results": [{"title": "Current documentation", "url": "https://example.test/current"}], "artifacts": [], "next_actions": []}

        runtime, _ = self.make_runtime(provider, executor, web_tools=[WEB_TOOL])
        events = list(runtime.run(session_id="prefetch", prompt="search the web for current documentation", workspace="", provider="fixture", model="fixture", mode="ask", web_search=True, config={}))
        self.assertIn("Live web search evidence", captured[-1]["content"])
        self.assertIn("https://example.test/current", captured[-1]["content"])
        self.assertTrue(any(item.get("type") == "tool_completed" and item.get("name") == "web_search" for item in events))

    def test_auto_web_recovers_from_model_capability_refusal(self):
        turns = 0
        executed = []

        def provider(provider, model, messages, tools, config, **kwargs):
            nonlocal turns
            turns += 1
            if turns == 1:
                yield "done", {"content": "I cannot perform live web searches in this environment.", "tool_calls": []}
            else:
                self.assertIn("Live result", messages[-1]["content"])
                yield "done", {"content": "I searched and found the current answer.", "tool_calls": []}

        def executor(name, arguments, workspace):
            executed.append(name)
            return {"status": "success", "summary": "Live result", "results": [{"url": "https://example.test/live"}], "artifacts": [], "next_actions": []}

        runtime, store = self.make_runtime(provider, executor, web_tools=[WEB_TOOL])
        events = list(runtime.run(session_id="auto", prompt="answer this", workspace="", provider="fixture", model="fixture", mode="ask", web_search=True, web_search_required=False, config={}))
        self.assertEqual(executed, ["web_search"])
        self.assertEqual(turns, 2)
        self.assertTrue(any(item.get("type") == "response_reset" for item in events))
        self.assertEqual(store.get_session("auto")["messages"][-1]["content"], "I searched and found the current answer.")
        self.assertEqual(events[-1]["status"], "completed")

    def test_prefetched_web_evidence_still_corrects_a_capability_refusal(self):
        turns = 0
        executed = []

        def provider(provider, model, messages, tools, config, **kwargs):
            nonlocal turns
            turns += 1
            if turns == 1:
                yield "done", {"content": "No. I have zero internet access, cannot download files, and run in a restricted sandbox.", "tool_calls": []}
            else:
                self.assertIn("runtime capabilities", messages[-1]["content"])
                yield "done", {"content": "Yes. Claude Desktop is available for Linux.", "tool_calls": []}

        def executor(name, arguments, workspace):
            executed.append(name)
            return {"status": "success", "summary": "Official Linux release found", "results": [{"url": "https://claude.com/download"}], "artifacts": [], "next_actions": []}

        runtime, store = self.make_runtime(provider, executor, web_tools=[WEB_TOOL])
        events = list(runtime.run(session_id="download", prompt="Can you download Claude Desktop on Linux?", workspace="", provider="fixture", model="fixture", mode="ask", approval_policy="ask", web_search=True, web_search_required=True, config={}))
        self.assertEqual(executed, ["web_search"])
        self.assertEqual(turns, 2)
        self.assertTrue(any(item.get("type") == "response_reset" and item.get("reason") == "correcting_capability_misstatement" for item in events))
        self.assertEqual(store.get_session("download")["messages"][-1]["content"], "Yes. Claude Desktop is available for Linux.")
        self.assertEqual(events[-1]["status"], "completed")

    def test_auto_web_does_not_search_when_model_has_sufficient_information(self):
        executed = []

        def provider(provider, model, messages, tools, config, **kwargs):
            yield "done", {"content": "The repository answer is available from context.", "tool_calls": []}

        runtime, _ = self.make_runtime(provider, lambda *args: executed.append(args), web_tools=[WEB_TOOL])
        list(runtime.run(session_id="auto", prompt="explain this file", workspace="", provider="fixture", model="fixture", mode="ask", web_search=True, web_search_required=False, config={}))
        self.assertEqual(executed, [])


if __name__ == "__main__":
    unittest.main()
