# Multiple collections and project-specific credentials

The review kit now separates company defaults, user-approved destinations, and project credential selection. This is a kit-side change: it requires no Hub migration or Hub API change. Update the `.github` kit in the projects where you use it.

## Company preset

A company preset should not choose one active Azure collection or reuse a stable repository ID for unrelated repositories. It can contain common defaults and non-secret credential labels:

```json
{
  "schema_version": "1.0",
  "project": {
    "profile": "auto",
    "git_provider": "auto",
    "azure_api_version": "6.0",
    "hub_credential_ref": "default",
    "azure_credential_ref": "default"
  },
  "user": {
    "azure_auth": "auto",
    "hub_url": "https://hub.example.invalid"
  }
}
```

Add your approved proxy/certificate/JFrog defaults and installer downloads as before. Do not include `repository_id`, `repository_name`, tokens or personal paths unless they are intended for one specific project/machine. The same `default` reference in different projects does **not** share their credentials: the stable project ID is also part of the storage key. If exporting settings manually, omit legacy `azure_devops_url`; retain `azure_devops_collections` only when you intend the distributed preset to grant trust to every listed collection. A trusted administrator can explicitly distribute such an approved list; otherwise approve collections individually on each workstation.

## Approve multiple Azure collections

In Copilot chat:

```text
/configure-review azure-trust-collection=https://ado.example.invalid/CollectionA
/configure-review azure-trust-collection=https://ado.example.invalid/CollectionB
```

Each command adds one exact URL without removing earlier approvals. The user file outside Git contains:

```json
{
  "schema_version": "1.0",
  "user": {
    "azure_auth": "windows",
    "azure_devops_collections": [
      "https://ado.example.invalid/CollectionA",
      "https://ado.example.invalid/CollectionB"
    ]
  }
}
```

Every project derives its collection, Azure project and repository from its own Git remote. There is no global active collection to switch. Trust is checked before Windows authentication or PAT retrieval for network requests. Approving a collection does not grant server permissions; existing Git authentication and Azure authorization remain in effect. Wildcard trust is not supported.

To remove one approval, use `/configure-review azure-untrust-collection=https://ado.example.invalid/CollectionA`. This does not revoke/delete any token, contact the server, or remove other approvals. An explicit empty `azure_devops_collections` list revokes all collection trust. Do not unset the list to revoke trust if a legacy `azure_devops_url` still exists: absent-list mode deliberately supports that legacy singleton.

Terminal equivalents (use your review Python interpreter):

```text
python .github/graphify-review/scripts/setup_review.py configure --repository . --non-interactive --trust-azure-collection https://ado.example.invalid/CollectionA --trust-azure-collection https://ado.example.invalid/CollectionB
python .github/graphify-review/scripts/setup_review.py configure --repository . --non-interactive --untrust-azure-collection https://ado.example.invalid/CollectionA
```

The wizard asks to approve the detected remote's collection only when it is missing. It preserves other approvals. Adding/removing trust automatically migrates a legacy singleton into the list without losing it. Existing presets/settings containing only `azure_devops_url` continue to work until a list is present; an explicit list, even empty, is authoritative. Presets still fill missing values rather than overwriting an existing user's list; use the additive trust command to add another collection later.

## Choose and save a project token

In each project, use a stable repository ID from `/setup-review`, then select a non-secret reference:

```text
/configure-review hub-credential-ref=reviewer azure-credential-ref=reviewer
```

This saves only labels in `.github/graphify-review/settings.json`. You can use `default`, `reviewer`, `implementation` or another label of 1–64 letters/digits/dots/underscores/hyphens, starting with a letter/digit. Labels are not tokens, Hub roles or permission grants. A reference can distinguish tokens for different Hub workspaces at the same Hub URL; the user is responsible for selecting the intended workspace/token and least-privilege server permissions.

From that project's own Windows terminal, save its Hub token:

```powershell
.\.graphify-review-venv\Scripts\python.exe .github\graphify-review\scripts\publish_review.py login --repository .
```

