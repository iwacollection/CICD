from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from dependency_plan import build_levels  # noqa: E402
from discover_matrix import build_matrix  # noqa: E402
from validate_config import load_catalog, validate_catalog  # noqa: E402


class CiPlatformTests(unittest.TestCase):
    def test_catalog_is_valid(self) -> None:
        data = load_catalog(ROOT / "ci" / "projects.json")
        self.assertEqual(validate_catalog(data), [])

    def test_disabled_soc_template_not_in_matrix(self) -> None:
        data = load_catalog(ROOT / "ci" / "projects.json")
        matrix = build_matrix(data)
        self.assertEqual([item["project"] for item in matrix["include"]], ["hello-cpp"])
        self.assertEqual(json.loads(matrix["include"][0]["runner_labels"]), ["ubuntu-latest"])

    def test_dependency_levels_allow_parallel_roots(self) -> None:
        data = {
            "projects": [
                {"name": "lib-a", "enabled": True, "depends_on": []},
                {"name": "lib-b", "enabled": True, "depends_on": []},
                {"name": "app", "enabled": True, "depends_on": ["lib-a", "lib-b"]},
            ]
        }
        self.assertEqual(build_levels(data), [["lib-a", "lib-b"], ["app"]])

    def test_dependency_cycle_is_rejected(self) -> None:
        data = {
            "projects": [
                {"name": "a", "enabled": True, "depends_on": ["b"]},
                {"name": "b", "enabled": True, "depends_on": ["a"]},
            ]
        }
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            build_levels(data)


if __name__ == "__main__":
    unittest.main()
