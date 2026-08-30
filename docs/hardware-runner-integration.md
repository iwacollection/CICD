# RK / Qualcomm / MediaTek real hardware CI integration

This phase moves the platform from a generic DAG into a controlled hardware execution plane. The DAG remains unchanged: a node either runs on a normal hosted/container toolchain or resolves its toolchain to a centrally managed hardware profile.

## Trust boundary

Pull requests never execute vendor code on privileged self-hosted Runners. A target whose Runner labels contain `self-hosted` is forced onto `ubuntu-latest` for PR validation and may run only its `pr_validation_command`. Vendor SDK, license and HIL access are reserved for `main` push or manual dispatch from `main`.

## Central ownership

Three catalogs own different concerns:

- `ci/projects.json`: product dependency graph, target, artifact paths and hosted-safe PR validation.
- `ci/toolchains.json`: toolchain lifecycle and immutable identity. Hardware SDK toolchains use `execution_mode=host`, `hardware_profile=<id>` and an immutable `host_identity` once active.
- `ci/hardware-profiles.json`: Runner labels, SDK root/identity, required host tools, license pool, HIL pool and the vendor adapters.

A project cannot override the hardware profile. The toolchain binding is the source of truth.

## SDK identity

Each dedicated Runner exposes the vendor SDK root through the profile-specific environment variable (`RK_SDK_ROOT`, `QCOM_SDK_ROOT`, or `MTK_SDK_ROOT`). Inside that SDK root the profile requires `.ci/sdk-identity.json`.

The file should describe the installed SDK/BSP release, vendor package/build identifiers and locally applied patches. The platform hashes the file as `sha256:<hex>` and compares it with both:

1. `ci/hardware-profiles.json -> sdk.expected_sha256`
2. `ci/toolchains.json -> host_identity`

Do not mark the profile/toolchain active until the real Runner has been prepared and the same digest is committed to both catalogs.

Example operator command on the Runner:

```bash
sha256sum "$RK_SDK_ROOT/.ci/sdk-identity.json"
```

Store only the digest in Git. Do not store proprietary SDK content in this repository.

## License pool

Qualcomm and MediaTek profiles require a centrally coordinated license lease. Rockchip is currently modeled without a required license seat, but the same broker can be enabled later.

The Runner service supplies, outside Git:

```text
CI_RESOURCE_BROKER_URL
CI_RESOURCE_BROKER_TOKEN
```

The client uses this protocol:

```text
POST /v1/leases
Authorization: Bearer <token>

{
  "kind": "license" | "hil",
  "pool": "<configured pool>",
  "holder": "<repo:run:job:profile:phase>",
  "ttl_seconds": 1800,
  "metadata": { ... }
}
```

Expected response:

```json
{
  "lease_id": "lease-123",
  "env": {
    "LM_LICENSE_FILE": "..."
  }
}
```

The broker may return temporary environment variables required by the vendor tool. They are injected only into the build/test process and are never printed or packaged. The client always calls `DELETE /v1/leases/<lease_id>` in a `finally` path. A release failure makes an otherwise successful phase fail closed.

## HIL device lease

The HIL pool uses the same broker. The broker must return at least:

```text
CI_HIL_DEVICE_ID=<leased device identity>
```

It may also return transport-specific values such as serial number, USB path, lab endpoint or power-controller address. The vendor HIL adapter receives those variables and delegates to the product repository's test recipe.

## Vendor adapters and product recipes

The platform owns stable adapters:

```text
scripts/vendor/rk/build.sh
scripts/vendor/rk/hil-test.sh
scripts/vendor/qcom/build.sh
scripts/vendor/qcom/hil-test.sh
scripts/vendor/mtk/build.sh
scripts/vendor/mtk/hil-test.sh
```

The actual product repository owns the vendor-specific commands because board/product names differ between BSPs:

```text
ci/vendor-rk-build.sh
ci/vendor-rk-hil-test.sh
ci/vendor-qcom-build.sh
ci/vendor-qcom-hil-test.sh
ci/vendor-mtk-build.sh
ci/vendor-mtk-hil-test.sh
```

For example, a Qualcomm product recipe can source that SDK's exact `envsetup` and select its real product target; the central platform does not guess `lunch` names or vendor make targets.

## Activation sequence

For each SoC, activate independently:

1. Register a dedicated ephemeral or resettable self-hosted Runner with the exact labels from the hardware profile.
2. Install the vendor SDK outside the repository.
3. Create the SDK identity file and record its SHA256 in the profile and toolchain.
4. Install all `required_tools` on the Runner.
5. Configure the resource broker URL/token in the Runner service environment.
6. Register real license pools for licensed SDKs.
7. Register HIL devices and make the broker return `CI_HIL_DEVICE_ID` plus transport metadata.
8. Add the product-specific vendor build/HIL recipes to the firmware repository.
9. Change exactly one hardware profile and its toolchain from `planned` to `active`.
10. Run `Hardware Runner Readiness` from `main`.
11. Only after readiness is green, enable the corresponding project target.

This ordering prevents a configured but non-functional Runner, missing SDK, missing license seat or unavailable board from producing a green build.

## Current status

The RK, Qualcomm and MediaTek profiles are deliberately `planned`. The repository now contains the real execution contract and gates, but no profile is declared production-ready until a real Runner/SDK/broker/device is connected and its immutable identity is known.
