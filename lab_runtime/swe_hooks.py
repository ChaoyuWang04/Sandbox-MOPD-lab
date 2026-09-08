"""Controller-owned SWE lifecycle isolation for trusted NOP/oracle pilots."""
import asyncio
import base64
import inspect
import json
from pathlib import Path
import shlex

from . import swe_workspace
from .swe_verify import patch_paths, safe_path


def attach_swe_hooks(trial, private_output_dir, source, profile, gold_patch_paths,
                     agent_name, hook_timeout=120, *, workspace_root='/testbed',
                     disposable=True):
    from harbor.trial.hooks import TrialEvent
    if agent_name not in ('nop', 'oracle', 'm1'):
        raise ValueError('unsupported agent for the private SWE boundary')
    if source not in ('smith', 'gym') or hook_timeout <= 0:
        raise ValueError('invalid hook configuration')
    expected = profile['instance_commit' if source == 'smith' else 'base_commit']
    source_id = profile['instance_id']
    private = Path(private_output_dir)
    evidence = dict(phase='registered', source_id=source_id)

    async def execute(command):
        result = await asyncio.wait_for(trial.agent_environment.exec(
            command=command, timeout_sec=hook_timeout), timeout=hook_timeout)
        if result.return_code != 0:
            raise RuntimeError('SWE isolation command failed: ' + str(result.stderr)[-2000:])
        return result.stdout

    async def absent(allow_solution=False):
        await execute('test ! -e /tests && test ! -L /tests' +
                      ('' if allow_solution else ' && test ! -e /solution && test ! -L /solution'))

    async def start(event):
        if evidence['phase'] != 'registered':
            raise RuntimeError('SWE hook phase order')
        evidence['phase'] = 'preparing'
        await absent()
        # Inline audited stdlib source, including the one shared path validator.
        # No patch contents, solution, or credentials enter this command.
        script = inspect.getsource(swe_workspace).replace('from .swe_verify import patch_paths, safe_path',
                    inspect.getsource(safe_path) + '\n' + inspect.getsource(patch_paths))
        config = dict(root=workspace_root, expected_commit=expected, source=source,
                      source_id=source_id, gold_patch_paths=gold_patch_paths,
                      disposable=disposable, timeout=hook_timeout)
        script += '\nimport json\nprint(json.dumps(prepare_workspace(**json.loads(' + repr(json.dumps(config)) + '))))\n'
        receipt = json.loads(await execute('python3 -I -c ' + shlex.quote(script)))
        restore = base64.b64decode(receipt.pop('restore_patch_b64'), validate=True)
        if receipt['source_id'] != source_id or receipt['original_commit'] != expected:
            raise RuntimeError('workspace receipt identity mismatch')
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        (private / 'prepared.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')
        if source == 'smith':
            (private / 'restore-tests.patch').write_bytes(restore)
        await absent()
        evidence.update(phase='agent_ready', receipt=receipt)

    async def end(event):
        if evidence['phase'] != 'agent_ready':
            raise RuntimeError('SWE hook phase order')
        await absent(allow_solution=agent_name == 'oracle')
        evidence['phase'] = 'agent_ended'

    async def verification(event):
        if evidence['phase'] != 'agent_ended':
            raise RuntimeError('SWE hook phase order')
        evidence['phase'] = 'uploading_verifier_receipt'
        await absent(allow_solution=agent_name == 'oracle')
        await execute('mkdir /tests')
        for name in ['prepared.json'] + (['restore-tests.patch'] if source == 'smith' else []):
            await asyncio.wait_for(trial.agent_environment.upload_file(
                source_path=private / name, target_path='/tests/' + name), timeout=hook_timeout)
        evidence['phase'] = 'verification_ready'

    for event, callback in [(TrialEvent.AGENT_START, start), (TrialEvent.AGENT_END, end),
                            (TrialEvent.VERIFICATION_START, verification)]:
        trial.add_hook(event, callback)
    return evidence
