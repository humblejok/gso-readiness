# Implementation validation: PowerShell and Sonar

## Native execution — implemented

`/implement-findings` and its independent targeted verifier use `run_validation.py` for inspected build/test commands. It starts one process, captures stdout/stderr separately as raw bytes, and writes a unique UTF-8 JSON receipt with the exit code and before/after source snapshots. **For Maven, its own build summary—not the exit code—is authoritative.** Other tools retain native-exit-code semantics.

Example in PowerShell, using the trusted original kit and the isolated implementation worktree:

```powershell
& 'C:\original-project\.graphify-review-venv\Scripts\python.exe' `
  'C:\original-project\.github\graphify-review\scripts\run_validation.py' `
  --repository 'C:\attempt\worktree' `
  --output-root 'C:\attempt\checks' `
  --timeout 900 -- mvnw.cmd -B verify
```

Use `mvn` for an approved Maven installation on PATH. Add `--cwd 'C:\attempt\worktree\module'` for a module-specific command. Pass arguments individually, never one quoted shell expression such as `"mvn verify"`. Do not chain commands or pipe the runner into `Tee-Object`/`Select-String`. Use separate invocations for separate checks. Without `--output-root`, evidence goes to a unique system-temporary directory. Evidence directories must be outside the tested checkout.

The runner prints a short summary and a `receipt` path. The receipt records `native_exit_code`, `result_policy`, `exit_code_disagrees`, `status` (`passed`, `failed`, `unavailable`), command, timing, source metadata and separate `stdout`/`stderr` paths. An exit-code discrepancy is diagnostic, not a Maven failure.

- Maven's complete `BUILD SUCCESS` summary passes, **even with exit code 1**; `BUILD FAILURE` fails, even with exit code 0.
- Missing or conflicting Maven summaries are `unavailable`. Do not infer success from a zero exit or an arbitrary mention of `BUILD SUCCESS` inside another message.
- Standard Maven summaries are recognized in either output stream, including ANSI-colored output. This deliberately does not interpret arbitrary custom log formats as proof of success.
- For non-Maven tools, exit 0 with unchanged source is process success, even if stderr contains warnings; a nonzero exit fails.
- A terminal-wrapper error with a complete successful receipt for that invocation is a wrapper discrepancy, not evidence of Maven failure. Missing/incomplete receipts do not prove success.
- Missing tools, timeouts, excessive output and source changes are unavailable evidence, not automatically a code defect.
- Raw logs avoid implicit Windows cp1252 decoding. Decode explicitly when viewing; UTF-8 with replacement is useful for diagnosis but does not guarantee the original tool used UTF-8.

The default `--result-policy auto` detects `mvn`/`mvnw` and their Windows launchers. No configuration change is needed for normal Maven builds. Use `--result-policy maven` for an inspected custom launcher that runs only Maven, not for a script combining Maven and another tool. `--result-policy native` is available as an explicit override. The runner itself exits 0 on a passed classified result, so Maven `BUILD SUCCESS` plus launcher exit 1 produces a successful runner exit, while preserving the original 1 in the receipt. Timeouts, output limits or source changes still prevent success.

If needed, capture `$LASTEXITCODE` immediately after the runner, before another native command. Read the saved receipt if the PowerShell host still reports a different status. Microsoft explains native exit codes and PowerShell stream handling in [Running commands in the shell](https://learn.microsoft.com/en-us/powershell/scripting/learn/shell/running-commands).

The runner attempts to terminate its own process tree on timeout/output limit; inspect for lingering children if termination fails. Keep logs local and redact excerpts: logs and arguments may contain private information. Known token/password flags are rejected, but this is not a universal secret detector. Do not put credentials in command arguments.

This is an execution/evidence aid, not a security sandbox or automatic certification. Agents must still inspect commands, respect network/installation permissions, verify test counts/skips and prove the finding corrected. Do not edit receipts, suppress errors globally, disable TLS or weaken execution policy to make checks pass. The Windows-specific batch test is skipped outside Windows; run `test_run_validation.py` on supported Windows hosts before corporate rollout.

## Sonar compliance — proposed, not yet enforced

The kit does **not yet run or enforce your corporate Sonar Quality Gate**. Passing compilation and targeted revalidation does not guarantee compliance with the server's active rules.

Recommended integration:

1. Use the approved corporate server, project key, scanner version, Quality Profile/Gate, coverage settings and supported branch/PR analysis mode. Prefer the existing CI configuration. Do not send source to a scanner's default/public destination or substitute another profile.
2. Implement the finding and regression tests in the isolated feature worktree. Connected IDE analysis/local analyzers can provide early feedback but do not replace the server gate.
3. Run the approved scanner/build and wait for **that exact submitted analysis**. Successful report upload is not a passed gate. `sonar.qualitygate.wait=true` and a bounded `sonar.qualitygate.timeout` allow the scanner to wait and fail on a gate failure; this can cause a genuine nonzero Maven exit. See [Sonar analysis parameters](https://docs.sonarsource.com/sonarqube-server/analyzing-source-code/analysis-parameters/parameters-not-settable-in-ui).
4. Retrieve violations and fix new-code issues introduced by the change. Bound the correction loop, for example to three attempts. Rerun tests and analysis after edits. Do not mute rules, exclude edited files, weaken thresholds or add blanket suppressions. Report unrelated legacy debt rather than silently expanding the finding's scope. A passing gate does not necessarily mean zero issues: enforce both the approved gate and the team's agreed rule-violation policy.
5. Independently revalidate the original finding after Sonar corrections. Both results must match the actual final source/commit, not an older analysis, main branch, another PR or another project.
6. Resolve the Hub finding only after all required checks pass. If Sonar runs on PRs in CI, the PR must exist **before** its gate result: create an unmerged/draft PR, keep the finding open/queued while checks/corrections are pending, then complete it after successful CI evidence. This needs a deliberate extension of the current delivery state machine, not an assertion that a pending PR passed.

Branch/PR support depends on the product/version/edition. If feature analysis is unavailable, do not analyze a feature worktree as production main. Agree an alternative CI/test-project strategy with the Sonar administrator. [Branch analysis documentation](https://docs.sonarsource.com/sonarqube-server/analyzing-source-code/branch-analysis/introduction).

Authentication is scanner-specific. Current Sonar documentation explicitly notes that `SONAR_TOKEN` is **not supported by SonarScanner for .NET**. Reuse the corporate CI integration's secure .NET authentication mechanism rather than placing secrets in agent prompts, receipts or arguments. Preserve corporate proxy and certificate validation.

Before wiring the server adapter and completion gate, confirm SonarQube Server vs Cloud, version/edition, existing scanner/CI configuration, project-key selection and branch/PR support. No Sonar token should be pasted in chat.
