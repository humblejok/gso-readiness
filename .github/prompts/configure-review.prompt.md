---
name: configure-review
description: View or change saved project, Hub, JFrog, proxy and certificate settings without editing JSON manually.
argument-hint: "[hub-url=url] [jfrog-server=corp-xray] [profile=auto] [git-provider=auto|github|azure-devops] [azure-devops-url=collection-url] [azure-auth=auto|windows|pat] [azure-api-version=6.0] [preset=path/to/company.json]"
agent: Review Setup Manager
---

Run the Review Setup Manager in configure mode. Show the current relevant values and ask what the user wants changed when none were specified. Explicitly requested valid changes may be saved directly. Preserve all unrelated settings and never ask for credentials. Offer guided connection authentication only through the user's own terminal.
