# IAM Template Reference

The JSON templates in `image/iam/` document the minimum role split used by CI. They intentionally contain placeholders and create no AWS resources.

## Placeholders

| Placeholder | Meaning |
| --- | --- |
| `${AWS_ACCOUNT_ID}` | Account containing the GitHub OIDC provider and roles |
| `${GITHUB_ORG}` | Exact GitHub organization or owner |
| `${GITHUB_REPOSITORY}` | Exact repository name |
| `${AWS_REGION}` | Build region where used by organization extensions |
| `${BUILD_KMS_KEY_ARN}` | Customer-managed key for the AMI's encrypted snapshots |
| `${BUILD_INSTANCE_ROLE_ARN}` | Sole EC2 role Packer may pass to its builder |
| `${VALIDATION_INSTANCE_ROLE_ARN}` | Sole EC2 role validation may pass at launch |
| `${ADVERTISED_ADDRESS_TAG}` | Sole tag key validation may add after launch |
| `${BUILD_ENVIRONMENT}` | Protected GitHub environment for the build role |
| `${VALIDATION_ENVIRONMENT}` | Protected GitHub environment for the validator role |

Render placeholders using an infrastructure deployment system that preserves JSON escaping. The static contract test substitutes inert examples and parses every `*.json.tmpl`, but that does not prove AWS authorization semantics.

## Security Review

The builder can create and clean up Packer EC2 resources, use only the named build KMS key, and pass only the named builder role to EC2. Creation-time tags must include the Packer ownership identity, and later tagging is limited to resources that already carry that identity. The validator cannot build or promote an AMI. It can launch validation-tagged instances, add only the configured advertised-address tag to those instances, reboot and terminate them, manage its ephemeral key pair, use the build KMS key to launch encrypted volumes, and remove only failed images and snapshots carrying Packer's RTPengine identity tags.

Several EC2 discovery and lifecycle APIs require `Resource: "*"`. Compensate with organization permission boundaries, SCPs, CloudTrail alerting, and conditions for approved regions, VPCs, subnets, security groups, AMI ownership, and request tags. Confirm that snapshot tags are present before relying on cleanup conditions. KMS key policy must separately trust each role for only the required operations.

Do not add secrets permissions to either CI role. Do not combine builder and validator roles. Restrict both GitHub environments to the protected `AMI_TRUSTED_REF`, require reviewers before AWS access, and do not broaden the OIDC subjects to other environments or repositories. Re-run IAM Access Analyzer whenever a template or deployment constraint changes.
