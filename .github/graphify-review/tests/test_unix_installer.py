# SPDX-FileCopyrightText: 2026 De Jonckheere Stéphane (humblejok)
# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("unix_installer", ROOT / "install-graphify-review-unix.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def make_bundle(extra=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name in installer.REQUIRED:
            archive.writestr("kit/.github/" + name, "fixture " + name)
        archive.writestr("kit/.github/graphify-review/scripts/tool.py", "print('new')\n")
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return data.getvalue()


class UnixInstallerTests(unittest.TestCase):
    def test_urls_and_hashes_reject_unsafe_inputs(self):
        for url in ("http://repo.local/file", "https://user:pass@repo.local/file",
                    "https://repo.local/file?token=secret", "https://artifactory.example.invalid/file",
                    "file:///tmp/kit.zip", "https://repo.local/\nfile"):
            with self.subTest(url=url), self.assertRaises(installer.InstallError):
                installer.validate_url(url, "artifact")
        with self.assertRaises(installer.InstallError):
            installer.validate_hash("fake")

    def test_download_checks_hash_and_does_not_forward_redirects(self):
        payload = b"test artifact"
        response = io.BytesIO(payload)
        response.status = 200
        opener = mock.Mock()
        opener.open.return_value = response
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "download"
            installer.download(opener, "https://repo.local/file", hashlib.sha256(payload).hexdigest(), path, "TOKEN", 1)
            self.assertEqual(path.read_bytes(), payload)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer TOKEN")
            response = io.BytesIO(payload)
            response.status = 200
            opener.open.return_value = response
            with self.assertRaises(installer.InstallError):
                installer.download(opener, "https://repo.local/file", "0" * 64, Path(temporary) / "bad", "TOKEN", 1)
        with self.assertRaises(installer.InstallError):
            installer.NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.local")

    def test_archive_rejects_escape_symlinks_missing_notices_and_multiple_roots(self):
        symlink = zipfile.ZipInfo("kit/.github/link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = [make_bundle({"../escape": "bad"}), make_bundle({"kit/.github/../../escape": "bad"}),
                 make_bundle({"second/.github/a": "bad"}), make_bundle({symlink: "/tmp"})]
        missing = io.BytesIO()
        with zipfile.ZipFile(missing, "w") as archive:
            archive.writestr(".github/graphify-review/VERSION", "1")
        cases.append(missing.getvalue())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "kit.zip"
            for data in cases:
                path.write_bytes(data)
                with self.assertRaises(installer.InstallError):
                    installer.read_bundle(path)

    def test_environment_is_idempotent_and_preserves_existing_maven_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "env.sh"
            path.write_text(installer.environment_script(Path("/tmp/graphify-runtime"), "JKS", True))
            for shell in ("bash", "zsh"):
                executable = shutil.which(shell)
                if not executable:
                    continue
                command = '. "$1"; first=$MAVEN_OPTS; . "$1"; test "$first" = "$MAVEN_OPTS"; printf "%s\\n%s" "$MAVEN_OPTS" "$PATH"'
                environment = {**os.environ, "MAVEN_OPTS": '-Xmx2g -Dcustom="keep me"', "PATH": "/usr/bin:/bin"}
                environment.pop("_GRAPHIFY_REVIEW_JAVA_OPTIONS", None)
                result = subprocess.run([executable, "-euc", command, "test", str(path)],
                                        capture_output=True, text=True, env=environment, check=True)
                options, path_value = result.stdout.split("\n")
                self.assertTrue(options.startswith('-Xmx2g -Dcustom="keep me" '))
                self.assertEqual(options.count("-Djavax.net.ssl.trustStore="), 1)
                self.assertEqual(path_value.count("/tmp/graphify-runtime/bin"), 1)

    def test_profiles_preserve_existing_startup_behavior(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / ".profile").write_text("export EXISTING=yes\n")
            self.assertEqual(installer.shell_profiles(home, "bash", {}), [home / ".profile", home / ".bashrc"])
            (home / ".bash_login").write_text("# login\n")
            self.assertEqual(installer.shell_profiles(home, "bash", {})[0], home / ".bash_login")
            original = "export EXISTING=yes\n"
            updated = installer.profile_content(original, home / "env.sh")
            self.assertTrue(updated.startswith(original))
            self.assertEqual(installer.profile_content(updated, home / "env.sh"), updated)
            self.assertEqual(installer.shell_profiles(home, "zsh", {"ZDOTDIR": str(home / 'zsh')}),
                             [home / "zsh/.zprofile", home / "zsh/.zshrc"])

    def test_end_to_end_install_backup_idempotence_and_failed_download(self):
        for system in ("Darwin", "Linux"):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                target = home / "project with spaces"
                target.mkdir()
                existing = target / ".github/workflows/existing.yml"
                existing.parent.mkdir(parents=True)
                existing.write_text("preserve: true\n")
                (target / ".venv").mkdir()
                sentinel = target / ".venv/sentinel"
                sentinel.write_text("application environment")
                args = installer.parser().parse_args([
                    "--target-repository", str(target), "--install-root", str(home / "runtime"),
                    "--github-bundle-url", "https://repo.local/kit.zip", "--github-bundle-sha256", "1" * 64,
                    "--truststore-url", "https://repo.local/cacerts", "--truststore-sha256", "2" * 64,
                    "--jfrog-cli-url", "https://repo.local/jf-{os}-{arch}", "--jfrog-cli-sha256", "3" * 64,
                    "--shell", "bash"])
                payloads = {"kit": make_bundle(), "cacerts": b"truststore", "jf": b"approved binary"}
                def fake_download(opener, url, checksum, destination, token, timeout):
                    destination.write_bytes(payloads[destination.name])
                with mock.patch.object(installer.platform, "system", return_value=system), \
                        mock.patch.object(installer.platform, "machine", return_value="arm64"), \
                        mock.patch.object(installer, "make_opener"), \
                        mock.patch.object(installer, "download", side_effect=fake_download):
                    first = installer.install(args, home=home)
                    second = installer.install(args, home=home)
                self.assertGreater(first["changed_files"], 0)
                self.assertEqual(second["changed_files"], 0)
                self.assertEqual(existing.read_text(), "preserve: true\n")
                self.assertEqual(sentinel.read_text(), "application environment")
                self.assertTrue((Path(first["backup_directory"]) / "github-before/workflows/existing.yml").exists())
                self.assertEqual((home / "runtime/bin/jf").stat().st_mode & 0o777, 0o755)
                self.assertEqual((home / "runtime/truststore/cacerts").stat().st_mode & 0o777, 0o600)
                before = (home / "runtime/env.sh").read_bytes()
                with mock.patch.object(installer.platform, "system", return_value=system), \
                        mock.patch.object(installer, "make_opener"), \
                        mock.patch.object(installer, "download", side_effect=installer.InstallError("bad hash")):
                    with self.assertRaises(installer.InstallError):
                        installer.install(args, home=home)
                self.assertEqual((home / "runtime/env.sh").read_bytes(), before)

    def test_symlink_destinations_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "external").mkdir()
            (root / "link").symlink_to(root / "external", target_is_directory=True)
            with self.assertRaises(installer.InstallError):
                installer.assert_writable_target(root / "link/file")

    def test_write_failure_rolls_back_previous_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / "first", root / "second"
            first.write_bytes(b"original")
            real_write = installer.atomic_write
            def fail_second(path, data, mode):
                if path == second:
                    raise OSError("simulated failure")
                real_write(path, data, mode)
            with mock.patch.object(installer, "atomic_write", side_effect=fail_second):
                with self.assertRaises(OSError):
                    installer.commit_files({first: (b"updated", 0o600), second: (b"new", 0o600)},
                                           root / "backups/run", root / ".github")
            self.assertEqual(first.read_bytes(), b"original")
            self.assertFalse(second.exists())


if __name__ == "__main__":
    unittest.main()
