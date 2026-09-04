#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'USAGE'
Usage: AMI_ID=... AWS_REGION=... EXPECTED_KMS_KEY_ID=... \
       SUBNET_ID=... SECURITY_GROUP_ID=... \
       validate-ami.sh --run

Launches and validates an Ubuntu 26.04 ARM64 RTPengine AMI. AWS calls are made
only with --run. The subnet must assign a route to an internet gateway and the
preexisting security group must allow SSH from the validator and the required
RTPengine test traffic. Set CLEAN_FAILED_AMI=true to deregister a failed AMI
and delete its snapshots. INSTANCE_TYPE defaults to m9g.large.
USAGE
}

[[ ${1:-} == --run && $# == 1 ]] || {
    usage
    exit 2
}

required=(AMI_ID AWS_REGION EXPECTED_KMS_KEY_ID SUBNET_ID SECURITY_GROUP_ID)
for name in "${required[@]}"; do
    [[ -n ${!name:-} ]] || {
        printf 'Required environment variable is unset: %s\n' "$name" >&2
        exit 2
    }
done

for command in aws ssh ssh-keygen; do
    command -v "$command" >/dev/null || {
        printf 'Required command is unavailable: %s\n' "$command" >&2
        exit 2
    }
done

readonly INSTANCE_TYPE=${INSTANCE_TYPE:-m9g.large}
readonly SSH_USER=${SSH_USER:-ubuntu}
readonly CLEAN_FAILED_AMI=${CLEAN_FAILED_AMI:-false}
readonly ADVERTISED_ADDRESS_TAG=${ADVERTISED_ADDRESS_TAG:-RtpEngineAdvertisedAddress}
readonly VALIDATION_INSTANCE_PROFILE=${VALIDATION_INSTANCE_PROFILE:-}
[[ $ADVERTISED_ADDRESS_TAG =~ ^[A-Za-z0-9+=._:/@-]{1,128}$ ]] || {
    printf 'ADVERTISED_ADDRESS_TAG is invalid\n' >&2
    exit 2
}
KEY_NAME="rtpengine-ami-validation-$(date +%s)-$$"
readonly KEY_NAME
work_dir=$(mktemp -d)
readonly work_dir
readonly key_file="$work_dir/id_ed25519"
instance_id=
public_ip=
private_ip=
snapshot_ids=()
root_device=
cleanup_eligible=false
AWS=(aws --no-cli-pager --region "$AWS_REGION")
expected_kms_arn=

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    if [[ -n $instance_id ]]; then
        "${AWS[@]}" ec2 terminate-instances --instance-ids "$instance_id" >/dev/null 2>&1 || true
        "${AWS[@]}" ec2 wait instance-terminated --instance-ids "$instance_id" >/dev/null 2>&1 || true
    fi
    "${AWS[@]}" ec2 delete-key-pair --key-name "$KEY_NAME" >/dev/null 2>&1 || true
    rm -rf "$work_dir"
    if (( status != 0 )) && [[ $CLEAN_FAILED_AMI == true && $cleanup_eligible == true ]]; then
        if ! "${AWS[@]}" ec2 deregister-image --image-id "$AMI_ID" >/dev/null 2>&1; then
            printf 'WARNING: failed to deregister candidate AMI %s\n' "$AMI_ID" >&2
        fi
        for snapshot_id in "${snapshot_ids[@]}"; do
            deleted=false
            for _ in {1..12}; do
                if "${AWS[@]}" ec2 delete-snapshot --snapshot-id "$snapshot_id" \
                    >/dev/null 2>&1; then
                    deleted=true
                    break
                fi
                sleep 5
            done
            if [[ $deleted != true ]]; then
                printf 'WARNING: failed to delete candidate snapshot %s\n' \
                    "$snapshot_id" >&2
            fi
        done
    elif (( status != 0 )) && [[ $CLEAN_FAILED_AMI == true ]]; then
        printf 'WARNING: candidate AMI identity was not eligible for automatic cleanup\n' >&2
    fi
    exit "$status"
}
trap cleanup EXIT INT TERM

