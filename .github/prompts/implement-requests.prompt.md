---
name: implement-requests
description: Implement user-specified Hub requests on isolated branches, verify independently and create PRs.
argument-hint: "[requests=<request-UUID>,<request-UUID>] [remote=origin]"
agent: Request Implementation Manager
---

Implement Specified requests assigned to this checkout's configured Hub project. With no filter use all returned Specified requests; the optional comma-separated `requests` UUID filter must never widen. Use only the user-accepted specification. Follow Request Implementation Manager for readiness, claims, isolated feature branches, optional Sonar, direct independent verification before/after committing, PRs and Hub completion. Never merge, close requests, rewrite approved specifications, self-certify, or invoke a revalidation manager as an intermediary. No model override. Leave tools unset here so configured Sonar MCP tools enabled in the chat remain available.
