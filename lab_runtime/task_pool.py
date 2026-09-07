"""Deterministic M1 Harbor pool. Generation and local checks need only stdlib.

The expected-value implementations below are separate from the emitted oracle
programs. This is normal-phase task checking, not adversarial process isolation.
"""
import csv
import hashlib
import io
import json
from pathlib import Path
import random
import textwrap


FAMILIES = ('fs_logs', 'fs_inventory', 'data_csv', 'data_json')
MAX_RESULT_BYTES = 65536
VERIFIER_TIMEOUT_SECONDS = 30
AGENT_TIMEOUT_SECONDS = 180


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + '\n'


def _digest(content):
    return hashlib.sha256(content).hexdigest()


COMMON_INSTRUCTION = '''
Use only the visible files under /app/input. Do not modify, delete, rename, or
add files within input; the verifier checks the exact original visible inputs.
Write the answer as UTF-8 JSON to /app/result.json, a regular file of at most
65536 bytes. No symlink answer is accepted. Do not print an answer in place of
creating the file. Include exactly the specified keys, use integers (not
booleans or floats) for integer values, and preserve required list order.
Object key order and JSON whitespace do not matter. There is no network or
package installation requirement; Python 3.12 and its standard library exist.
All string comparisons and tie breaks below are case-sensitive lexicographic
comparisons. Paths are relative POSIX paths; inspect only the stated input tree.
'''

INSTRUCTIONS = {
    'fs_logs': '''Summarize the JSONL events in input/logs/*.jsonl.
1. A record is eligible only if 100 <= timestamp < 200, level is exactly ERROR,
   status is not ignored, and latency_ms >= 0. Filter before deduplication.
2. Deduplicate eligible records by event_id. Keep greatest timestamp; tied
   timestamps use lexicographically smallest filename, then earliest line
   number (one-based). Other fields do not break this tie.
3. Group retained events by user. Count events, sum latency_ms, and take the
   maximum latency_ms. Include a user only if its retained event count >= 2.
4. Sort users by decreasing count, then user ascending. kept_events counts ALL
   deduplicated eligible events, including users omitted by rule 3.
Output: {"kept_events": integer, "users": [{"user": string, "count": integer,
"total_latency_ms": integer, "max_latency_ms": integer}, ...]}.
''',
    'fs_inventory': '''Inventory regular files recursively inside input/tree only.
1. Ignore any file whose relative path has a component starting with a dot.
2. Keep only case-sensitive .txt and .log suffixes, with byte size 1 through 64
   inclusive. Do not count directories. A zero-byte file is excluded.
3. A file's group is its first path component, or "." for a file directly in
   the tree root. Count nonblank lines using UTF-8 splitlines(): a line is
   nonblank if stripping whitespace leaves characters. Byte size counts all
   bytes, including whitespace and newlines, before any decoding or stripping.
4. For each group sum eligible file_count, total_bytes, and nonblank_lines.
   Include only groups with >= 2 eligible files. Sort groups by decreasing
   total_bytes, then group ascending.
5. eligible_files lists ALL eligible relative file paths ascending, including
   files in groups omitted by rule 4. Do not list result.json or /tests.
Output: {"eligible_files": [string, ...], "groups": [{"group": string,
"file_count": integer, "total_bytes": integer, "nonblank_lines": integer}, ...]}.
''',
    'data_csv': '''Aggregate input/orders.csv (CSV header present; use a CSV parser).
1. Filter rows to status exactly paid, 1 <= day <= 7, amount_cents >= 0.
2. Deduplicate eligible rows by order_id, keeping greatest integer revision;
   tied revisions keep the earliest row in the original CSV (header excluded).
3. After deduplication, keep only category exactly books. A later eligible
   non-books revision can therefore remove an earlier books order.
4. Group by customer, counting selected orders and summing amount_cents. Keep
   customers whose total_cents >= 100. Sort decreasing total_cents, then customer
   ascending. selected_orders counts ALL books orders after deduplication,
   including customers excluded by the total threshold.
Output: {"selected_orders": integer, "customers": [{"customer": string,
"orders": integer, "total_cents": integer}, ...]}.
''',
    'data_json': '''Summarize input/catalog.json, which contains warehouses and items.
1. Read items only from warehouses with enabled=true. Keep only items with
   active=true, qty > 0, price_cents >= 0, category exactly food or tools.
2. Deduplicate globally by sku. Pick lowest price_cents; tied price picks
   greatest qty; tied price and qty picks warehouse name ascending. Each
   warehouse has unique names and no duplicate eligible sku within itself.
3. For each retained item's category count distinct sku_count, sum total_qty,
   and sum inventory_value_cents = qty * price_cents. Keep categories with
   total_qty >= 5 and sort category ascending.
4. chosen lists ALL retained items sorted by sku, including items in any
   category omitted by rule 3. No warehouse or category ordering comes from
   the original array ordering.
Output: {"categories": [{"category": string, "sku_count": integer,
"total_qty": integer, "inventory_value_cents": integer}, ...],
"chosen": [{"sku": string, "warehouse": string, "qty": integer,
"price_cents": integer}, ...]}.
''',
}


