"""Offline provisioning checks. All host-changing commands are stubs."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "setup_zsh.sh"


class ShellSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "commands.log"
        self.env = dict(os.environ, PATH=str(self.root), USER="testuser",
                        SHELL=str(self.root / "zsh"), TEST_LOG=str(self.log),
                        TEST_MOUNTED="1")
        self.env.pop("HF_HOME", None)
        for command in ["sudo", "chsh", "zsh"]:
            self.stub(command, 'echo "$0 $*" >> "$TEST_LOG"')
        self.stub("sudo", 'echo "argc=$#" >> "$TEST_LOG"; echo "$0 $*" >> "$TEST_LOG"')
        self.stub("mountpoint", 'exit "$((1 - TEST_MOUNTED))"')
        self.stub("id", 'echo testgroup')

    def stub(self, command, body):
        path = self.root / command
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def run_setup(self):
        result = subprocess.run(["/bin/sh", str(SCRIPT)], env=self.env,
                                capture_output=True, text=True)
        log = self.log.read_text() if self.log.exists() else ""
        return result, log

    def test_default_cache_with_no_interactive_environment(self):
        result, log = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sudo install -d -o testuser -g testgroup /mnt/data/hf_cache", log)
        self.assertNotIn("chsh", log)
        self.assertNotIn("chown", log)

    def test_unmounted_data_disk_never_changes_cache(self):
        self.env["TEST_MOUNTED"] = "0"
        result, log = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("sudo", log)
        self.assertIn("not mounted", result.stderr)

    def test_custom_cache_path_and_shell_change(self):
        self.env.update(HF_HOME=str(self.root / "cache with spaces"), SHELL="/bin/bash")
        result, log = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("chsh -s " + str(self.root / "zsh"), log)
        self.assertIn("cache with spaces", log)
        self.assertIn("argc=7", log)

    def test_missing_zsh_fails_before_privileged_commands(self):
        (self.root / "zsh").unlink()
        result, log = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(log, "")
        self.assertIn("Install zsh", result.stderr)


if __name__ == "__main__":
    unittest.main()
