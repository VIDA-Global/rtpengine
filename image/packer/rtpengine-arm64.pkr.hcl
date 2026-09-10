locals {
  image_dir         = dirname(abspath(path.root))
  architecture      = "arm64"
  source_provenance = jsondecode(file("${local.image_dir}/build/source/source-manifest.json"))
  source_short      = substr(local.source_provenance.commit, 0, 12)
  version_name = replace(
    replace(replace(local.source_provenance.version, "+", "-"), ":", "-"),
    "~",
    "-"
  )
  build_time = formatdate("YYYYMMDDhhmmss", timestamp())
  image_name = "${var.ami_name_prefix}-${local.version_name}-arm64-${local.source_short}-${var.build_id}-${local.build_time}"
  common_tags = merge(var.additional_tags, {
    Application     = var.application_name
    Architecture    = local.architecture
    BuildId         = var.build_id
    Environment     = var.environment
    ImageFamily     = var.ami_name_prefix
    ManagedBy       = "packer"
    RTPengineCommit = local.source_provenance.commit
    SourceAMI       = var.source_ami_id
    SourceSHA256    = local.source_provenance.archive_sha256
    Version         = local.source_provenance.version
  })
  provision_input = {
    rtpengine_ami_version                = local.source_provenance.version
    rtpengine_ami_source_commit          = local.source_provenance.commit
    rtpengine_ami_source_tree            = local.source_provenance.tree
    rtpengine_ami_source_timestamp       = local.source_provenance.commit_timestamp
    rtpengine_ami_source_sha256          = local.source_provenance.archive_sha256
    rtpengine_ami_advertised_address_tag = var.rtpengine_advertised_address_tag
    rtpengine_ami_table                  = var.rtpengine_table
    rtpengine_ami_no_fallback            = var.rtpengine_no_fallback
    rtpengine_ami_media_port_min         = var.rtpengine_media_port_min
    rtpengine_ami_media_port_max         = var.rtpengine_media_port_max
    rtpengine_ami_ng_port                = var.rtpengine_ng_port
    rtpengine_ami_cli_port               = var.rtpengine_cli_port
    rtpengine_ami_http_port              = var.rtpengine_http_port
    rtpengine_ami_nftables_family        = var.rtpengine_nftables_family
    rtpengine_ami_nftables_chain         = var.rtpengine_nftables_chain
    rtpengine_ami_nftables_base_chain    = var.rtpengine_nftables_base_chain
    rtpengine_ami_max_sessions           = var.rtpengine_max_sessions
    rtpengine_ami_log_level              = var.rtpengine_log_level
    rtpengine_ami_tos                    = var.rtpengine_tos
    rtpengine_ami_metadata_attempts      = var.rtpengine_metadata_attempts
    rtpengine_ami_metadata_timeout       = var.rtpengine_metadata_timeout
    rtpengine_ami_firstboot_timeout      = var.rtpengine_firstboot_timeout
  }
}

source "amazon-ebs" "rtpengine_arm64" {
  region                      = var.aws_region
  source_ami                  = var.source_ami_id
  instance_type               = var.instance_type
  vpc_id                      = var.vpc_id
  subnet_id                   = var.subnet_id
  security_group_ids          = var.security_group_ids
  iam_instance_profile        = var.build_instance_profile
  associate_public_ip_address = true

  communicator              = "ssh"
  ssh_username              = var.ssh_username
  ssh_interface             = "public_ip"
  ssh_timeout               = var.ssh_timeout
  ssh_clear_authorized_keys = true

  ami_name                = local.image_name
  ami_description         = "RTPengine ${local.source_provenance.version} on Ubuntu 26.04 ARM64 from ${local.source_short}"
  ami_groups              = []
  ami_users               = []
  ami_virtualization_type = "hvm"
  ena_support             = true
  imds_support            = "v2.0"

  metadata_options {
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
    http_tokens                 = "required"
    instance_metadata_tags      = "enabled"
  }

  launch_block_device_mappings {
    delete_on_termination = true
    device_name           = var.root_device_name
    encrypted             = true
    iops                  = var.root_volume_iops
    kms_key_id            = var.kms_key_id
    throughput            = var.root_volume_throughput
    volume_size           = var.root_volume_size
    volume_type           = "gp3"
  }

  run_tags        = merge(local.common_tags, { Name = "${var.ami_name_prefix}-packer-${var.build_id}" })
  run_volume_tags = local.common_tags
  snapshot_tags   = local.common_tags
  tags            = local.common_tags
}

build {
  name    = "rtpengine-arm64"
  sources = ["source.amazon-ebs.rtpengine_arm64"]

  provisioner "shell" {
    inline = ["install -d -m 0700 /tmp/rtpengine-image-upload /tmp/rtpengine-image-upload/assets /tmp/rtpengine-image-upload/provision"]
  }

  provisioner "file" {
    source      = "${local.image_dir}/build/source/rtpengine-head.tar.gz"
    destination = "/tmp/rtpengine-image-upload/source.tar.gz"
  }

  provisioner "file" {
    source      = "${local.image_dir}/build/source/source-manifest.json"
    destination = "/tmp/rtpengine-image-upload/source.json"
  }

  provisioner "file" {
    source      = "${local.image_dir}/assets/"
    destination = "/tmp/rtpengine-image-upload/assets"
  }
  provisioner "file" {
    source      = "${local.image_dir}/provision/"
    destination = "/tmp/rtpengine-image-upload/provision"
  }
  provisioner "file" {
    content     = jsonencode(local.provision_input)
    destination = "/tmp/rtpengine-image-upload/input.json"
  }
  provisioner "shell" {
    inline = [
      "sudo install -d -o root -g root -m 0755 /opt/rtpengine-image-build",
      "sudo cp -a /tmp/rtpengine-image-upload/. /opt/rtpengine-image-build/",
      "sudo chown -R root:root /opt/rtpengine-image-build",
      "sudo chmod -R go-w /opt/rtpengine-image-build",
      "sudo bash /opt/rtpengine-image-build/provision/provision.sh preflight",
      "sudo bash /opt/rtpengine-image-build/provision/provision.sh upgrade"
    ]
  }
  provisioner "shell" {
    expect_disconnect = true
    inline            = ["sudo reboot"]
  }
  provisioner "shell" {
    pause_before = "30s"
    inline = [
      "sudo bash /opt/rtpengine-image-build/provision/provision.sh dependencies",
      "sudo bash /opt/rtpengine-image-build/provision/provision.sh build",
      "sudo bash /opt/rtpengine-image-build/provision/provision.sh install",
      "sudo bash /opt/rtpengine-image-build/provision/provision.sh configure",
      "sudo bash /opt/rtpengine-image-build/provision/provision.sh hold",
      "sudo bash /opt/rtpengine-image-build/provision/provision.sh cleanup",
      "sudo bash /opt/rtpengine-image-build/provision/provision.sh verify",
      "sudo bash /opt/rtpengine-image-build/provision/provision.sh sanitize"
    ]
  }

  post-processor "manifest" {
    output     = "${local.image_dir}/build/packer-manifest.json"
    strip_path = true
    custom_data = {
      ami_name       = local.image_name
      architecture   = local.architecture
      build_id       = var.build_id
      source_ami_id  = var.source_ami_id
      source_commit  = local.source_provenance.commit
      source_sha256  = local.source_provenance.archive_sha256
      source_tree    = local.source_provenance.tree
      source_version = local.source_provenance.version
      ubuntu_release = "26.04"
    }
  }
}