expected_kms_arn=$("${AWS[@]}" kms describe-key --key-id "$EXPECTED_KMS_KEY_ID" \
    --query 'KeyMetadata.Arn' --output text)
readonly expected_kms_arn

while IFS= read -r snapshot_id; do
    [[ -n $snapshot_id ]] && snapshot_ids+=("$snapshot_id")
done < <("${AWS[@]}" ec2 describe-images --image-ids "$AMI_ID" \
    --owners self --query 'Images[0].BlockDeviceMappings[].Ebs.SnapshotId' --output text | tr '\t' '\n')
[[ ${#snapshot_ids[@]} -gt 0 && -n ${snapshot_ids[0]} ]] || {
    printf 'AMI has no EBS snapshots\n' >&2
    exit 1
}
# Backticks below are JMESPath literals, not shell substitutions.
# shellcheck disable=SC2016
managed_by=$("${AWS[@]}" ec2 describe-images --image-ids "$AMI_ID" --owners self \
    --query 'Images[0].Tags[?Key==`ManagedBy`].Value | [0]' --output text)
# shellcheck disable=SC2016
application=$("${AWS[@]}" ec2 describe-images --image-ids "$AMI_ID" --owners self \
    --query 'Images[0].Tags[?Key==`Application`].Value | [0]' --output text)
if [[ $managed_by == packer && $application == rtpengine ]]; then
    cleanup_eligible=true
fi

[[ $("${AWS[@]}" ec2 describe-images --image-ids "$AMI_ID" --owners self \
    --query 'Images[0].Public' --output text) == False ]] || {
    printf 'AMI must be private\n' >&2
    exit 1
}
root_device=$("${AWS[@]}" ec2 describe-images --image-ids "$AMI_ID" --owners self \
    --query 'Images[0].RootDeviceName' --output text)
[[ -n $root_device && $root_device != None ]] || {
    printf 'AMI has no root device\n' >&2
    exit 1
}
[[ $("${AWS[@]}" ec2 describe-images --image-ids "$AMI_ID" --owners self \
    --query 'Images[0].Architecture' --output text) == arm64 ]] || {
    printf 'AMI architecture must be arm64\n' >&2
    exit 1
}
for snapshot_id in "${snapshot_ids[@]}"; do
    [[ $("${AWS[@]}" ec2 describe-snapshots --snapshot-ids "$snapshot_id" \
        --query 'Snapshots[0].Encrypted' --output text) == True ]] || {
        printf 'Unencrypted AMI snapshot: %s\n' "$snapshot_id" >&2
        exit 1
    }
    [[ $("${AWS[@]}" ec2 describe-snapshots --snapshot-ids "$snapshot_id" \
        --query 'Snapshots[0].KmsKeyId' --output text) == "$expected_kms_arn" ]] || {
        printf 'AMI snapshot does not use expected KMS key: %s\n' "$snapshot_id" >&2
        exit 1
    }
done

ssh-keygen -q -t ed25519 -N '' -f "$key_file"
"${AWS[@]}" ec2 import-key-pair --key-name "$KEY_NAME" \
    --public-key-material "fileb://${key_file}.pub" >/dev/null

run_arguments=(
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --network-interfaces "DeviceIndex=0,SubnetId=${SUBNET_ID},Groups=${SECURITY_GROUP_ID},AssociatePublicIpAddress=true" \
    --metadata-options 'HttpEndpoint=enabled,HttpTokens=required,HttpPutResponseHopLimit=1,InstanceMetadataTags=enabled' \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=rtpengine-ami-validation},{Key=Purpose,Value=ami-validation}]' \
    --query 'Instances[0].InstanceId' --output text
)
if [[ -n $VALIDATION_INSTANCE_PROFILE ]]; then
    run_arguments+=(--iam-instance-profile "Name=$VALIDATION_INSTANCE_PROFILE")
