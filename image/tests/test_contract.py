from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


IMAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = IMAGE_ROOT.parent


def text(relative: str) -> str:
    return (IMAGE_ROOT / relative).read_text(encoding="utf-8")


class ImageContractTests(unittest.TestCase):
    def test_root_make_targets_are_thin_and_default_is_unchanged(self) -> None:
        makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn(".DEFAULT_GOAL := all", makefile)
        targets = {
            "ami-help": "help",
            "ami-init": "init",
            "ami-fmt": "fmt",
            "ami-validate": "validate",
            "ami-lint": "lint",
            "ami-test": "test",
            "ami-inspect": "inspect",
            "ami-build": "build",
            "ami-validate-ami": "validate-ami",
            "ami-clean": "clean",
        }
        for root_target, image_target in targets.items():
            with self.subTest(target=root_target):
                self.assertIn(
                    f"{root_target}:\n\t$(MAKE) -C image {image_target}", makefile
                )

        result = subprocess.run(
            ["make", "--no-print-directory", "-s", "ami-help"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RTPengine Ubuntu 26.04 ARM64 AMI targets", result.stdout)

    def test_packer_requires_approved_ubuntu_2604_arm64_image(self) -> None:
        variables = text("packer/variables.pkr.hcl")
        template = text("packer/rtpengine-arm64.pkr.hcl")
        self.assertRegex(variables, r'variable\s+"source_ami_id"')
        self.assertIn("Ubuntu 26.04 ARM64", variables)
        self.assertNotRegex(
            variables.split('variable "source_ami_id"', 1)[1].split("}\n", 1)[0],
            r"\bdefault\s*=",
        )
        self.assertIn('default     = "m9g.large"', variables)
        self.assertIn("source_ami                  = var.source_ami_id", template)
        self.assertIn("associate_public_ip_address = true", template)
        self.assertNotIn("source_ami_filter", template)
        self.assertNotIn("ami_regions", template)

    def test_image_is_private_encrypted_and_imdsv2_only(self) -> None:
        template = text("packer/rtpengine-arm64.pkr.hcl")
        for contract in (
            "ami_groups              = []",
            "ami_users               = []",
            "encrypted             = true",
            'http_tokens                 = "required"',
            'instance_metadata_tags      = "enabled"',
        ):
            self.assertIn(contract, template)
        self.assertIn('ManagedBy       = "packer"', template)

    def test_runtime_fails_closed_and_binds_controls_privately(self) -> None:
        variables = text("packer/variables.pkr.hcl")
        config = text("ansible/roles/rtpengine_ami/templates/rtpengine.conf.j2")
        firstboot = text("ansible/roles/rtpengine_ami/files/rtpengine-firstboot.py")
        self.assertIn('default     = "RtpEngineAdvertisedAddress"', variables)
        self.assertIn("condition     = var.rtpengine_no_fallback", variables)
        self.assertIn("no-fallback =", config)
        for listener in ("listen-ng", "listen-cli", "listen-http"):
            self.assertRegex(config, rf"{listener} = @PRIVATE_IPV4@:")
        self.assertIn('if not config.getboolean("no-fallback")', firstboot)
        self.assertIn('"X-aws-ec2-metadata-token"', firstboot)

    def test_kernel_is_updated_before_dkms_then_held(self) -> None:
        tasks = text("ansible/roles/rtpengine_ami/tasks/main.yml")
        update = tasks.index("Update all packages, including the kernel")
        reboot = tasks.index("Reboot into the updated kernel")
        build = tasks.index("Build native full-transcoding binary packages")
        hold = tasks.index("Hold the running kernel and matching headers")
        self.assertLess(update, reboot)
        self.assertLess(reboot, build)
        self.assertLess(build, hold)
        self.assertIn("apt-mark hold", tasks)

    def test_workflow_has_ref_gate_split_oidc_and_failed_cleanup(self) -> None:
        workflow = (REPOSITORY_ROOT / ".github/workflows/rtpengine-ami.yml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(workflow, r"on:\n  push:\n")
        self.assertNotIn("workflow_dispatch", workflow)
        self.assertGreaterEqual(workflow.count("github.ref == vars.AMI_TRUSTED_REF"), 2)
        self.assertIn("AMI_BUILDER_ROLE_ARN", workflow)
        self.assertIn("AMI_VALIDATOR_ROLE_ARN", workflow)
        self.assertIn("environment: rtpengine-ami-build", workflow)
        self.assertIn("environment: rtpengine-ami-validation", workflow)
        self.assertIn("AMI_UBUNTU_2604_ARM64_SOURCE_AMI_ID", workflow)
        self.assertIn('CLEAN_FAILED_AMI: \'true\'', workflow)
        self.assertNotRegex(workflow, r"(?i)promot|ami_regions|destination.region")
        for action in ("actions/checkout@", "configure-aws-credentials@", "upload-artifact@"):
            match = re.search(re.escape(action) + r"([^\s]+)", workflow)
            self.assertIsNotNone(match)
            self.assertRegex(match.group(1), r"^[0-9a-f]{40}$")

    def test_iam_templates_render_and_contain_no_secret_access(self) -> None:
        replacements = {
            "${AWS_ACCOUNT_ID}": "123456789012",
            "${AWS_REGION}": "us-east-2",
            "${GITHUB_ORG}": "example",
            "${GITHUB_REPOSITORY}": "rtpengine",
            "${TRUSTED_REF}": "refs/heads/main",
            "${BUILD_KMS_KEY_ARN}": "arn:aws:kms:us-east-2:123456789012:key/example",
            "${BUILD_INSTANCE_ROLE_ARN}": "arn:aws:iam::123456789012:role/builder",
            "${VALIDATION_INSTANCE_ROLE_ARN}": "arn:aws:iam::123456789012:role/validator",
            "${ADVERTISED_ADDRESS_TAG}": "RtpEngineAdvertisedAddress",
            "${BUILD_ENVIRONMENT}": "rtpengine-ami-build",
            "${VALIDATION_ENVIRONMENT}": "rtpengine-ami-validation",
        }
        for path in sorted((IMAGE_ROOT / "iam").glob("*.json.tmpl")):
            rendered = path.read_text(encoding="utf-8")
            self.assertNotRegex(rendered, r"(?i)secretsmanager|ssm:GetParameter")
            for placeholder, value in replacements.items():
                rendered = rendered.replace(placeholder, value)
            with self.subTest(template=path.name):
                self.assertNotIn("${", rendered)
                json.loads(rendered)

        builder = text("iam/builder-policy.json.tmpl")
        validator = text("iam/validator-policy.json.tmpl")
        for action in ("ec2:RunInstances", "kms:CreateGrant", "iam:PassRole"):
            self.assertIn(action, builder)
        for action in (
            "ec2:ImportKeyPair",
            "ec2:RebootInstances",
            "ec2:TerminateInstances",
            "ec2:DeregisterImage",
            "ec2:DeleteSnapshot",
        ):
            self.assertIn(action, validator)

        self.assertIn("ec2:CreateAction", builder)
        self.assertIn("ec2:ResourceTag/ManagedBy", builder)
        self.assertIn("aws:TagKeys", validator)
        self.assertIn("ec2:ResourceTag/Purpose", validator)


if __name__ == "__main__":
    unittest.main()
