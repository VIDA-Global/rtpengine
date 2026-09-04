variable "aws_region" {
  type        = string
  description = "The single AWS region in which the AMI is built."
  default     = "us-east-2"

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]+$", var.aws_region))
    error_message = "The aws_region value must be a valid commercial AWS region name."
  }
}

variable "source_ami_id" {
  type        = string
  description = "Required, explicitly approved Ubuntu 26.04 ARM64 source AMI ID."

  validation {
    condition     = can(regex("^ami-[0-9a-f]{17}$", var.source_ami_id))
    error_message = "The source_ami_id value must be a full EC2 AMI ID."
  }
}

variable "instance_type" {
  type        = string
  description = "ARM64 instance type used for the native package and DKMS build."
  default     = "m9g.large"
}

variable "vpc_id" {
  type        = string
  description = "Required existing VPC for the temporary builder."

  validation {
    condition     = can(regex("^vpc-[0-9a-f]{8,17}$", var.vpc_id))
    error_message = "The vpc_id value must be an EC2 VPC ID."
  }
}

variable "subnet_id" {
  type        = string
  description = "Required existing subnet that can assign the builder a public IPv4 address."

  validation {
    condition     = can(regex("^subnet-[0-9a-f]{8,17}$", var.subnet_id))
    error_message = "The subnet_id value must be an EC2 subnet ID."
  }
}

variable "security_group_ids" {
  type        = list(string)
  description = "Required existing security groups permitting public-IP SSH from Packer."

  validation {
    condition     = length(var.security_group_ids) > 0 && alltrue([for id in var.security_group_ids : can(regex("^sg-[0-9a-f]{8,17}$", id))])
    error_message = "The security_group_ids value must contain at least one EC2 security group ID."
  }
}

variable "build_instance_profile" {
  type        = string
  description = "Optional existing IAM instance profile for the temporary builder."
  default     = ""
}

variable "kms_key_id" {
  type        = string
  description = "Required customer-managed KMS key ARN, alias, or key ID for the AMI snapshot."

  validation {
    condition     = length(trimspace(var.kms_key_id)) > 0
    error_message = "The kms_key_id value is required; the default AWS-managed EBS key is not accepted."
  }
}

variable "ssh_username" {
  type        = string
  description = "SSH user supplied by the approved Ubuntu source AMI."
  default     = "ubuntu"
}

variable "ssh_timeout" {
  type        = string
  description = "Maximum wait for public-IP SSH connectivity."
  default     = "20m"
}

variable "root_device_name" {
  type        = string
  description = "Root device name used by the approved source AMI."
  default     = "/dev/sda1"
}

variable "root_volume_size" {
  type        = number
  description = "Encrypted GP3 root volume size in GiB."
  default     = 24

  validation {
    condition     = var.root_volume_size >= 16
    error_message = "The root_volume_size value must be at least 16 GiB."
  }
}

variable "root_volume_iops" {
  type        = number
  description = "Root GP3 volume provisioned IOPS."
  default     = 3000
}

variable "root_volume_throughput" {
  type        = number
  description = "Root GP3 volume throughput in MiB/s."
  default     = 125
}

variable "ami_name_prefix" {
  type        = string
  description = "Immutable AMI family/name prefix."
  default     = "rtpengine"

  validation {
    condition     = can(regex("^[A-Za-z0-9() ./_-]{3,64}$", var.ami_name_prefix))
    error_message = "The ami_name_prefix value contains characters unsupported by EC2 AMI names."
  }
}

variable "application_name" {
  type        = string
  description = "Application identity tag."
  default     = "rtpengine"
}

variable "environment" {
  type        = string
  description = "Environment identity tag for image-build resources."
  default     = "image-build"
}

variable "build_id" {
  type        = string
  description = "Unique immutable build identity, normally a CI run ID and attempt."
  default     = "local"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+$", var.build_id))
    error_message = "The build_id value may contain only letters, digits, dots, underscores, and hyphens."
  }
}

variable "additional_tags" {
  type        = map(string)
  description = "Additional organization tags. Reserved immutable identity tags win on collision."
  default     = {}
}

variable "rtpengine_advertised_address_tag" {
  type        = string
  description = "EC2 instance tag read at boot for the advertised media address."
  default     = "RtpEngineAdvertisedAddress"

  validation {
    condition     = can(regex("^[A-Za-z0-9+=._:/@-]{1,128}$", var.rtpengine_advertised_address_tag))
    error_message = "The rtpengine_advertised_address_tag value is not a valid EC2 tag key."
  }
}

variable "rtpengine_table" {
  type        = number
  description = "Kernel forwarding table ID."
  default     = 0

  validation {
    condition     = var.rtpengine_table == floor(var.rtpengine_table) && var.rtpengine_table >= 0 && var.rtpengine_table <= 63
    error_message = "The rtpengine_table value must be an integer from 0 through 63."
  }
}

variable "rtpengine_no_fallback" {
  type        = bool
  description = "Require kernel forwarding. Production images intentionally cannot enable userspace fallback."
  default     = true

  validation {
    condition     = var.rtpengine_no_fallback
    error_message = "The rtpengine_no_fallback value must remain true for this production image."
  }
}

variable "rtpengine_media_port_min" {
  type        = number
  description = "First UDP media port."
  default     = 30000

  validation {
    condition     = var.rtpengine_media_port_min == floor(var.rtpengine_media_port_min) && var.rtpengine_media_port_min >= 1024 && var.rtpengine_media_port_min <= 65535
    error_message = "The rtpengine_media_port_min value must be an integer from 1024 through 65535."
  }
}

