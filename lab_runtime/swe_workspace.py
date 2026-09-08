"""History removal exclusively for disposable source-image workspaces (Python 3.8)."""
import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from .swe_verify import patch_paths, safe_path


def prepare_workspace(root, expected_commit, source, source_id, gold_patch_paths,
                      *, disposable=False, timeout=120):
    root = Path(root)
    if (not disposable or root.name != 'testbed' or not root.is_absolute() or root.is_symlink()
            or root.resolve() != root or root == Path.home()
            or (str(root) != '/testbed' and len(root.parts) < 4)):
        raise ValueError('requires an explicit disposable testbed root')
    metadata = root / '.git'
    if metadata.is_symlink() or not metadata.is_dir():
        raise ValueError('requires real local .git directory')
    if source not in ('smith', 'gym') or not re.fullmatch(r'[0-9a-f]{40}', expected_commit):
        raise ValueError('invalid source or expected commit')
    deadline = time.monotonic() + timeout
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
               GIT_AUTHOR_DATE='2000-01-01T00:00:00+00:00',
               GIT_COMMITTER_DATE='2000-01-01T00:00:00+00:00')
    def git(*args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null',
            '-c', 'user.name=Isolated workspace', '-c', 'user.email=workspace@example.invalid',
            '-c', 'commit.gpgSign=false', *args], cwd=root, env=env,
            stderr=subprocess.PIPE, timeout=max(.001, deadline - time.monotonic()))
    original = git('rev-parse', 'HEAD').decode().strip()
    if original != expected_commit:
        raise ValueError('source HEAD mismatch')
    if git('rev-parse', '--show-toplevel').decode().strip() != str(root):
        raise ValueError('wrong repository root')
    tracked = [os.fsdecode(p) for p in git('ls-files', '-z').split(b'\0') if p]
    restore = git('diff', '--binary', 'HEAD', 'HEAD~1').decode() if source == 'smith' else ''
    paths = patch_paths(restore) if restore.strip() else []
    if set(paths) & {safe_path(p) for p in gold_patch_paths}:
        raise ValueError('test restoration overlaps implementation')
    # All identity/path checks precede the sole destructive operation. No backup
    # is retained in the container, including dangling objects or alternate refs.
    shutil.rmtree(metadata)
    git('init')
    git('symbolic-ref', 'HEAD', 'refs/heads/workspace')
    for offset in range(0, len(tracked), 100):
        existing = [p for p in tracked[offset:offset + 100] if os.path.lexists(root / p)]
        if existing:
            git('--literal-pathspecs', 'add', '-f', '--', *existing)
    git('commit', '--allow-empty', '-m', 'Disposable workspace baseline')
    baseline = git('rev-parse', 'HEAD').decode().strip()
    count = int(git('rev-list', '--all', '--count'))
    remotes = git('remote').decode().splitlines()
    parents = git('rev-list', '--parents', '-n', '1', 'HEAD').decode().split()
    if count != 1 or remotes or parents != [baseline]:
        raise ValueError('fresh root proof failed')
    return dict(source_id=source_id, original_commit=original, baseline_commit=baseline,
                tree=git('rev-parse', 'HEAD^{tree}').decode().strip(),
                proof=dict(all_refs_commit_count=count, remotes=remotes, parents=[]),
                restore_patch_b64=base64.b64encode(restore.encode()).decode())
