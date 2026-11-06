from __future__ import annotations
import json
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/ci'))
from toolchain_versions import build_matrix, resolve_version, validate_versions
from toolchain_catalog import load_toolchain_catalog

class ToolchainVersionRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((ROOT / 'ci/toolchain-versions.json').read_text(encoding='utf-8'))
        cls.catalog = load_toolchain_catalog(ROOT / 'ci/toolchains.json')
    def test_registry_validates(self):
        self.assertEqual([], validate_versions(self.registry, self.catalog))
    def test_matrix_contains_only_active_and_candidate(self):
        matrix = build_matrix(self.registry, self.catalog)
        self.assertEqual({'java-temurin-17-maven-3.10', 'go-1.26.10', 'cpp-gcc-13'}, {x['combination_id'] for x in matrix['include']})
        self.assertTrue(all(x['status'] in {'active', 'candidate'} for x in matrix['include']))
    def test_project_resolution_rejects_candidate(self):
        with self.assertRaisesRegex(ValueError, 'allowed statuses'):
            resolve_version(self.registry, self.catalog, 'java', '17.0.21+9', 'maven', '3.10.0')
    def test_verification_can_resolve_candidate_digest(self):
        result = resolve_version(self.registry, self.catalog, 'java', '17.0.21+9', 'maven', '3.10.0', True)
        self.assertEqual('java17-maven', result['toolchain_id'])
        self.assertIn('@sha256:', result['immutable_reference'])
    def test_planned_combination_is_not_runnable(self):
        with self.assertRaisesRegex(ValueError, 'is planned'):
            resolve_version(self.registry, self.catalog, 'go', '1.25.14', 'go', '1.25.14', True)
    def test_retired_combination_is_not_runnable(self):
        with self.assertRaisesRegex(ValueError, 'is retired'):
            resolve_version(self.registry, self.catalog, 'go', '1.25.14', 'go', '1.25.14', True)
    def test_unknown_patch_version_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'matches=0'):
            resolve_version(self.registry, self.catalog, 'go', '1.26', 'go', '1.26', True)
    def test_duplicate_combination_id_is_rejected(self):
        broken = json.loads(json.dumps(self.registry)); broken['combinations'].append(dict(broken['combinations'][0]))
        self.assertTrue(any('duplicate version combination id' in e for e in validate_versions(broken, self.catalog)))

if __name__ == '__main__': unittest.main()
