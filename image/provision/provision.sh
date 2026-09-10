#!/usr/bin/env bash
# Image-only provisioning. Never execute on a serving media node.
set -Eeuo pipefail
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
umask 022
root=/opt/rtpengine-image-build
build=/usr/local/src/rtpengine-ami-build
source_directory=$build/source
bootstrap=(build-essential devscripts equivs fakeroot pkgconf)
runtime=(ca-certificates iproute2 kmod nftables python3 file)
test "$EUID" -eq 0 && test "$#" -eq 1
exec 9>/run/rtpengine-image-build.lock
flock -n 9
trap 'printf "RTPengine image phase failed at line %s\n" "$LINENO" >&2' ERR

case "$1" in
    preflight)
        test "$(uname -m)" = aarch64 && test "$(dpkg --print-architecture)" = arm64
        # shellcheck disable=SC1091
        . /etc/os-release
        test "$ID" = ubuntu && test "$VERSION_ID" = 26.04
        python3 "$root/provision/inputs.py" check
        ;;
    upgrade)
        timeout 300 cloud-init status --wait
        timeout 300 apt-get -o Acquire::Retries=3 update
        timeout 1200 apt-get -y dist-upgrade
        ;;
    dependencies)
        timeout 300 cloud-init status --wait
        timeout 900 apt-get install -y --no-install-recommends \
            "${bootstrap[@]}" "${runtime[@]}" "linux-headers-$(uname -r)"
        apt-mark manual "${runtime[@]}" "linux-headers-$(uname -r)"
        ;;
    build)
        python3 "$root/provision/inputs.py" check
        rm -rf -- "$build"
        install -d -m 0755 "$source_directory" "$build/approved"
        tar -xzf "$root/source.tar.gz" --strip-components=1 -C "$source_directory"
        (
            cd "$source_directory/pkg/deb"
            ./generator.sh
            ./backports/resolute
            rm -rf ../../debian
            mv resolute ../../debian
        )
        cd "$source_directory"
        unset DEB_BUILD_PROFILES
        timeout 1200 mk-build-deps --install --remove \
            --tool 'apt-get -o APT::Install-Recommends=false -y' --build-dep debian/control
        export with_transcoding=yes
        timeout 1800 make check
        make clean
        SOURCE_DATE_EPOCH=$(python3 "$root/provision/inputs.py" source_date_epoch)
        export SOURCE_DATE_EPOCH
        DEB_BUILD_OPTIONS="noautodbgsym parallel=$(nproc)" \
            timeout 3600 dpkg-buildpackage --build=binary --no-sign --jobs="$(nproc)"
        expected_version=$(python3 "$root/provision/inputs.py" version)
        for name in rtpengine-daemon rtpengine-utils rtpengine-kernel-dkms; do
            count=0
            for candidate in "$build"/*.deb; do
                if [[ $(dpkg-deb -f "$candidate" Package) == "$name" ]]; then
                    expected_arch=all
                    [[ $name != rtpengine-daemon ]] || expected_arch=arm64
                    [[ $(dpkg-deb -f "$candidate" Architecture) == "$expected_arch" ]]
                    [[ $(dpkg-deb -f "$candidate" Version) == "$expected_version" ]]
                    cp -- "$candidate" "$build/approved/$name.deb"
                    count=$((count + 1))
                fi
            done
            [[ $count == 1 ]]
        done
        ;;
    install)
        policy=/usr/sbin/policy-rc.d
        backup=$root/policy-rc.d.backup
        test ! -L "$policy"
        had_policy=false
        if [[ -e $policy ]]; then cp -p "$policy" "$backup"; had_policy=true; fi
        restore_policy() {
            if "$had_policy"; then cp -p "$backup" "$policy"; else rm -f "$policy"; fi
        }
        trap restore_policy EXIT
        printf '#!/bin/sh\nexit 101\n' > "$policy"
        chmod 0755 "$policy"
        timeout 1200 apt-get install -y --no-install-recommends "$build/approved/"*.deb
        ;;
    configure)
        install -d -m 0755 /usr/share/rtpengine-ami /usr/libexec/rtpengine-ami /etc/rtpengine
        install -m 0644 "$root/source.json" /usr/share/rtpengine-ami/build-source.json
        install -m 0755 "$root/assets/rtpengine-firstboot.py" /usr/libexec/rtpengine-ami/firstboot.py
        install -m 0755 "$root/assets/smoke.py" /usr/libexec/rtpengine-ami/smoke.py
        python3 "$root/provision/inputs.py" configure
        systemctl daemon-reload
        systemctl enable rtpengine-firstboot.service rtpengine-daemon.service
        ;;
    hold)
        kernel=$(uname -r)
        packages=$(dpkg-query -W -f='${binary:Package} ${db:Status-Abbrev}\n' 'linux-*')
        selected=()
        while read -r name status; do
            [[ $status == ii ]] || continue
            name=${name%%:*}
            case "$name" in
                linux-aws|linux-image-aws|linux-headers-aws|linux-*-"$kernel"|linux-headers-"${kernel%-*}")
                    selected+=("$name") ;;
            esac
        done <<< "$packages"
        [[ ${#selected[@]} -gt 0 ]]
        apt-mark hold "${selected[@]}"
        ;;
    cleanup)
        packages=$(dpkg-query -W -f='${binary:Package}\n' '*build-deps*' 2>/dev/null || true)
        for name in $packages; do
            case "$name" in rtpengine-build-deps*) timeout 600 apt-get purge -y "$name" ;; esac
        done
        timeout 600 apt-get purge -y --auto-remove "${bootstrap[@]}"
        apt-get clean
        rm -rf -- "$build" /var/lib/apt/lists/*
        ;;
    verify)
        kernel=$(uname -r)
        module=$(modinfo -n nft_rtpengine)
        case "$module" in /lib/modules/"$kernel"/updates/dkms/nft_rtpengine.ko*) ;; *) exit 1 ;; esac
        [[ $(modinfo -F vermagic nft_rtpengine) == "$kernel "* ]]
        dkms status -m rtpengine -k "$kernel" -a aarch64 | grep -q 'installed$'
        file /usr/bin/rtpengine | grep -q 'ARM aarch64'
        dependencies=$(ldd /usr/bin/rtpengine)
        if grep -q 'not found' <<< "$dependencies"; then exit 1; fi
        modprobe nft_rtpengine
        grep -q '^nft_rtpengine ' /proc/modules
        systemd-analyze verify /etc/systemd/system/rtpengine-firstboot.service
        python3 "$root/provision/inputs.py" manifest
        ;;
    sanitize)
        modprobe -r nft_rtpengine
        cloud-init clean --logs --machine-id
        rm -f /etc/ssh/ssh_host_* /root/.bash_history /home/ubuntu/.bash_history
        find /var/log -type f -exec truncate -s 0 {} +
        rm -rf -- "$root" /tmp/rtpengine-image-upload
        ;;
    *) printf '%s\n' 'unknown image phase' >&2; exit 64 ;;
esac
