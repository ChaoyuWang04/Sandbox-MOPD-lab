"""Offline acceptance of the frozen M1 pool; no provider or model calls."""
import importlib
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class TaskPoolTest(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('lab_runtime.task_pool'), 'pool generator missing')
        return importlib.import_module('lab_runtime.task_pool')

    def test_pool_is_deterministic_and_splits_are_disjoint(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first = m.build_pool(root/'first')
            second = m.build_pool(root/'second')
            self.assertEqual(first, second)
            self.assertEqual(first, json.loads((root/'first/manifest.json').read_text()))
            self.assertEqual(len(first['instances']), 32)
            self.assertEqual(len({row['seed'] for row in first['instances']}), 32)
            for family in {row['family'] for row in first['instances']}:
                for split in ('train', 'eval'):
                    self.assertEqual(sum(row['family'] == family and row['split'] == split for row in first['instances']), 4)
            one = {str(p.relative_to(root/'first')): p.read_bytes() for p in (root/'first').rglob('*') if p.is_file()}
            two = {str(p.relative_to(root/'second')): p.read_bytes() for p in (root/'second').rglob('*') if p.is_file()}
            self.assertEqual(one, two)

    def test_refuses_existing_content_and_symlinks(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            occupied = root/'occupied'
            occupied.mkdir()
            (occupied/'keep').write_text('owned by someone else')
            with self.assertRaises(ValueError):
                m.build_pool(occupied)
            link = root/'linked'
            link.symlink_to(root/'destination', target_is_directory=True)
            with self.assertRaises(ValueError):
                m.build_pool(link)
            self.assertEqual((occupied/'keep').read_text(), 'owned by someone else')

    def test_all_instances_oracle_nop_and_targeted_error(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            manifest = m.build_pool(root/'pool')
            for row in manifest['instances']:
                with self.subTest(instance=row['instance']):
                    task = root/'pool'/row['path']
                    workspace = root/'workspace'/row['instance']
                    shutil.copytree(task/'environment/input', workspace/'input')
                    reward = root/'reward'/row['instance']
                    def grade():
                        subprocess.run([sys.executable, str(task/'tests/verify.py'), str(workspace), str(reward)], check=True, timeout=5)
                        return int((reward/'reward.txt').read_text())
                    self.assertEqual(grade(), 0, 'NOP must fail')
                    subprocess.run([sys.executable, str(task/'solution/oracle.py'), str(workspace)], check=True, timeout=5)
                    self.assertEqual(grade(), 1, 'independent oracle must pass')
                    result = json.loads((workspace/'result.json').read_text())
                    self.assertNotEqual(result, {})
                    # A realistic wrong rule: reverse the required sorted list.
                    key = {'fs_logs': 'users', 'fs_inventory': 'eligible_files', 'data_csv': 'customers', 'data_json': 'chosen'}[row['family']]
                    self.assertGreaterEqual(len(result[key]), 2)
                    result[key] = list(reversed(result[key]))
                    (workspace/'result.json').write_text(json.dumps(result))
                    self.assertEqual(grade(), 0, 'wrong sorting must fail')
                    result[key].reverse()
                    good = copy.deepcopy(result)
                    result[key].pop()
                    (workspace/'result.json').write_text(json.dumps(result))
                    self.assertEqual(grade(), 0, 'missing record must fail')
                    result = copy.deepcopy(good)
                    collection, number = {'fs_logs': ('users', 'total_latency_ms'),
                                          'fs_inventory': ('groups', 'total_bytes'),
                                          'data_csv': ('customers', 'total_cents'),
                                          'data_json': ('chosen', 'qty')}[row['family']]
                    result[collection][0][number] += 1
                    (workspace/'result.json').write_text(json.dumps(result))
                    self.assertEqual(grade(), 0, 'wrong numeric total must fail')
                    result = copy.deepcopy(good)
                    result[collection][0][number] = float(result[collection][0][number])
                    (workspace/'result.json').write_text(json.dumps(result))
                    self.assertEqual(grade(), 0, 'equal-valued float must fail integer contract')
                    (workspace/'result.json').write_text(json.dumps(good))
                    changed_input = next(path for path in (workspace/'input').rglob('*') if path.is_file())
                    changed_input.write_bytes(changed_input.read_bytes()+b'\n')
                    self.assertEqual(grade(), 0, 'modified visible input must fail')

    def test_answer_hardening_and_private_files(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            manifest = m.build_pool(root/'pool')
            for row in manifest['instances']:
                task = root/'pool'/row['path']
                for name, expected in row['file_sha256'].items():
                    self.assertEqual(hashlib.sha256((task/name).read_bytes()).hexdigest(), expected)
                self.assertFalse(list((task/'environment').rglob('expected*')))
                self.assertNotIn('tests', (task/'environment/Dockerfile').read_text())
                self.assertNotIn('solution', (task/'environment/Dockerfile').read_text())
            task = root/'pool'/manifest['instances'][0]['path']
            spec = importlib.util.spec_from_file_location('private_verifier', task/'tests/verify.py')
            grader = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(grader)
            self.assertFalse(grader.equal(True, 1))
            self.assertFalse(grader.equal(False, 0))
            self.assertFalse(grader.equal(1.0, 1))
            workspace = root/'workspace'
            shutil.copytree(task/'environment/input', workspace/'input')
            rewards = root/'rewards'
            def reward():
                result = subprocess.run([sys.executable, str(task/'tests/verify.py'), str(workspace), str(rewards)], timeout=5)
                self.assertEqual(result.returncode, 0, 'bad answers are normal reward=0, not verifier exceptions')
                return int((rewards/'reward.txt').read_text())
            answer = workspace/'result.json'
            answer.symlink_to(task/'tests/expected.json')
            self.assertEqual(reward(), 0)
            answer.unlink()
            answer.write_bytes(b' ' * (m.MAX_RESULT_BYTES+1))
            self.assertEqual(reward(), 0)
            answer.write_text('{"users": [], "users": []}')
            self.assertEqual(reward(), 0)

    def test_fixed_boundary_witnesses(self):
        m = self.module()
        for seed in range(41000, 41004):
            logs, expected = m._logs(seed)
            self.assertEqual(expected['kept_events'], 13)
            self.assertEqual([row['user'] for row in expected['users']], ['Ada', 'Ben', 'Cy'])
            inventory, expected = m._inventory(seed)
            self.assertIn('alpha/b.log', expected['eligible_files'])  # Exactly 64 bytes.
            self.assertNotIn('alpha/too-big.txt', expected['eligible_files'])
            self.assertNotIn('alpha/empty.txt', expected['eligible_files'])
            self.assertNotIn('alpha/upper.TXT', expected['eligible_files'])
            self.assertNotIn('.hidden/leak.txt', expected['eligible_files'])
            self.assertIn('gamma/only.txt', expected['eligible_files'])
            self.assertNotIn('gamma', [row['group'] for row in expected['groups']])
            orders, expected = m._csv(seed)
            boundary = next(row for row in expected['customers'] if row['customer'] == 'Boundary')
            self.assertEqual(boundary, dict(customer='Boundary', orders=4, total_cents=111))
            self.assertNotIn('Under', [row['customer'] for row in expected['customers']])
            catalog, expected = m._catalog(seed)
            chosen = {row['sku']: row for row in expected['chosen']}
            self.assertEqual(chosen['apple']['price_cents'], 0)
            self.assertEqual(chosen['tie-name']['warehouse'], 'alpha')
            self.assertEqual(chosen['tie-qty']['qty'], 2 if seed % 4 in (0, 1) else 4)
            self.assertNotIn('zero-qty', chosen)
            self.assertNotIn('ghost', chosen)

    def test_official_harbor_task_parser(self):
        m = self.module()
        from harbor.models.task.task import Task
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            manifest = m.build_pool(root/'pool')
            for row in manifest['instances']:
                task = Task(task_dir=root/'pool'/row['path'])
                self.assertTrue(task.is_valid_dir(root/'pool'/row['path']))
                self.assertEqual(task.config.verifier.timeout_sec, 30)
                self.assertEqual(task.config.agent.timeout_sec, 180)

    def test_json_threshold_has_below_and_exact_boundary_and_mutant_fails(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            manifest = m.build_pool(root/'pool')
            below, exact = 0, 0
            for row in manifest['instances']:
                if row['family'] != 'data_json':
                    continue
                task = root/'pool'/row['path']
                expected = json.loads((task/'tests/expected.json').read_text())['answer']
                catalog = json.loads((task/'environment/input/catalog.json').read_text())
                identities = {(warehouse['name'], item['sku']): item for warehouse in catalog['warehouses'] for item in warehouse['items']}
                summaries = {}
                for chosen in expected['chosen']:
                    item = identities[(chosen['warehouse'], chosen['sku'])]
                    summary = summaries.setdefault(item['category'], dict(category=item['category'], sku_count=0, total_qty=0, inventory_value_cents=0))
                    summary['sku_count'] += 1
                    summary['total_qty'] += item['qty']
                    summary['inventory_value_cents'] += item['qty']*item['price_cents']
                exact += sum(value['total_qty'] == 5 for value in summaries.values())
                low = [value for value in summaries.values() if value['total_qty'] < 5]
                below += len(low)
                if not low:
                    continue
                self.assertTrue(all(value['category'] not in {category['category'] for category in expected['categories']} for value in low))
                # Mutant: omit the category-total >=5 filter, keep other rules.
                mutant = dict(expected, categories=sorted(summaries.values(), key=lambda value: value['category']))
                workspace = root/'workspace'/row['instance']
                shutil.copytree(task/'environment/input', workspace/'input')
                (workspace/'result.json').write_text(json.dumps(mutant))
                reward = root/'rewards'/row['instance']
                subprocess.run([sys.executable, str(task/'tests/verify.py'), str(workspace), str(reward)], check=True, timeout=5)
                self.assertEqual((reward/'reward.txt').read_text().strip(), '0')
            self.assertGreaterEqual(below, 2, 'threshold-removal mutant must be observable')
            self.assertGreaterEqual(exact, 2, 'inclusive threshold boundary must be exercised')


if __name__ == '__main__':
    unittest.main()
