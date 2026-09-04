# RTPengine Ubuntu 26.04 ARM64 AMI

This image project builds RTPengine from the repository's exact committed `HEAD`, packages the daemon, utilities, and DKMS kernel module natively on ARM64, and produces one private encrypted AMI in one AWS region. It does not promote or copy images across regions.

## Fixed Design

| Setting | Contract |
| --- | --- |
| Operating system | Canonical Ubuntu 26.04 ARM64 from an explicitly approved AMI ID |
| Builder | `m9g.large`; no automatic instance-family or architecture fallback |
| Source | Deterministic archive of committed `HEAD`, with commit, tree, version, and SHA-256 provenance |
| Packages | `rtpengine-daemon`, `rtpengine-utils`, and `rtpengine-kernel-dkms` |
| Storage | Encrypted GP3 using the configured customer-managed KMS key |
| AMI visibility | Private; no public launch permissions |
| Build connectivity | Public-IP SSH through a pre-existing subnet and security groups |
| Runtime control listeners | Bound to the instance's primary private IPv4 address |
| Media forwarding | Kernel forwarding is mandatory; `no-fallback = true` |

The source AMI is never discovered by a broad name filter. `source_ami_id` is required and has no default. Before changing it, an operator must verify Canonical ownership, Ubuntu release `26.04`, ARM64 architecture, virtualization type, root device, publication provenance, and the organization's patch approval. The AMI ID is regional.

`m9g.large` must be available in the chosen region and Availability Zone. An unavailable type fails the build rather than silently selecting x86_64 or another family.

## Approval Boundary

These commands are AWS-free. `init` downloads pinned Packer plugins and Ansible collections but does not authenticate to AWS:

```bash
make ami-help
make ami-init
make ami-fmt
make ami-validate
make ami-lint
make ami-test
make ami-inspect
make ami-clean
```

These commands create, mutate, or delete AWS resources and require explicit change approval and an approved role session:

```bash
make ami-build VARS_FILE=packer/rtpengine.pkrvars.hcl
make ami-validate-ami AMI_ID=ami-... AWS_REGION=us-east-2 \
  EXPECTED_KMS_KEY_ID=arn:aws:kms:us-east-2:123456789012:key/... \
  SUBNET_ID=subnet-... SECURITY_GROUP_ID=sg-... \
  VALIDATION_INSTANCE_PROFILE=rtpengine-validator
```

**Do not run either mutating command merely to check syntax.** A build creates a temporary instance, key pair, volume, snapshot, and AMI. Validation imports a temporary key, launches and tags an instance, reboots it, terminates it, and deletes the key. CI sets `CLEAN_FAILED_AMI=true`, so failed validation also deregisters the newly built AMI and deletes its snapshots. Confirm cleanup in AWS after every failed or cancelled run.

## Network Risk

The current Packer and validation communicators use public IPv4 SSH. The VPC, subnet, route table, network ACLs, and security groups must already exist. Security groups must limit TCP/22 to the fixed self-hosted runner egress addresses; never allow `0.0.0.0/0` or `::/0`. Temporary instances need controlled package-repository egress. Packer does not create or broaden networking.

Runtime security groups should separately restrict:

- UDP media ports `30000-39999` to approved media peers.
- UDP/2223 NG control to approved SIP proxies only.
- TCP/2224 CLI and TCP/2225 HTTP telemetry to private operator and monitoring networks only.
- SSH to reviewed administrative sources, or disable SSH in the deployment layer.

Public SSH is a deliberate transitional risk, not a recommended production topology. Moving to private runner routing or Session Manager requires a separately reviewed communicator and IAM design.

## First Boot

The launch template must expose instance tags through IMDS and require IMDSv2:

```text
HttpEndpoint=enabled
HttpTokens=required
HttpPutResponseHopLimit=1
InstanceMetadataTags=enabled
```

Before RTPengine can start, deployment automation must set the instance tag `RtpEngineAdvertisedAddress` to the exact unicast IPv4 address advertised in SDP. The first-boot service obtains a fresh IMDSv2 token, reads the instance's primary private IPv4 address and that tag, validates both, renders `/etc/rtpengine/rtpengine.conf` atomically as root:`rtpengine` mode `0640`, and only then permits the daemon. A missing, malformed, multicast, loopback, link-local, or unusable address fails closed. The tag may be private when private media routing is intended.

The generated interface is `<private-ip>!<advertised-ip>`. NG, CLI, and HTTP controls bind only to `<private-ip>`. The image does not read Secrets Manager, Parameter Store, user data, or arbitrary daemon arguments.

## Kernel Lifecycle

The bake updates Ubuntu, reboots into the updated kernel, installs matching headers, builds and verifies `nft_rtpengine` through DKMS, then holds the running kernel and matching kernel/header packages. This protects the module ABI at launch. It also means normal unattended kernel updates are not the patch process.

For a kernel security update:

1. Approve a current Ubuntu 26.04 ARM64 source AMI and update `source_ami_id`.
2. Rebuild from the intended RTPengine commit.
3. Verify DKMS status, module path, module vermagic, nftables state, media forwarding, reboot behavior, and package inventory.
4. Deploy the new AMI gradually and retain the prior validated AMI for rollback.
5. Deregister old images only after proving no launch template, Auto Scaling Group, instance refresh, or rollback record references them.

Never remove the kernel holds in place on a production instance. There is no userspace forwarding fallback if the module fails; this is intentional so media failure is visible rather than silently changing capacity or performance.

## Variables

Create an ignored `image/packer/rtpengine.pkrvars.hcl` from the retained `.example` file. Real account, network, profile, and KMS values must not be committed.

| Variable | Purpose |
| --- | --- |
| `aws_region` | Single build region |
| `source_ami_id` | Required approved Canonical Ubuntu 26.04 ARM64 AMI ID |
| `instance_type` | Native ARM64 builder, fixed to `m9g.large` in CI |
| `vpc_id`, `subnet_id` | Existing public-SSH build network |
| `security_group_ids` | Existing groups allowing narrowly sourced SSH |
| `build_instance_profile` | Optional temporary builder instance profile |
| `kms_key_id` | Existing customer-managed EBS KMS key |
| `root_volume_*` | Encrypted GP3 size, IOPS, and throughput |
| `build_id`, `additional_tags` | Immutable build and organization identity |
| `rtpengine_advertised_address_tag` | First-boot address tag key |
| `rtpengine_table`, `rtpengine_no_fallback` | Kernel table and mandatory no-fallback policy |
| `rtpengine_media_port_min`, `rtpengine_media_port_max` | Media UDP range |
| `rtpengine_ng_port`, `rtpengine_cli_port`, `rtpengine_http_port` | Private control ports |
| `rtpengine_nftables_*` | Dedicated nftables family and chain settings |
| `rtpengine_max_sessions`, `rtpengine_log_level`, `rtpengine_tos` | Capacity, logging, and packet marking |
| `rtpengine_metadata_*`, `rtpengine_firstboot_timeout` | Bounded first-boot metadata retries and service timeout |

Typed validations reject unsafe ranges and disabling `rtpengine_no_fallback`. Read [Operations](docs/operations.md) for CI, IAM, validation, deployment, and rollback procedures.
