from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from unittest import mock
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / ".github" / "graphify-review" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from review_core import calculate_confidence, calculate_scores, load_yaml  # noqa: E402
from profile_core import ProfileError, load_profiles, profile_summary, resolve_profile, validate_profile  # noqa: E402
from build_evidence import build as build_evidence  # noqa: E402
from bootstrap_environment import (  # noqa: E402
    BootstrapError,
    DEFAULT_VENV_NAME,
    bootstrap,
    environment_commands,
    inspect_environment,
    required_graphify_version,
    workspace_path,
)
from select_review_samples import select  # noqa: E402
from reproducibility import ReproducibilityError, prepare_run, source_snapshot  # noqa: E402
from finding_identity import continuity_findings, ensure_identity, reconcile  # noqa: E402
from aspnet_checks import analyze as analyze_aspnet  # noqa: E402
from spring_checks import analyze as analyze_spring  # noqa: E402
from run_audits import classify as classify_audit  # noqa: E402
from render_review import render  # noqa: E402
from rescore_review import conservative_confidence  # noqa: E402
from hash_inputs import build_manifest  # noqa: E402
from map_standards import map_findings  # noqa: E402
from validate_evidence import validate_evidence  # noqa: E402
from validate_policy import validate_policy  # noqa: E402
from validate_review import validate_review  # noqa: E402

POLICY_PATH = ROOT / ".github" / "graphify-review" / "policy.yaml"
REVIEW_EXAMPLE = ROOT / ".github" / "graphify-review" / "examples" / "review.example.json"


def finding(
    finding_id: str = "F-001",
    category: str = "security",
    severity: str = "medium",
    impact: str = "post_release",
    status: str = "supported",
    quality_impact: str | None = None,
) -> dict:
    quality_impact = quality_impact or {
        "blocker": "systemic", "required_before_production": "material", "post_release": "localized", "none": "none"
    }[impact]
    return {
        "id": finding_id,
        "category": category,
        "severity": severity,
        "technical_quality_impact": quality_impact,
        "production_impact": impact,
        "verification_status": status,
        "evidence": [{"type": "source", "path": "src/example.py"}],
    }


def evidence(**states: str) -> dict:
    defaults = {
        "source_analysis": "passed", "graph_analysis": "passed", "tests_executed": "passed",
        "dependency_vulnerability_scan": "passed", "secret_scan": "passed",
        "static_security_scan": "passed", "configuration_analysis": "passed",
    }
    defaults.update(states)
    completion = {
        key: "not_applicable" if state == "not_applicable" else "complete" if state in {"passed", "failed"} else "not_completed"
        for key, state in defaults.items()
    }
    return {
        "source": {"state": defaults["source_analysis"], "completion": completion["source_analysis"]},
        "graph": {"state": defaults["graph_analysis"], "completion": completion["graph_analysis"]},
        "tests": {"state": defaults["tests_executed"], "completion": completion["tests_executed"]},
        "security": {
            "dependency_vulnerability_scan": {"state": defaults["dependency_vulnerability_scan"], "completion": completion["dependency_vulnerability_scan"]},
            "secret_scan": {"state": defaults["secret_scan"], "completion": completion["secret_scan"]},
            "static_security_scan": {"state": defaults["static_security_scan"], "completion": completion["static_security_scan"]},
        },
        "configuration": {"state": defaults["configuration_analysis"], "completion": completion["configuration_analysis"]},
        "limitations": [],
    }


def recalculate(review: dict, policy: dict, profile: dict | None = None) -> None:
    result = calculate_scores(review["findings"], policy, review["assessment_confidence"]["score_percent"], profile=profile)
    result["summary"] = review["production_readiness"].get("summary", "")
    result["scoring_notes"] = review["production_readiness"].get("scoring_notes", "")
    review["production_readiness"] = result
    review["statistics"]["verified_findings"] = len(review["findings"])
    review["statistics"]["rejected_findings"] = len(review["rejected_findings"])
    review["statistics"]["inconclusive_findings"] = len(review["inconclusive_findings"])
    review["statistics"]["by_severity"] = {key: sum(item["severity"] == key for item in review["findings"]) for key in ("critical", "high", "medium", "low", "informational")}
    review["statistics"]["by_category"] = {key: sum(item["category"] == key for item in review["findings"]) for key in ("architecture", "correctness", "maintainability", "security", "deployability", "testing", "operations")}
    review["statistics"]["by_technical_quality_impact"] = {key: sum(item["technical_quality_impact"] == key for item in review["findings"]) for key in ("systemic", "material", "localized", "none")}
    review["statistics"]["by_production_impact"] = {key: sum(item["production_impact"] == key for item in review["findings"]) for key in ("blocker", "required_before_production", "post_release", "none")}


class ScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_yaml(POLICY_PATH)

    def test_same_input_same_score(self) -> None:
        findings = [finding()]
        self.assertEqual(calculate_scores(findings, self.policy, 100), calculate_scores(findings, self.policy, 100))

    def test_critical_blocker_applies_cap(self) -> None:
        result = calculate_scores([finding(severity="critical", impact="blocker")], self.policy, 100)
        self.assertLessEqual(result["score_10"], 4.0)
        self.assertEqual(result["release_recommendation"], "NO_GO")

    def test_exposed_secret_applies_stricter_cap(self) -> None:
        item = finding(severity="critical", impact="blocker")
        item["hard_gate_tags"] = ["exposed_secret"]
        result = calculate_scores([item], self.policy, 100)
        self.assertEqual(result["score_10"], 3.0)

    def test_unsupported_findings_do_not_affect_score(self) -> None:
        result = calculate_scores([finding(severity="critical", impact="blocker", status="unsupported")], self.policy, 100)
        self.assertEqual(result["score_10"], 10.0)
        self.assertEqual(result["release_recommendation"], "GO")

    def test_category_scores_are_clamped(self) -> None:
        result = calculate_scores([finding(f"F-{index}", severity="critical", impact="blocker") for index in range(4)], self.policy, 100)
        self.assertEqual(result["category_scores"]["security"], 0.0)

    def test_category_weights_are_honored(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["scoring"]["category_weights"] = {key: float(key == "architecture") for key in policy["scoring"]["category_weights"]}
        result = calculate_scores([finding(category="architecture", severity="medium", impact="post_release")], policy, 100)
        self.assertEqual(result["score_10"], result["category_scores"]["architecture"])

    def test_active_and_expired_risk_acceptance(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["accepted_risks"] = [{
            "finding_id": "F-001", "status": "accepted", "owner": "team", "rationale": "migration",
            "accepted_until": "2026-12-31", "production_impact_override": "post_release",
        }]
        active = calculate_scores([finding(impact="blocker")], policy, 100, date(2026, 8, 19))
        expired = calculate_scores([finding(impact="blocker")], policy, 100, date(2027, 1, 1))
        self.assertEqual(active["release_recommendation"], "GO_WITH_ACTIONS")
        self.assertEqual(expired["release_recommendation"], "NO_GO")
        self.assertEqual(active["category_scores"], expired["category_scores"], "risk acceptance must not change technical score")

    def test_risk_acceptance_does_not_remove_technical_hard_gate_cap(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["accepted_risks"] = [{
            "finding_id": "F-001", "status": "accepted", "owner": "team", "rationale": "temporary",
            "accepted_until": "2026-12-31", "production_impact_override": "post_release",
        }]
        result = calculate_scores([finding(severity="critical", impact="blocker")], policy, 100, date(2026, 8, 19))
        self.assertEqual(result["score_10"], 4.0)
        self.assertEqual(result["release_recommendation"], "GO_WITH_ACTIONS")

    def test_technical_quality_reduces_score_without_constraining_release(self) -> None:
        item = finding(category="maintainability", severity="medium", impact="none", quality_impact="material")
        result = calculate_scores([item], self.policy, 100)
        self.assertEqual(result["category_scores"]["maintainability"], 7.8)
        self.assertEqual(result["release_recommendation"], "GO")

    def test_profile_systemic_rule_applies_category_cap(self) -> None:
        profile = resolve_profile("asp.net", ROOT)
        item = finding(category="architecture", severity="low", impact="none", quality_impact="systemic")
        item["profile_rule_id"] = "ASPNET-DI-002"
        result = calculate_scores([item], self.policy, 100, profile=profile)
        self.assertEqual(result["category_scores"]["architecture"], 7.0)
        self.assertEqual(result["release_recommendation"], "GO")
        self.assertEqual(result["applied_category_caps"][0]["cap"], "aspnet-systemic-architecture")

    def test_multiple_non_production_findings_materially_lower_grade(self) -> None:
        findings = [
            finding(f"MAINT-{index}", "maintainability", "medium", "none", quality_impact="localized")
            for index in range(3)
        ] + [
            finding(f"SEC-{index}", "security", "medium", "none", quality_impact="localized")
            for index in range(3)
        ]
        result = calculate_scores(findings, self.policy, 100)
        self.assertEqual(result["category_scores"]["maintainability"], 6.2)
        self.assertEqual(result["category_scores"]["security"], 6.2)
        self.assertEqual(result["score_10"], 8.1)
        self.assertEqual(result["release_recommendation"], "GO")

    def test_supported_finding_cannot_be_score_neutral(self) -> None:
        item = finding(severity="medium", impact="none", quality_impact="none")
        with self.assertRaisesRegex(ValueError, "must have technical_quality_impact"):
            calculate_scores([item], self.policy, 100)


class ConfidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_yaml(POLICY_PATH)

    def test_missing_scanners_lower_confidence_not_quality(self) -> None:
        full = calculate_confidence(evidence(), self.policy)
        missing = calculate_confidence(evidence(dependency_vulnerability_scan="not_run", secret_scan="not_run", static_security_scan="unavailable"), self.policy)
        self.assertLess(missing["score_percent"], full["score_percent"])
        score = calculate_scores([], self.policy, missing["score_percent"])
        self.assertEqual(score["score_10"], 10.0)

    def test_threshold_triggers_insufficient_evidence(self) -> None:
        confidence = calculate_confidence(evidence(tests_executed="not_run", dependency_vulnerability_scan="not_run", secret_scan="not_run", static_security_scan="not_run", configuration_analysis="unavailable"), self.policy)
        self.assertTrue(confidence["below_decision_threshold"])
        self.assertEqual(calculate_scores([], self.policy, confidence["score_percent"])["release_recommendation"], "INSUFFICIENT_EVIDENCE")

    def test_mandatory_evidence_blocks_decision_even_at_threshold(self) -> None:
        result = calculate_confidence(evidence(
            tests_executed="unavailable", secret_scan="unavailable", static_security_scan="unavailable"
        ), self.policy)
        self.assertEqual(result["score_percent"], 60)
        self.assertTrue(result["below_decision_threshold"])
        self.assertEqual(result["unmet_decision_requirements"], ["secret_scan", "static_security_scan", "tests_executed"])
        score = calculate_scores([], self.policy, result["score_percent"], confidence_below_threshold=result["below_decision_threshold"])
        self.assertEqual(score["release_recommendation"], "INSUFFICIENT_EVIDENCE")

    def test_legacy_rescore_does_not_infer_completed_scans(self) -> None:
        review = json.loads(REVIEW_EXAMPLE.read_text(encoding="utf-8"))
        review["assessment_confidence"].pop("completions")
        result = conservative_confidence(review, self.policy)
        self.assertEqual(result["score_percent"], 45)
        self.assertTrue(result["below_decision_threshold"])
        self.assertEqual(
            result["unmet_decision_requirements"],
            ["dependency_vulnerability_scan", "secret_scan", "static_security_scan", "tests_executed"],
        )


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_yaml(POLICY_PATH)

    def test_bad_enum_fails(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["deployment"]["type"] = "spaceship"
        self.assertTrue(any("deployment.type" in item for item in validate_policy(policy)))

    def test_invalid_weight_sum_fails(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["scoring"]["category_weights"]["architecture"] = 0.5
        self.assertTrue(any("sum to 1.0" in item for item in validate_policy(policy)))

    def test_missing_mandatory_key_fails_actionably(self) -> None:
        policy = copy.deepcopy(self.policy)
        del policy["confidence"]
        self.assertTrue(any("missing mandatory key: confidence" in item for item in validate_policy(policy)))

    def test_runtime_path_cannot_escape_workspace(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["runtime"]["venv_path"] = "../shared-venv"
        self.assertTrue(any("runtime.venv_path must remain" in item for item in validate_policy(policy)))

    def test_overall_aggregation_weights_must_sum_to_one(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["scoring"]["overall_aggregation"]["weakest_category_weight"] = 0.5
        self.assertTrue(any("overall_aggregation weights must sum" in item for item in validate_policy(policy)))


class ProfileTests(unittest.TestCase):
    def test_bundled_profiles_are_valid(self) -> None:
        profiles = load_profiles()
        self.assertEqual(set(profiles), {"generic", "aspnet-core-api", "spring-boot"})
        for entry in profiles.values():
            self.assertEqual(validate_profile(entry["definition"]), [])

    def test_aliases_normalize_to_canonical_ids(self) -> None:
        self.assertEqual(resolve_profile("asp.net", ROOT)["profile"]["id"], "aspnet-core-api")
        self.assertEqual(resolve_profile("spring", ROOT)["profile"]["id"], "spring-boot")

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(ProfileError, "Unknown profile"):
            resolve_profile("unknown-framework", ROOT)

    def test_auto_detects_aspnet_core_from_framework_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Api.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk.Web"></Project>\n', encoding="utf-8")
            (root / "Program.cs").write_text("var builder = WebApplication.CreateBuilder(args);\n", encoding="utf-8")
            resolved = resolve_profile("auto", root)
            self.assertEqual(resolved["profile"]["id"], "aspnet-core-api")
            self.assertEqual(resolved["detection"]["state"], "matched")

    def test_auto_detects_spring_boot_from_framework_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pom.xml").write_text("<artifactId>spring-boot-starter-web</artifactId>\n", encoding="utf-8")
            resolved = resolve_profile("auto", root)
            self.assertEqual(resolved["profile"]["id"], "spring-boot")

    def test_auto_rejects_ambiguous_frameworks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Api.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk.Web"></Project>\n', encoding="utf-8")
            (root / "pom.xml").write_text("<artifactId>spring-boot-starter-web</artifactId>\n", encoding="utf-8")
            with self.assertRaisesRegex(ProfileError, "ambiguous"):
                resolve_profile("auto", root)

    def test_profile_patterns_extend_evidence_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Api.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk.Web"></Project>\n', encoding="utf-8")
            (root / "Program.cs").write_text("var builder = WebApplication.CreateBuilder(args);\n", encoding="utf-8")
            (root / "appsettings.Production.json").write_text("{}\n", encoding="utf-8")
            resolved = resolve_profile("asp.net", root)
            result = build_evidence(root, None, [], resolved)
            self.assertEqual(result["profile"]["id"], "aspnet-core-api")
            self.assertIn("Api.csproj", result["dependencies"]["items"])
            self.assertIn("appsettings.Production.json", result["profile"]["configuration_files"])
            self.assertIn("Program.cs", result["profile"]["startup_files"])
            self.assertTrue(any(item["id"] == "dotnet-build" for item in result["profile"]["applicable_audits"]))


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_yaml(POLICY_PATH)

    def test_requirement_is_exactly_pinned(self) -> None:
        requirements = ROOT / ".github" / "graphify-review" / "requirements.txt"
        self.assertEqual(required_graphify_version(requirements), "0.9.46")

    def test_workspace_path_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(BootstrapError):
                workspace_path(Path(directory), "../outside", "runtime.venv_path")

    def test_check_only_reports_missing_environment_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            application_venv = workspace / ".venv"
            application_venv.mkdir()
            sentinel = application_venv / "application-owned.txt"
            sentinel.write_text("unchanged\n", encoding="utf-8")
            requirements = workspace / "requirements.txt"
            requirements.write_text("graphifyy==0.9.46\n", encoding="utf-8")
            policy = copy.deepcopy(self.policy)
            policy["runtime"].update({"venv_path": DEFAULT_VENV_NAME, "requirements_file": "requirements.txt"})
            state = bootstrap(workspace, policy, check_only=True)
            self.assertFalse(state["ready"])
            self.assertEqual(state["action"], "required")
            self.assertFalse((workspace / DEFAULT_VENV_NAME).exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged\n")

    def test_non_venv_directory_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / DEFAULT_VENV_NAME).mkdir()
            (workspace / "requirements.txt").write_text("graphifyy==0.9.46\n", encoding="utf-8")
            policy = copy.deepcopy(self.policy)
            policy["runtime"]["requirements_file"] = "requirements.txt"
            with self.assertRaisesRegex(BootstrapError, "refusing to modify"):
                bootstrap(workspace, policy)

    def test_environment_probe_accepts_matching_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            venv = Path(directory) / DEFAULT_VENV_NAME
            venv.mkdir()
            (venv / "pyvenv.cfg").write_text("home = test\n", encoding="utf-8")
            python_path, graphify_path = environment_commands(venv)
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.touch()
            graphify_path.touch()
            completed = mock.Mock(stdout=json.dumps({"python_version": [3, 12, 1], "graphifyy": "0.9.46"}))
            with mock.patch("bootstrap_environment.subprocess.run", return_value=completed):
                state = inspect_environment(venv, "0.9.46", (3, 10))
            self.assertTrue(state["ready"])
            self.assertEqual(state["python"], str(python_path))
            self.assertEqual(state["python_version"], [3, 12, 1])


class EvidenceAndStandardsTests(unittest.TestCase):
    def test_example_evidence_is_valid(self) -> None:
        example = json.loads((ROOT / ".github" / "graphify-review" / "examples" / "evidence.example.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_evidence(example), [])

    def test_invalid_execution_state_fails(self) -> None:
        item = evidence()
        item.update({"schema_version": "2.1", "repository": {}, "coverage": {"state": "not_run"}, "dependencies": {"state": "passed"}, "tools": [], "limitations": []})
        item["security"]["secret_scan"]["state"] = "unknown"
        self.assertTrue(any("secret_scan.state is invalid" in error for error in validate_evidence(item)))

    def test_scanner_rule_mapping_is_deterministic(self) -> None:
        mappings = json.loads((ROOT / ".github" / "skills" / "standards-mapping" / "mappings.json").read_text(encoding="utf-8"))
        item = finding()
        item["standards"] = []
        item["evidence"] = [{"type": "tool", "tool": "bandit", "rule_id": "B608"}]
        mapped = map_findings([item], mappings)[0]["standards"]
        self.assertEqual([entry["id"] for entry in mapped], ["CWE-89", "A03:2021"])


class SamplingTests(unittest.TestCase):
    def test_sampling_is_seeded_and_bounded(self) -> None:
        policy = load_yaml(POLICY_PATH)
        policy["review"].update({"random_sampling_percent": 10, "random_sampling_min_files": 3, "random_sampling_max_files": 5})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(30):
                (root / f"module_{index}.py").write_text(f"VALUE = {index}\n", encoding="utf-8")
            first = select(root, {"nodes": [], "edges": [], "derived": {"cycles": []}}, policy, "commit-a")
            second = select(root, {"nodes": [], "edges": [], "derived": {"cycles": []}}, policy, "commit-a")
            policy_changed = copy.deepcopy(policy)
            policy_changed["scoring"]["penalties"]["medium"]["localized"] = 9.0
            changed_policy = select(root, {"nodes": [], "edges": [], "derived": {"cycles": []}}, policy_changed, "commit-b")
            (root / "module_0.py").write_text("VALUE = 'changed'\n", encoding="utf-8")
            changed_source = select(root, {"nodes": [], "edges": [], "derived": {"cycles": []}}, policy, "commit-a")
            self.assertEqual(first, second)
            self.assertEqual(first["seed"], changed_policy["seed"], "scoring-only policy changes must not resample files")
            self.assertNotEqual(first["seed"], changed_source["seed"])
            self.assertEqual(len(first["random_selected"]), 3)


class ReproducibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_yaml(POLICY_PATH)

    def test_source_snapshot_changes_only_when_review_inputs_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Program.cs"
            source.write_text("class Program {}\n", encoding="utf-8")
            first = source_snapshot(root)
            (root / "notes.txt").write_text("ignored\n", encoding="utf-8")
            self.assertEqual(first["hash"], source_snapshot(root)["hash"])
            source.write_text("class Program { static void Main() {} }\n", encoding="utf-8")
            self.assertNotEqual(first["hash"], source_snapshot(root)["hash"])

    def test_generated_graph_can_be_excluded_from_authored_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Program.cs").write_text("class Program {}\n", encoding="utf-8")
            graph = root / "graph.json"
            graph.write_text('{"generated": 1}\n', encoding="utf-8")
            first = source_snapshot(root, excluded_paths=[graph])
            graph.write_text('{"generated": 2}\n', encoding="utf-8")
            self.assertEqual(first["hash"], source_snapshot(root, excluded_paths=[graph])["hash"])

    def test_versioned_runs_require_baseline_for_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Program.cs").write_text("class Program {}\n", encoding="utf-8")
            with self.assertRaises(ReproducibilityError):
                prepare_run(root, self.policy, "revalidate", root / "runs")
            context = prepare_run(root, self.policy, "fresh", root / "runs")
            self.assertTrue(Path(context["output_directory"]).is_dir())
            self.assertEqual(context["mode"], "fresh")

    def test_baseline_inventory_includes_inconclusive_claims(self) -> None:
        baseline = json.loads(REVIEW_EXAMPLE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Program.cs").write_text("class Program {}\n", encoding="utf-8")
            baseline_path = root / "baseline.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            context = prepare_run(root, self.policy, "revalidate", root / "runs", baseline_path)
            self.assertEqual(context["baseline"]["verified_finding_count"], 2)
            self.assertEqual(context["baseline"]["inconclusive_finding_count"], 1)
            self.assertEqual(context["baseline"]["finding_count"], 3)

    def test_manifest_is_stable_and_identifies_model_and_tool_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = root / "graph.json"
            graph.write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
            kit = root / "kit"
            kit.mkdir()
            (kit / "VERSION").write_text("2.4.0\n", encoding="utf-8")
            context = {
                "run_id": "run-one", "mode": "fresh", "created_at_utc": "2026-08-31T10:00:00+00:00",
                "source_snapshot": {"hash": "sha256:" + "1" * 64}, "baseline": None,
            }
            tools = [{"tool": "test", "output_hash": "sha256:" + "2" * 64}]
            models = [{"agent": "reviewer", "model": "GPT-5.6 Luna", "role": "specialist", "task_complexity": "normal"}]
            first = build_manifest(root, graph, POLICY_PATH, kit, models, tools, run_context=context)
            second = build_manifest(root, graph, POLICY_PATH, kit, models, tools, run_context=context)
            self.assertEqual(first, second)
            self.assertEqual(first["timestamp_utc"], context["created_at_utc"])
            changed = build_manifest(root, graph, POLICY_PATH, kit, [{**models[0], "model": "different"}], tools, run_context=context)
            self.assertNotEqual(first["review_id"], changed["review_id"])


class BaselineAndAnchorTests(unittest.TestCase):
    def test_missing_baseline_disposition_is_rejected(self) -> None:
        baseline = [finding("SEC-OLD")]
        with self.assertRaisesRegex(ValueError, "has no disposition"):
            reconcile(baseline, [], [])

    def test_not_reproduced_baseline_is_retained_as_inconclusive(self) -> None:
        old = ensure_identity(finding("SEC-OLD"))
        result = reconcile([old], [], [{
            "baseline_fingerprint": old["fingerprint"], "status": "not_reproduced",
            "rationale": "Caller inventory is unavailable.", "evidence": [{"type": "source", "path": "src/example.py"}],
        }])
        self.assertEqual(result["statistics"]["not_reproduced"], 1)
        self.assertEqual(result["baseline_inconclusive_findings"][0]["verification_status"], "inconclusive")

    def test_aspnet_anchors_are_stable_and_cover_common_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Api.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk.Web"><ItemGroup><Reference Include="X"><HintPath>C:\\Dev\\X.dll</HintPath></Reference></ItemGroup></Project>\n', encoding="utf-8")
            (root / "Program.cs").write_text("var builder = WebApplication.CreateBuilder(args);\nbuilder.Services.BuildServiceProvider();\n", encoding="utf-8")
            (root / "Service.cs").write_text("var value = client.GetAsync(url).Result;\n[Fact(Skip = \"later\")] public void Test() {}\n", encoding="utf-8")
            first = analyze_aspnet(root)
            second = analyze_aspnet(root)
            self.assertEqual(first, second)
            signals = {item["signal"] for item in first["anchors"]}
            self.assertTrue({"machine-local-build-or-runtime-path", "container-built-during-registration", "sync-over-async", "skipped-test", "no-in-process-authentication-or-authorization-marker"} <= signals)

    def test_spring_anchors_are_stable_and_cover_common_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Application.java").write_text(
                "@SpringBootApplication class Application {}\n"
                "@RestController class Api { @Autowired Service service; }\n"
                "class Worker { @Async void run() { future.join(); } }\n",
                encoding="utf-8",
            )
            (root / "ApiTest.java").write_text("@Disabled class ApiTest {}\n", encoding="utf-8")
            (root / "application.properties").write_text(
                "management.endpoints.web.exposure.include=*\n", encoding="utf-8"
            )
            first = analyze_spring(root)
            second = analyze_spring(root)
            self.assertEqual(first, second)
            signals = {item["signal"] for item in first["anchors"]}
            self.assertTrue({
                "annotation-driven-member-injection", "blocking-or-waiting-on-async-work", "disabled-test",
                "wildcard-actuator-web-exposure", "no-in-process-spring-security-marker",
                "async-usage-without-visible-executor-ownership",
            } <= signals)

    def test_security_scanner_crash_is_not_complete_evidence(self) -> None:
        audit = {"evidence_kind": "static_security_scan"}
        execution = {"stdout": "", "stderr": "internal error", "exit_code": 2, "timed_out": False}
        self.assertEqual(classify_audit(audit, execution)[:2], ("unavailable", "not_completed"))
        findings_execution = {"stdout": "finding", "stderr": "", "exit_code": 1, "timed_out": False}
        self.assertEqual(classify_audit(audit, findings_execution)[:2], ("failed", "complete"))

    def test_test_command_must_discover_and_execute_tests(self) -> None:
        audit = {"evidence_kind": "tests_executed"}
        no_tests = {"stdout": "No test is available in Example.dll", "stderr": "", "exit_code": 0, "timed_out": False}
        self.assertEqual(classify_audit(audit, no_tests)[:2], ("unavailable", "not_completed"))
        compile_error = {"stdout": "error CS1002: ; expected", "stderr": "", "exit_code": 1, "timed_out": False}
        self.assertEqual(classify_audit(audit, compile_error)[:2], ("unavailable", "not_completed"))
        failed_test = {"stdout": "Failed! Failed: 1, Passed: 10", "stderr": "", "exit_code": 1, "timed_out": False}
        self.assertEqual(classify_audit(audit, failed_test)[:2], ("failed", "complete"))

    def test_markdown_rendering_is_deterministic(self) -> None:
        review = json.loads(REVIEW_EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(render(review), render(copy.deepcopy(review)))
        self.assertIn("Baseline Reconciliation", render(review))


class ReviewValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_yaml(POLICY_PATH)
        self.review = json.loads(REVIEW_EXAMPLE.read_text(encoding="utf-8"))

    def errors(self, review: dict, verified=None) -> list[str]:
        return validate_review(review, self.policy, ROOT, verified)

    def test_example_is_valid(self) -> None:
        self.assertEqual(self.errors(self.review), [])

    def test_revalidation_requires_exact_baseline_inventory(self) -> None:
        review = copy.deepcopy(self.review)
        claims = continuity_findings(review)
        review["run"].update({
            "mode": "revalidate",
            "baseline": {
                "finding_count": len(claims),
                "finding_fingerprints": sorted(item["fingerprint"] for item in claims),
            },
            "finding_reconciliation": [{
                "baseline_id": item["id"], "baseline_fingerprint": item["fingerprint"],
                "status": "still_present", "current_id": item["id"],
            } for item in claims],
        })
        self.assertEqual(self.errors(review), [])
        review["run"]["finding_reconciliation"].pop()
        self.assertTrue(any("one disposition" in error or "immutable baseline inventory" in error for error in self.errors(review)))

    def test_every_framework_anchor_requires_a_linked_disposition(self) -> None:
        review = copy.deepcopy(self.review)
        anchored = copy.deepcopy(review["findings"][0])
        anchored["identity"]["anchor_id"] = "anchor-example"
        anchored = ensure_identity(anchored)
        review["findings"][0] = anchored
        review["run"]["anchor_dispositions"] = [{
            "anchor_id": "anchor-example", "status": "promoted", "finding_id": anchored["id"],
            "rationale": "Source verification confirmed the anchor claim.",
            "evidence": [{"type": "source", "path": "src/project/settings.py"}],
        }]
        anchors = {"anchors": [{"id": "anchor-example"}]}
        self.assertEqual(validate_review(review, self.policy, ROOT, anchors=anchors), [])
        review["run"]["anchor_dispositions"] = []
        self.assertTrue(any("every deterministic anchor" in error for error in validate_review(review, self.policy, ROOT, anchors=anchors)))

    def test_cve_without_tool_evidence_fails_and_with_evidence_passes(self) -> None:
        review = copy.deepcopy(self.review)
        item = review["findings"][0]
        item["description"] += " It is affected by CVE-2026-12345."
        self.assertTrue(any("CVE-2026-12345" in error for error in self.errors(review)))
        item["evidence"].append({
            "type": "dependency_vulnerability", "package": "example", "installed_version": "1.2.3",
            "advisory_id": "CVE-2026-12345", "source": {"tool": "pip-audit"},
        })
        self.assertFalse(any("CVE-2026-12345" in error for error in self.errors(review)))

    def test_critical_graphify_only_finding_fails(self) -> None:
        review = copy.deepcopy(self.review)
        item = review["findings"][0]
        item.update({"severity": "critical", "production_impact": "blocker", "remediation_priority": "P0"})
        item["evidence"] = [{"type": "graphify", "source_id": "cycle-1"}]
        recalculate(review, self.policy)
        errors = self.errors(review)
        self.assertTrue(any("Graphify-only" in error or "deterministic evidence" in error for error in errors))

    def test_synthesis_cannot_change_immutable_fields(self) -> None:
        verified = {"findings": copy.deepcopy(self.review["findings"])}
        review = copy.deepcopy(self.review)
        review["findings"][0]["severity"] = "medium"
        recalculate(review, self.policy)
        self.assertTrue(any("immutable field severity" in error for error in self.errors(review, verified)))

    def test_model_score_override_fails(self) -> None:
        review = copy.deepcopy(self.review)
        review["production_readiness"]["score_10"] = 1.0
        self.assertTrue(any("score_10 differs" in error for error in self.errors(review)))

    def test_model_score_breakdown_override_fails(self) -> None:
        review = copy.deepcopy(self.review)
        review["production_readiness"]["score_breakdown"]["weakest_category_weight"] = 0.0
        self.assertTrue(any("score_breakdown differs" in error for error in self.errors(review)))

    def test_supported_finding_with_no_quality_impact_fails(self) -> None:
        review = copy.deepcopy(self.review)
        review["findings"][0]["technical_quality_impact"] = "none"
        errors = self.errors(review)
        self.assertTrue(any("scoreable technical_quality_impact" in error for error in errors))

    def test_framework_rule_and_profile_are_validated(self) -> None:
        profile = resolve_profile("asp.net", ROOT)
        review = copy.deepcopy(self.review)
        summary = profile_summary(profile)
        review["review"]["profile"] = summary
        review["review"]["manifest"]["profile"] = summary
        review["findings"][0]["profile_rule_id"] = "ASPNET-CONFIG-001"
        review["findings"][1]["profile_rule_id"] = "ASPNET-ARCH-001"
        review["findings"][0]["identity"]["profile_rule_id"] = "ASPNET-CONFIG-001"
        review["findings"][1]["identity"]["profile_rule_id"] = "ASPNET-ARCH-001"
        review["findings"] = [ensure_identity(item) for item in review["findings"]]
        recalculate(review, self.policy, profile)
        self.assertEqual(validate_review(review, self.policy, ROOT, profile=profile), [])
        review["findings"][0]["profile_rule_id"] = "ASPNET-NOT-A-RULE"
        self.assertTrue(any("does not exist" in error for error in validate_review(review, self.policy, ROOT, profile=profile)))

    def test_rejected_finding_cannot_retain_quality_impact(self) -> None:
        review = copy.deepcopy(self.review)
        review["rejected_findings"][0]["technical_quality_impact"] = "localized"
        self.assertTrue(any("technical_quality_impact=none" in error for error in self.errors(review)))

    def test_active_accepted_risk_is_visible_and_waives_release_only(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["accepted_risks"] = [{
            "finding_id": "DEPLOY-001", "status": "accepted", "owner": "release-team", "rationale": "controlled rollout",
            "accepted_until": "2099-12-31", "production_impact_override": "post_release",
        }]
        review = copy.deepcopy(self.review)
        acceptance = {**policy["accepted_risks"][0], "expired": False}
        review["findings"][0]["risk_acceptance"] = acceptance
        review["accepted_risks"] = [acceptance]
        recalculate(review, policy)
        self.assertEqual(review["production_readiness"]["release_recommendation"], "GO_WITH_ACTIONS")
        self.assertEqual(review["production_readiness"]["category_scores"]["deployability"], 6.0)
        self.assertEqual(validate_review(review, policy, ROOT), [])

    def test_expired_accepted_risk_cannot_waive_release(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["accepted_risks"] = [{
            "finding_id": "DEPLOY-001", "status": "accepted", "owner": "release-team", "rationale": "old exception",
            "accepted_until": "2000-01-01", "production_impact_override": "post_release",
        }]
        review = copy.deepcopy(self.review)
        acceptance = {**policy["accepted_risks"][0], "expired": True}
        review["findings"][0]["risk_acceptance"] = acceptance
        review["accepted_risks"] = [acceptance]
        recalculate(review, policy)
        self.assertEqual(review["production_readiness"]["release_recommendation"], "CONDITIONAL")
        review["production_readiness"]["release_recommendation"] = "GO_WITH_ACTIONS"
        self.assertTrue(any("release_recommendation differs" in error for error in validate_review(review, policy, ROOT)))


class SyntheticEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_yaml(POLICY_PATH)

    def test_project_a_healthy(self) -> None:
        result = calculate_scores([], self.policy, 100)
        self.assertEqual(result["release_recommendation"], "GO")
        self.assertEqual(result["score_10"], 10.0)

    def test_project_b_serious_defects(self) -> None:
        secret = finding("SEC-001", "security", "critical", "blocker")
        secret["hard_gate_tags"] = ["exposed_secret"]
        cycle = finding("ARCH-001", "architecture", "high", "post_release")
        result = calculate_scores([secret, cycle], self.policy, 85)
        self.assertEqual(result["release_recommendation"], "NO_GO")
        self.assertEqual(result["score_10"], 3.0)

    def test_project_c_insufficient_evidence(self) -> None:
        result = calculate_scores([], self.policy, 20)
        self.assertEqual(result["release_recommendation"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["score_10"], 10.0, "missing evidence must not fabricate poor quality")


if __name__ == "__main__":
    unittest.main()
