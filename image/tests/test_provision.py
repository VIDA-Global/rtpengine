import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rtp_image_inputs", ROOT / "provision/inputs.py")
assert SPEC and SPEC.loader
INPUTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INPUTS)


def valid():
    values = {key: minimum for key, (minimum, maximum) in INPUTS.NUMBERS.items()}
    values.update({"version": "26.3.0.0", "source_commit": "a" * 40,
                   "source_tree": "b" * 40, "source_sha256": "c" * 64,
                   "source_timestamp": "2026-09-10T00:00:00Z", "advertised_address_tag": "MediaIP",
                   "nftables_family": "ip", "nftables_chain": "rtpengine",
                   "nftables_base_chain": "INPUT", "no_fallback": True,
                   "media_port_max": 39999, "ng_port": 2223, "cli_port": 2224, "http_port": 2225})
    return {INPUTS.PREFIX + key: value for key, value in values.items()}


class ProvisionTests(unittest.TestCase):
    def test_valid_inputs_render_installed_assets(self):
        values = INPUTS.validate(valid())
        for name in ("rtpengine.conf.template", "ami-runtime.ini.template",
                     "rtpengine-firstboot.service.template"):
            rendered = INPUTS.render((ROOT / "assets" / name).read_text(), values)
            self.assertNotIn("{{", rendered)
        self.assertIn("no-fallback = true", INPUTS.render(
            (ROOT / "assets/rtpengine.conf.template").read_text(), values))

    def test_bad_fields_cannot_enter_shell_or_templates(self):
        for key, value in (("version", "x\ncommand"), ("ng_port", 2224), ("no_fallback", False),
                           ("media_port_min", 40000), ("metadata_timeout", float("nan")),
                           ("table", 1.5), ("source_commit", "main"),
                           ("advertised_address_tag", "aws:reserved")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                INPUTS.validate({**valid(), INPUTS.PREFIX + key: value})
        with self.assertRaises(ValueError):
            INPUTS.validate({**valid(), "unexpected_secret": "x"})
        with self.assertRaises(ValueError):
            INPUTS.unique_object([("x", 1), ("x", 2)])
        with self.assertRaises(ValueError):
            INPUTS.render("{{ unknown }}", valid())
        with self.assertRaises(ValueError):
            INPUTS.render("{{ invalid | expression }}", valid())

    def test_postinstall_policy_is_restored_on_failure(self):
        script = (ROOT / "provision/provision.sh").read_text()
        self.assertIn("trap restore_policy EXIT", script)
        self.assertIn('cp -p "$backup" "$policy"', script)


if __name__ == "__main__":
    unittest.main()
