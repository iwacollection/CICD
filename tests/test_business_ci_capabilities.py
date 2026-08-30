from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from capability_policy import load_json as load_capabilities, validate_policy as validate_capabilities  # noqa: E402
from db_migration_policy import load_json as load_db_policy, scan_text, validate_policy as validate_db_policy  # noqa: E402


class BusinessCapabilityPolicyTests(unittest.TestCase):
    def test_capability_catalog_is_valid_and_workflows_exist(self) -> None:
        policy = load_capabilities(ROOT / "ci" / "capabilities.json")
        self.assertEqual(validate_capabilities(policy, ROOT), [])
        self.assertEqual(
            set(policy["capabilities"]),
            {"quality", "test", "container", "db_migration"},
        )

    def test_capabilities_cannot_take_core_release_or_runner_ownership(self) -> None:
        policy = load_capabilities(ROOT / "ci" / "capabilities.json")
        for capability in policy["capabilities"].values():
            self.assertFalse(capability["may_publish_release"])
            self.assertFalse(capability["may_deploy"])
            self.assertFalse(capability["may_use_self_hosted"])
            self.assertEqual(capability["runner_class"], "hosted-only")

    def test_profiles_reference_only_known_capabilities(self) -> None:
        policy = load_capabilities(ROOT / "ci" / "capabilities.json")
        known = set(policy["capabilities"])
        for enabled in policy["profiles"].values():
            self.assertTrue(set(enabled) <= known)

    def test_reusable_capabilities_keep_least_privilege_and_no_publish_path(self) -> None:
        for name in (
            "reusable-quality.yml",
            "reusable-test.yml",
            "reusable-container.yml",
            "reusable-db-migration.yml",
        ):
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn("permissions:\n  contents: read", text, name)
            self.assertIn("runs-on: ubuntu-latest", text, name)
            self.assertNotIn("self-hosted", text, name)
            self.assertNotIn("id-token: write", text, name)
            self.assertNotIn("attestations: write", text, name)
            self.assertNotIn("packages: write", text, name)
            self.assertNotIn("deployments: write", text, name)
            self.assertNotIn("gh release", text, name)
            self.assertNotIn("docker push", text, name)
            self.assertNotIn("promote.yml", text, name)
            self.assertNotIn("rollback.yml", text, name)

    def test_container_reuses_central_supply_chain_policy_without_publishing(self) -> None:
        text = (ROOT / ".github" / "workflows" / "reusable-container.yml").read_text(encoding="utf-8")
        self.assertIn("Validate Dockerfile policy", text)
        self.assertIn("scan-type: image", text)
        self.assertIn("image-sbom.cdx.json", text)
        self.assertIn("supply_chain_policy.py", text)
        self.assertIn("Enforce non-root runtime user", text)
        self.assertNotIn("docker login", text)

    def test_db_capability_uses_pinned_ephemeral_postgres_and_no_production_secret(self) -> None:
        text = (ROOT / ".github" / "workflows" / "reusable-db-migration.yml").read_text(encoding="utf-8")
        self.assertIn("postgres@sha256:", text)
        self.assertIn("ci_test", text)
        self.assertIn("db_migration_policy.py", text)
        self.assertIn("schema-after-migration.sql", text)
        self.assertNotIn("secrets:", text)
        self.assertNotIn("production", text.lower())


class DatabaseMigrationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_db_policy(ROOT / "ci" / "db-migration-policy.json")
        self.assertEqual(validate_db_policy(self.policy), [])

    def test_safe_migration_passes(self) -> None:
        sql = "CREATE TABLE users (id bigint primary key);\nUPDATE users SET id = id WHERE id = 1;\n"
        self.assertEqual(scan_text(sql, self.policy, "safe.sql"), [])

    def test_destructive_sql_is_rejected(self) -> None:
        cases = {
            "DROP DATABASE prod;": "drop-database",
            "DROP SCHEMA public;": "drop-schema",
            "TRUNCATE TABLE users;": "truncate",
            "DELETE FROM users;": "unconditional-delete",
            "UPDATE users SET name = 'x';": "unconditional-update",
        }
        for sql, rule_id in cases.items():
            with self.subTest(sql=sql):
                findings = scan_text(sql, self.policy, "bad.sql")
                self.assertTrue(any(rule_id in finding for finding in findings), findings)

    def test_comments_do_not_trigger_policy(self) -> None:
        sql = "-- DROP DATABASE prod;\n/* TRUNCATE users; */\nCREATE TABLE ok(id int);"
        self.assertEqual(scan_text(sql, self.policy, "comments.sql"), [])


if __name__ == "__main__":
    unittest.main()
