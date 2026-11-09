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
        self.assertEqual({'java-temurin-17-maven-3.10', 'java-temurin-21-maven-3.10', 'go-1.26.10', 'cpp-gcc-13', 'cpp-gcc-12', 'cpp-gcc13-conan234'}, {x['combination_id'] for x in matrix['include']})
        self.assertTrue(all(x['status'] in {'active', 'candidate'} for x in matrix['include']))
    def test_project_resolution_rejects_candidate(self):
        with self.assertRaisesRegex(ValueError, 'allowed statuses'):
            resolve_version(self.registry, self.catalog, 'java', '17.0.21+9', 'maven', '3.10.0')
    def test_verification_can_resolve_candidate_digest(self):
        result = resolve_version(self.registry, self.catalog, 'java', '17.0.21+9', 'maven', '3.10.0', True)
        self.assertEqual('java17-maven', result['toolchain_id'])
        self.assertEqual('', result['immutable_reference'])
        self.assertTrue(result['verification_requires_image_build'])
    def test_planned_combination_is_not_runnable(self):
        planned_registry = json.loads(json.dumps(self.registry))
        planned_catalog = json.loads(json.dumps(self.catalog))
        combination = next(x for x in planned_registry['combinations'] if x['id'] == 'java-temurin-21-maven-3.10')
        combination['status'] = 'planned'
        toolchain = next(x for x in planned_catalog['toolchains'] if x['id'] == 'java21-maven')
        toolchain['status'] = 'planned'
        with self.assertRaisesRegex(ValueError, 'is planned'):
            resolve_version(planned_registry, planned_catalog, 'java', '21.0.12.1+1', 'maven', '3.10.0', True)
    def test_retired_combination_is_not_runnable(self):
        with self.assertRaisesRegex(ValueError, 'is retired'):
            resolve_version(self.registry, self.catalog, 'go', '1.25.14', 'go', '1.25.14', True)
    def test_unknown_patch_version_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'matches=0'):
            resolve_version(self.registry, self.catalog, 'go', '1.26', 'go', '1.26', True)
    def test_duplicate_combination_id_is_rejected(self):
        broken = json.loads(json.dumps(self.registry)); broken['combinations'].append(dict(broken['combinations'][0]))
        self.assertTrue(any('duplicate version combination id' in e for e in validate_versions(broken, self.catalog)))

    def test_planned_toolchains_have_executable_consumer_fixtures(self):
        from toolchain_catalog import build_publish_matrix
        matrix = build_publish_matrix(self.catalog, ROOT, include_planned=True)
        by_id = {row["toolchain"]: row for row in matrix["include"]}
        self.assertEqual("examples/java17-maven-service", by_id["java17-maven"]["consumer_fixture"])
        self.assertEqual("17.0.21+9", by_id["java17-maven"]["jdk_version"])
        self.assertEqual("examples/go-toolchain-consumer", by_id["go126"]["verification_fixture"])
        self.assertIn("go test ./...", by_id["go126"]["verification_command"])
        self.assertEqual("examples/java21-maven-service", by_id["java21-maven"]["verification_fixture"])
        self.assertEqual("examples/cpp-gcc12-service", by_id["gcc12"]["verification_fixture"])


if __name__ == '__main__': unittest.main()
