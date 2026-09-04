import configparser
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[2]
MODULE_PATH = ROOT / "image/ansible/roles/rtpengine_ami/files/smoke.py"
SPEC = importlib.util.spec_from_file_location("rtpengine_smoke", MODULE_PATH)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class BencodeTests(unittest.TestCase):
    def test_round_trip_protocol_dictionary(self):
        value = {
            "command": "offer",
            "flags": ["trust-address", "replace-origin"],
            "sequence": 42,
        }
        decoded = smoke.bdecode(smoke.bencode(value))
        self.assertEqual(decoded[b"command"], b"offer")
        self.assertEqual(decoded[b"sequence"], 42)
        self.assertEqual(decoded[b"flags"][0], b"trust-address")

    def test_known_encoding_is_canonical(self):
        self.assertEqual(
            smoke.bencode({"b": 2, "a": b"x"}),
            b"d1:a1:x1:bi2ee",
        )

    def test_decoder_rejects_malformed_payloads(self):
        for payload in (b"i01e", b"3:ab", b"l", b"d1:bi1e1:ai2ee", b"1:aJ"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                smoke.bdecode(payload)


class SdpTests(unittest.TestCase):
    def test_parses_audio_relay(self):
        sdp = (
            "v=0\r\nc=IN IP4 10.0.0.8\r\n"
            "m=audio 31002 RTP/AVP 0\r\n"
        )
        self.assertEqual(smoke.parse_relay_sdp(sdp), ("10.0.0.8", 31002))

    def test_rejects_port_outside_configured_range(self):
        sdp = "c=IN IP4 10.0.0.8\nm=audio 29998 RTP/AVP 0\n"
        with self.assertRaises(smoke.SmokeError):
            smoke.parse_relay_sdp(sdp)

    def test_endpoint_sdp_contains_bound_socket(self):
        sdp = smoke.endpoint_sdp("10.0.0.9", 45000, 123)
        self.assertIn("c=IN IP4 10.0.0.9", sdp)
        self.assertIn("m=audio 45000 RTP/AVP 0", sdp)


class SystemCheckTests(unittest.TestCase):
    def config(self, **values):
        parser = configparser.ConfigParser()
        parser["rtpengine"] = {
            "table": "0",
            "no-fallback": "true",
            "nftables-family": "ip",
            "nftables-chain": "rtpengine",
            "nftables-base-chain": "INPUT",
            **values,
        }
        return parser["rtpengine"]

    def test_kernel_state_accepts_active_table(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            modules = root / "sys"
            proc.mkdir()
            modules.mkdir()
            (proc / "control").touch()
            (proc / "list").write_text("0\n", encoding="ascii")
            (modules / "nft_rtpengine").mkdir()
            smoke.check_kernel_state(self.config(), proc, modules)

    def test_kernel_state_enforces_no_fallback(self):
        with self.assertRaises(smoke.SmokeError):
            smoke.check_kernel_state(self.config(**{"no-fallback": "false"}))

    def test_kernel_state_accepts_configurable_table(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            modules = root / "sys"
            proc.mkdir()
            modules.mkdir()
            (proc / "control").touch()
            (proc / "list").write_text("42\n", encoding="ascii")
            (modules / "nft_rtpengine").mkdir()
            smoke.check_kernel_state(self.config(table="42"), proc, modules)

    def test_nftables_requires_named_rtpengine_chain(self):
        ruleset = {
            "nftables": [
                {"chain": {"family": "ip", "table": "filter", "name": "INPUT"}},
                {"chain": {"family": "ip", "table": "filter", "name": "rtpengine"}},
                {
                    "rule": {
                        "family": "ip",
                        "table": "filter",
                        "chain": "rtpengine",
                        "expr": [{"rtpengine": {"id": 0}}],
                    }
                },
            ]
        }

        def runner(*_args, **_kwargs):
            return subprocess.CompletedProcess([], 0, json.dumps(ruleset), "")

        smoke.check_nftables(self.config(), runner=runner)


if __name__ == "__main__":
    unittest.main()