Paste the token at the hidden prompt, not into Copilot. Native storage is keyed by **service destination + stable repository ID + reference**, independently for Hub and Azure. Logging into project B does not replace project A's token, even when they share the same Hub/collection/reference name. Two checkouts of the same stable project ID and same reference intentionally select the same local credential. Each workstation/user stores its own secrets; committed references do not distribute authentication.

If Azure accepts your Windows identity, no Azure PAT is needed. To use a PAT for just this project:

```text
/configure-review project-azure-auth=pat azure-credential-ref=implementation
```

Then in its terminal:

```powershell
.\.graphify-review-venv\Scripts\python.exe .github\graphify-review\scripts\azure_devops.py login --repository .
```

Azure login reads the selected remote (default `origin`; override with `--remote NAME`) locally, verifies its collection is approved, and stores the PAT in the project's selected slot. It performs no network request. `project.azure_auth` overrides the user/company default; changing it does not switch authentication for other projects. Clear it with `--unset project.azure_auth` to inherit the user default again. Selecting a PAT reference alone does not force PAT mode.

After configuration, commit the non-secret project settings before strict review/implementation. Run `/doctor-review network=true` for an explicit read-only connectivity check. The existing Hub scopes and Azure repository permissions still apply; choosing a label does not add permissions.

## Existing credentials, rotation and logout

- New logins require a configured stable project ID and always write a project-specific slot. No new destination-wide tokens are written.
- With an explicit reference, a missing project token is an error. The kit never borrows another reference, project or legacy destination-wide token.
- For compatibility, a project with no explicit reference first tries its default project slot, then its previously saved destination-wide token if no project token exists. Set an explicit `default` reference and log in to opt into strict selection. Existing destination-wide credentials are neither copied nor deleted automatically.
- `logout --repository .` affects only the selected project/reference/destination. It overwrites the local secret with a non-secret logout marker so an old shared token cannot silently become active again. Other projects and secret-manager environment overrides remain unaffected. Log in again to replace the marker. Revoke the actual token in the Hub/Azure server if it must be invalidated globally.
- Finish active `/implement-findings` attempts before rotating their tokens or changing references/project authentication. Hub claims are bound to the token that started them. New attempt states also pin the non-secret project identity, references and effective Azure authentication mode and refuse continuation after a selection change. Do not change saved state to bypass that check.
- A full import envelope must match the configured checkout's stable project ID before any project token is used. Use the matching project's checkout/settings for another project's envelope.

## Headless environment overrides

Supply secrets through an approved secret manager, not checked-in files, command arguments, chat or user-wide global environment settings. A token override still requires an exact destination binding, and now also a project binding whenever the checkout has a stable project ID:

| Service | Secret | Destination | Project binding | Reference binding |
| --- | --- | --- | --- | --- |
| Hub | `FINDING_HUB_TOKEN` | `FINDING_HUB_TOKEN_URL` | `FINDING_HUB_TOKEN_REPOSITORY_ID` | `FINDING_HUB_TOKEN_CREDENTIAL_REF` |
| Azure | `GRAPHIFY_AZURE_PAT` | `GRAPHIFY_AZURE_PAT_URL` | `GRAPHIFY_AZURE_PAT_REPOSITORY_ID` | `GRAPHIFY_AZURE_PAT_CREDENTIAL_REF` |

The project value must equal `project.repository_id`. The reference value is required when an explicit reference is selected and must equal that label. A supplied reference is checked even without an explicit setting (the effective name is `default`). Mismatches block instead of falling back to native credentials. This is an intentional tightening for existing CI jobs: add the non-secret project/reference metadata to their secret injection. Unconfigured legacy callers still accept destination-only binding; normal guided project use should always configure a stable ID and explicit reference.

Environment overrides belong to the invoking process and its children, not inherently to a repository folder. Launch the actual tool process with the intended environment; do not assume an already-running VS Code instance picks up variables from a different terminal. Use native project slots for normal interactive multi-project work.
