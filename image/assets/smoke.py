#!/usr/bin/python3
"""Local forwarding smoke check for an RTPengine AMI instance, not HA evidence."""

import argparse
import configparser
import json
import os
import pathlib
import random
import re
import select
import socket
import struct
import subprocess
import time


class SmokeError(RuntimeError):
    """A smoke-test assertion or protocol operation failed."""


def bencode(value):
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, str):
        return bencode(value.encode("utf-8"))
    if isinstance(value, bool):
        raise TypeError("booleans are not valid bencode integers")
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, (list, tuple)):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        items = []
        if not all(isinstance(key, (bytes, str)) for key in value):
            raise TypeError("bencode dictionary keys must be strings")
        for key in sorted(value, key=lambda item: item.encode() if isinstance(item, str) else item):
            items.extend((bencode(key), bencode(value[key])))
        return b"d" + b"".join(items) + b"e"
    raise TypeError(f"cannot bencode {type(value).__name__}")


def bdecode(payload, max_depth=32):
    if not isinstance(payload, bytes):
        raise TypeError("bencoded payload must be bytes")

    def parse(position, depth):
        if depth > max_depth or position >= len(payload):
            raise ValueError("invalid or excessively nested bencode")
        token = payload[position : position + 1]
        if token == b"i":
            end = payload.find(b"e", position + 1)
            if end < 0:
                raise ValueError("unterminated integer")
            raw = payload[position + 1 : end]
            if not re.fullmatch(rb"0|-?[1-9][0-9]*", raw):
                raise ValueError("invalid integer")
            try:
                return int(raw), end + 1
            except ValueError as error:
                raise ValueError("invalid integer") from error
        if token in (b"l", b"d"):
            result = [] if token == b"l" else {}
            position += 1
            previous = None
            while position < len(payload) and payload[position : position + 1] != b"e":
                item, position = parse(position, depth + 1)
                if token == b"l":
                    result.append(item)
                    continue
                if not isinstance(item, bytes):
                    raise ValueError("dictionary key is not a byte string")
                if previous is not None and item <= previous:
                    raise ValueError("dictionary keys are not strictly sorted")
                previous = item
                value, position = parse(position, depth + 1)
                result[item] = value
            if position >= len(payload):
                raise ValueError("unterminated collection")
            return result, position + 1
        if b"0" <= token <= b"9":
            colon = payload.find(b":", position)
            if colon < 0:
                raise ValueError("byte string has no length separator")
            raw_length = payload[position:colon]
            if not raw_length or (raw_length.startswith(b"0") and raw_length != b"0"):
                raise ValueError("invalid byte string length")
            try:
                length = int(raw_length)
            except ValueError as error:
                raise ValueError("invalid byte string length") from error
            start, end = colon + 1, colon + 1 + length
            if end > len(payload):
                raise ValueError("truncated byte string")
            return payload[start:end], end
        raise ValueError("unknown bencode token")

    value, consumed = parse(0, 0)
    if consumed != len(payload):
        raise ValueError("trailing bencode data")
    return value


def response_text(response, key):
    value = response.get(key.encode())
    if not isinstance(value, bytes):
        raise SmokeError(f"NG response has no string {key!r}")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SmokeError(f"NG response {key!r} is not UTF-8") from error


class NGClient:
    def __init__(self, host, port=2223, timeout=3.0):
        self.address = (host, port)
        self.timeout = timeout

    def request(self, command):
        cookie = f"smoke-{random.getrandbits(64):016x}".encode()
        packet = cookie + b" " + bencode(command)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
            control.settimeout(self.timeout)
            control.sendto(packet, self.address)
            try:
                response, source = control.recvfrom(65535)
            except socket.timeout as error:
                raise SmokeError(f"NG request timed out: {command.get('command')}") from error
        if source[0] != self.address[0] or not response.startswith(cookie + b" "):
            raise SmokeError("NG response source or cookie did not match request")
        try:
            decoded = bdecode(response[len(cookie) + 1 :])
        except ValueError as error:
            raise SmokeError(f"invalid NG response: {error}") from error
        if not isinstance(decoded, dict):
            raise SmokeError("NG response is not a dictionary")
        result = response_text(decoded, "result")
        if result not in ("ok", "pong"):
            reason = decoded.get(b"error-reason", b"unspecified error")
            raise SmokeError(f"NG request failed: {reason!r}")
        return decoded


