---
name: update-vulnerable-dependencies
description: Independently scan and conservatively update eligible vulnerable direct dependency declarations using Artifactory-available Xray fixes.
argument-hint: "[severity=all|critical|high|medium|low|informational|unknown|comma-list] [jfrog-server=config-id] [artifactory-repositories=repo1,repo2] [allow-dirty=false]"
agent: Vulnerability Upgrade Manager
---

Run the vulnerability upgrade manager with `action=update`.

Use `severity=all` when omitted. A named severity is an exact filter; accept a comma-separated explicit set. Pass through only an existing `jfrog-server` configuration ID, optional Artifactory local/remote-cache repository keys, and explicit `allow-dirty=true`.

This command is independent from `/check-vulnerability-upgrades`: do not require or consume its output. Run a new JFrog Xray audit, independently derive conservative exact fixed versions, verify exact Artifactory availability, rediscover direct declarations, and update only declarations the deterministic script marks safe and editable. Generate JSON and Markdown including missing libraries, skipped/manual items, backups, changed files, and generated lockfiles requiring refresh. Do not update generated lockfiles or run restore/build/test commands automatically.