def _logs(seed):
    rng = random.Random(seed)
    rows = []
    for user in ('Ada', 'Ben', 'Cy'):
        for index in range(3):
            rows.append(dict(event_id=f'{user}-{index}', user=user, timestamp=100+index,
                             level='ERROR', status='ok', latency_ms=rng.randrange(0, 40)))
    rows += [
        dict(event_id='boundary-100', user='Ada', timestamp=100, level='ERROR', status='ok', latency_ms=0),
        dict(event_id='boundary-199', user='Ben', timestamp=199, level='ERROR', status='ok', latency_ms=9),
        dict(event_id='boundary-99', user='Cy', timestamp=99, level='ERROR', status='ok', latency_ms=500),
        dict(event_id='boundary-200', user='Cy', timestamp=200, level='ERROR', status='ok', latency_ms=500),
        dict(event_id='ignored', user='Cy', timestamp=120, level='ERROR', status='ignored', latency_ms=500),
        dict(event_id='negative', user='Cy', timestamp=120, level='ERROR', status='ok', latency_ms=-1),
        dict(event_id='lowercase', user='Cy', timestamp=120, level='error', status='ok', latency_ms=500),
        dict(event_id='Ada-0', user='Cy', timestamp=180, level='ERROR', status='ignored', latency_ms=500),
        dict(event_id='Ben-0', user='Ben', timestamp=170, level='ERROR', status='ok', latency_ms=2),
        dict(event_id='solo', user='OnlyOne', timestamp=140, level='ERROR', status='ok', latency_ms=3),
    ]
    rng.shuffle(rows)
    by_file = {'logs/a.jsonl': rows[::2], 'logs/z.jsonl': rows[1::2]}
    # Cross-file and same-file ties are explicit, irrespective of seeded shuffle.
    tie = dict(event_id='tie', user='Ada', timestamp=150, level='ERROR', status='ok', latency_ms=7)
    by_file['logs/a.jsonl'] += [tie, dict(tie, user='Cy', latency_ms=70)]
    by_file['logs/z.jsonl'] += [dict(tie, user='Ben', latency_ms=700)]
    retained = {}
    for name, records in by_file.items():
        for line, row in enumerate(records, 1):
            if not (100 <= row['timestamp'] < 200 and row['level'] == 'ERROR' and row['status'] != 'ignored' and row['latency_ms'] >= 0):
                continue
            priority = (-row['timestamp'], name, line)
            key = row['event_id']
            if key not in retained or priority < retained[key][0]:
                retained[key] = (priority, row)
    users = []
    for user in sorted({row['user'] for _, row in retained.values()}):
        values = [row['latency_ms'] for _, row in retained.values() if row['user'] == user]
        if len(values) >= 2:
            users.append(dict(user=user, count=len(values), total_latency_ms=sum(values), max_latency_ms=max(values)))
    users.sort(key=lambda row: (-row['count'], row['user']))
    files = {name: ''.join(json.dumps(row, sort_keys=True)+'\n' for row in records).encode() for name, records in by_file.items()}
    return files, dict(kept_events=len(retained), users=users)