def parse_relay_sdp(sdp, minimum=30000, maximum=39999):
    connection = None
    port = None
    for raw_line in sdp.replace("\r", "").split("\n"):
        fields = raw_line.strip().split()
        if len(fields) == 3 and fields[:2] == ["c=IN", "IP4"]:
            connection = fields[2]
        if fields and fields[0] == "m=audio" and len(fields) >= 2:
            try:
                port = int(fields[1])
            except ValueError as error:
                raise SmokeError("relay SDP has a non-numeric audio port") from error
    if connection is None or port is None:
        raise SmokeError("relay SDP is missing IPv4 connection or audio port")
    try:
        socket.inet_aton(connection)
    except OSError as error:
        raise SmokeError("relay SDP has an invalid IPv4 address") from error
    if not minimum <= port <= maximum:
        raise SmokeError(f"relay port {port} is outside {minimum}-{maximum}")
    return connection, port


def endpoint_sdp(host, port, session_id):
    return "\r\n".join(
        (
            "v=0",
            f"o=smoke {session_id} 1 IN IP4 {host}",
            "s=rtpengine-ami-smoke",
            f"c=IN IP4 {host}",
            "t=0 0",
            f"m=audio {port} RTP/AVP 0",
            "a=rtpmap:0 PCMU/8000",
            "a=sendrecv",
            "",
        )
    )


def read_runtime_config(path):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with open(path, encoding="ascii") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError, configparser.Error) as error:
        raise SmokeError(f"cannot read runtime configuration: {error}") from error
    if "rtpengine" not in parser:
        raise SmokeError("runtime configuration has no [rtpengine] section")
    return parser["rtpengine"]


def check_kernel_state(config, proc_root="/proc/rtpengine", sys_root="/sys/module"):
    table = config.getint("table", fallback=-1)
    if not 0 <= table <= 63:
        raise SmokeError("kernel forwarding table is outside 0-63")
    if not config.getboolean("no-fallback", fallback=False):
        raise SmokeError("userspace fallback is not disabled")
    if not pathlib.Path(sys_root, "nft_rtpengine").is_dir():
        raise SmokeError("nft_rtpengine kernel module is not loaded")
    proc = pathlib.Path(proc_root)
    if not (proc / "control").exists() or not (proc / "list").exists():
        raise SmokeError("rtpengine proc control files are unavailable")
    active = (proc / "list").read_text(encoding="ascii").split()
    table_name = str(table)
    if table_name not in active and not (proc / table_name).is_dir():
        raise SmokeError(f"rtpengine kernel table {table} is not active")


