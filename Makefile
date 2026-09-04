.DEFAULT_GOAL := all

ifeq (,$(filter pkg.ngcp-rtpengine.no-transcoding pkg.rtpengine.no-transcoding,$(DEB_BUILD_PROFILES)))
with_transcoding ?= yes
else
with_transcoding ?= no
endif

export with_transcoding

export top_srcdir = $(CURDIR)

ifneq ($(strip $(MAKECMDGOALS)),)
ifeq ($(strip $(filter-out ami-%,$(MAKECMDGOALS))),)
ami_only_goals := yes
endif
endif

# Initialize all flags, so that we only compute them once.
ifneq ($(ami_only_goals),yes)
include lib/deps.Makefile

include lib/lib.Makefile
endif

.PHONY:	all distclean clean coverity
.PHONY:	ami-help ami-init ami-fmt ami-validate ami-lint ami-test ami-inspect
.PHONY:	ami-build ami-validate-ami ami-clean

ami-help:
	$(MAKE) -C image help

ami-init:
	$(MAKE) -C image init

ami-fmt:
	$(MAKE) -C image fmt

ami-validate:
	$(MAKE) -C image validate

ami-lint:
	$(MAKE) -C image lint

ami-test:
	$(MAKE) -C image test

ami-inspect:
	$(MAKE) -C image inspect

ami-build:
	$(MAKE) -C image build

ami-validate-ami:
	$(MAKE) -C image validate-ami

ami-clean:
	$(MAKE) -C image clean

all:
	$(MAKE) -C daemon
ifeq ($(with_transcoding),yes)
	$(MAKE) -C recording-daemon
	$(MAKE) -C perf-tester
endif
ifneq (,$(filter pkg.ngcp-rtpengine.pysip-lite pkg.rtpengine.pysip-lite,$(DEB_BUILD_PROFILES)))
	$(MAKE) -C python
endif

install:
	$(MAKE) -C daemon install
ifeq ($(with_transcoding),yes)
	$(MAKE) -C recording-daemon install
	$(MAKE) -C perf-tester install
endif
	mkdir -p $(DESTDIR)/usr/libexec/rtpengine/ $(DESTDIR)/usr/bin $(DESTDIR)/usr/share/man/man1
	install -m 0755 utils/rtpengine-get-table $(DESTDIR)/usr/libexec/rtpengine/
	install -m 0755 utils/rtpengine-ctl utils/rtpengine-ng-client $(DESTDIR)/usr/bin/
	install -m 0644 utils/rtpengine-ctl.1 utils/rtpengine-ng-client.1 $(DESTDIR)/usr/share/man/man1

coverity:
	$(MAKE) -C daemon
ifeq ($(with_transcoding),yes)
	$(MAKE) -C recording-daemon
	$(MAKE) -C perf-tester
endif

.PHONY: with-kernel

with-kernel: all
	$(MAKE) -C kernel-module

install-with-kernel: all install
	$(MAKE) -C kernel-module install

distclean clean:
	$(MAKE) -C daemon clean
	$(MAKE) -C recording-daemon clean
	$(MAKE) -C perf-tester clean
	$(MAKE) -C kernel-module clean
	$(MAKE) -C t clean
	$(MAKE) -C lib clean
	$(MAKE) -C python clean
	rm -f config.mk

.DEFAULT:
	$(MAKE) -C daemon $@
	$(MAKE) -C recording-daemon $@
	$(MAKE) -C perf-tester
	$(MAKE) -C kernel-module $@

.PHONY: check asan-check asan

check: all
	$(MAKE) -C t

asan-check:
	DO_ASAN_FLAGS=1 $(MAKE) check

asan:
	DO_ASAN_FLAGS=1 $(MAKE)
