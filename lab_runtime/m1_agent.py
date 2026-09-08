"""Bounded Harbor agent using only the owned loopback model and sandbox exec.

This trace is M1 evidence, not an ATIF/TITO training trajectory certification.
"""
import asyncio
from collections.abc import Mapping
from functools import lru_cache
import json
import time

from harbor.agents.base import BaseAgent

from lab_runtime.home5090 import MODEL, SETTINGS
from lab_runtime.home5090_run import http_json

TOOLS = [{'type': 'function', 'function': {
    'name': 'shell', 'description': 'Run a shell command in the task sandbox. Write the requested output files before finishing.',
    'parameters': {'type': 'object', 'properties': {'command': {'type': 'string'}},
                   'required': ['command'], 'additionalProperties': False}}}]
ISOLATION = "if ls -ld /tests /solution >/dev/null 2>&1; then exit 1; fi; for p in /tests /solution; do if test -e \"$p\" || test -L \"$p\"; then exit 1; fi; done; printf private-absent"


@lru_cache(maxsize=1)
def tokenizer():
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(str(MODEL), local_files_only=True)


def count_prompt(messages):
    encoded = tokenizer().apply_chat_template(messages, tools=TOOLS, tokenize=True,
                                              add_generation_prompt=True, enable_thinking=False)
    if isinstance(encoded, Mapping):
        encoded = encoded['input_ids']
    if not isinstance(encoded, list) or any(type(token) is not int for token in encoded):
        raise ValueError('unexpected chat-template token shape')
    return len(encoded)


def parse_command(function):
    if function.get('name') != 'shell':
        raise ValueError('unknown tool')
    args = json.loads(function['arguments'])
    if not isinstance(args, dict) or set(args) != {'command'} or not isinstance(args['command'], str):
        raise ValueError('shell requires exactly one string command')
    if not args['command'].strip() or len(args['command'].encode()) > 24000 or '\0' in args['command']:
        raise ValueError('empty or oversized command')
    return args['command']


def observation(result):
    streams = [result.stdout or '', result.stderr or '']
    return {'return_code': result.return_code,
            'stdout': streams[0].encode()[:12000].decode(errors='ignore'),
            'stderr': streams[1].encode()[:12000].decode(errors='ignore'),
            'truncated': any(len(s.encode()) > 12000 for s in streams),
            'original_bytes': [len(s.encode()) for s in streams]}


def validate_choice(response):
    choices = response.get('choices')
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError('expected one model choice')
    choice = choices[0]
    message = choice.get('message')
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        raise ValueError('invalid assistant message')
    finish = choice.get('finish_reason')
    calls = message.get('tool_calls') or []
    if finish not in ('stop', 'length', 'tool_calls') or not isinstance(calls, list):
        raise ValueError('invalid finish reason')
    if (finish == 'tool_calls' and not calls) or (finish == 'stop' and calls):
        raise ValueError('finish reason and tools disagree')
    ids = []
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get('id'), str) or not call['id'] or not isinstance(call.get('function'), dict):
            raise ValueError('invalid tool call shape')
        ids.append(call['id'])
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate tool IDs')
    return choice


async def request_once(request, deadline, record):
    # Shield the worker so cancellation cannot orphan an in-flight request.
    # http_json independently bounds socket reads by this same deadline.
    worker = asyncio.create_task(asyncio.to_thread(http_json, 'POST', '/v1/chat/completions', request, deadline))
    try:
        async with asyncio.timeout(max(0, deadline-time.monotonic())):
            return await asyncio.shield(worker)
    except BaseException:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            record['late_response'] = worker.result()
            record['inflight_status'] = 'finished_after_cancel'
        except BaseException as exc:
            record['inflight_status'] = 'failed_usage_unknown'
            record['inflight_error_type'] = type(exc).__name__
        raise


