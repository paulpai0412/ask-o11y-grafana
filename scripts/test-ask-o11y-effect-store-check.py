#!/usr/bin/env python3
"""Offline negative checks for the deployment probe; never invokes Docker."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("effect_check", Path(__file__).with_name("check-ask-o11y-effect-store.py"))
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

ENV = "PLUGIN_PID=27\nGF_PLUGIN_ASKO11Y_EFFECT_STORE=/var/lib/grafana/ask-o11y-operations"
MOUNTS = [{"Type": "volume", "Destination": "/var/lib/grafana", "RW": True}]
MOUNT = json.dumps(MOUNTS)
IDENTITY = "Uid:\t472 472 472 472\nGid:\t0 0 0 0\nGroups:\t0 0"


class EffectStoreCheckTests(unittest.TestCase):
    def test_probe_uses_matching_identity(self):
        with patch.object(sys, "argv", ["check"]), patch.object(check, "docker", side_effect=[ENV, MOUNT, IDENTITY, "0", ""]) as docker:
            with contextlib.redirect_stdout(io.StringIO()):
                check.main()
            self.assertEqual(docker.call_args_list[-1].args[:3], ("exec", "--user", "472:0"))
            self.assertIn("chmod 700", docker.call_args_list[-1].args[-3])

    def test_inherited_host_environment_or_missing_canary_stops_before_probe(self):
        for flag in ["HOST_ENV_FORWARDED=1", "HOST_ENV_MARKER_MISSING=1"]:
            with self.subTest(flag=flag), patch.object(sys, "argv", ["check"]):
                with patch.object(check, "docker", return_value=ENV + "\n" + flag) as docker:
                    with self.assertRaisesRegex(SystemExit, "host-environment isolation"):
                        check.main()
                    self.assertEqual(docker.call_count, 1)

    def test_group_mismatch_stops_before_write(self):
        with patch.object(sys, "argv", ["check"]), patch.object(check, "docker", side_effect=[ENV, MOUNT, IDENTITY, "0 100"]) as docker:
            with self.assertRaisesRegex(SystemExit, "groups differ"):
                check.main()
            self.assertEqual(docker.call_count, 4)

    def test_missing_store_stops_before_probe(self):
        with patch.object(sys, "argv", ["check"]), patch.object(check, "docker", return_value="PLUGIN_PID=27") as docker:
            with self.assertRaisesRegex(SystemExit, "no absolute effect store"):
                check.main()
            self.assertEqual(docker.call_count, 1)

    def test_nested_readonly_mount_stops_before_probe(self):
        mounts = MOUNTS + [{"Type": "bind", "Destination": "/var/lib/grafana/ask-o11y-operations", "RW": False}]
        with patch.object(sys, "argv", ["check"]), patch.object(check, "docker", side_effect=[ENV, json.dumps(mounts)]) as docker:
            with self.assertRaisesRegex(SystemExit, "writable persistent mount"):
                check.main()
            self.assertEqual(docker.call_count, 2)


if __name__ == "__main__":
    unittest.main()
