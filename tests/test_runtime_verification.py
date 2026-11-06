from __future__ import annotations
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
from verify_runtime_deployment import verify_runtime  # noqa: E402
from register_runtime_deployment import register_runtime  # noqa: E402

DIGEST = "sha256:" + "a" * 64
MANIFEST = "b" * 64
REVISION = "c" * 40
IMAGE = "ghcr.io/example/payments@" + DIGEST

def fixture():
    app = {"metadata": {"name": "payments"}, "status": {
        "sync": {"status": "Synced", "revision": REVISION},
        "health": {"status": "Healthy"}}}
    pods = {"items": [{
        "metadata": {"name": "payments-abc", "namespace": "production", "uid": "pod-uid"},
        "spec": {"nodeName": "worker-1", "containers": [{"name": "app", "image": IMAGE}]},
        "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                   "containerStatuses": [{"name": "app", "ready": True,
                     "imageID": "containerd://ghcr.io/example/payments@" + DIGEST}]}}]}
    return app, pods

class RuntimeVerificationTests(unittest.TestCase):
    def kwargs(self):
        return {"application": "payments", "namespace": "production",
                "selector": "app.kubernetes.io/instance=payments",
                "expected_image": IMAGE, "expected_revision": REVISION,
                "manifest_sha256": MANIFEST, "artifact_name": "payments-release",
                "bundle_sha256": "e" * 64, "source_sha": "f" * 40,
                "source_run_id": "45678", "target_environment": "production"}

    def test_exact_argo_revision_and_runtime_image_digest_pass(self):
        app, pods = fixture()
        result = verify_runtime(app, pods, **self.kwargs())
        self.assertEqual("Synced", result["argocd_sync"])
        self.assertEqual("Healthy", result["argocd_health"])
        self.assertEqual(DIGEST, result["image_digest"])
        self.assertEqual(1, result["pod_count"])
        self.assertEqual(64, len(result["evidence_sha256"]))

    def test_unsynced_or_unhealthy_application_fails(self):
        app, pods = fixture()
        app["status"]["sync"]["status"] = "OutOfSync"
        with self.assertRaisesRegex(ValueError, "not Synced"):
            verify_runtime(app, pods, **self.kwargs())
        app, pods = fixture()
        app["status"]["health"]["status"] = "Degraded"
        with self.assertRaisesRegex(ValueError, "not Healthy"):
            verify_runtime(app, pods, **self.kwargs())

    def test_wrong_gitops_revision_fails(self):
        app, pods = fixture()
        app["status"]["sync"]["revision"] = "d" * 40
        with self.assertRaisesRegex(ValueError, "revision mismatch"):
            verify_runtime(app, pods, **self.kwargs())

    def test_wrong_runtime_digest_fails_even_when_spec_image_matches(self):
        app, pods = fixture()
        pods["items"][0]["status"]["containerStatuses"][0]["imageID"] = (
            "containerd://ghcr.io/example/payments@sha256:" + "d" * 64)
        with self.assertRaisesRegex(ValueError, "runtime imageID does not match"):
            verify_runtime(app, pods, **self.kwargs())

    def test_tag_only_pod_spec_fails(self):
        app, pods = fixture()
        pods["items"][0]["spec"]["containers"][0]["image"] = "ghcr.io/example/payments:latest"
        with self.assertRaisesRegex(ValueError, "does not declare expected image"):
            verify_runtime(app, pods, **self.kwargs())

    def test_not_ready_pod_fails(self):
        app, pods = fixture()
        pods["items"][0]["status"]["conditions"][0]["status"] = "False"
        with self.assertRaisesRegex(ValueError, "not Ready"):
            verify_runtime(app, pods, **self.kwargs())

    def test_empty_pod_set_fails(self):
        app, pods = fixture()
        pods["items"] = []
        with self.assertRaisesRegex(ValueError, "no Pods"):
            verify_runtime(app, pods, **self.kwargs())

    def test_verified_runtime_record_is_registered(self):
        app, pods = fixture()
        evidence = verify_runtime(app, pods, **self.kwargs())
        calls = []
        import register_runtime_deployment as module
        original = module._request
        def fake_request(url, token, *, method="GET", payload=None):
            calls.append((url, method, payload))
            return {"id": 12345} if method == "POST" and url.endswith("/deployments") else {"state": "success"}
        module._request = fake_request
        try:
            result = register_runtime(api_url="https://api.github.com",
                repository="example/repo", token="token", workflow_sha="f" * 40, evidence=evidence)
        finally:
            module._request = original
        self.assertEqual("12345", result["deployment_id"])
        self.assertEqual(DIGEST, result["image_digest"])
        self.assertEqual(2, len(calls))
        self.assertEqual("runtime-production", calls[0][2]["environment"])

    def test_registry_refuses_tampered_evidence_hash(self):
        app, pods = fixture()
        evidence = verify_runtime(app, pods, **self.kwargs())
        evidence["verified_pods"][0]["images"][0]["image_id"] = "sha256:" + "d" * 64
        with self.assertRaisesRegex(ValueError, "evidence SHA256"):
            register_runtime(api_url="https://api.github.com", repository="example/repo",
                token="token", workflow_sha="f" * 40, evidence=evidence)

    def test_registry_refuses_unhealthy_evidence(self):
        app, pods = fixture()
        evidence = verify_runtime(app, pods, **self.kwargs())
        evidence["argocd_health"] = "Degraded"
        with self.assertRaisesRegex(ValueError, "argocd_health"):
            register_runtime(api_url="https://api.github.com", repository="example/repo",
                token="token", workflow_sha="f" * 40, evidence=evidence)

if __name__ == "__main__":
    unittest.main()
