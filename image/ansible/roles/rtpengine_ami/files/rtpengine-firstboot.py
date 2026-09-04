#!/usr/bin/python3
"""Render the instance-specific RTPengine configuration on first boot."""

import argparse
import configparser
import grp
import ipaddress
import os
import pathlib
import pwd
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


IMDS = "http://169.254.169.254/latest"
TAG_RE = re.compile(r"^[A-Za-z0-9_.:,=+@-]{1,128}$")
PLACEHOLDER_RE = re.compile(r"@[A-Z][A-Z0-9_]*@")
NFT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
class ConfigurationError(RuntimeError):
    """An installed or instance-provided value is unsafe or incomplete."""


def private_ipv4(value):
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as error:
        raise ConfigurationError("primary address is not valid IPv4") from error
    if (
        address.version != 4
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    ):
        raise ConfigurationError("primary address is not usable unicast IPv4")
    return str(address)


def advertised_ipv4(value):
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as error:
        raise ConfigurationError("advertised address is not valid IPv4") from error
    if (
        address.version != 4
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    ):
        raise ConfigurationError("advertised address is not a usable unicast IPv4")
    return str(address)


def load_defaults(path):
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="ascii") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError, configparser.Error) as error:
        raise ConfigurationError(f"cannot read defaults: {error}") from error
    if set(parser.sections()) != {"runtime"}:
        raise ConfigurationError("defaults must contain only [runtime]")
    allowed = {"advertised_address_tag", "metadata_attempts", "metadata_timeout"}
    unknown = set(parser["runtime"]) - allowed
    if unknown:
        raise ConfigurationError(f"unknown defaults: {', '.join(sorted(unknown))}")
    tag = parser["runtime"].get("advertised_address_tag", "").strip()
    if not TAG_RE.fullmatch(tag) or tag.lower().startswith("aws:"):
        raise ConfigurationError("unsafe advertised-address tag key")
    try:
        attempts = parser["runtime"].getint("metadata_attempts", fallback=6)
        timeout = parser["runtime"].getfloat("metadata_timeout", fallback=2.0)
    except ValueError as error:
        raise ConfigurationError("metadata retry values must be numeric") from error
    if not 1 <= attempts <= 20 or not 0.2 <= timeout <= 10:
        raise ConfigurationError("metadata retry values are outside safe bounds")
    return tag, attempts, timeout


def metadata_request(path, token=None, method="GET", timeout=2.0):
    headers = {}
    if token is not None:
        headers["X-aws-ec2-metadata-token"] = token
    if method == "PUT":
        headers["X-aws-ec2-metadata-token-ttl-seconds"] = "60"
    request = urllib.request.Request(
        f"{IMDS}/{path.lstrip('/')}", headers=headers, method=method
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("ascii").strip()


def fetch_instance_addresses(tag, attempts, timeout, sleep=time.sleep):
    last_error = None
    tag_path = urllib.parse.quote(tag, safe="")
    for attempt in range(attempts):
        try:
            token = metadata_request("api/token", method="PUT", timeout=timeout)
            if not token:
                raise ConfigurationError("IMDSv2 returned an empty token")
            private = metadata_request(
                "meta-data/local-ipv4", token=token, timeout=timeout
            )
            advertised = metadata_request(
                f"meta-data/tags/instance/{tag_path}",
                token=token,
                timeout=timeout,
            )
            return private_ipv4(private), advertised_ipv4(advertised)
        except (
            ConfigurationError,
            OSError,
            UnicodeError,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ) as error:
            last_error = error
            if attempt + 1 < attempts:
                sleep(min(0.5 * (2**attempt), 4.0))
    raise ConfigurationError(
        f"IMDSv2 did not return valid runtime addresses after {attempts} attempts"
    ) from last_error


def render_template(template, values):
    try:
        rendered = pathlib.Path(template).read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"cannot read configuration template: {error}") from error
    for key, value in values.items():
        rendered = rendered.replace(f"@{key}@", value)
    unresolved = sorted(set(PLACEHOLDER_RE.findall(rendered)))
    if unresolved:
        raise ConfigurationError(f"unresolved template values: {', '.join(unresolved)}")
    return rendered


