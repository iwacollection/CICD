from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TrustedAttestationGateTests(unittest.TestCase):
    def test_attestation_uses_always_to_survive_empty_skipped_levels(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("name: Attest trusted DAG artifacts", workflow)
        self.assertIn(
            "if: always() && github.event_name != 'pull_request' && needs.discover.result == 'success' && needs.build_complete.result == 'success' && needs.discover.outputs.total_targets != '0'",
            workflow,
        )

    def test_build_gate_fails_closed_when_trusted_attestation_does_not_succeed(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn('ATTEST_RESULT: ${{ needs.attest.result }}', workflow)
        self.assertIn(
            'if [[ "$EVENT_NAME" != "pull_request" && "$TOTAL_TARGETS" != "0" && "$ATTEST_RESULT" != "success" ]]; then',
            workflow,
        )
        self.assertIn("Trusted DAG artifact attestation failed", workflow)


if __name__ == "__main__":
    unittest.main()
