"""Offline checks for inputs and the guest security configuration."""
import importlib.util
import base64
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("render", Path(__file__).with_name("render.py"))
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / "key.pub"
        def field(value):
            return struct.pack(">I", len(value)) + value
        blob = base64.b64encode(field(b"ssh-ed25519") + field(bytes(range(32)))).decode()
        self.public_key = f"ssh-ed25519 {blob}"
        self.key.write_text(self.public_key + " test@example\n")

    def render(self, cpus="8"):
        renderer.render(self.key, cpus, "16384", "100", self.root / "out")

    def test_guest_boundary_and_deterministic_manifest(self):
        self.render()
        out = self.root / "out"
        before = (out / "manifest.json").read_bytes()
        config = json.loads((out / "user-data").read_text().split("\n", 1)[1])
        self.assertFalse(config["ssh_pwauth"])
        self.assertTrue(config["disable_root"])
        self.assertEqual([u["name"] for u in config["users"]], ["codex"])
        self.assertEqual(config["users"][0]["sudo"], "ALL=(ALL) NOPASSWD:ALL")
        self.assertNotIn("mounts", config)
        self.assertNotIn(".codex/config.toml", str(config))
        self.render()
        self.assertEqual(before, (out / "manifest.json").read_bytes())
        self.key.write_text(self.public_key + " changed-client\n")
        self.render()
        self.assertNotEqual(before, (out / "manifest.json").read_bytes())

    def test_rejects_private_keys_options_multiline_and_shell_input(self):
        for key in ["-----BEGIN OPENSSH PRIVATE KEY-----", 'command="id" ssh-ed25519 AAAA',
                    "ssh-ed25519 AAAA\nssh-ed25519 AAAA", "ssh-ed25519 !!!"]:
            with self.subTest(key=key):
                self.key.write_text(key)
                with self.assertRaises(ValueError):
                    self.render()
                self.assertFalse((self.root / "out").exists())
        with self.assertRaises(ValueError):
            self.render("8; touch /tmp/should-not-exist")

    def test_rejects_resource_bounds_before_writing(self):
        for cpus in ["0", "1", "257", "-8"]:
            with self.subTest(cpus=cpus), self.assertRaises(ValueError):
                self.render(cpus)
        self.assertFalse((self.root / "out").exists())

    def test_valid_base64_but_invalid_key_is_rejected(self):
        self.key.write_text("ssh-ed25519 AAAA")
        with self.assertRaises(ValueError):
            self.render()

    def test_wrapper_skips_or_validates_before_sudo(self):
        script = Path(__file__).resolve().parents[2] / "setup_codex_vm.sh"
        env = dict(os.environ, DOTFILES_CODEX_VM="0")
        result = subprocess.run([str(script)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Skipping", result.stdout)
        self.key.write_text("not a public key")
        marker = self.root / "sudo-called"
        shim = self.root / "sudo"
        shim.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 99\n')
        shim.chmod(0o755)
        env.update(DOTFILES_CODEX_VM="1", CODEX_VM_SSH_PUBLIC_KEY=str(self.key),
                   PATH=str(self.root) + os.pathsep + os.environ["PATH"])
        result = subprocess.run([str(script)], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
