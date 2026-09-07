import importlib.util
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import time
from unittest.mock import patch

from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext


class M1AgentTest(unittest.IsolatedAsyncioTestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('lab_runtime.m1_agent'), 'M1 agent missing')
        from lab_runtime import m1_agent
        return m1_agent

    async def test_real_loop_preserves_tool_observation_and_usage(self):
        m = self.module()
        class Environment:
            async def exec(self, command, **kwargs):
                return ExecResult(return_code=0, stdout='private-absent' if 'private-absent' in command else '42')
        responses = [
            {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'shell', 'arguments': '{"command":"printf 42"}'}}]}, 'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 50, 'completion_tokens': 20}},
            {'choices': [{'message': {'role': 'assistant', 'content': 'Done.'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 80, 'completion_tokens': 3}},
        ]
        with tempfile.TemporaryDirectory() as d:
            agent = m.M1Agent(logs_dir=Path(d), model_name='Qwen/Qwen3-4B', seed=7)
            context = AgentContext()
            with patch.object(m, 'count_prompt', side_effect=[50, 80]), patch.object(m, 'http_json', side_effect=responses) as http:
                await agent.run('Write the result.', Environment(), context)
            self.assertEqual(context.n_output_tokens, 23)
            self.assertEqual(context.n_input_tokens, 130)
            self.assertEqual(context.metadata['termination'], 'completed')
            request = http.call_args_list[1].args[2]
            self.assertEqual(request['messages'][-1]['role'], 'tool')
            self.assertIn('42', request['messages'][-1]['content'])
            self.assertEqual(request['max_tokens'], 4076)
            trace = json.loads((Path(d)/'m1-trace.json').read_text())
            self.assertEqual(len(trace['turns']), 2)
            self.assertEqual(len(trace['isolation']), 2)

    async def test_context_budget_does_not_send_request(self):
        m = self.module()
        class Environment:
            async def exec(self, **kwargs):
                return ExecResult(return_code=0, stdout='private-absent')
        with tempfile.TemporaryDirectory() as d:
            agent = m.M1Agent(logs_dir=Path(d), model_name='Qwen/Qwen3-4B')
            context = AgentContext()
            with patch.object(m, 'count_prompt', return_value=17000), patch.object(m, 'http_json') as http:
                await agent.run('task', Environment(), context)
            http.assert_not_called()
            self.assertEqual(context.metadata['termination'], 'context_budget')

    def test_command_schema_and_observation_bound(self):
        m = self.module()
        for value in ('{}', '{"command":7}', '{"command":"x","extra":1}', '[]'):
            with self.assertRaises(ValueError):
                m.parse_command({'name': 'shell', 'arguments': value})
        self.assertEqual(m.parse_command({'name': 'shell', 'arguments': '{"command":"pwd"}'}), 'pwd')
        result = m.observation(ExecResult(return_code=2, stdout='文'*20000, stderr='error'))
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['stdout'].encode()), 12000)

    async def test_private_material_visibility_is_hard_error(self):
        m = self.module()
        class Environment:
            async def exec(self, **kwargs):
                return ExecResult(return_code=1, stdout='/tests')
        with tempfile.TemporaryDirectory() as d:
            context = AgentContext()
            with self.assertRaises(RuntimeError):
                await m.M1Agent(logs_dir=Path(d), model_name='Qwen/Qwen3-4B').run('task', Environment(), context)
            self.assertTrue((Path(d)/'m1-trace.json').exists())

    async def test_failed_tool_is_recorded_and_end_probe_runs(self):
        m = self.module()
        class Environment:
            async def exec(self, command, **kwargs):
                if 'private-absent' in command:
                    return ExecResult(return_code=0, stdout='private-absent')
                raise TimeoutError('tool timed out')
        response = {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'c', 'function': {'name': 'shell', 'arguments': '{"command":"sleep 99"}'}}]}, 'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 50, 'completion_tokens': 10}}
        with tempfile.TemporaryDirectory() as d:
            context = AgentContext()
            with patch.object(m, 'count_prompt', return_value=50), patch.object(m, 'http_json', return_value=response), self.assertRaises(TimeoutError):
                await m.M1Agent(logs_dir=Path(d), model_name='Qwen/Qwen3-4B').run('task', Environment(), context)
            trace = json.loads((Path(d)/'m1-trace.json').read_text())
            self.assertEqual(trace['error_phase'], 'tool')
            self.assertEqual(trace['turns'][0]['tools'][0]['error_type'], 'TimeoutError')
            self.assertEqual(trace['isolation'][-1]['phase'], 'end')
            self.assertIn('elapsed_seconds', trace['isolation'][-1])

    async def test_prompt_count_drift_is_rejected(self):
        m = self.module()
        class Environment:
            async def exec(self, **kwargs):
                return ExecResult(return_code=0, stdout='private-absent')
        response = {'choices': [{'message': {'role': 'assistant', 'content': 'Done'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 99, 'completion_tokens': 10}}
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m, 'count_prompt', return_value=50), patch.object(m, 'http_json', return_value=response), self.assertRaisesRegex(RuntimeError, 'prompt token'):
                await m.M1Agent(logs_dir=Path(d), model_name='Qwen/Qwen3-4B').run('task', Environment(), AgentContext())

    def test_invalid_finish_schema_is_not_completed(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'validate_choice'))
        for choice in ({'message': {'role': 'assistant'}, 'finish_reason': 'tool_calls'},
                       {'message': {'role': 'assistant'}, 'finish_reason': None},
                       {'message': {'role': 'user'}, 'finish_reason': 'stop'}):
            with self.assertRaises(ValueError):
                m.validate_choice({'choices': [choice]})

    async def test_cancel_waits_for_inflight_http_and_records_late_usage(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'request_once'))
        completed = []
        def response(*args):
            time.sleep(0.03)
            completed.append(True)
            return {'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
        record = {}
        with patch.object(m, 'http_json', side_effect=response):
            task = asyncio.create_task(m.request_once({}, time.monotonic()+1, record))
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(completed)
        self.assertEqual(record['late_response']['usage']['completion_tokens'], 2)
        self.assertEqual(record['inflight_status'], 'finished_after_cancel')

    async def test_invalid_usage_is_not_reported_complete(self):
        m = self.module()
        class Environment:
            async def exec(self, **kwargs):
                return ExecResult(return_code=0, stdout='private-absent')
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m, 'count_prompt', return_value=50), patch.object(m, 'http_json', return_value={'usage': {'prompt_tokens': 50, 'completion_tokens': -1}}), self.assertRaises(RuntimeError):
                await m.M1Agent(logs_dir=Path(d), model_name='Qwen/Qwen3-4B').run('task', Environment(), AgentContext())
            self.assertFalse(json.loads((Path(d)/'m1-trace.json').read_text())['usage_complete'])

    async def test_missing_late_usage_is_not_reported_complete(self):
        m = self.module()
        class Environment:
            async def exec(self, **kwargs):
                return ExecResult(return_code=0, stdout='private-absent')
        async def cancel(request, deadline, record):
            record['late_response'] = {}
            raise asyncio.CancelledError()
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m, 'count_prompt', return_value=50), patch.object(m, 'request_once', side_effect=cancel), self.assertRaises(asyncio.CancelledError):
                await m.M1Agent(logs_dir=Path(d), model_name='Qwen/Qwen3-4B').run('task', Environment(), AgentContext())
            self.assertFalse(json.loads((Path(d)/'m1-trace.json').read_text())['usage_complete'])
