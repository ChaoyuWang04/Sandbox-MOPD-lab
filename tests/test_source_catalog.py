import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lab_runtime import source_catalog as catalog


def row(**updates):
    return dict(instance_id='one', repo='org/repo', base_commit='abc',
                problem_statement='Fix issue', patch='diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n',
                FAIL_TO_PASS='["test_a"]', PASS_TO_PASS=['test_b'], extra={'keep': True}, **updates)


class CatalogTests(unittest.TestCase):
    def fixture(self, root):
        path = root / 'swe-smith/a.parquet'
        path.parent.mkdir()
        path.write_bytes(b'parquet-fixture')
        return {'assets': [dict(path='swe-smith/a.parquet', revision='fixed', size=path.stat().st_size,
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest())]}

    def test_batches_are_small_and_lazy(self):
        calls = []
        class Parquet:
            def iter_batches(self, batch_size):
                calls.append(batch_size)
                class Batch:
                    def to_pylist(self):
                        return [row()]
                yield Batch()
        stream = catalog.iter_source_rows('x', 'swe-smith', parquet_factory=lambda p: Parquet())
        self.assertEqual(calls, [])
        self.assertEqual(next(stream), row())
        self.assertEqual(calls, [16])

    def test_complete_original_and_compact_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manifest = self.fixture(root)
            with patch.object(catalog, 'iter_source_rows', return_value=iter([row()])):
                result = catalog.catalog_sources(root, manifest, root / 'catalog')
            self.assertEqual(result['rows'], 1)
            self.assertEqual(json.loads((root / 'catalog/swe-smith.jsonl').read_text()), row())
            index = json.loads((root / 'catalog/index.jsonl').read_text())
            self.assertEqual(index['changed_files'], ['src/a.py'])
            self.assertEqual(index['fail_to_pass_count'], 1)
            self.assertEqual(index['source_revision'], 'fixed')
            self.assertEqual(index['patch_sha256'], hashlib.sha256(row()['patch'].encode()).hexdigest())
            with self.assertRaises(ValueError):
                catalog.catalog_sources(root, manifest, root / 'catalog')

    def test_checksum_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manifest = self.fixture(root)
            manifest['assets'][0]['sha256'] = '0' * 64
            with patch.object(catalog, 'iter_source_rows', side_effect=AssertionError('read before verify')):
                with self.assertRaises(ValueError):
                    catalog.catalog_sources(root, manifest, root / 'catalog')
            self.assertFalse((root / 'catalog').exists())

    def test_hunk_content_is_not_a_file_header(self):
        self.assertEqual(catalog._changed_files('diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n--- text\n+++ text\n'), ['a'])

    def test_invalid_patch_without_file_headers(self):
        with self.assertRaises(ValueError):
            catalog._changed_files('this is not a patch')

    def test_unknown_validation_error_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manifest = self.fixture(root)
            with patch.object(catalog, 'iter_source_rows', return_value=iter([row()])), patch.object(catalog, '_index', side_effect=ValueError('programming_error')):
                with self.assertRaisesRegex(ValueError, 'programming_error'):
                    catalog.catalog_sources(root, manifest, root / 'catalog')
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['swe-smith'])

    def test_unclosed_quoted_diff_header_has_fixed_reason(self):
        with self.assertRaisesRegex(ValueError, '^invalid_diff_header$'):
            catalog._changed_files('diff --git "a/a b/b')

    def test_git_paths_with_spaces_and_extended_headers(self):
        patch_text = ('diff --git a/old name.py b/new name.py\n'
                      'similarity index 100%\nrename from old name.py\nrename to new name.py\n'
                      'diff --git a/new name.py b/copy name.py\n'
                      'similarity index 100%\ncopy from new name.py\ncopy to copy name.py\n')
        self.assertEqual(catalog._changed_files(patch_text), ['copy name.py', 'new name.py', 'old name.py'])
        self.assertEqual(catalog._changed_files('diff --git a/a file.py b/a file.py\n--- a/a file.py\t\n+++ b/a file.py\t\n@@ -1 +1 @@\n-a\n+b\n'), ['a file.py'])

    def test_full_gym_requires_commit(self):
        spec = dict(path='swe-gym/a.parquet', revision='r', sha256='h')
        self.assertEqual(catalog._index(row(), spec, 1)['base_commit'], 'abc')
        bad = row()
        del bad['base_commit']
        with self.assertRaises(ValueError):
            catalog._index(bad, spec, 1)

    def test_smith_official_shape_has_image_not_commit(self):
        original = row()
        del original['base_commit']
        original['image_name'] = 'swesmith/image:fixed'
        result = catalog._index(original, dict(path='swe-smith/a.parquet', revision='r', sha256='h'), 1)
        self.assertIsNone(result['base_commit'])
        self.assertEqual(result['image_name'], original['image_name'])
        with self.assertRaises(ValueError):
            catalog._index(original, dict(path='swe-gym-lite/a.parquet', revision='r', sha256='h'), 1)

    def test_bad_rows_and_budget_leave_no_output(self):
        invalid = [None, {}, dict(row(), FAIL_TO_PASS=None), dict(row(), PASS_TO_PASS='{}'),
                   dict(row(), patch='diff --git a/../secret b/../secret\n'),
                   dict(row(), patch='--- /etc/passwd\n+++ b/a\n')]
        for bad in invalid:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                manifest = self.fixture(root)
                with patch.object(catalog, 'iter_source_rows', return_value=iter([bad])):
                    result = catalog.catalog_sources(root, manifest, root / 'catalog')
                self.assertEqual(result['rejected'], 1)
                self.assertEqual(result['accepted'], 0)
        self.assert_failed([row(), row()])
        with patch.object(catalog, 'MAX_OUTPUT_BYTES', 10):
            self.assert_failed([row()])

    def assert_failed(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manifest = self.fixture(root)
            with patch.object(catalog, 'iter_source_rows', return_value=iter(rows)):
                with self.assertRaises(ValueError):
                    catalog.catalog_sources(root, manifest, root / 'catalog')
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['swe-smith'])

    def test_quarantine_retains_raw_and_tracks_rows_across_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manifest = self.fixture(root)
            second = dict(manifest['assets'][0], path='swe-smith/b.parquet')
            (root / second['path']).write_bytes(b'parquet-fixture')
            manifest['assets'].append(second)
            bad = dict(row(), problem_statement='')
            good = dict(row(), instance_id='two')
            with patch.object(catalog, 'iter_source_rows', side_effect=[iter([bad]), iter([good])]):
                result = catalog.catalog_sources(root, manifest, root / 'catalog')
            self.assertEqual((result['rows'], result['accepted'], result['rejected']), (2, 1, 1))
            self.assertEqual([json.loads(x) for x in (root / 'catalog/swe-smith.jsonl').read_text().splitlines()], [bad, good])
            rejection = json.loads((root / 'catalog/rejected.jsonl').read_text())
            self.assertEqual(rejection['reason'], 'invalid_problem_statement')
            self.assertEqual((rejection['source_line'], rejection['asset_row']), (1, 1))
            accepted = json.loads((root / 'catalog/index.jsonl').read_text())
            self.assertEqual((accepted['source_line'], accepted['asset_row']), (2, 1))
        self.assert_failed([dict(row(), problem_statement=''), row()])
