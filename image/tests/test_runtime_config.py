import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).parents[2]
MODULE_PATH = (
    ROOT
    / "image/ansible/roles/rtpengine_ami/files/rtpengine-firstboot.py"
)
SPEC = importlib.util.spec_from_file_location("rtpengine_firstboot", MODULE_PATH)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class AddressValidationTests(unittest.TestCase):
    def test_private_ipv4_accepts_ec2_unicast_address(self):
        self.assertEqual(runtime.private_ipv4("10.2.3.4"), "10.2.3.4")
        self.assertEqual(runtime.private_ipv4("100.64.1.2"), "100.64.1.2")
        for value in ("169.254.1.2", "127.0.0.1", "2001:db8::1", "invalid"):
            with self.subTest(value=value), self.assertRaises(
                runtime.ConfigurationError
            ):
                runtime.private_ipv4(value)

    def test_advertised_ipv4_rejects_non_unicast_values(self):
        self.assertEqual(runtime.advertised_ipv4("8.8.8.8"), "8.8.8.8")
        for value in ("0.0.0.0", "127.0.0.1", "224.0.0.1", "::1"):
            with self.subTest(value=value), self.assertRaises(
                runtime.ConfigurationError
            ):
                runtime.advertised_ipv4(value)


class DefaultsTests(unittest.TestCase):
    def write_defaults(self, content):
        temporary = tempfile.NamedTemporaryFile("w", encoding="ascii", delete=False)
        self.addCleanup(lambda: os.unlink(temporary.name))
        with temporary:
            temporary.write(content)
        return temporary.name

    def test_load_defaults(self):
        path = self.write_defaults(
            "[runtime]\n"
            "advertised_address_tag = RtpEngineAdvertisedAddress\n"
            "metadata_attempts = 4\nmetadata_timeout = 1.5\n"
        )
        self.assertEqual(
            runtime.load_defaults(path),
            ("RtpEngineAdvertisedAddress", 4, 1.5),
        )

    def test_rejects_shell_content_and_unsafe_tag(self):
        path = self.write_defaults(
            "[runtime]\nadvertised_address_tag = $(id)\n"
        )
        with self.assertRaises(runtime.ConfigurationError):
            runtime.load_defaults(path)

    def test_rejects_unknown_setting(self):
        path = self.write_defaults(
            "[runtime]\nadvertised_address_tag = Address\nsecret = value\n"
        )
        with self.assertRaises(runtime.ConfigurationError):
            runtime.load_defaults(path)


class MetadataTests(unittest.TestCase):
    def test_fetch_uses_new_imdsv2_token_for_retry(self):
        replies = [OSError("not ready"), "token", "10.0.0.8", "8.8.4.4"]
        with mock.patch.object(runtime, "metadata_request", side_effect=replies) as request:
            addresses = runtime.fetch_instance_addresses(
                "Address", 2, 1.0, sleep=lambda _delay: None
            )
        self.assertEqual(addresses, ("10.0.0.8", "8.8.4.4"))
        self.assertEqual(request.call_count, 4)
        self.assertEqual(request.call_args_list[1].kwargs["method"], "PUT")

    def test_fetch_is_bounded_and_fails_closed(self):
        with mock.patch.object(
            runtime, "metadata_request", side_effect=OSError("down")
        ) as request:
            with self.assertRaises(runtime.ConfigurationError):
                runtime.fetch_instance_addresses(
                    "Address", 3, 1.0, sleep=lambda _delay: None
                )
        self.assertEqual(request.call_count, 3)


class RenderingTests(unittest.TestCase):
    def test_template_requires_all_values(self):
        with tempfile.NamedTemporaryFile("w", encoding="ascii", delete=False) as stream:
            stream.write("interface=@PRIVATE_IPV4@!@ADVERTISED_IPV4@\n")
            path = stream.name
        self.addCleanup(lambda: os.unlink(path))
        with self.assertRaises(runtime.ConfigurationError):
            runtime.render_template(path, {"PRIVATE_IPV4": "10.0.0.1"})

    def test_atomic_install_sets_mode_and_replaces_content(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "rtpengine.conf"
            target.write_text("old", encoding="ascii")
            account = mock.Mock(pw_uid=os.getuid())
            group = mock.Mock(gr_gid=os.getgid())
            with (
                mock.patch.object(runtime.pwd, "getpwnam", return_value=account),
                mock.patch.object(runtime.grp, "getgrnam", return_value=group),
                mock.patch.object(runtime.os, "fchown"),
            ):
                runtime.atomic_install(target, "new\n")
            self.assertEqual(target.read_text(encoding="ascii"), "new\n")
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_rendered_configuration_enforces_private_controls(self):
        valid = """[rtpengine]
table = 0
no-fallback = true
interface = 10.0.0.1!198.51.100.8
listen-ng = 10.0.0.1:2223
listen-cli = 10.0.0.1:2224
listen-http = 10.0.0.1:2225
port-min = 30000
port-max = 39999
nftables-family = ip
nftables-chain = rtpengine
nftables-base-chain = INPUT
max-sessions = 5000
log-level = 6
tos = 184
"""
        runtime.validate_rendered(valid, "10.0.0.1", "198.51.100.8")
        with self.assertRaises(runtime.ConfigurationError):
            runtime.validate_rendered(
                valid.replace("10.0.0.1:2225", "0.0.0.0:2225"),
                "10.0.0.1",
                "198.51.100.8",
            )

    def test_rendered_configuration_enforces_kernel_mode(self):
        invalid = """[rtpengine]
table = -1
no-fallback = false
port-min = 30000
port-max = 39999
max-sessions = 1
log-level = 6
tos = 184
"""
        with self.assertRaises(runtime.ConfigurationError):
            runtime.validate_rendered(invalid, "10.0.0.1", "10.0.0.1")

    def test_rendered_configuration_accepts_configurable_kernel_table(self):
        valid = """[rtpengine]
table = 42
no-fallback = true
interface = 10.0.0.1!10.0.0.2
listen-ng = 10.0.0.1:12223
listen-cli = 10.0.0.1:12224
listen-http = 10.0.0.1:12225
port-min = 20000
port-max = 20999
nftables-family = ip
nftables-chain = relay
nftables-base-chain = INPUT
max-sessions = 1000
log-level = 5
tos = 46
"""
        runtime.validate_rendered(valid, "10.0.0.1", "10.0.0.2")


if __name__ == "__main__":
    unittest.main()