def _inventory(seed):
    rng = random.Random(seed)
    files = {
        'tree/alpha/a.txt': b'A\n\n B \n', 'tree/alpha/b.log': b'x'*64,
        'tree/alpha/too-big.txt': b'x'*65, 'tree/alpha/empty.txt': b'',
        'tree/beta/nested/a.txt': b'one\r\n \r\ntwo\r\n', 'tree/beta/b.log': b'Z\n',
        'tree/.hidden/leak.txt': b'not included\n', 'tree/beta/.secret.log': b'not included\n',
        'tree/gamma/only.txt': b'q', 'tree/alpha/upper.TXT': b'not included\n',
        'tree/root.txt': b'r\n', 'tree/root.log': b's\n\t\n', 'tree/alpha/notes.csv': b'ignore\n',
    }
    for group in ('alpha', 'beta', 'delta'):
        for index in range(2):
            files[f'tree/{group}/random-{index}.txt'] = (f'case {seed}\n' + 'v\n'*rng.randrange(1, 8)).encode()
    eligible = {}
    for name, content in files.items():
        relative = name.removeprefix('tree/')
        parts = relative.split('/')
        if any(part.startswith('.') for part in parts) or not relative.endswith(('.txt', '.log')) or not 1 <= len(content) <= 64:
            continue
        eligible[relative] = content
    groups = []
    group_names = {name.split('/')[0] if '/' in name else '.' for name in eligible}
    for group in group_names:
        contents = [content for name, content in eligible.items() if (name.split('/')[0] if '/' in name else '.') == group]
        if len(contents) >= 2:
            groups.append(dict(group=group, file_count=len(contents), total_bytes=sum(map(len, contents)),
                               nonblank_lines=sum(sum(bool(line.strip()) for line in content.decode().splitlines()) for content in contents)))
    groups.sort(key=lambda row: (-row['total_bytes'], row['group']))
    return files, dict(eligible_files=sorted(eligible), groups=groups)


def _csv(seed):
    rng = random.Random(seed)
    rows = []
    for customer in ('Ada', 'Ben', 'Cy, Inc'):
        for index in range(3):
            rows.append(dict(order_id=f'{customer}-{index}', customer=customer, category='books',
                             amount_cents=50+rng.randrange(10), status='paid', day=index+1, revision=1))
    rows += [
        dict(order_id='day-1', customer='Boundary', category='books', amount_cents=100, status='paid', day=1, revision=1),
        dict(order_id='day-7', customer='Boundary', category='books', amount_cents=0, status='paid', day=7, revision=1),
        dict(order_id='day-0', customer='Boundary', category='books', amount_cents=900, status='paid', day=0, revision=1),
        dict(order_id='day-8', customer='Boundary', category='books', amount_cents=900, status='paid', day=8, revision=1),
        dict(order_id='negative', customer='Boundary', category='books', amount_cents=-1, status='paid', day=2, revision=1),
        dict(order_id='case', customer='Boundary', category='books', amount_cents=900, status='Paid', day=2, revision=1),
        dict(order_id='under', customer='Under', category='books', amount_cents=99, status='paid', day=2, revision=1),
        dict(order_id='Ada-0', customer='Ada', category='toys', amount_cents=1000, status='paid', day=3, revision=2),
        dict(order_id='Ben-0', customer='Ben', category='books', amount_cents=1000, status='cancelled', day=3, revision=9),
        dict(order_id='lex-revision', customer='Boundary', category='books', amount_cents=90, status='paid', day=2, revision=9),
        dict(order_id='lex-revision', customer='Boundary', category='books', amount_cents=10, status='paid', day=2, revision=10),
    ]
    rng.shuffle(rows)
    rows += [dict(order_id='tie', customer='Boundary', category='books', amount_cents=1, status='paid', day=2, revision=2),
             dict(order_id='tie', customer='Boundary', category='books', amount_cents=500, status='paid', day=2, revision=2)]
    chosen = {}
    for row in rows:
        if row['status'] == 'paid' and 1 <= row['day'] <= 7 and row['amount_cents'] >= 0:
            if row['order_id'] not in chosen or row['revision'] > chosen[row['order_id']]['revision']:
                chosen[row['order_id']] = row
    books = [row for row in chosen.values() if row['category'] == 'books']
    customers = []
    for customer in {row['customer'] for row in books}:
        amounts = [row['amount_cents'] for row in books if row['customer'] == customer]
        if sum(amounts) >= 100:
            customers.append(dict(customer=customer, orders=len(amounts), total_cents=sum(amounts)))
    customers.sort(key=lambda row: (-row['total_cents'], row['customer']))
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=('order_id', 'customer', 'category', 'amount_cents', 'status', 'day', 'revision'), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return {'orders.csv': buffer.getvalue().encode()}, dict(selected_orders=len(books), customers=customers)


