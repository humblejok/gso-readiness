---
name: implement-findings
description: Implement Hub findings marked to implement, independently revalidate them, and create feature-branch PRs.
argument-hint: "[findings=ARCH-C001,COR-C005] [remote=origin]"
agent: Finding Implementation Manager
---

Use the Finding Implementation Manager to process open findings marked to implement in this checkout's configured Hub project. An optional comma-separated `findings` list limits the queue; do not broaden it. This request authorizes the scoped code changes, per-finding remote feature branches, targeted validation, commits/PRs and factual Hub completion comments described by the workflow. Never merge PRs, modify the original checkout, force-update existing remote branches, execute untrusted proposal commands blindly or change unrelated findings. Every feature branch must start from the original checked-out branch/commit, not the preceding finding's branch. Stop and report unavailable permissions or unsafe/ambiguous state.
