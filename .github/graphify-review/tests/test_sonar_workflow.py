"""Static contracts for the Copilot integration, not a live MCP/agent simulation."""

import ast
import unittest
from pathlib import Path

GITHUB = Path(__file__).resolve().parents[2]


def document(relative):
    text = (GITHUB / relative).read_text(encoding="utf-8")
    _, header, body = text.split("---", 2)
    fields = dict(line.split(":", 1) for line in header.strip().splitlines())
    return {key: value.strip() for key, value in fields.items()}, body


class SonarWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.manager_header, self.manager = document(
            "agents/finding-implementation-manager.agent.md"
        )
        self.prompt_header, self.prompt = document(
            "prompts/implement-findings.prompt.md"
        )

    def test_chat_tool_selection_is_not_shadowed(self):
        self.assertNotIn("tools", self.manager_header)
        self.assertNotIn("tools", self.prompt_header)
        self.assertEqual(self.prompt_header["agent"], self.manager_header["name"])
        self.assertIn("Optional Sonar MCP assessment", self.prompt)

    def test_independent_verification_stays_restricted(self):
        agents = ast.literal_eval(self.manager_header["agents"])
        self.assertEqual(agents, ["Targeted Finding Verifier"])
        for name in ("finding-revalidation-manager", "targeted-finding-verifier"):
            header, _ = document(f"agents/{name}.agent.md")
            tools = ast.literal_eval(header["tools"])
            self.assertTrue(tools)
            self.assertNotIn("*", tools)
            self.assertFalse(any("sonar" in tool.casefold() for tool in tools))

    def test_corrections_precede_independent_verification_and_delivery(self):
        assessment = self.manager.index("Before step 5, perform")
        verify = self.manager.index("5. Perform **Direct targeted verification**")
        commit = self.manager.index("6. Once the provisional targeted result")
        deliver = self.manager.index("8. Write a short factual")
        self.assertLess(assessment, verify)
        self.assertLess(verify, commit)
        self.assertLess(commit, deliver)

    def test_source_freshness_and_optional_failure_are_explicit(self):
        for requirement in (
            "not exposed in this chat",
            "verify the mapping before using path-based analysis",
            "supplied complete current file content",
            "three correction rounds per finding",
            "After the last edit, obtain fresh analysis",
            "A repository/user-required Sonar check remains required",
            "not a full Sonar Quality Gate",
            "sonar-assessment.md",
            "completion/failure summary sent to the Hub",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, self.manager)

    def test_easy_fixes_do_not_authorize_blind_deletions(self):
        for constraint in (
            "reflection",
            "dependency injection",
            "serialization",
            "side effects",
            "files already changed for this finding",
            "Defer ambiguous or unrelated issues",
            "mark Sonar issues resolved/false-positive",
            "never unrelated MCP services",
        ):
            with self.subTest(constraint=constraint):
                self.assertIn(constraint, self.manager)

    def test_optional_limitations_are_not_required_checks_or_fabricated_passes(self):
        for name in ("targeted-finding-verifier", "request-implementation-verifier"):
            _, verifier = document(f"agents/{name}.agent.md")
            with self.subTest(verifier=name):
                self.assertIn("`rationale`", verifier)
                self.assertIn("full Quality Gate was not verified", verifier)
                self.assertIn("required", verifier)
                self.assertIn("Every recorded check", verifier)
        for instruction in ("not in required `checks`", "never delete a check", "preserve its output",
                            "fresh independent assessment", "terminal failed attempt"):
            self.assertIn(instruction, self.manager)

    def test_recovery_preserves_history_and_requires_new_verification_in_each_phase(self):
        self.assertIn("retry=", self.prompt_header["argument-hint"])
        self.assertIn("or reuse old verification", self.prompt)
        for instruction in ("Repeat the direct verifier readiness preflight", "successor state",
                            "each provisional and committed phase", "prepare-verification",
                            "new exclusive Hub claim"):
            self.assertIn(instruction, self.manager)


class DirectVerifierWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.header, self.manager = document(
            "agents/finding-implementation-manager.agent.md"
        )
        self.verifier_header, self.verifier = document(
            "agents/targeted-finding-verifier.agent.md"
        )

    def test_both_entry_points_have_only_one_delegation_level(self):
        standalone, _ = document("agents/finding-revalidation-manager.agent.md")
        for header in (self.header, standalone):
            self.assertEqual(
                ast.literal_eval(header["agents"]), [self.verifier_header["name"]]
            )
        self.assertEqual(ast.literal_eval(self.verifier_header["agents"]), [])
        self.assertEqual(
            ast.literal_eval(self.verifier_header["tools"]),
            ["read", "search", "execute"],
        )
        prompt, _ = document("prompts/revalidate-finding.prompt.md")
        self.assertEqual(prompt["agent"], standalone["name"])

    def test_readiness_is_required_before_start_but_not_evidence(self):
        preflight = self.manager.index(
            "Before any `implement_findings.py plan` or `start`"
        )
        start = self.manager.index("3. Run `implement_findings.py start")
        self.assertLess(preflight, start)
        for text in (self.manager, self.verifier):
            self.assertIn("mode=readiness", text)
        self.assertIn(
            '"status":"ready","agent":"Targeted Finding Verifier"', self.verifier
        )
        self.assertIn("A readiness acknowledgement is not verification", self.manager)
        self.assertIn("Do not inspect source, execute commands", self.verifier)

    def test_real_verification_and_packaging_are_required_twice(self):
        for instruction in (
            "phase `provisional` with `--allow-dirty`",
            "phase `committed`",
            "without `--allow-dirty`",
            "Prepare a new request and make a fresh direct",
            "revalidate_finding.py prepare --repository <actual-worktree>",
            "state.item.repository_external_id",
            "revalidate_finding.py build --request <request-path>",
            "require `outcome=resolved`",
            "Serialize only that returned object",
            "not cryptographically signed",
            "Never call `publish-revalidation`",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, self.manager)

    def test_denied_call_cannot_be_packaged_as_a_finding_result(self):
        for instruction in (
            "whether a call was actually attempted",
            "sanitized tool error verbatim",
            "say the cause is unknown",
            "do not generate a substitute envelope",
            "turn a denied invocation into `not_reproduced`",
            "completion_pending",
            "uncertain-PR exceptions",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, self.manager)


if __name__ == "__main__":
    unittest.main()