def _catalog(seed):
    rng = random.Random(seed)
    def item(sku, category='food', qty=3, price=10, active=True):
        return dict(sku=sku, category=category, qty=qty, price_cents=price, active=active)
    warehouses = [
        dict(name='alpha', enabled=True, items=[item('apple', qty=5, price=0), item('bolt', 'tools', 2, 7),
             item('tie-name', 'tools', 3, 20), item('tie-qty', qty=2, price=9), item('zero-qty', qty=0),
             item('negative-price', price=-1), item('inactive', active=False), item('wrong-category', 'Food')]),
        dict(name='zeta', enabled=True, items=[item('apple', qty=30, price=1), item('bolt', 'tools', 9, 8),
             item('tie-name', 'food', 3, 20), item('tie-qty', qty=4, price=9), item('negative-qty', qty=-1)]),
        dict(name='disabled', enabled=False, items=[item('apple', qty=1000, price=0), item('ghost', 'tools', 1000, 0)]),
    ]
    for index in range(3):
        warehouses[index % 2]['items'].append(item(f'seed-{seed}-{index}', 'tools' if index % 2 else 'food', rng.randrange(1, 7), rng.randrange(1, 20)))
    # Preassigned boundary variants: one category remains in chosen below five,
    # while the other lands exactly on five. Other variants retain larger sums.
    if seed % 4 in (0, 1):
        for warehouse in warehouses[:2]:
            for row in warehouse['items']:
                if row['sku'] in ('apple', 'bolt', 'tie-name') or row['sku'].startswith('seed-'):
                    row['qty'] = 1
                elif row['sku'] == 'tie-qty':
                    row['qty'] = 1 if warehouse['name'] == 'alpha' else 2
                if seed % 4 == 1 and row['sku'] == f'seed-{seed}-2':
                    row['category'], row['qty'] = 'tools', 2
    rng.shuffle(warehouses)
    for warehouse in warehouses:
        rng.shuffle(warehouse['items'])
    chosen = {}
    for warehouse in warehouses:
        if warehouse['enabled'] is not True:
            continue
        for row in warehouse['items']:
            if row['active'] is not True or row['qty'] <= 0 or row['price_cents'] < 0 or row['category'] not in ('food', 'tools'):
                continue
            priority = (row['price_cents'], -row['qty'], warehouse['name'])
            if row['sku'] not in chosen or priority < chosen[row['sku']][0]:
                chosen[row['sku']] = (priority, warehouse['name'], row)
    categories = []
    for category in ('food', 'tools'):
        records = [row for _, _, row in chosen.values() if row['category'] == category]
        quantity = sum(row['qty'] for row in records)
        if quantity >= 5:
            categories.append(dict(category=category, sku_count=len(records), total_qty=quantity,
                                   inventory_value_cents=sum(row['qty']*row['price_cents'] for row in records)))
    result = [dict(sku=sku, warehouse=warehouse, qty=row['qty'], price_cents=row['price_cents'])
              for sku, (_, warehouse, row) in sorted(chosen.items())]
    return {'catalog.json': _json(dict(warehouses=warehouses)).encode()}, dict(categories=categories, chosen=result)


