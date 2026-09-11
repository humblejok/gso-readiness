---
name: analyse-requests
description: Analyze open Hub bug/feature requests against this project and submit specifications for human acceptance.
argument-hint: "[requests=<request-UUID>,<request-UUID>]"
agent: Request Analysis Manager
---

Analyze the Open requests assigned to this checkout's configured Hub project, optionally limited by the comma-separated `requests` UUID filter. Use the currently selected model. Follow Request Analysis Manager: inspect backend/frontend responsibilities, ask relevant questions in this chat, and submit complete implementation specifications to the Hub as Analyzed. Do not accept the analysis for the user, implement code, claim findings, create branches/PRs, or expand the request filter. User review and acceptance in the Hub moves requests to Specified.