fi
instance_id=$("${AWS[@]}" ec2 run-instances "${run_arguments[@]}")
"${AWS[@]}" ec2 wait instance-status-ok --instance-ids "$instance_id"
root_volume=$("${AWS[@]}" ec2 describe-instances --instance-ids "$instance_id" \
    --query "Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName=='${root_device}'].Ebs.VolumeId | [0]" \
    --output text)
[[ -n $root_volume && $root_volume != None && \
    $("${AWS[@]}" ec2 describe-volumes --volume-ids "$root_volume" \
        --query 'Volumes[0].Encrypted' --output text) == True ]] || {
    printf 'Launched root volume is not encrypted\n' >&2
    exit 1
}
[[ $("${AWS[@]}" ec2 describe-volumes --volume-ids "$root_volume" \
    --query 'Volumes[0].KmsKeyId' --output text) == "$expected_kms_arn" ]] || {
    printf 'Launched root volume does not use the expected KMS key\n' >&2
    exit 1
}
private_ip=$("${AWS[@]}" ec2 describe-instances --instance-ids "$instance_id" \
    --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text)
public_ip=$("${AWS[@]}" ec2 describe-instances --instance-ids "$instance_id" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
[[ $private_ip != None && $public_ip != None && -n $private_ip && -n $public_ip ]] || {
    printf 'Instance did not receive both private and public IPv4 addresses\n' >&2
    exit 1
}
"${AWS[@]}" ec2 create-tags --resources "$instance_id" \
    --tags "Key=${ADVERTISED_ADDRESS_TAG},Value=${private_ip}"

ssh_options=(
    -i "$key_file"
    -o BatchMode=yes
    -o ConnectTimeout=5
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=accept-new
    -o "UserKnownHostsFile=$work_dir/known_hosts"
)
target="${SSH_USER}@${public_ip}"

wait_for_ssh() {
    local attempt
    for attempt in {1..60}; do
        if ssh "${ssh_options[@]}" "$target" true 2>/dev/null; then
            return 0
        fi
        sleep 5
    done
    printf 'SSH did not become available\n' >&2
    return 1
}

start_services() {
    ssh "${ssh_options[@]}" "$target" sudo bash -s <<'REMOTE'
set -Eeuo pipefail
for attempt in {1..12}; do
    systemctl reset-failed rtpengine-firstboot.service || true
    if systemctl restart rtpengine-firstboot.service; then
        break
    fi
    (( attempt < 12 )) || exit 1
    sleep 5
done
systemctl is-enabled rtpengine-daemon.service
for attempt in {1..12}; do
    systemctl reset-failed rtpengine-daemon.service || true
    if systemctl start rtpengine-daemon.service; then
        break
    fi
    (( attempt < 12 )) || exit 1
    sleep 5
done
systemctl is-active --quiet rtpengine-firstboot.service
systemctl is-active --quiet rtpengine-daemon.service
REMOTE
}

validate_core() {
    ssh "${ssh_options[@]}" "$target" sudo bash -s -- "$private_ip" <<'REMOTE'
set -Eeuo pipefail
private_ip=$1
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 26.04 ]]
[[ $(dpkg --print-architecture) == arm64 ]]
[[ $(uname -m) == aarch64 ]]

expected=$'rtpengine-daemon\nrtpengine-kernel-dkms\nrtpengine-utils'
installed=$(dpkg-query -W -f='${binary:Package}\t${db:Status-Abbrev}\n' 'rtpengine*' 2>/dev/null |
    awk '$2 == "ii" { sub(/:.*/, "", $1); print $1 }' | sort -u)
[[ $installed == "$expected" ]]
for unwanted in rtpengine-recording-daemon rtpengine-perftest rtpengine-perftest-data; do
    ! dpkg-query -W -f='${db:Status-Abbrev}' "$unwanted" 2>/dev/null | grep -q '^ii'
done