variable "rtpengine_media_port_max" {
  type        = number
  description = "Last UDP media port."
  default     = 39999

  validation {
    condition     = var.rtpengine_media_port_max == floor(var.rtpengine_media_port_max) && var.rtpengine_media_port_max >= 1024 && var.rtpengine_media_port_max <= 65535
    error_message = "The rtpengine_media_port_max value must be an integer from 1024 through 65535."
  }
}

variable "rtpengine_ng_port" {
  type        = number
  description = "Private-address NG control port."
  default     = 2223

  validation {
    condition     = var.rtpengine_ng_port == floor(var.rtpengine_ng_port) && var.rtpengine_ng_port >= 1024 && var.rtpengine_ng_port <= 65535
    error_message = "The rtpengine_ng_port value must be an integer from 1024 through 65535."
  }
}

variable "rtpengine_cli_port" {
  type        = number
  description = "Private-address CLI control port."
  default     = 2224

  validation {
    condition     = var.rtpengine_cli_port == floor(var.rtpengine_cli_port) && var.rtpengine_cli_port >= 1024 && var.rtpengine_cli_port <= 65535
    error_message = "The rtpengine_cli_port value must be an integer from 1024 through 65535."
  }
}

variable "rtpengine_http_port" {
  type        = number
  description = "Private-address HTTP telemetry port."
  default     = 2225

  validation {
    condition     = var.rtpengine_http_port == floor(var.rtpengine_http_port) && var.rtpengine_http_port >= 1024 && var.rtpengine_http_port <= 65535
    error_message = "The rtpengine_http_port value must be an integer from 1024 through 65535."
  }
}

variable "rtpengine_nftables_family" {
  type        = string
  description = "nftables address family."
  default     = "ip"

  validation {
    condition     = contains(["ip", "ip6", "ip,ip6", "inet"], var.rtpengine_nftables_family)
    error_message = "The rtpengine_nftables_family value must be ip, ip6, ip,ip6, or inet."
  }
}

variable "rtpengine_nftables_chain" {
  type        = string
  description = "Dedicated nftables chain managed by RTPengine."
  default     = "rtpengine"

  validation {
    condition     = can(regex("^[A-Za-z_][A-Za-z0-9_]{0,31}$", var.rtpengine_nftables_chain))
    error_message = "The rtpengine_nftables_chain value must be a safe nftables identifier."
  }
}

variable "rtpengine_nftables_base_chain" {
  type        = string
  description = "Existing nftables base chain receiving the RTPengine jump."
  default     = "INPUT"

  validation {
    condition     = can(regex("^[A-Za-z_][A-Za-z0-9_]{0,31}$", var.rtpengine_nftables_base_chain))
    error_message = "The rtpengine_nftables_base_chain value must be a safe nftables identifier."
  }
}

variable "rtpengine_max_sessions" {
  type        = number
  description = "Maximum admitted media sessions."
  default     = 5000

  validation {
    condition     = var.rtpengine_max_sessions == floor(var.rtpengine_max_sessions) && var.rtpengine_max_sessions > 0
    error_message = "The rtpengine_max_sessions value must be a positive integer."
  }
}

variable "rtpengine_log_level" {
  type        = number
  description = "RTPengine syslog-compatible log level (0-7)."
  default     = 6

  validation {
    condition     = var.rtpengine_log_level == floor(var.rtpengine_log_level) && var.rtpengine_log_level >= 0 && var.rtpengine_log_level <= 7
    error_message = "The rtpengine_log_level value must be an integer from 0 through 7."
  }
}

variable "rtpengine_tos" {
  type        = number
  description = "IPv4 TOS/DSCP byte applied to relayed media."
  default     = 184

  validation {
    condition     = var.rtpengine_tos == floor(var.rtpengine_tos) && var.rtpengine_tos >= 0 && var.rtpengine_tos <= 255
    error_message = "The rtpengine_tos value must be an integer from 0 through 255."
  }
}

variable "rtpengine_metadata_attempts" {
  type        = number
  description = "Maximum IMDSv2 attempts made by the first-boot renderer."
  default     = 12

  validation {
    condition     = var.rtpengine_metadata_attempts == floor(var.rtpengine_metadata_attempts) && var.rtpengine_metadata_attempts >= 1 && var.rtpengine_metadata_attempts <= 20
    error_message = "The rtpengine_metadata_attempts value must be an integer from 1 through 20."
  }
}

variable "rtpengine_metadata_timeout" {
  type        = number
  description = "Timeout in seconds for each IMDSv2 request."
  default     = 2

  validation {
    condition     = var.rtpengine_metadata_timeout >= 0.2 && var.rtpengine_metadata_timeout <= 10
    error_message = "The rtpengine_metadata_timeout value must be from 0.2 through 10 seconds."
  }
}

variable "rtpengine_firstboot_timeout" {
  type        = number
  description = "Systemd timeout in seconds for first-boot configuration."
  default     = 120

  validation {
    condition     = var.rtpengine_firstboot_timeout == floor(var.rtpengine_firstboot_timeout) && var.rtpengine_firstboot_timeout >= 30 && var.rtpengine_firstboot_timeout <= 600
    error_message = "The rtpengine_firstboot_timeout value must be an integer from 30 through 600 seconds."
  }
}
