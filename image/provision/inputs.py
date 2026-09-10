"""Closed-schema build inputs and deterministic rendering without a template engine."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

NUMBERS = {
    "table": (0, 63), "media_port_min": (1024, 65535), "media_port_max": (1024, 65535),
    "ng_port": (1024, 65535), "cli_port": (1024, 65535), "http_port": (1024, 65535),
    "max_sessions": (1, 1000000), "log_level": (0, 7), "tos": (0, 255),
    "metadata_attempts": (1, 20), "metadata_timeout": (0.2, 10), "firstboot_timeout": (1, 900),
}
PATTERNS = {
    "version": r"[0-9A-Za-z.+:~_-]{1,128}", "source_commit": r"[a-f0-9]{40}",
    "source_tree": r"[a-f0-9]{40}", "source_sha256": r"[a-f0-9]{64}",
    "source_timestamp": r"[0-9T:+Z -]{1,64}",
    "advertised_address_tag": r"[A-Za-z0-9_.:,=+@-]{1,128}",
    "nftables_family": r"ip|ip6|ip,ip6|inet",
    "nftables_chain": r"[A-Za-z_][A-Za-z0-9_]{0,31}",
    "nftables_base_chain": r"[A-Za-z_][A-Za-z0-9_]{0,31}",
}
PREFIX = "rtpengine_ami_"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate image input")
        result[key] = value
    return result


def validate(raw):
    expected = {PREFIX + key for key in (*NUMBERS, *PATTERNS, "no_fallback")}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("unexpected image input fields")
    values = {key.removeprefix(PREFIX): value for key, value in raw.items()}
    for key, (minimum, maximum) in NUMBERS.items():
        value = values[key]
        if type(value) not in (int, float) or not minimum <= value <= maximum:
            raise ValueError("numeric image input out of range")
        if key != "metadata_timeout" and int(value) != value:
            raise ValueError("image input must be integral")
    for key, pattern in PATTERNS.items():
        if not isinstance(values[key], str) or re.fullmatch(pattern, values[key]) is None:
            raise ValueError("unsafe text image input")
    if values["no_fallback"] is not True:
        raise ValueError("kernel forwarding is required")
    if values["media_port_min"] >= values["media_port_max"]:
        raise ValueError("invalid media range")
    if len({values[key] for key in ("ng_port", "cli_port", "http_port")}) != 3:
        raise ValueError("duplicate listener port")
    if values["advertised_address_tag"].lower().startswith("aws:"):
        raise ValueError("reserved metadata tag")
    return raw


def render(template, values):
    def replace(match):
        key = match.group(1)
        if key not in values:
            raise ValueError("unknown template input")
        value = values[key]
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)
    result = re.sub(r"{{\s*([a-z_]+)\s*}}", replace, template)
    if "{{" in result or "}}" in result:
        raise ValueError("unresolved template expression")
    return result


def output(*args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def main():
    root = Path(__file__).resolve().parent.parent
    values = validate(json.loads((root / "input.json").read_text(), object_pairs_hook=unique_object))
    provenance = json.loads((root / "source.json").read_text(), object_pairs_hook=unique_object)
    operation = sys.argv[1]
    if operation == "check":
        for key, source_key in (("source_commit", "commit"), ("source_tree", "tree"),
                                ("source_sha256", "archive_sha256"), ("version", "version"),
                                ("source_timestamp", "commit_timestamp")):
            if values[PREFIX + key] != provenance[source_key]:
                raise ValueError("source provenance mismatch")
        if hashlib.sha256((root / "source.tar.gz").read_bytes()).hexdigest() != provenance["archive_sha256"]:
            raise ValueError("source archive checksum mismatch")
    elif operation in ("version", "source_date_epoch"):
        value = provenance[operation]
        if operation == "source_date_epoch" and (type(value) is not int or value < 0):
            raise ValueError("invalid source date epoch")
        print(value)
    elif operation == "configure":
        for name, destination in (
            ("rtpengine.conf.template", "/usr/share/rtpengine-ami/rtpengine.conf.template"),
            ("ami-runtime.ini.template", "/etc/rtpengine/ami-runtime.ini"),
            ("rtpengine-firstboot.service.template", "/etc/systemd/system/rtpengine-firstboot.service"),
        ):
            target = Path(destination)
            target.write_text(render((root / "assets" / name).read_text(), values))
            target.chmod(0o644)
    elif operation == "manifest":
        names = ("rtpengine-daemon", "rtpengine-utils", "rtpengine-kernel-dkms")
        packages = dict(line.split("=", 1) for line in output(
            "dpkg-query", "-W", "-f=${Package}=${Version}\\n", *names).splitlines())
        if set(packages) != set(names) or set(packages.values()) != {provenance["version"]}:
            raise ValueError("installed package version mismatch")
        manifest = {
            "architecture": "arm64", "distribution": "Ubuntu 26.04",
            "kernel": output("uname", "-r"), "module_path": output("modinfo", "-n", "nft_rtpengine"),
            "module_vermagic": output("modinfo", "-F", "vermagic", "nft_rtpengine"),
            "dkms_module": output("dkms", "status", "-m", "rtpengine"), "packages": packages,
            "source_commit": provenance["commit"], "source_tree": provenance["tree"],
            "source_version": provenance["version"], "source_archive_sha256": provenance["archive_sha256"],
            "source_commit_timestamp": provenance["commit_timestamp"],
        }
        Path("/usr/share/rtpengine-ami/source-manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    else:
        raise ValueError("unknown image operation")


if __name__ == "__main__":
    main()
