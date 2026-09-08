"""Local self48 contract, including real repaired-code execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class SelfPoolV2Test(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('lab_runtime.task_pool_v2'))
        from lab_runtime import task_pool_v2
        return task_pool_v2

    def test_determinism_and_original_bytes(self):
        m = self.module()
        from lab_runtime.task_pool import build_pool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            old = build_pool(root/'old')
            result = m.materialize_self_pool(root/'new')
            self.assertEqual(result, m.materialize_self_pool(root/'again'))
            self.assertEqual(len(result['instances']), 48)
            for row in old['instances']:
                new = next(r for r in result['instances'] if r['instance'] == row['instance'])
                self.assertEqual(new['split'], row['split'])
                self.assertEqual(new['file_sha256'], row['file_sha256'])
                for name in row['file_sha256']:
                    self.assertEqual((root/'old'/row['path']/name).read_bytes(),
                                     (root/'new'/new['path']/name).read_bytes())
            frozen_manifest = json.loads((Path(__file__).parents[1]/'configs/m1-pool-manifest.json').read_text())
            self.assertEqual(old['instances'], frozen_manifest['instances'])
            added = result['instances'][32:]
            self.assertEqual(len({r['family'] for r in added}), 4)
            self.assertTrue(all(r['domain'] == 'B' and r['provisional_split'] == 'train' for r in added))
            for row in result['instances']:
                for name, digest in row['file_sha256'].items():
                    self.assertEqual(hashlib.sha256((root/'new'/row['path']/name).read_bytes()).hexdigest(), digest)
            with self.assertRaises(ValueError):
                m.materialize_self_pool(root/'new')
            (root/'link').symlink_to(root/'absent', target_is_directory=True)
            with self.assertRaises(ValueError):
                m.materialize_self_pool(root/'link')

    def test_each_new_instance_oracle_nop_and_wrong_repair(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            manifest = m.materialize_self_pool(root/'pool')
            for row in manifest['instances'][32:]:
                with self.subTest(instance=row['instance']):
                    task = root/'pool'/row['path']
                    workspace = root/row['instance']
                    shutil.copytree(task/'environment/input', workspace/'input')
                    shutil.copytree(task/'environment/work', workspace/'work')
                    rewards = workspace/'rewards'
                    def grade():
                        subprocess.run([sys.executable, str(task/'tests/verify.py'), str(workspace), str(rewards)], check=True, timeout=10)
                        return (rewards/'reward.txt').read_text().strip()
                    self.assertEqual(grade(), '0', 'untouched defective implementation must fail')
                    subprocess.run([sys.executable, str(task/'solution/oracle.py'), str(workspace)], check=True, timeout=5)
                    self.assertEqual(grade(), '1', 'independent repair must pass behavioral checks')
                    fixed = (workspace/'work/target.py').read_text()
                    (workspace/'work/target.py').unlink()
                    (workspace/'work/target.py').symlink_to(task/'solution/oracle.py')
                    self.assertEqual(grade(), '0', 'symlink implementation must fail')
                    (workspace/'work/target.py').unlink()
                    (workspace/'work/target.py').write_text(m.WRONG_REPAIRS[row['family']])
                    self.assertEqual(grade(), '0', 'plausible incomplete repair must fail')
                    (workspace/'work/target.py').write_text(fixed)
                    (workspace/'input/spec.json').write_text('{}')
                    self.assertEqual(grade(), '0', 'input modification must fail')

    def test_harbor_and_private_separation(self):
        from harbor.models.task.task import Task
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            manifest = m.materialize_self_pool(root/'pool')
            for row in manifest['instances'][32:]:
                task = root/'pool'/row['path']
                self.assertTrue(Task.is_valid_dir(task))
                self.assertEqual(Task(task_dir=task).config.environment.gpus, 0)
                self.assertFalse(list((task/'environment').rglob('*oracle*')))
                self.assertFalse(list((task/'environment').rglob('*check*')))


if __name__ == '__main__':
    unittest.main()
