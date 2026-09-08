"""Local, bounded source inventory; selection and classification are human work."""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import tempfile

from lab_runtime.task_sources import relative_path, safe_path

MAX_OUTPUT_BYTES = 8 * 1024 ** 3


def iter_source_rows(asset_path, source, parquet_factory=None):
    """Yield unchanged records, loading at most sixteen rows per Arrow batch."""
    if parquet_factory is None:
        from pyarrow.parquet import ParquetFile
        parquet_factory = ParquetFile
    parquet = parquet_factory(asset_path)
    try:
        for batch in parquet.iter_batches(batch_size=16):
            yield from batch.to_pylist()
    finally:
        close = getattr(parquet, 'close', None)
        if close:
            close()


def _changed_files(patch):
    paths = set()
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith('diff --git '):
            in_hunk = False
            header = line[len('diff --git '):]
            # Git does not quote ordinary spaces. Split at its b/ prefix,
            # not whitespace; quoted headers still follow Git's two tokens.
            if header.startswith('"'):
                values = shlex.split(header)
            else:
                candidates = [(header[:m.start()], header[m.start() + 1:])
                              for m in re.finditer(r' b/', header)]
                same_path = [pair for pair in candidates if pair[0][2:] == pair[1][2:]]
                if len(same_path) == 1:
                    values = same_path[0]
                elif len(candidates) == 1:
                    values = candidates[0]
                else:
                    raise ValueError('ambiguous_diff_header')
            if len(values) != 2:
                raise ValueError('invalid_diff_header')
        elif line.startswith('@@'):
            in_hunk = True
            continue
        elif in_hunk:
            continue
        elif line.startswith(('--- ', '+++ ')):
            value = line[4:].split('\t', 1)[0]
            values = shlex.split(value) if value.startswith('"') else [value]
        elif line.startswith(('rename from ', 'rename to ', 'copy from ', 'copy to ')):
            # Git extended headers omit the a/ or b/ prefix.
            value = line.split(' ', 2)[2]
            values = ['a/' + value]
        else:
            continue
        for value in values:
            if value == '/dev/null':
                continue
            if not value.startswith(('a/', 'b/')):
                raise ValueError('unsafe_patch_path')
            value = value[2:]
            path = PurePosixPath(value)
            if (not value or path.is_absolute() or '..' in path.parts or
                    '\\' in value or any(ord(c) < 32 for c in value) or value == '.'):
                raise ValueError('unsafe_patch_path')
            paths.add(value)
    if not paths:
        raise ValueError('missing_patch_file_headers')
    return sorted(paths)


def _index(row, spec, line_number):
    if not isinstance(row, dict):
        raise ValueError('invalid_record')
    fields = ('instance_id', 'repo', 'problem_statement', 'patch')
    if any(not isinstance(row.get(k), str) or not row[k].strip() for k in fields):
        raise ValueError('invalid_record')
    source = spec['path'].split('/')[0]
    base_commit = row.get('base_commit')
    if source != 'swe-smith' or base_commit is not None:
        if not isinstance(base_commit, str) or not base_commit.strip():
            raise ValueError('invalid_base_commit')
    if source == 'swe-smith' and base_commit is None:
        if not isinstance(row.get('image_name'), str) or not row['image_name'].strip():
            raise ValueError('missing_image_identity')
    counts = {}
    for key in ('FAIL_TO_PASS', 'PASS_TO_PASS'):
        tests = row.get(key)
        if isinstance(tests, str):
            tests = json.loads(tests)
        if not isinstance(tests, list) or any(not isinstance(t, str) for t in tests):
            raise ValueError('invalid_test_list')
        counts[key.lower() + '_count'] = len(tests)
    return dict({k: row[k] for k in fields[:-1]}, **counts,
                base_commit=base_commit, image_name=row.get('image_name'),
                patch_sha256=hashlib.sha256(row['patch'].encode()).hexdigest(),
                changed_files=_changed_files(row['patch']),
                source=source, source_revision=spec['revision'],
                source_asset=spec['path'], source_asset_sha256=spec['sha256'],
                source_line=line_number)


def catalog_sources(source_root, manifest, output_root):
    """Verify fixed local assets then atomically publish raw JSONL and an index.

    The output directory must not exist. A parent-directory lock serializes
    cooperating publishers. Failure removes only this invocation's staging tree.
    """
    source_root, output_root = safe_path(Path(source_root)), safe_path(Path(output_root))
    assets = [s for s in manifest['assets'] if s['path'].endswith('.parquet')]
    if not assets:
        raise ValueError('no_parquet_assets')
    seen_assets = set()
    for spec in assets:
        path = relative_path(source_root, spec['path'])
        if spec['path'] in seen_assets or not re.fullmatch(r'[a-z0-9-]+', spec['path'].split('/')[0]):
            raise ValueError('invalid_asset')
        seen_assets.add(spec['path'])
        if not isinstance(spec.get('revision'), str) or not spec['revision']:
            raise ValueError('missing_revision')
        if path.stat().st_size != spec['size']:
            raise ValueError('checksum_or_size')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != spec['sha256']:
            raise ValueError('checksum_or_size')
    output_root.parent.mkdir(parents=True, exist_ok=True)
    parent_fd = os.open(output_root.parent, os.O_RDONLY)
    staging = None
    try:
        fcntl.flock(parent_fd, fcntl.LOCK_EX)
        if output_root.exists():
            raise ValueError('output_exists')
        staging = Path(tempfile.mkdtemp(prefix='.catalog-', dir=output_root.parent))
        total = 0
        rows = 0
        seen = {}
        lines = {}

        def write(stream, value):
            nonlocal total
            data = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode()
            total += len(data)
            if total > MAX_OUTPUT_BYTES:
                raise ValueError('output_size_limit')
            stream.write(data)

        with (staging / 'index.jsonl').open('xb') as index:
            for spec in assets:
                source = spec['path'].split('/')[0]
                seen.setdefault(source, set())
                lines.setdefault(source, 0)
                with (staging / (source + '.jsonl')).open('ab') as raw:
                    for row in iter_source_rows(relative_path(source_root, spec['path']), source):
                        record = _index(row, spec, lines[source] + 1)
                        if record['instance_id'] in seen[source]:
                            raise ValueError('duplicate_instance_id')
                        seen[source].add(record['instance_id'])
                        write(raw, row)
                        write(index, record)
                        lines[source] += 1
                        rows += 1
                    raw.flush()
                    os.fsync(raw.fileno())
            index.flush()
            os.fsync(index.fileno())
        if output_root.exists():
            raise ValueError('output_exists')
        staging.rename(output_root)
        staging = None
        return {'rows': rows, 'bytes': total, 'sources': lines}
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        os.close(parent_fd)