ORACLES = {
    'fs_logs': '''
events = []
for path in sorted((root/'input/logs').glob('*.jsonl')):
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        row = json.loads(line)
        if row['timestamp'] not in range(100, 200) or row['level'] != 'ERROR':
            continue
        if row['status'] == 'ignored' or row['latency_ms'] < 0:
            continue
        events.append((row, path.name, line_number))
events.sort(key=lambda value: (-value[0]['timestamp'], value[1], value[2]))
seen, totals = set(), {}
for row, filename, line in events:
    if row['event_id'] in seen:
        continue
    seen.add(row['event_id'])
    summary = totals.setdefault(row['user'], dict(user=row['user'], count=0, total_latency_ms=0, max_latency_ms=0))
    summary['count'] += 1
    summary['total_latency_ms'] += row['latency_ms']
    summary['max_latency_ms'] = max(summary['max_latency_ms'], row['latency_ms'])
users = [summary for summary in totals.values() if summary['count'] > 1]
users.sort(key=lambda row: (-row['count'], row['user']))
answer = dict(kept_events=len(seen), users=users)
''',
    'fs_inventory': '''
base = root/'input/tree'
eligible, groups = [], {}
for path in sorted(base.rglob('*')):
    parts = path.relative_to(base).parts
    if not path.is_file() or any(part[:1] == '.' for part in parts):
        continue
    if path.suffix not in {'.log', '.txt'}:
        continue
    data = path.read_bytes()
    if len(data) == 0 or len(data) > 64:
        continue
    eligible.append(path.relative_to(base).as_posix())
    group = parts[0] if len(parts) > 1 else '.'
    summary = groups.setdefault(group, dict(group=group, file_count=0, total_bytes=0, nonblank_lines=0))
    summary['file_count'] += 1
    summary['total_bytes'] += len(data)
    for line in data.decode('utf-8').splitlines():
        if line.strip():
            summary['nonblank_lines'] += 1
answer = dict(eligible_files=sorted(eligible), groups=sorted(
    [value for value in groups.values() if value['file_count'] >= 2],
    key=lambda row: (-row['total_bytes'], row['group'])))
''',
    'data_csv': '''
import csv
with (root/'input/orders.csv').open(newline='') as handle:
    rows = list(csv.DictReader(handle))
eligible = []
for position, row in enumerate(rows):
    for key in ('revision', 'day', 'amount_cents'):
        row[key] = int(row[key])
    if row['status'] != 'paid' or not 1 <= row['day'] <= 7 or row['amount_cents'] < 0:
        continue
    eligible.append((row, position))
eligible.sort(key=lambda pair: (-pair[0]['revision'], pair[1]))
seen, summaries, count = set(), {}, 0
for row, position in eligible:
    if row['order_id'] in seen:
        continue
    seen.add(row['order_id'])
    if row['category'] != 'books':
        continue
    count += 1
    summary = summaries.setdefault(row['customer'], dict(customer=row['customer'], orders=0, total_cents=0))
    summary['orders'] += 1
    summary['total_cents'] += row['amount_cents']
answer = dict(selected_orders=count, customers=sorted(
    [row for row in summaries.values() if row['total_cents'] >= 100],
    key=lambda row: (-row['total_cents'], row['customer'])))
''',
    'data_json': '''
candidates = []
for warehouse in json.loads((root/'input/catalog.json').read_text())['warehouses']:
    if not warehouse['enabled']:
        continue
    for row in warehouse['items']:
        if not row['active'] or row['qty'] < 1 or row['price_cents'] < 0:
            continue
        if row['category'] in {'food', 'tools'}:
            candidates.append((warehouse['name'], row))
candidates.sort(key=lambda pair: (pair[1]['price_cents'], -pair[1]['qty'], pair[0]))
seen, chosen, summaries = set(), [], {}
for warehouse, row in candidates:
    if row['sku'] in seen:
        continue
    seen.add(row['sku'])
    chosen.append(dict(sku=row['sku'], warehouse=warehouse, qty=row['qty'], price_cents=row['price_cents']))
    summary = summaries.setdefault(row['category'], dict(category=row['category'], sku_count=0, total_qty=0, inventory_value_cents=0))
    summary['sku_count'] += 1
    summary['total_qty'] += row['qty']
    summary['inventory_value_cents'] += row['qty']*row['price_cents']
answer = dict(chosen=sorted(chosen, key=lambda row: row['sku']), categories=sorted(
    [row for row in summaries.values() if row['total_qty'] >= 5], key=lambda row: row['category']))
''',
}


VERIFIER = '''
"""Grade the actual visible input snapshot and a bounded regular JSON answer."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

def equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(equal(actual[key], expected[key]) for key in expected)
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(equal(a, b) for a, b in zip(actual, expected))
    return actual == expected

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result

def grade(root):
    private = json.loads((Path(__file__).parent/'expected.json').read_text())
    base = root/'input'
    if base.is_symlink() or not base.is_dir():
        return False
    actual_files = {}
    for path in base.rglob('*'):
        if path.is_symlink():
            return False
        if path.is_file():
            relative = path.relative_to(base).as_posix()
            expected_size = private['input_sizes'].get(relative)
            if expected_size is None or path.stat().st_size != expected_size:
                return False
            actual_files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.is_dir():
            return False
    if actual_files != private['input_sha256']:
        return False
    answer_path = root/'result.json'
    # O_NOFOLLOW closes the ordinary lstat/open symlink race on the final file.
    descriptor = os.open(answer_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            return False
        raw = handle.read(65537)
    if len(raw) > 65536:
        return False
    actual = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
    return equal(actual, private['answer'])

if __name__ == '__main__':
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/app')
    rewards = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('/logs/verifier')
    try:
        passed = grade(root)
        detail = 'pass' if passed else 'answer_or_input_mismatch'
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        passed, detail = False, 'missing_or_invalid_answer_or_input'
    rewards.mkdir(parents=True, exist_ok=True)
    (rewards/'reward.txt').write_text('1\\n' if passed else '0\\n')
    (rewards/'result.json').write_text(json.dumps({'passed': passed, 'detail': detail})+'\\n')
'''