def check_nftables(config, runner=subprocess.run):
    family = config.get("nftables-family", "").strip()
    nft_table = "filter"
    chain = config.get("nftables-chain", "").strip()
    base_chain = config.get("nftables-base-chain", "").strip()
    if (
        family not in {"ip", "ip6", "ip,ip6", "inet"}
        or not chain
        or not base_chain
    ):
        raise SmokeError("nftables configuration is incomplete")
    result = runner(
        ["nft", "--json", "list", "ruleset"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
    )
    try:
        ruleset = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SmokeError("nft returned invalid JSON") from error
    configured_families = {family} if family != "ip,ip6" else {"ip", "ip6"}
    chains = {
        (
            entry["chain"].get("family"),
            entry["chain"].get("table"),
            entry["chain"].get("name"),
        )
        for entry in ruleset.get("nftables", [])
        if isinstance(entry, dict) and isinstance(entry.get("chain"), dict)
    }

    def has_rtpengine_expression(value):
        if isinstance(value, dict):
            return "rtpengine" in value or any(
                has_rtpengine_expression(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(has_rtpengine_expression(item) for item in value)
        return False

    if not all((item, nft_table, chain) in chains for item in configured_families):
        raise SmokeError(f"nftables RTPengine chain {chain!r} is not active")
    if not all((item, nft_table, base_chain) in chains for item in configured_families):
        raise SmokeError(f"nftables base chain {base_chain!r} is not active")
    matching_rules = [
        entry["rule"]
        for entry in ruleset.get("nftables", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("rule"), dict)
        and entry["rule"].get("family") in configured_families
        and entry["rule"].get("table") == nft_table
        and entry["rule"].get("chain") == chain
    ]
    if not any(has_rtpengine_expression(rule.get("expr")) for rule in matching_rules):
        raise SmokeError("configured nftables chain has no RTPengine expression")


def rtp_packet(marker, sequence):
    payload = marker.encode("ascii")
    return struct.pack("!BBHII", 0x80, 0, sequence, sequence * 160, 0x52545045) + payload


def relay_once(sender, relay, receiver, marker, timeout):
    packet = rtp_packet(marker, random.randrange(1, 65535))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sender.sendto(packet, relay)
        readable, _, _ = select.select([receiver], [], [], min(0.2, deadline - time.monotonic()))
        if readable:
            received, _ = receiver.recvfrom(65535)
            if received == packet:
                return
    raise SmokeError(f"timed out relaying identifiable {marker} packet")


def run_media_smoke(host, advertised, port, timeout, minimum, maximum):
    client = NGClient(host, port, timeout)
    if response_text(client.request({"command": "ping"}), "result") != "pong":
        raise SmokeError("NG ping did not return pong")
    call_id = f"ami-smoke-{os.getpid()}-{random.getrandbits(32):08x}"
    from_tag, to_tag = "smoke-a", "smoke-b"
    common_flags = ["trust-address", "replace-origin", "replace-session-connection"]
    left = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    right = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    left.bind((host, 0))
    right.bind((host, 0))
    session_id = random.randrange(1, 2**31)
    created = False
    try:
        offer = client.request(
            {
                "command": "offer",
                "call-id": call_id,
                "from-tag": from_tag,
                "sdp": endpoint_sdp(host, left.getsockname()[1], session_id),
                "flags": common_flags,
            }
        )
        created = True
        offer_address, right_relay_port = parse_relay_sdp(
            response_text(offer, "sdp"), minimum, maximum
        )
        if offer_address != advertised:
            raise SmokeError("offer SDP does not contain the advertised address")
        answer = client.request(
            {
                "command": "answer",
                "call-id": call_id,
                "from-tag": from_tag,
                "to-tag": to_tag,
                "sdp": endpoint_sdp(host, right.getsockname()[1], session_id + 1),
                "flags": common_flags,
            }
        )
        answer_address, left_relay_port = parse_relay_sdp(
            response_text(answer, "sdp"), minimum, maximum
        )
        if answer_address != advertised:
            raise SmokeError("answer SDP does not contain the advertised address")
        relay_once(left, (host, left_relay_port), right, "LEFT-TO-RIGHT", timeout)
        relay_once(right, (host, right_relay_port), left, "RIGHT-TO-LEFT", timeout)
    finally:
        if created:
            try:
                client.request(
                    {
                        "command": "delete",
                        "call-id": call_id,
                        "from-tag": from_tag,
                        "to-tag": to_tag,
                    }
                )
            except SmokeError:
                pass
        left.close()
        right.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="private IPv4 listener")
    parser.add_argument("--port", type=int, default=2223)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--config", default="/etc/rtpengine/rtpengine.conf")
    parser.add_argument("--port-min", type=int, default=30000)
    parser.add_argument("--port-max", type=int, default=39999)
    parser.add_argument("--skip-system-checks", action="store_true")
    arguments = parser.parse_args(argv)
    config = read_runtime_config(arguments.config)
    try:
        external, internal = config.get("interface", "").split(";")
        if not external.startswith("external/"):
            raise ValueError("external interface missing")
        private, advertised = external.removeprefix("external/").split("!", 1)
        if internal != "internal/" + private:
            raise ValueError("internal interface mismatch")
    except ValueError as error:
        raise SmokeError("media interface has no advertised address") from error
    if private != arguments.host:
        raise SmokeError("media interface does not use the requested private host")
    expected_listener = f"{arguments.host}:{arguments.port}"
    if config.get("listen-ng") != expected_listener:
        raise SmokeError(f"NG listener is not configured as {expected_listener}")
    if not arguments.skip_system_checks:
        check_kernel_state(config)
        check_nftables(config)
    run_media_smoke(
        arguments.host,
        advertised,
        arguments.port,
        arguments.timeout,
        arguments.port_min,
        arguments.port_max,
    )
    print("RTPengine AMI smoke test passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SmokeError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f"rtpengine smoke: {error}") from error
