---
name: doctor-review
description: Diagnose review setup with actionable messages; no installs, audits or project edits.
argument-hint: "[network=false|true]"
agent: Review Setup Manager
---

Run the Review Setup Manager in doctor mode. Default to local checks only. Pass --network only if the caller explicitly supplied network=true or asked to test connectivity. Explain missing tools versus actual configuration errors. Do not automatically fix, install, authenticate or change anything.