class M1Agent(BaseAgent):
    def __init__(self, *args, temperature=1.0, seed=0, **kwargs):
        super().__init__(*args, **kwargs)
        if self.model_name != SETTINGS['model']:
            raise ValueError('M1 model identity mismatch')
        if temperature not in (0.6, 1.0) or type(seed) is not int:
            raise ValueError('M1 sampling configuration mismatch')
        self.temperature = temperature
        self.seed = seed

    @staticmethod
    def name():
        return 'm1-loopback-shell'

    def version(self):
        return '1'

    async def setup(self, environment):
        await asyncio.to_thread(tokenizer)

    async def run(self, instruction, environment, context):
        start = time.monotonic()
        deadline = start + 180
        work_deadline = deadline - 10  # Reserve END evidence within the 180s budget.
        messages = [{'role': 'system', 'content': 'Solve the task by using the shell tool in the sandbox. Inspect the supplied inputs, follow every rule, and write the required output. Do not modify input files. Finish only after checking your work.'},
                    {'role': 'user', 'content': instruction}]
        trace = {'schema_version': 1, 'model': self.model_name, 'revision': SETTINGS['revision'],
                 'temperature': self.temperature, 'seed': self.seed, 'turns': [], 'isolation': [],
                 'termination': 'not_started'}
        context.n_input_tokens = 0
        context.n_output_tokens = 0
        context.n_cache_tokens = None  # Not measured; do not fabricate zero hits.
        tools_used = 0
        phase = 'isolation_start'
        failure = None

        async def isolation(phase):
            record = {'phase': phase, 'elapsed_seconds': time.monotonic()-start, 'status': 'not_checked'}
            trace['isolation'].append(record)
            seconds = min(10, deadline-time.monotonic())
            if seconds <= 0:
                record['reason'] = 'overall_deadline'
                raise TimeoutError('no isolation budget remaining')
            try:
                async with asyncio.timeout(seconds):
                    result = await environment.exec(command=ISOLATION, timeout_sec=max(1, int(seconds)))
                record.update(observation(result))
                if result.return_code != 0 or result.stdout not in ('private-absent', 'private-absent\n', 'private-absent\r\n'):
                    raise RuntimeError('private task material visible or isolation probe failed')
                record['status'] = 'passed'
            except BaseException as exc:
                record.update(status='failed', error_type=type(exc).__name__)
                raise

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        try:
            await isolation('start')
            for turn in range(10):
                if time.monotonic() >= work_deadline:
                    trace['termination'] = 'agent_timeout'
                    break
                budget = 4096 - context.n_output_tokens
                if budget <= 0:
                    trace['termination'] = 'output_budget'
                    break
                phase = 'tokenizer'
                prompt_tokens = count_prompt(messages)
                if prompt_tokens + budget > 16384:
                    trace['termination'] = 'context_budget'
                    break
                request = {'model': self.model_name, 'messages': json.loads(json.dumps(messages)),
                           'tools': TOOLS, 'tool_choice': 'auto', 'parallel_tool_calls': False,
                           'temperature': self.temperature, 'seed': self.seed + turn,
                           'max_tokens': budget, 'chat_template_kwargs': {'enable_thinking': False}}
                record = {'request': request, 'prompt_tokens_local': prompt_tokens, 'tools': []}
                trace['turns'].append(record)
                phase = 'model_http'
                request_deadline = min(work_deadline, time.monotonic()+60)
                response = await request_once(request, request_deadline, record)
                record['response'] = response
                phase = 'model_response'
                usage = response['usage']
                if any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('prompt_tokens', 'completion_tokens')) or usage['completion_tokens'] > budget:
                    raise RuntimeError('invalid model usage')
                context.n_input_tokens += usage['prompt_tokens']
                context.n_output_tokens += usage['completion_tokens']
                record['usage_accounted'] = True
                if usage['prompt_tokens'] != prompt_tokens:
                    raise RuntimeError('prompt token count differs from serving template')
                choice = validate_choice(response)
                message = choice['message']
                messages.append(message)
                if choice['finish_reason'] == 'length':
                    trace['termination'] = 'output_budget'
                    break
                calls = message.get('tool_calls') or []
                if not calls:
                    trace['termination'] = 'completed'
                    break
                for call in calls:
                    if tools_used >= 10 or time.monotonic() >= work_deadline:
                        trace['termination'] = 'tool_budget'
                        break
                    tools_used += 1
                    tool_record = {'id': call.get('id'), 'elapsed_seconds': time.monotonic()-start}
                    record['tools'].append(tool_record)
                    try:
                        command = parse_command(call['function'])
                    except (ValueError, KeyError, TypeError):
                        result = {'tool_error': 'invalid shell arguments'}
                    else:
                        before = time.monotonic()
                        phase = 'tool'
                        try:
                            seconds = max(0, min(20, work_deadline-before))
                            async with asyncio.timeout(seconds):
                                result = observation(await environment.exec(command=command, timeout_sec=max(1, int(seconds))))
                        except BaseException as exc:
                            tool_record['error_type'] = type(exc).__name__
                            raise
                        finally:
                            tool_record['wall_seconds'] = time.monotonic()-before
                    tool_record['observation'] = result
                    messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(result, ensure_ascii=False)})
                if trace['termination'] == 'tool_budget':
                    break
            else:
                trace['termination'] = 'round_budget'
        except BaseException as exc:
            trace['termination'] = 'runtime_error'
            trace['error_type'] = type(exc).__name__
            trace['error_phase'] = phase
            failure = exc
        finally:
            try:
                await isolation('end')
            except BaseException as exc:
                if failure is None:
                    failure = exc
                    trace.update(termination='runtime_error', error_type=type(exc).__name__, error_phase='isolation_end')
            trace['wall_seconds'] = time.monotonic()-start
            for record in trace['turns']:
                late_response = record.get('late_response')
                late = late_response.get('usage') if isinstance(late_response, dict) else None
                if isinstance(late, dict) and all(type(late.get(k)) is int and late[k] >= 0 for k in ('prompt_tokens', 'completion_tokens')) and late['completion_tokens'] <= record['request']['max_tokens']:
                    context.n_input_tokens += late['prompt_tokens']
                    context.n_output_tokens += late['completion_tokens']
                    record['usage_accounted'] = True
            trace['usage_complete'] = all(record.get('usage_accounted') is True for record in trace['turns'])
            trace['tool_calls'] = tools_used
            trace['usage'] = {'input': context.n_input_tokens, 'output': context.n_output_tokens}
            context.metadata = {k: trace[k] for k in ('termination', 'wall_seconds', 'tool_calls')}
            (self.logs_dir/'m1-trace.json').write_text(json.dumps(trace, indent=2, ensure_ascii=False)+'\n')
        if failure is not None:
            raise failure
