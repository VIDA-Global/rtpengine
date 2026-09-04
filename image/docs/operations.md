# AMI Operations

## Repository Gates

The root `ami-*` targets are thin delegates to `image/Makefile`; the repository's default `all` target remains the normal RTPengine build. Run the AWS-free gates before requesting approval:

```bash
make ami-init
make ami-validate
make ami-lint
make ami-test
make ami-inspect
```

Validation checks Packer formatting and syntax, Ansible playbook syntax, Python compilation, and unit/contract tests. Lint runs ShellCheck, yamllint, and ansible-lint. A missing tool produces a named error and a nonzero exit rather than skipping a gate. `packer validate -syntax-only` uses no AWS credentials. `inspect` and source preparation are also local operations.

Formatting with `make ami-fmt` changes Packer files. Review its diff. `make ami-clean` removes generated image build state. Neither command calls AWS.

## GitHub Configuration

The `RTPengine ARM64 AMI` workflow triggers on pushes, but every self-hosted job is restricted to the exact protected branch ref in `AMI_TRUSTED_REF`. Configure branch protection on that ref, require review and passing checks, restrict pushes, and prevent untrusted workflows from using the runner group.

All jobs use `[self-hosted, linux, ARM64, rtpengine-ami]`. The runner must be an isolated, preferably ephemeral ARM64 host with reviewed network egress and the documented tools. Pinned action commit SHAs prevent mutable action tags. GitHub grants `id-token: write` only to the builder and validator jobs; they assume distinct roles after approval through the protected `rtpengine-ami-build` and `rtpengine-ami-validation` environments.

Configure these non-secret repository or organization variables:

| Variable | Description |
| --- | --- |
| `AWS_ACCOUNT_ID` | Expected 12-digit AWS account for OIDC sessions |
| `AMI_TRUSTED_REF` | Exact protected ref, for example `refs/heads/main` |
| `AMI_BUILD_REGION` | Region containing every referenced resource |
| `AMI_UBUNTU_2604_ARM64_SOURCE_AMI_ID` | Explicitly approved regional Ubuntu 26.04 ARM64 AMI ID |
| `AMI_BUILDER_ROLE_ARN` | GitHub OIDC builder role |
| `AMI_VALIDATOR_ROLE_ARN` | Separate GitHub OIDC validator role |
| `AMI_BUILD_VPC_ID` | Existing VPC |
| `AMI_BUILD_SUBNET_ID` | Existing subnet providing temporary public IPv4 connectivity |
| `AMI_BUILD_SECURITY_GROUP_IDS_JSON` | Non-empty JSON array of existing build security group IDs |
| `AMI_BUILD_INSTANCE_PROFILE` | Optional EC2 profile attached only when build egress requires it |
| `AMI_BUILD_KMS_KEY_ID` | Existing customer-managed EBS KMS key ARN or ID |
| `AMI_VALIDATION_SUBNET_ID` | Existing validation subnet with temporary public IPv4 connectivity |
| `AMI_VALIDATION_SECURITY_GROUP_ID` | Existing narrowly scoped validation security group |
| `AMI_VALIDATION_INSTANCE_PROFILE` | Optional EC2 profile used by the launched validator instance |

These are identifiers, not credentials. Store no AWS access keys in GitHub. OIDC trust must match the exact repository and trusted ref. The workflow does not promote, copy, share, or replicate AMIs.

## IAM Installation

Files under `image/iam/` are reviewable policy templates, not deployment automation. Substitute every placeholder, parse the result as JSON, run IAM Access Analyzer, apply organization permission boundaries/SCPs, and review the relevant KMS key policy before attaching it.

- `github-oidc-build-trust.json.tmpl` is the builder role trust policy.
- `github-oidc-validator-trust.json.tmpl` is the validator role trust policy.
- `builder-policy.json.tmpl` permits the EC2 lifecycle Packer needs, use of one KMS key, and passing one builder instance role.
- `validator-policy.json.tmpl` permits validation launch/tag/key/reboot/termination, one validation instance role, encrypted-image use, and cleanup of failed Packer-tagged RTPengine AMIs and snapshots.

Both trust templates require the GitHub audience `sts.amazonaws.com` and an exact repository/environment subject. Configure each environment with required reviewers and a deployment branch rule allowing only `AMI_TRUSTED_REF`; do not replace either subject with a wildcard. Further constrain VPC, subnet, security group, source AMI, and tag conditions with permission boundaries where your EC2 authorization model supports them.

Neither role includes Secrets Manager or Parameter Store access. The AMI has no runtime-secret contract. The builder instance profile should be empty unless the approved Ubuntu/package-egress design has a concrete need. The validator instance profile should likewise contain only deployment-approved runtime permissions.

## Build And Validation

After explicit approval, a local build is:

```bash
AWS_PROFILE=rtpengine-ami-builder \
  make ami-build VARS_FILE=packer/rtpengine.pkrvars.hcl
```

The manifest at `image/build/packer-manifest.json` records the built AMI, approved source AMI, RTPengine source identity, Ubuntu release, architecture, and build ID. Retain it with the change record.

CI performs these stages in order:

1. Run static validation, lint, and tests without AWS credentials.
2. On only `AMI_TRUSTED_REF`, assume the builder role and render non-secret Packer variables.
3. Obtain approval through the protected build environment, then build one private encrypted source-region AMI on `m9g.large`.
4. Upload Packer, source, and normalized AMI identity manifests.
5. Obtain approval through the protected validation environment, then assume the separate validator role.
6. Launch with IMDSv2 and tag access, set `RtpEngineAdvertisedAddress`, and verify Ubuntu 26.04, ARM64, package inventory, encrypted storage, DKMS/module state, private control bindings, smoke traffic, service state, and reboot survival.
7. Terminate the instance and delete its ephemeral key; on failure, deregister the candidate AMI and delete its snapshots.

Cancellation can interrupt shell traps. Operators must inspect instances, key pairs, volumes, AMIs, and snapshots carrying the workflow build tags after a cancelled or failed run. Never delete an older validated AMI as part of failed-build cleanup.

## Deployment Acceptance

Before admitting calls, the launch template must require IMDSv2, expose tags through IMDS, and supply `RtpEngineAdvertisedAddress`. Confirm the advertised address is routed from both media sides. Confirm security groups allow the entire configured UDP media range and permit NG control only from approved SIP proxies. CLI and HTTP telemetry must remain private.

The image-level smoke test proves one host's kernel forwarding and control contract. Deployment tests must additionally prove offer/answer/delete behavior, RTP and RTCP in both directions, expected SDP advertisement, packet marking, capacity limits, monitoring, draining, and replacement behavior under realistic load. Because `no-fallback` is mandatory, loss of `nft_rtpengine` must fail readiness and remove the node from service.

## Updates And Rollback

Treat the image as immutable. Do not run distribution upgrades, release upgrades, RTPengine package replacements, DKMS rebuilds, or kernel unholds on a serving instance. Bake and validate a replacement for every source, Ubuntu package, kernel, module, configuration, or source-AMI change.

Rollback points the launch template or deployment reference to the previous validated AMI and replaces instances. Drain new sessions, allow established media to finish for the agreed timeout, remove the node from control-plane selection, replace it, then repeat launch and media acceptance checks. The advertised-address tag and network policy must remain compatible with the older image.

There is no automated retention or cross-region promotion. Keep enough validated AMIs and manifests for the agreed rollback window. Deregister and delete snapshots only through a separately approved retention procedure after checking launch templates, Auto Scaling Groups, running instances, deployment records, and incident rollback requirements.
