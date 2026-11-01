"""Check key-download failures without touching APT or trusted host files."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "provision/apt-repos.d"


class AptRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "calls.jsonl"
        self.env = dict(os.environ, PATH=str(self.root) + ":/usr/bin:/bin",
                        TEST_LOG=str(self.log), TMPDIR=str(self.root))
        self.env.pop("FAIL_STAGE", None)
        for command in ["curl", "gpg", "sudo", "dpkg"]:
            self.stub(command)

    def stub(self, command):
        path = self.root / command
        path.write_text("#!" + sys.executable + "\n" + """
import json, os
from pathlib import Path
import sys
command = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["TEST_LOG"], "a") as log:
    log.write(json.dumps([command] + args) + "\\n")
stage = os.environ.get("FAIL_STAGE")
if command == "curl":
    if stage == "curl" or (stage == "policy" and any("1password.pol" in a for a in args)):
        sys.exit(22)
    Path(args[args.index("-o") + 1]).write_text("downloaded input")
elif command == "gpg":
    if stage == "gpg":
        sys.exit(2)
    Path(args[args.index("--output") + 1]).write_text("converted key")
elif command == "dpkg":
    print("amd64")
""")
        path.chmod(0o755)

    def run_repo(self, name):
        self.log.unlink(missing_ok=True)
        result = subprocess.run(["/bin/sh", str(SCRIPTS / name)], env=self.env,
                                capture_output=True, text=True)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return result, calls

    def test_failed_download_or_conversion_never_replaces_trusted_files(self):
        for name in ["vscode.sh", "google-chrome.sh", "1password.sh"]:
            for stage in ["curl", "gpg"]:
                with self.subTest(name=name, stage=stage):
                    self.env["FAIL_STAGE"] = stage
                    result, calls = self.run_repo(name)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(any(c[0] == "sudo" for c in calls), calls)
                    self.assertEqual(list(self.root.glob("tmp.*")), [])

    def test_1password_policy_failure_preserves_all_trusted_files(self):
        self.env["FAIL_STAGE"] = "policy"
        result, calls = self.run_repo("1password.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == "sudo" for c in calls), calls)

    def test_success_installs_converted_key_before_source_definition(self):
        for name, key in [("vscode.sh", "microsoft"), ("google-chrome.sh", "google"),
                          ("1password.sh", "1password")]:
            with self.subTest(name=name):
                result, calls = self.run_repo(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                key_install = next(i for i, c in enumerate(calls)
                                   if c[0:2] == ["sudo", "install"] and
                                   c[-1] == "/etc/apt/keyrings/" + key + ".gpg")
                convert = next(i for i, c in enumerate(calls) if c[0] == "gpg")
                source = next(i for i, c in enumerate(calls) if c[0:2] == ["sudo", "tee"])
                self.assertLess(convert, key_install)
                self.assertLess(key_install, source)
                self.assertEqual(list(self.root.glob("tmp.*")), [])


if __name__ == "__main__":
    unittest.main()
