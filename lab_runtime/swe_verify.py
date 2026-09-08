"""Private post-agent preparation and grading, compatible with Python 3.8+."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from .swe_test_runner import run_tests, _stop_owned_group


def safe_path(value):
    if (not isinstance(value, str) or not value or '\\' in value or '\x00' in value
            or any(c.isspace() for c in value) or value.startswith('/')
            or any(p in ('', '.', '..', '.git') for p in value.split('/'))):
        raise ValueError('unsafe relative path')
    return value


def patch_paths(patch):
    """Reject ambiguous/quoted names and symlinks; validate rename endpoints."""
    paths = set()
    for line in patch.splitlines():
        if line.startswith('diff --git '):
            match = re.fullmatch(r'diff --git a/(\S+) b/(\S+)', line)
            if not match:
                raise ValueError('unsupported patch header')
            paths.update(safe_path(p) for p in match.groups())
        elif line.startswith(('--- ', '+++ ')):
            value = line[4:].split('\t', 1)[0]
            if value != '/dev/null':
                if not value.startswith(('a/', 'b/')):
                    raise ValueError('unsafe patch prefix')
                paths.add(safe_path(value[2:]))
        elif line.startswith(('rename from ', 'rename to ', 'copy from ', 'copy to ')):
            paths.add(safe_path(line.split(' ', 2)[2]))
        elif line.startswith(('new file mode 120', 'deleted file mode 120', 'old mode 120', 'new mode 120')):
            raise ValueError('unsupported patch mode')
    if not paths:
        raise ValueError('patch has no paths')
    return sorted(paths)


def _target(root, name):
    safe_path(name)
    path = root / name
    for part in (path,) + tuple(path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError('symlink test or implementation path')
    return path


def verify_swe(private_dir, output_dir, *, cwd='/testbed'):
    """Never reset implementation. Preparation failures have no numeric reward.

    Hook contract: prepared.json is private and contains exact source_id and
    baseline_commit of a fresh root created before the agent. Smith additionally
    supplies restore-tests.patch (branch tip -> HEAD~1, generated before history
    removal). The controller owns the trust boundary for these private files.
    """
    private, output, repo = Path(private_dir), Path(output_dir), Path(cwd)
    output.mkdir(parents=True, exist_ok=True)
    for name in ('reward.txt', 'grade.json'):
        path = output / name
        if path.exists():
            path.unlink()
    phase = 'receipt'
    deadline = time.monotonic() + 30
    def command(args, **kwargs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('preparation timeout')
        return subprocess.run(args, cwd=repo, check=True, capture_output=True,
                              timeout=remaining, **kwargs).stdout
    try:
        config = json.loads((private / 'private.json').read_text())
        deadline = time.monotonic() + config['timeout']
        receipt = json.loads((private / 'prepared.json').read_text())
        if receipt['source_id'] != config['source_id']:
            raise ValueError('source identity mismatch')
        baseline = receipt['baseline_commit']
        if not re.fullmatch(r'[0-9a-f]{40}', baseline):
            raise ValueError('invalid baseline commit')
        parents = command(['git', 'rev-list', '--parents', '-n', '1', baseline], text=True).strip().split()
        if parents != [baseline]:
            raise ValueError('baseline is not a fresh root')
        command(['git', 'merge-base', '--is-ancestor', baseline, 'HEAD'])
        gold_paths = set(safe_path(p) for p in config['gold_patch_paths'])
        patch_file = private / ('test.patch' if config['source'] == 'gym' else 'restore-tests.patch')
        patch = patch_file.read_text()
        test_paths = patch_paths(patch) if patch.strip() else []
        if config['source'] == 'gym' and set(test_paths) != set(config['test_patch_paths']):
            raise ValueError('test patch identity mismatch')
        if gold_paths & set(test_paths):
            raise ValueError('test restoration overlaps implementation')
        for name in test_paths:
            _target(repo, name)
        profile = config['profile']
        activation = profile.get('activation_command', 'source /opt/miniconda3/bin/activate\nconda activate testbed')
        if config['source'] == 'gym':
            phase = 'reinstall'
            changed = command(['git', 'diff', '--name-only', '-z', baseline], text=False).decode().split('\0')
            untracked = command(['git', 'ls-files', '--others', '--exclude-standard', '-z']).decode().split('\0')
            implementation = (set(filter(None, changed + untracked)) | gold_paths) - set(test_paths)
            def snapshot():
                result = {}
                for name in sorted(implementation):
                    path = _target(repo, name)
                    result[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                return result
            before = snapshot()
            shell = '\n'.join(['set -e', activation] + profile.get('eval_commands', []) + [profile.get('verify_reinstall_command', '')])
            try:
                with (output / 'prepare.log').open('wb') as log:
                    process = subprocess.Popen(['/bin/bash', '-c', shell], cwd=repo,
                                               stdout=log, stderr=subprocess.STDOUT,
                                               start_new_session=True)
                    try:
                        returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
                    finally:
                        if not _stop_owned_group(process):
                            raise ValueError('prepare_process_group_cleanup_uncertain')
                    if returncode:
                        raise ValueError('reinstall command failed: ' + str(returncode))
            finally:
                if before != snapshot():
                    raise ValueError('implementation_changed_by_reinstall')
        phase = 'restore_tests'
        for name in test_paths:
            path = _target(repo, name)
            present = command(['git', 'ls-tree', '--name-only', baseline, '--', name], text=True).strip()
            if present:
                content = command(['git', 'show', baseline + ':' + name])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            elif path.exists():
                if not path.is_file():
                    raise ValueError('non-file test path')
                path.unlink()
        if patch.strip():
            command(['git', 'apply', '--check', str(patch_file.resolve())])
            command(['git', 'apply', str(patch_file.resolve())])
        (output / 'prepare.json').write_text(json.dumps(dict(outcome='prepared', source_id=config['source_id'], baseline_commit=baseline), sort_keys=True) + '\n')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('preparation exhausted verifier timeout')
        # The frozen Gym profiles have only export directives. Replay those in
        # the test shell so exports survive the preparation subprocess while
        # preserving the runner's private pytest-plugin PYTHONPATH injection.
        setup = ''
        if config['source'] == 'gym':
            setup = activation + '\n' + '\n'.join(c for c in profile.get('eval_commands', [])
                                                   if c.lstrip().startswith('export ')) + '\n'
        return run_tests('set -e\n' + setup + profile['test_command'],
                         config, output, cwd=repo, timeout=remaining)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result = dict(outcome='invalid', reward=None, phase=phase, error=str(exc))
        (output / 'prepare.json').write_text(json.dumps(result, sort_keys=True) + '\n')
        return result


def main():
    result = verify_swe('/tests', '/logs/verifier')
    raise SystemExit(0 if result.get('reward') is not None else 1)


if __name__ == '__main__':
    main()