kernel=$(uname -r)
mapfile -t kernel_packages < <(
    dpkg-query -W -f='${binary:Package}\t${db:Status-Abbrev}\n' 'linux-*' 2>/dev/null |
        awk -v kernel="$kernel" '$2 == "ii" { sub(/:.*/, "", $1); if ($1 ~ /^(linux|linux-image|linux-headers)-aws$/ || index($1, kernel)) print $1 }'
)
[[ ${#kernel_packages[@]} -gt 0 ]]
for package in "${kernel_packages[@]}"; do
    apt-mark showhold | grep -Fxq "$package"
done
dkms status | grep -Eiq 'rtpengine/.+, .+, .+: installed'
modprobe nft_rtpengine
[[ -d /sys/module/nft_rtpengine && -e /proc/rtpengine/control && -e /proc/rtpengine/list ]]
[[ $(modinfo -F vermagic nft_rtpengine) == "${kernel} "* ]]

config=/etc/rtpengine/rtpengine.conf
[[ $(stat -c '%U:%G:%a' "$config") == root:rtpengine:640 ]]
config_value() {
    local key=$1
    awk -F= -v key="$key" '$1 ~ "^[[:space:]]*" key "[[:space:]]*$" {
        value=$2; gsub(/^[[:space:]]+|[[:space:]]+$/, "", value); print value
    }' "$config"
}
table=$(config_value table)
[[ $table =~ ^([0-9]|[1-5][0-9]|6[0-3])$ ]]
[[ $(config_value no-fallback) == true ]]
[[ $(config_value interface) == "${private_ip}!${private_ip}" ]]
ng_listener=$(config_value listen-ng)
cli_listener=$(config_value listen-cli)
http_listener=$(config_value listen-http)
[[ $ng_listener == "${private_ip}:"* ]]
[[ $cli_listener == "${private_ip}:"* ]]
[[ $http_listener == "${private_ip}:"* ]]
ng_port=${ng_listener##*:}
cli_port=${cli_listener##*:}
http_port=${http_listener##*:}
port_min=$(config_value port-min)
port_max=$(config_value port-max)
[[ $port_min =~ ^[0-9]+$ && $port_max =~ ^[0-9]+$ && $port_min -le $port_max ]]
[[ -n $(config_value nftables-family) ]]
[[ -n $(config_value nftables-chain) && -n $(config_value nftables-base-chain) ]]
systemctl is-enabled rtpengine-daemon.service
systemctl is-active --quiet rtpengine-daemon.service
ss -H -lun | awk '{print $5}' | grep -Fxq "$ng_listener"
ss -H -ltn | awk '{print $4}' | grep -Fxq "$cli_listener"
ss -H -ltn | awk '{print $4}' | grep -Fxq "$http_listener"
! ss -H -lntu | awk '{print $4}' | grep -Eq "^((0\\.0\\.0\\.0)|\\*):(${ng_port}|${cli_port}|${http_port})$"
/usr/libexec/rtpengine-ami/smoke.py \
    --host "$private_ip" --port "$ng_port" \
    --port-min "$port_min" --port-max "$port_max"
REMOTE
}

wait_for_ssh
start_services
validate_core
boot_id=$(ssh "${ssh_options[@]}" "$target" cat /proc/sys/kernel/random/boot_id)
"${AWS[@]}" ec2 reboot-instances --instance-ids "$instance_id"
for attempt in {1..60}; do
    new_boot_id=$(ssh "${ssh_options[@]}" "$target" cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)
    if [[ -n $new_boot_id && $new_boot_id != "$boot_id" ]]; then
        break
    fi
    (( attempt < 60 )) || {
        printf 'Instance did not return after reboot\n' >&2
        exit 1
    }
    sleep 5
done
ssh "${ssh_options[@]}" "$target" sudo systemctl is-active --quiet rtpengine-firstboot.service
validate_core
printf 'AMI validation passed: %s\n' "$AMI_ID"
