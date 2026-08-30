from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_broker_module():
    path = ROOT / "ops" / "rk-runner" / "local_hil_broker.py"
    spec = importlib.util.spec_from_file_location("local_hil_broker", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RKPhysicalBringupTests(unittest.TestCase):
    def test_rk_target_arch_is_not_runner_arch(self) -> None:
        data = json.loads((ROOT / "ci" / "hardware-profiles.json").read_text(encoding="utf-8"))
        rk = next(item for item in data["profiles"] if item["soc"] == "rk")
        self.assertEqual(rk["arch"], "arm64")
        self.assertEqual(rk["runner_arch"], "x64")
        self.assertEqual(rk["runner_labels"], ["self-hosted", "linux", "x64", "soc-rk"])
        self.assertIn("lsusb", rk["required_tools"])

    def test_runner_bootstrap_is_version_and_digest_pinned(self) -> None:
        text = (ROOT / "ops" / "rk-runner" / "bootstrap-runner.sh").read_text(encoding="utf-8")
        self.assertIn('RUNNER_VERSION="2.337.0"', text)
        self.assertIn("70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613", text)
        self.assertIn("sha256sum --check --strict", text)
        self.assertIn("do not register the physical Self-hosted Runner to the public central CICD repository", text)
        self.assertIn('"$(uname -m)" != "x86_64"', text)

    def test_private_caller_workflows_own_physical_runner_labels(self) -> None:
        enrollment = (ROOT / ".github" / "workflows" / "reusable-rk-enrollment.yml").read_text(encoding="utf-8")
        readiness = (ROOT / ".github" / "workflows" / "reusable-rk-physical-readiness.yml").read_text(encoding="utf-8")
        for workflow in (enrollment, readiness):
            self.assertIn("runs-on: [self-hosted, linux, x64, soc-rk]", workflow)
            self.assertIn("platform_ref", workflow)
            self.assertIn("refs/heads/main", workflow)
        self.assertFalse((ROOT / ".github" / "workflows" / "rk-sdk-enrollment.yml").exists())
        self.assertFalse((ROOT / ".github" / "workflows" / "hardware-readiness.yml").exists())

    def test_single_host_broker_leases_only_one_board_at_a_time(self) -> None:
        broker_module = load_broker_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = root / "inventory.json"
            state = root / "state.json"
            inventory.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "pools": {
                            "rk-linux-arm64": [
                                {"id": "board-01", "env": {"RK_HIL_BOARD": "board-01"}}
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            broker = broker_module.Broker(inventory, state, "x" * 32)
            first = broker.acquire(
                {
                    "kind": "hil",
                    "pool": "rk-linux-arm64",
                    "holder": "job-1",
                    "ttl_seconds": 300,
                    "metadata": {},
                }
            )
            self.assertEqual(first["env"]["CI_HIL_DEVICE_ID"], "board-01")
            with self.assertRaises(RuntimeError):
                broker.acquire(
                    {
                        "kind": "hil",
                        "pool": "rk-linux-arm64",
                        "holder": "job-2",
                        "ttl_seconds": 300,
                        "metadata": {},
                    }
                )
            broker.release(first["lease_id"])
            second = broker.acquire(
                {
                    "kind": "hil",
                    "pool": "rk-linux-arm64",
                    "holder": "job-2",
                    "ttl_seconds": 300,
                    "metadata": {},
                }
            )
            self.assertEqual(second["env"]["CI_HIL_DEVICE_ID"], "board-01")


if __name__ == "__main__":
    unittest.main()
