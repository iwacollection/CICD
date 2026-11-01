from __future__ import annotations

import copy
import sys
import unittest
from unittest.mock import patch

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from release_state_store import load_state, publish_state, _state_digest  # noqa: E402


class ReleaseStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {
            "schema_version": 1,
            "environment": "production",
            "service": "checkout",
            "status": "planned",
            "candidate_digest": "b" * 64,
            "baseline_digest": "a" * 64,
            "waves": [],
            "history": [{"action": "plan"}],
        }
        self.deployment = {"id": 501}
        self.payload = {
            "kind": "progressive-release-state",
            "environment": "production",
            "service": "checkout",
            "revision": 1,
            "parent_deployment_id": "",
            "state_sha256": _state_digest(self.state),
            "state": copy.deepcopy(self.state),
        }

    @patch("release_state_store._statuses", return_value=[{"state": "success"}])
    @patch("release_state_store._list_all_pages")
    def test_load_restores_latest_verified_checkpoint(self, pages, statuses) -> None:
        pages.return_value = [{**self.deployment, "payload": self.payload}]
        state = load_state(
            api_url="https://api.github.com", repository="o/r", token="t",
            environment="production", service="checkout", candidate_digest="b" * 64,
        )
        self.assertEqual(state["status"], "planned")
        self.assertEqual(state["_store"]["deployment_id"], "501")
        statuses.assert_called_once()

    @patch("release_state_store._statuses", return_value=[{"state": "success"}])
    @patch("release_state_store._list_all_pages")
    def test_load_rejects_modified_checkpoint_state(self, pages, statuses) -> None:
        altered = copy.deepcopy(self.payload)
        altered["state"]["candidate_digest"] = "c" * 64
        pages.return_value = [{**self.deployment, "payload": altered}]
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            load_state(
                api_url="https://api.github.com", repository="o/r", token="t",
                environment="production", service="checkout", candidate_digest="b" * 64,
            )

    @patch("release_state_store._request")
    @patch("release_state_store._statuses", return_value=[{"state": "success"}])
    @patch("release_state_store._list_all_pages")
    def test_publish_advance_rejects_replayed_older_revision(self, pages, statuses, request) -> None:
        pages.return_value = [{**self.deployment, "payload": self.payload}]
        stale = {**self.state, "_store": {
            "deployment_id": "499", "revision": 1,
            "state_sha256": self.payload["state_sha256"],
        }}
        with self.assertRaisesRegex(ValueError, "stale release state"):
            publish_state(
                api_url="https://api.github.com", repository="o/r", token="t",
                environment="production", service="checkout", action="advance", state=stale,
            )
        request.assert_not_called()

    @patch("release_state_store._request")
    @patch("release_state_store._statuses", return_value=[{"state": "success"}])
    @patch("release_state_store._list_all_pages")
    def test_publish_advance_creates_child_revision(self, pages, statuses, request) -> None:
        pages.return_value = [{**self.deployment, "payload": self.payload}]
        request.side_effect = [{"id": 502}, {"id": 900}]
        loaded = {
            **self.state,
            "_store": {
                "deployment_id": "501", "revision": 1,
                "state_sha256": self.payload["state_sha256"],
                "parent_deployment_id": "", "environment": "production",
                "service": "checkout", "candidate_digest": "b" * 64,
            },
        }
        changed = {**loaded, "status": "in_progress", "history": [{"action": "plan"}, {"action": "advance"}]}
        result = publish_state(
            api_url="https://api.github.com", repository="o/r", token="t",
            environment="production", service="checkout", action="advance", state=changed,
        )
        self.assertEqual(result["_store"]["deployment_id"], "502")
        self.assertEqual(result["_store"]["revision"], 2)
        self.assertEqual(request.call_args_list[0].kwargs["payload"]["payload"]["parent_deployment_id"], "501")

    @patch("release_state_store._request")
    @patch("release_state_store._statuses", return_value=[{"state": "success"}])
    @patch("release_state_store._list_all_pages")
    def test_new_plan_rejected_while_previous_release_active(self, pages, statuses, request) -> None:
        pages.return_value = [{**self.deployment, "payload": self.payload}]
        with self.assertRaisesRegex(ValueError, "active release already exists"):
            publish_state(
                api_url="https://api.github.com", repository="o/r", token="t",
                environment="production", service="checkout", action="plan", state=self.state,
            )
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
