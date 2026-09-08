"""Synthetic metadata only: these fixtures are not selected or runnable tasks."""
import copy
import json
from pathlib import Path

import importlib.util
import unittest


def fixture():
    legacy = json.loads((Path(__file__).parents[1] / 'configs/m1-pool-manifest.json').read_text())
    records = []
    quotas = {'self': {'A': 24, 'B': 24}, 'swe-smith': {'A': 26, 'B': 26},
              'swe-gym': {'A': 20, 'B': 20, 'combo': 10},
              'terminal-bench': {'A': 10, 'B': 10, 'combo': 10, 'OOD': 20}}
    for source, skills in quotas.items():
        for skill, n in skills.items():
            for i in range(n):
                ident = f'synthetic:{source}:{skill}:{i}'
                records.append(dict(id=ident, source=source, source_id=ident,
                    source_revision='synthetic-v1', source_sha256='a'*64, license='MIT',
                    primary_skill=skill, secondary_skills=[], mechanism='synthetic test',
                    generation_method='synthetic fixture only', repo=None if source in ('self', 'terminal-bench') else 'org/repo',
                    function_groups=[], leakage_group=ident, split='final',
                    teacher_route={'A':'A','B':'B','combo':'both','OOD':'none'}[skill],
                    classification_reason='synthetic fixture', resource_profile=dict(cpu=1, ram_mb=1024, gpu=0, network=False, timeout_sec=30),
                    task_files_sha256={'instruction.md':'b'*64}, frozen_legacy=False, legacy_eval=False))
    for skill, families in [('A', ('fs_logs', 'data_csv', 'data_json')), ('B', ('fs_inventory',))]:
        targets = [r for r in records if r['source']=='self' and r['primary_skill']==skill]
        for record, old in zip(targets, [x for x in legacy['instances'] if x['family'] in families]):
            record.update(id=old['instance'], source_id=old['instance'],
                task_files_sha256=copy.deepcopy(old['file_sha256']), frozen_legacy=True,
                legacy_eval=old['split']=='eval', leakage_group=old['family'])
        eligible = [r for r in records if r['primary_skill']==skill and r['source']!='terminal-bench' and not r['legacy_eval']]
        for i, record in enumerate(eligible[:50]):
            record['split'] = 'train' if i < 40 else 'dev'
    return records, legacy


def validate_manifest(*args):
    assert importlib.util.find_spec('lab_runtime.pool_manifest_v2'), 'manifest validator missing'
    from lab_runtime.pool_manifest_v2 import validate_manifest
    return validate_manifest(*args)


MUTATIONS = [
    lambda r: r.append(copy.deepcopy(r[0])),
    lambda r: r[0].pop('license'),
    lambda r: r[0].update(source_sha256='x'*64),
    lambda r: r[0].update(source='swe-smith'),
    lambda r: r[0].update(teacher_route='none'),
    lambda r: r[0]['resource_profile'].update(gpu=1),
    lambda r: r[0]['resource_profile'].update(cpu=True),
    lambda r: r[0]['task_files_sha256'].update({'instruction.md':'c'*64}),
    lambda r: r[0].update(frozen_legacy=False),
    lambda r: next(x for x in r if x['legacy_eval']).update(split='train'),
    lambda r: next(x for x in r if not x['frozen_legacy']).update(legacy_eval=True),
    lambda r: next(x for x in r if not x['frozen_legacy']).update(leakage_group='fs_logs'),
    lambda r: r[0].update(function_groups=['canonical:function']),
    lambda r: next(x for x in r if x['source']=='swe-smith').update(source='swe-gym'),
    lambda r: next(x for x in r if x['split']=='dev').update(split='train'),
    lambda r: next(x for x in r if x['source']=='terminal-bench').update(split='dev'),
    lambda r: next(x for x in r if x['primary_skill']=='combo').update(split='train'),
    lambda r: next(x for x in r if x['primary_skill']=='OOD').update(teacher_route='both'),
    lambda r: r[0].update(task_files_sha256={}),
    lambda r: r[0].update(secondary_skills='A'),
    lambda r: next(x for x in r if x['source']=='swe-smith').update(repo=None),
    lambda r: r[0]['resource_profile'].update(timeout_sec=float('inf')),
]


class ManifestTest(unittest.TestCase):
    def test_valid_metadata_only(self):
        result = validate_manifest(*fixture())
        self.assertTrue(result['metadata_validated'])
        self.assertNotIn('ready', result)
        self.assertEqual(result['total'], 200)
        self.assertEqual(result['split_skill']['final'], {'A':30,'B':30,'combo':20,'OOD':20})

    def test_rejects_contract_violations(self):
        for i, mutation in enumerate(MUTATIONS):
            with self.subTest(mutation=i):
                records, legacy = fixture()
                next(x for x in records if x['split']=='dev')['function_groups'] = ['canonical:function']
                mutation(records)
                with self.assertRaises(ValueError):
                    validate_manifest(records, legacy)

    def test_legacy_reference_must_be_complete(self):
        records, legacy = fixture()
        legacy['instances'].pop()
        with self.assertRaises(ValueError):
            validate_manifest(records, legacy)

    def test_every_required_field_is_checked(self):
        records, legacy = fixture()
        for field in records[0]:
            with self.subTest(field=field):
                candidate = copy.deepcopy(records)
                candidate[0].pop(field)
                with self.assertRaises(ValueError):
                    validate_manifest(candidate, legacy)

    def test_validation_does_not_mutate_inputs(self):
        records, legacy = fixture()
        before = copy.deepcopy((records, legacy))
        validate_manifest(records, legacy)
        self.assertEqual((records, legacy), before)

    def test_duplicate_upstream_identity_with_distinct_ids_and_groups(self):
        records, legacy = fixture()
        candidates = [r for r in records if r['source'] == 'swe-smith']
        candidates[1]['source_id'] = candidates[0]['source_id']
        with self.assertRaisesRegex(ValueError, 'source identity'):
            validate_manifest(records, legacy)

    def test_self_mapping_cannot_be_swapped_even_with_quotas_preserved(self):
        records, legacy = fixture()
        a = next(r for r in records if r['source'] == 'self' and r['primary_skill'] == 'A' and r['split'] == 'train')
        b = next(r for r in records if r['source'] == 'self' and not r['frozen_legacy'] and r['split'] == 'train')
        a.update(primary_skill='B', teacher_route='B')
        b.update(primary_skill='A', teacher_route='A')
        with self.assertRaisesRegex(ValueError, 'self skill'):
            validate_manifest(records, legacy)