def validate_rendered(content, private, advertised):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(content)
    except configparser.Error as error:
        raise ConfigurationError(f"rendered configuration is invalid: {error}") from error
    if set(parser.sections()) != {"rtpengine"}:
        raise ConfigurationError("rendered configuration must contain only [rtpengine]")
    config = parser["rtpengine"]
    allowed = {
        "table",
        "no-fallback",
        "interface",
        "listen-ng",
        "listen-cli",
        "listen-http",
        "port-min",
        "port-max",
        "nftables-family",
        "nftables-chain",
        "nftables-base-chain",
        "max-sessions",
        "log-level",
        "tos",
    }
    unknown = set(config) - allowed
    if unknown:
        raise ConfigurationError(
            f"rendered configuration has unknown settings: {', '.join(sorted(unknown))}"
        )
    try:
        table = config.getint("table")
        if not config.getboolean("no-fallback"):
            raise ConfigurationError("userspace fallback must be disabled")
        minimum = config.getint("port-min")
        maximum = config.getint("port-max")
        max_sessions = config.getint("max-sessions")
        log_level = config.getint("log-level")
        tos = config.getint("tos")
    except (ValueError, configparser.Error) as error:
        raise ConfigurationError("rendered numeric/boolean value is invalid") from error
    if not 0 <= table <= 63:
        raise ConfigurationError("kernel table is outside 0-63")
    if not 1024 <= minimum <= maximum <= 65535:
        raise ConfigurationError("media port range is invalid")
    if max_sessions < 1 or not 0 <= log_level <= 7 or not 0 <= tos <= 255:
        raise ConfigurationError("session, log, or TOS value is outside safe bounds")
    if config.get("interface") != f"{private}!{advertised}":
        raise ConfigurationError("media interface does not match runtime addresses")
    listener_ports = []
    for name in ("listen-ng", "listen-cli", "listen-http"):
        value = config.get(name, "")
        try:
            host, raw_port = value.rsplit(":", 1)
            port = int(raw_port)
        except ValueError as error:
            raise ConfigurationError(f"{name} is invalid") from error
        if host != private or not 1024 <= port <= 65535:
            raise ConfigurationError(f"{name} is not a private listener")
        listener_ports.append(port)
    if len(set(listener_ports)) != len(listener_ports):
        raise ConfigurationError("control listener ports must be distinct")
    if config.get("nftables-family") not in {"ip", "ip6", "ip,ip6", "inet"}:
        raise ConfigurationError("nftables family is invalid")
    for name in ("nftables-chain", "nftables-base-chain"):
        if not NFT_NAME_RE.fullmatch(config.get(name, "")):
            raise ConfigurationError(f"{name} is invalid")


def atomic_install(path, content, user="root", group="rtpengine", mode=0o640):
    destination = pathlib.Path(path)
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    try:
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid
    except KeyError as error:
        raise ConfigurationError(f"required account is missing: {error}") from error
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), uid, gid)
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def permit_daemon():
    subprocess.run(
        ["systemctl", "unmask", "rtpengine-daemon.service"], check=True
    )
    subprocess.run(
        ["systemctl", "enable", "rtpengine-daemon.service"], check=True
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--defaults", default="/etc/rtpengine/ami-runtime.ini"
    )
    parser.add_argument(
        "--template", default="/usr/share/rtpengine-ami/rtpengine.conf.template"
    )
    parser.add_argument("--output", default="/etc/rtpengine/rtpengine.conf")
    parser.add_argument("--render-only", action="store_true")
    arguments = parser.parse_args(argv)

    tag, attempts, timeout = load_defaults(arguments.defaults)
    private, advertised = fetch_instance_addresses(tag, attempts, timeout)
    content = render_template(
        arguments.template,
        {"PRIVATE_IPV4": private, "ADVERTISED_IPV4": advertised},
    )
    validate_rendered(content, private, advertised)
    atomic_install(arguments.output, content)
    if not arguments.render_only:
        permit_daemon()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigurationError as error:
        raise SystemExit(f"rtpengine first boot: {error}") from error
