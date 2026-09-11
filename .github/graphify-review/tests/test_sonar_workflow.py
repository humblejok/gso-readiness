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
        self.assertEqual(agents, ["Finding Revalidation Manager"])
        for name in ("finding-revalidation-manager", "targeted-finding-verifier"):
            header, _ = document(f"agents/{name}.agent.md")
            tools = ast.literal_eval(header["tools"])
            self.assertTrue(tools)
            self.assertNotIn("*", tools)
            self.assertFalse(any("sonar" in tool.casefold() for tool in tools))

    def test_corrections_precede_independent_verification_and_delivery(self):
        assessment = self.manager.index("Before step 5, perform")
        verify = self.manager.index("5. Invoke **Finding Revalidation Manager**")
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


if __name__ == "__main__":
    unittest.main()