def _task_files(family, instance, seed):
    inputs, answer = {'fs_logs': _logs, 'fs_inventory': _inventory, 'data_csv': _csv, 'data_json': _catalog}[family](seed)
    oracle = "import json\nfrom pathlib import Path\nimport sys\nroot = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/app')\n"
    oracle += textwrap.dedent(ORACLES[family]).lstrip()
    oracle += "(root/'result.json').write_text(json.dumps(answer, sort_keys=True)+'\\n')\n"
    files = {f'environment/input/{name}': data for name, data in inputs.items()}
    private = dict(answer=answer, input_sha256={name: _digest(data) for name, data in sorted(inputs.items())},
                   input_sizes={name: len(data) for name, data in sorted(inputs.items())})
    text_files = {
        'instruction.md': f'# {instance}\n\n' + textwrap.dedent(COMMON_INSTRUCTION).strip() + '\n\n' + INSTRUCTIONS[family],
        'task.toml': f'''schema_version = "1.4"
[task]
name = "sandbox-mopd/{instance}"
version = "1.0.0"
authors = []
keywords = ["m1", "{family}"]
[metadata]
difficulty = "medium"
category = "data-processing"
tags = ["stdlib", "deterministic"]
[verifier]
timeout_sec = {VERIFIER_TIMEOUT_SECONDS}.0
[agent]
timeout_sec = {AGENT_TIMEOUT_SECONDS}.0
[environment]
build_timeout_sec = 120.0
cpus = 1
memory_mb = 1024
storage_mb = 3072
gpus = 0
''',
        'environment/Dockerfile': 'FROM python:3.12.13-slim\nWORKDIR /app\nCOPY input/ /app/input/\n',
        'solution/solve.sh': '#!/bin/sh\nset -eu\nexec python3 "$(dirname "$0")/oracle.py" "${1:-/app}"\n',
        'solution/oracle.py': oracle,
        'tests/test.sh': '#!/bin/sh\nset -eu\nexec python3 "$(dirname "$0")/verify.py" "${1:-/app}" "${2:-/logs/verifier}"\n',
        'tests/verify.py': textwrap.dedent(VERIFIER).lstrip(),
        'tests/expected.json': _json(private),
    }
    files.update({name: value.encode('utf-8') for name, value in text_files.items()})
    return files


def build_pool(output_dir: Path) -> dict:
    """Create 32 fixed instances, refusing symlinks and nonempty output.

    The caller chooses storage, not seeds, split assignments, or thresholds.
    No subprocess, dependency install, provider, model, or secret access occurs.
    """
    output = Path(output_dir).absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError('output path must not traverse a symlink')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('output must be nonexistent or an empty directory')
    output.mkdir(parents=True, exist_ok=True)
    manifest = dict(schema_version=1, pool='m1-small-pool-v1',
                    split_policy='Preassigned 4 train and 4 eval per family; never selected using scores.',
                    instances=[])
    for family_index, family in enumerate(FAMILIES):
        for split_index, split in enumerate(('train', 'eval')):
            for index in range(4):
                seed = 41000 + 100*family_index + 10*split_index + index
                instance = f'{family}-{split}-{index:02d}'
                relative = f'tasks/{instance}'
                files = _task_files(family, instance, seed)
                for name, content in sorted(files.items()):
                    path = output/relative/name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('xb') as handle:
                        handle.write(content)
                    if path.suffix == '.sh':
                        path.chmod(0o755)
                manifest['instances'].append(dict(family=family, domain='FS' if family.startswith('fs_') else 'DATA',
                    instance=instance, split=split, seed=seed, path=relative,
                    file_sha256={name: _digest(content) for name, content in sorted(files.items())}))
    with (output/'manifest.json').open('x') as handle:
        handle.write(_json(manifest))
    return manifest
