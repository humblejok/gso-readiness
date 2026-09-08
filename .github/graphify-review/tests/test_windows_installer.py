from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "install-graphify-review.ps1"


class WindowsInstallerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = INSTALLER.read_text(encoding="utf-8")

    def test_required_artifacts_are_https_and_hash_verified(self):
        self.assertIn("Assert-HttpsUrl", self.script)
        self.assertIn("Assert-Sha256", self.script)
        self.assertIn("Get-FileHash -LiteralPath $Destination -Algorithm SHA256", self.script)
        self.assertIn("Invoke-VerifiedDownload", self.script)

    def test_maven_opts_is_user_scoped_and_preserves_unowned_options(self):
        self.assertIn("'MAVEN_OPTS'", self.script)
        self.assertIn("[EnvironmentVariableTarget]::User", self.script)
        self.assertIn("javax.net.ssl.trustStore", self.script)
        self.assertIn("Remove-OwnedJavaOption", self.script)

    def test_installer_validates_and_backs_up_github_directory(self):
        self.assertIn("exactly one .github directory", self.script)
        self.assertIn("agents\\review-manager.agent.md", self.script)
        self.assertIn("prompts\\full-project-review.prompt.md", self.script)
        self.assertIn("Copy-Item -LiteralPath $destinationGithub -Destination $backupDirectory", self.script)

    def test_installer_does_not_disable_tls_or_embed_a_secret(self):
        lowered = self.script.lower()
        self.assertNotIn("skipcertificatecheck", lowered)
        self.assertNotIn("certificatepolicy", lowered)
        self.assertNotIn("servercertificatevalidationcallback", lowered)
        self.assertNotIn("-truststorepassword", lowered)
        self.assertNotIn("replace_with_access_token", lowered)

    def test_guided_setup_preset_optional_java_and_settings_preservation(self):
        for text in ("[string]$Preset", "[switch]$NonInteractive", "[switch]$SkipSetup", "$PSBoundParameters.ContainsKey", "setup_review.py", "--installed-root", "if ($installTrustStore)", "graphify-review/settings.json"):
            self.assertIn(text, self.script)


if __name__ == "__main__":
    unittest.main()
