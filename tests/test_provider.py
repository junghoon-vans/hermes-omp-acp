import asyncio
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import closing
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.auxiliary_client import _to_async_client
from providers import get_provider_profile, register_provider

from hermes_omp_acp.provider import register


class ProviderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.home),
            "PI_CODING_AGENT_DIR": str(self.home / "omp"),
            "HERMES_OMP_ACP_COMMAND": sys.executable,
            "HERMES_OMP_ACP_ARGS": shlex.join([str(Path(__file__).with_name("acp_peer.py"))]),
        })
        environment.start()
        os.environ.pop("HERMES_OMP_ACP_MCP_ENABLED", None)
        self.addCleanup(environment.stop)
        register()
        self.profile = get_provider_profile("omp-acp")

    def configure(self, **settings):
        (self.home / "config.yaml").write_text(json.dumps({
            "plugins": {"enabled": ["omp-acp-provider"],
                        "entries": {"omp-acp-provider": {"settings": settings}}},
        }))
        register()
        self.profile = get_provider_profile("omp-acp")

    def test_settings_launch_options_preserve_spaces_and_gateway_placeholders(self):
        peer = self.home / "ACP peer with spaces.py"
        shutil.copyfile(Path(__file__).with_name("acp_peer.py"), peer)
        with patch.dict(os.environ):
            os.environ.pop("HERMES_OMP_ACP_COMMAND")
            os.environ.pop("HERMES_OMP_ACP_ARGS")
            self.configure(command=sys.executable, args=[str(peer)])
            self.assertEqual(self.profile.fetch_models(timeout=5), ["fixture/model"])
            with closing(self.profile.create_client(
                command=None, args=[], acp_cwd=str(self.home),
            )) as client:
                response = client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                    timeout=5,
                )
            self.assertEqual(response.choices[0].message.content, "first second")

    def test_environment_overrides_configured_launch_options(self):
        self.configure(command=str(self.home / "missing-omp"), args=["missing-argument"])
        self.assertEqual(self.profile.fetch_models(timeout=5), ["fixture/model"])
        with closing(self.client()) as client:
            response = client.chat.completions.create(
                model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                timeout=5,
            )
        self.assertEqual(response.choices[0].message.content, "first second")

    def test_empty_configured_args_support_an_executable_wrapper(self):
        wrapper = self.home / "OMP wrapper with spaces"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.join([
            sys.executable, str(Path(__file__).with_name("acp_peer.py")),
        ]) + "\n")
        wrapper.chmod(0o755)
        with patch.dict(os.environ):
            os.environ.pop("HERMES_OMP_ACP_COMMAND")
            os.environ.pop("HERMES_OMP_ACP_ARGS")
            self.configure(command=str(wrapper), args=[])
            with closing(self.client()) as client:
                response = client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                    timeout=5,
                )
            self.assertEqual(response.choices[0].message.content, "first second")

    def test_invalid_plugin_settings_are_rejected(self):
        for settings, key in (
            ({"command": ""}, "command"),
            ({"command": ["omp"]}, "command"),
            ({"args": "acp --no-title"}, "args"),
            ({"args": ["acp", 1]}, "args"),
            ({"args": None}, "args"),
            ({"mcp_enabled": "false"}, "mcp_enabled"),
            ({"mcp_enabled": 0}, "mcp_enabled"),
        ):
            with self.subTest(settings=settings), self.assertRaisesRegex(ValueError, key):
                self.configure(**settings)

    def client(self):
        return self.profile.create_client(acp_cwd=str(self.home))

    def _wait_for_peers(self, *names, timeout=5) -> dict[str, int]:
        deadline = time.monotonic() + timeout
        pids: dict[str, int] = {}
        while len(pids) < len(names):
            for name in names:
                marker = self.home / f"{name}.ready"
                if name not in pids and marker.exists():
                    try:
                        pids[name] = int(marker.read_text(encoding="ascii"))
                    except (OSError, ValueError):
                        pass
            if len(pids) == len(names):
                return pids
            if time.monotonic() >= deadline:
                missing = sorted(set(names) - pids.keys())
                raise AssertionError(f"Timed out waiting for ACP peer readiness: {missing}")
            time.sleep(0.01)
        return pids

    @staticmethod
    def _process_is_alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _assert_process_reaped(self, pid, timeout=5):
        deadline = time.monotonic() + timeout
        while self._process_is_alive(pid):
            if time.monotonic() >= deadline:
                self.fail(f"ACP peer process {pid} was not reaped")
            time.sleep(0.01)

    def _release_peers(self, *names):
        for name in names:
            (self.home / f"{name}.release").touch()

    def _cleanup_requests(self, client, executor, futures, peers):
        try:
            client.close()
        finally:
            self._release_peers(*peers)
            wait(futures, timeout=5)
            executor.shutdown(wait=True, cancel_futures=True)

    def test_missing_executable_does_not_break_the_model_picker(self):
        with patch.dict(os.environ, {"HERMES_OMP_ACP_COMMAND": str(self.home / "missing-omp")}):
            self.assertIsNone(self.profile.fetch_models(timeout=1))

    def test_base_provider_remains_usable_without_delegation_apis(self):
        with patch("hermes_omp_acp.provider.register_provider", wraps=register_provider) as register_profile:
            with patch.dict(sys.modules, {
                "agent.delegation_provider": None,
                "agent.delegation_registry": None,
            }):
                register(scope=str(self.home))

        register_profile.assert_called_once()
        profile = get_provider_profile("omp-acp")
        self.assertIn("fixture/model", profile.fetch_models(timeout=5) or [])

    def test_selected_worktree_backend_fails_actionably_without_delegation_apis(self):
        (self.home / "config.yaml").write_text(
            '{"delegation": {"backend": "omp-worktree"}}',
            encoding="utf-8",
        )
        with patch.dict(sys.modules, {
            "agent.delegation_provider": None,
            "agent.delegation_registry": None,
        }):
            with self.assertRaisesRegex(RuntimeError, "requires Hermes delegation APIs"):
                register(scope=str(self.home))

    def test_peer_exit_reports_its_failure_instead_of_waiting_for_a_timeout(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "exit"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}), closing(self.client()) as client:
            with self.assertRaises(RuntimeError) as error:
                client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Do the task"}], timeout=2,
                )
        self.assertIn("fixture peer failed during initialization", str(error.exception))

    def test_model_discovery_remains_available_inside_an_async_caller(self):
        async def exercise():
            self.assertIn("fixture/model", self.profile.fetch_models(timeout=5) or [])

        asyncio.run(exercise())

    def test_async_auxiliary_completion_does_not_block_the_event_loop(self):
        async def exercise():
            with closing(self.client()) as client:
                auxiliary, model = _to_async_client(client, "fixture/model")
                heartbeat = asyncio.create_task(asyncio.sleep(0.01))
                response = await auxiliary.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": "A short response"}], timeout=5,
                )
                self.assertTrue(heartbeat.done(), "The subprocess blocked Hermes's async event loop")
                self.assertEqual(response.choices[0].finish_reason, "stop")
        asyncio.run(exercise())

    def test_async_stream_preserves_the_completion_and_stop_boundary(self):
        async def exercise():
            with closing(self.client()) as client:
                auxiliary, model = _to_async_client(client, "fixture/model")
                stream = await auxiliary.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": "A short response"}], stream=True, timeout=5,
                )
                chunks = [chunk async for chunk in stream]
                choices = [choice for chunk in chunks for choice in chunk.choices]
                content = "".join(choice.delta.content or "" for choice in choices)
                self.assertEqual(content, "first second")
                self.assertEqual(choices[-1].finish_reason, "stop")
        asyncio.run(exercise())

    def test_immediate_burst_preserves_ordered_text_reasoning_and_stream_stop(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "burst"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}), closing(self.client()) as client:
            stream = client.chat.completions.create(
                model="fixture/model", messages=[{"role": "user", "content": "Return the chunks"}],
                stream=True, timeout=5,
            )
            chunks = list(stream)

        choices = [choice for chunk in chunks for choice in chunk.choices]
        content = "".join(choice.delta.content or "" for choice in choices)
        reasoning = "".join(
            getattr(choice.delta, "reasoning_content", None)
            or getattr(choice.delta, "reasoning", None)
            or ""
            for choice in choices
        )
        self.assertEqual(content, "".join(f"text-{index:02d}|" for index in range(40)))
        self.assertEqual(reasoning, "".join(f"reason-{index:02d}|" for index in range(40)))
        self.assertEqual(choices[-1].finish_reason, "stop")
        self.assertEqual(sum(choice.finish_reason is not None for choice in choices), 1)

    def test_model_selection_failure_prevents_prompt_execution(self):
        cases = (
            ("config", "fixture/missing", RuntimeError),
            ("reject", "fixture/model", RuntimeError),
            ("mismatch", "fixture/model", RuntimeError),
            ("timeout", "fixture/model", TimeoutError),
            ("legacy-reject", "fixture/model", RuntimeError),
            ("legacy", "fixture/missing", RuntimeError),
        )
        for mode, model, error in cases:
            with self.subTest(mode=mode, model=model):
                args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), mode])
                (self.home / "prompt-executed").unlink(missing_ok=True)
                with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
                    with closing(self.client()) as client:
                        with self.assertRaises(error):
                            client.chat.completions.create(
                                model=model, messages=[{"role": "user", "content": "Do the task"}],
                                timeout=1 if mode == "timeout" else 5,
                            )
                self.assertFalse((self.home / "prompt-executed").exists(),
                                 "A failed model selection must not execute the task")

    def test_timeout_budget_covers_initialize_new_session_and_model_selection(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "budget"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}), closing(self.client()) as client:
            with self.assertRaises(TimeoutError):
                client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Do the task"}], timeout=3.2,
                )

        pids = self._wait_for_peers(
            "budget_initialize", "budget_new_session", "budget_selection", timeout=1,
        )
        self.assertFalse((self.home / "prompt-executed").exists())
        self._assert_process_reaped(pids["budget_selection"])

    def test_legacy_model_selection_completes_the_task(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "legacy"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            with closing(self.client()) as client:
                response = client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                    timeout=5,
                )
        self.assertEqual(response.choices[0].message.content, "first second")

    def test_invalid_mcp_config_blocks_tasks_without_breaking_model_discovery(self):
        agent_dir = Path(os.environ["PI_CODING_AGENT_DIR"])
        agent_dir.mkdir()
        (agent_dir / "mcp.json").write_text('{"secret": "private-config-value", broken')
        self.assertIn("fixture/model", self.profile.fetch_models(timeout=5))
        with closing(self.client()) as client:
            with self.assertRaisesRegex(RuntimeError, "MCP.*malformed") as error:
                client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                    timeout=5,
                )
        self.assertNotIn("private-config-value", str(error.exception))
        self.assertFalse((self.home / "prompt-executed").exists())

    def test_mcp_environment_toggle_skips_config_until_reenabled(self):
        agent_dir = Path(os.environ["PI_CODING_AGENT_DIR"])
        agent_dir.mkdir()
        (agent_dir / "mcp.json").write_text('{"mcpServers": broken')
        with closing(self.client()) as client:
            for value in ("0", " FALSE ", "no", "OFF"):
                with self.subTest(disabled=value), patch.dict(
                    os.environ, {"HERMES_OMP_ACP_MCP_ENABLED": value},
                ):
                    response = client.chat.completions.create(
                        model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                        timeout=5,
                    )
                    self.assertEqual(response.choices[0].message.content, "first second")
            for value in ("", "1", "true", "YES", " on "):
                with self.subTest(enabled=value), patch.dict(
                    os.environ, {"HERMES_OMP_ACP_MCP_ENABLED": value},
                ):
                    with self.assertRaisesRegex(RuntimeError, "MCP.*malformed"):
                        client.chat.completions.create(
                            model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                            timeout=5,
                        )

    def test_mcp_setting_skips_config_and_environment_can_reenable_it(self):
        agent_dir = Path(os.environ["PI_CODING_AGENT_DIR"])
        agent_dir.mkdir()
        (agent_dir / "mcp.json").write_text('{"mcpServers": broken')
        self.configure(mcp_enabled=False)
        self.assertIn("fixture/model", self.profile.fetch_models(timeout=5))
        with closing(self.client()) as client:
            for value in ("", "   "):
                with self.subTest(unset_override=value), patch.dict(
                    os.environ, {"HERMES_OMP_ACP_MCP_ENABLED": value},
                ):
                    response = client.chat.completions.create(
                        model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                        timeout=5,
                    )
                    self.assertEqual(response.choices[0].message.content, "first second")
            with patch.dict(os.environ, {"HERMES_OMP_ACP_MCP_ENABLED": "true"}):
                with self.assertRaisesRegex(RuntimeError, "MCP.*malformed"):
                    client.chat.completions.create(
                        model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                        timeout=5,
                    )

    def test_invalid_mcp_environment_toggle_blocks_tasks(self):
        with patch.dict(os.environ, {"HERMES_OMP_ACP_MCP_ENABLED": "typo"}):
            self.assertIn("fixture/model", self.profile.fetch_models(timeout=5))
            with closing(self.client()) as client:
                with self.assertRaisesRegex(RuntimeError, "HERMES_OMP_ACP_MCP_ENABLED"):
                    client.chat.completions.create(
                        model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                        timeout=5,
                    )
        self.assertFalse((self.home / "prompt-executed").exists())

    def test_unsupported_mcp_transport_blocks_tasks_before_execution(self):
        agent_dir = Path(os.environ["PI_CODING_AGENT_DIR"])
        agent_dir.mkdir()
        (agent_dir / "mcp.json").write_text(
            '{"mcpServers":{"remote":{"type":"http","url":"http://127.0.0.1:1/mcp"}}}'
        )
        with closing(self.client()) as client:
            with self.assertRaisesRegex(RuntimeError, "does not support.*http"):
                client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                    timeout=5,
                )
        self.assertFalse((self.home / "prompt-executed").exists())

    def test_worktree_backend_refuses_completion_without_repository_metadata(self):
        (self.home / "config.yaml").write_text('{"delegation": {"backend": "omp-worktree"}}')
        with closing(self.client()) as client:
            with self.assertRaises(ValueError):
                client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Do not run unisolated"}],
                    timeout=1,
                )

    def test_acp_permission_requests_are_cancelled(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "permission"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            with closing(self.client()) as client:
                response = client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Request a protected action"}],
                    timeout=5,
                )
        self.assertEqual(response.choices[0].message.content, "permission outcome: cancelled")

    def test_filesystem_callbacks_allow_safe_reads_and_writes_and_reject_escape(self):
        secret = "fixture-read-secret-123456789"
        (self.home / "allowed.txt").write_text(f"read-ok\nAPI_KEY={secret}\n", encoding="utf-8")
        outside_read_path = self.home.parent / f"{self.home.name}-outside-read.txt"
        outside_read_path.write_text("outside-read-secret", encoding="utf-8")
        outside_write_path = self.home.parent / f"{self.home.name}-outside-write.txt"
        outside_symlink_write_path = self.home.parent / f"{self.home.name}-outside-symlink-write.txt"
        outside_symlink_write_path.write_text("symlink-target-unchanged", encoding="utf-8")
        outside_read_link = self.home / "outside-read-link.txt"
        outside_read_link.symlink_to(outside_read_path)
        outside_write_link = self.home / "outside-write-link.txt"
        outside_write_link.symlink_to(outside_symlink_write_path)
        protected_write_path = self.home / ".env"
        allowed_write_path = self.home / "nested" / "allowed-write.txt"
        for path in (
            outside_read_path, outside_write_path, outside_symlink_write_path,
            outside_read_link, outside_write_link, protected_write_path,
        ):
            self.addCleanup(path.unlink, missing_ok=True)

        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "filesystem"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            with closing(self.client()) as client:
                response = client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Inspect the fixture files"}],
                    timeout=5,
                )

        content = response.choices[0].message.content
        self.assertIn("read-ok", content)
        self.assertNotIn(secret, content)
        self.assertIn("allowed_write='allowed-write\\n'", content)
        self.assertEqual(allowed_write_path.read_bytes(), b"allowed-write\n")
        self.assertIn("outside_read_denied=True", content)
        self.assertNotIn("outside-read-secret", content)
        self.assertIn("outside_link_read_denied=True", content)
        self.assertIn("protected_write_denied=True", content)
        self.assertIn("outside_write_denied=True", content)
        self.assertIn("outside_link_write_denied=True", content)
        self.assertFalse(protected_write_path.exists())
        self.assertFalse(outside_write_path.exists())
        self.assertEqual(outside_symlink_write_path.read_text(encoding="utf-8"), "symlink-target-unchanged")

    def test_reasoning_and_hermes_tool_calls_survive_completion_conversion(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "conversion"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            with closing(self.client()) as client:
                response = client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Look something up"}],
                    timeout=5,
                )

        choice = response.choices[0]
        self.assertEqual(choice.finish_reason, "tool_calls")
        self.assertEqual(choice.message.content, "Before the call.\nAfter the call.")
        self.assertEqual(choice.message.reasoning_content, "The agent's private reasoning.")
        self.assertEqual(len(choice.message.tool_calls), 1)
        tool_call = choice.message.tool_calls[0]
        self.assertEqual(tool_call.id, "fixture-call")
        self.assertEqual(tool_call.function.name, "lookup")
        self.assertEqual(tool_call.function.arguments, '{"query":"hermes"}')

    def test_concurrent_completions_rendezvous_and_keep_sessions_isolated(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "isolation"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            client = self.client()
            executor = ThreadPoolExecutor(max_workers=2)
            futures = []
            try:
                alpha = executor.submit(
                    client.chat.completions.create,
                    model="fixture/model", messages=[{"role": "user", "content": "request iso_alpha"}], timeout=8,
                )
                beta = executor.submit(
                    client.chat.completions.create,
                    model="fixture/model", messages=[{"role": "user", "content": "request iso_beta"}], timeout=8,
                )
                futures.extend((alpha, beta))
                pids = self._wait_for_peers("iso_alpha", "iso_beta")
                self.assertFalse(alpha.done())
                self.assertFalse(beta.done())
                self.assertTrue(self._process_is_alive(pids["iso_alpha"]))
                self.assertTrue(self._process_is_alive(pids["iso_beta"]))

                self._release_peers("iso_alpha")
                alpha_response = alpha.result(timeout=5)
                self._assert_process_reaped(pids["iso_alpha"])
                self.assertFalse(beta.done())
                self.assertTrue(self._process_is_alive(pids["iso_beta"]))

                self._release_peers("iso_beta")
                beta_response = beta.result(timeout=5)
                self._assert_process_reaped(pids["iso_beta"])
                self.assertEqual(alpha_response.choices[0].message.content, "reply to alpha")
                self.assertEqual(beta_response.choices[0].message.content, "reply to beta")
            finally:
                self._cleanup_requests(client, executor, futures, ("iso_alpha", "iso_beta"))

    def test_timeout_reaps_only_its_overlapping_session(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "lifecycle"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            client = self.client()
            executor = ThreadPoolExecutor(max_workers=2)
            futures = []
            try:
                timed = executor.submit(
                    client.chat.completions.create,
                    model="fixture/model",
                    messages=[{"role": "user", "content": "request timeout_primary"}],
                    timeout=5,
                )
                sibling = executor.submit(
                    client.chat.completions.create,
                    model="fixture/model",
                    messages=[{"role": "user", "content": "request timeout_sibling"}],
                    timeout=10,
                )
                futures.extend((timed, sibling))
                pids = self._wait_for_peers("timeout_primary", "timeout_sibling", timeout=4)
                self.assertFalse(timed.done())
                self.assertFalse(sibling.done())
                self.assertTrue(self._process_is_alive(pids["timeout_primary"]))
                self.assertTrue(self._process_is_alive(pids["timeout_sibling"]))

                with self.assertRaises(TimeoutError):
                    timed.result(timeout=7)
                self.assertTrue(timed.done())
                self._assert_process_reaped(pids["timeout_primary"])
                self.assertFalse(sibling.done())
                self.assertTrue(self._process_is_alive(pids["timeout_sibling"]))

                self._release_peers("timeout_sibling")
                response = sibling.result(timeout=5)
                self._assert_process_reaped(pids["timeout_sibling"])
                self.assertEqual(response.choices[0].message.content, "reply to timeout_sibling")
            finally:
                self._cleanup_requests(
                    client, executor, futures, ("timeout_primary", "timeout_sibling"),
                )

    def test_cancelling_one_async_request_preserves_its_overlapping_sibling(self):
        async def exercise():
            args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "lifecycle"])
            with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
                client = self.client()
                tasks = []
                try:
                    cancelled = asyncio.create_task(client.chat.completions.create(
                        model="fixture/model",
                        messages=[{"role": "user", "content": "request cancel_primary"}],
                        timeout=10,
                    ))
                    sibling = asyncio.create_task(client.chat.completions.create(
                        model="fixture/model",
                        messages=[{"role": "user", "content": "request cancel_sibling"}],
                        timeout=10,
                    ))
                    tasks.extend((cancelled, sibling))
                    pids = await asyncio.to_thread(
                        self._wait_for_peers, "cancel_primary", "cancel_sibling", timeout=5,
                    )
                    self.assertFalse(cancelled.done())
                    self.assertFalse(sibling.done())
                    self.assertTrue(self._process_is_alive(pids["cancel_primary"]))
                    self.assertTrue(self._process_is_alive(pids["cancel_sibling"]))

                    cancelled.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(cancelled, timeout=5)
                    await asyncio.to_thread(self._assert_process_reaped, pids["cancel_primary"])
                    self.assertFalse(sibling.done())
                    self.assertTrue(self._process_is_alive(pids["cancel_sibling"]))

                    self._release_peers("cancel_sibling")
                    response = await asyncio.wait_for(sibling, timeout=5)
                    await asyncio.to_thread(self._assert_process_reaped, pids["cancel_sibling"])
                    self.assertEqual(response.choices[0].message.content, "reply to cancel_sibling")
                finally:
                    try:
                        client.close()
                    finally:
                        self._release_peers("cancel_primary", "cancel_sibling")
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*tasks, return_exceptions=True), timeout=5,
                            )
                        except TimeoutError:
                            pass

        asyncio.run(exercise())

    def test_close_reaps_two_active_session_processes(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "lifecycle"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            client = self.client()
            executor = ThreadPoolExecutor(max_workers=2)
            futures = []
            try:
                left = executor.submit(
                    client.chat.completions.create,
                    model="fixture/model", messages=[{"role": "user", "content": "request close_left"}], timeout=10,
                )
                right = executor.submit(
                    client.chat.completions.create,
                    model="fixture/model", messages=[{"role": "user", "content": "request close_right"}], timeout=10,
                )
                futures.extend((left, right))
                pids = self._wait_for_peers("close_left", "close_right")
                self.assertFalse(left.done())
                self.assertFalse(right.done())
                self.assertTrue(self._process_is_alive(pids["close_left"]))
                self.assertTrue(self._process_is_alive(pids["close_right"]))

                client.close()
                for future in futures:
                    with self.assertRaises((asyncio.CancelledError, RuntimeError)):
                        future.result(timeout=5)
                    self.assertTrue(future.done())
                self._assert_process_reaped(pids["close_left"])
                self._assert_process_reaped(pids["close_right"])
            finally:
                self._cleanup_requests(client, executor, futures, ("close_left", "close_right"))

if __name__ == "__main__":
    unittest.main()
