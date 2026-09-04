SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c

PACKER_DIR := packer
ANSIBLE_DIR := ansible
BUILD_DIR := build
VARS_FILE ?= packer/rtpengine.pkrvars.hcl
VARS_FILE_ABS = $(abspath $(VARS_FILE))

.DEFAULT_GOAL := help

.PHONY: help init source fmt check validate lint test inspect build validate-ami clean

help:
	@printf '%s\n' \
	  'RTPengine Ubuntu 26.04 ARM64 AMI targets:' \
	  '  make source                       Create deterministic committed-HEAD inputs (AWS-free)' \
	  '  make init                         Install pinned Packer plugins/Ansible collections (AWS-free)' \
	  '  make fmt                          Format Packer files (AWS-free)' \
	  '  make check                        Run local syntax and format checks (AWS-free)' \
	  '  make validate                     Validate Packer with non-secret placeholders (AWS-free)' \
	  '  make lint                         Run shell, YAML, and Ansible linters (AWS-free)' \
	  '  make test                         Run image unit and contract tests (AWS-free)' \
	  '  make inspect                      Inspect the Packer template (AWS-free)' \
	  '  make build VARS_FILE=<file>       Create an AMI and EBS snapshot (MUTATES AWS)' \
	  '  make validate-ami                 Launch and test an AMI (MUTATES AWS)' \
	  '  make clean                        Remove generated local source/build metadata (AWS-free)'

init:
	packer init $(PACKER_DIR)
	ansible-galaxy collection install -r $(ANSIBLE_DIR)/requirements.yml

source:
	./scripts/prepare-source.sh

fmt:
	packer fmt -recursive $(PACKER_DIR)

check: source
	./scripts/validate-local.sh

validate: check
	packer validate \
	  -var='source_ami_id=ami-00000000000000000' \
	  -var='vpc_id=vpc-00000000000000000' \
	  -var='subnet_id=subnet-00000000000000000' \
	  -var='security_group_ids=["sg-00000000000000000"]' \
	  -var='kms_key_id=00000000-0000-0000-0000-000000000000' \
	  $(PACKER_DIR)

lint:
	./scripts/lint.sh

test:
	python3 -m unittest discover -s tests -p 'test_*.py'

inspect: source
	packer inspect $(PACKER_DIR)

build: source
	@test -f "$(VARS_FILE)" || { printf 'missing VARS_FILE: %s\n' "$(VARS_FILE)" >&2; exit 64; }
	@printf 'WARNING: packer build will create billable AWS resources and an AMI.\n' >&2
	packer build -var-file="$(VARS_FILE_ABS)" $(PACKER_DIR)

validate-ami:
	./scripts/validate-ami.sh --run

clean:
	rm -rf -- "$(BUILD_DIR)"
