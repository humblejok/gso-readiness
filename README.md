# Graphify VS Code Production Review Kit

Original concept and project creator: **De Jonckheere Stéphane (humblejok)**.

Copyright © 2026 De Jonckheere Stéphane (humblejok). Licensed under [GNU AGPL-3.0-only](LICENSE). See [NOTICE](NOTICE) for attribution and scope, and [third-party acknowledgements](THIRD_PARTY_NOTICES.md) for Graphify and the integrated tools.

An evidence-first production-readiness workflow for VS Code, GitHub Copilot agents, and Graphify. The runtime kit lives under `.github/`; the repository-root Windows and macOS/Linux installers are distribution helpers. The authoritative output is JSON and the companion `REVIEW.md` is generated for people.

## Revalidate and implement findings

To check one corrected item without rerunning the entire review:

```text
/revalidate-finding finding=ARCH-C001 baseline=.github/graphify-review/output/runs/<baseline-run>/review.json
```

Commit intended changes first. Add `allow-dirty=true` only for a provisional local check. The command independently verifies that one finding and creates a new `revalidation-envelope.json`; other findings remain **not revalidated**, and whole-project grades are unchanged. It does not rewrite `REVIEW.md` or an older full-review envelope. Add `publish=true` to update only that finding in the configured Hub (requires a clean commit, the matching imported baseline and a token with `findings:read` + `reviews:write`). Use `/full-project-review mode=revalidate baseline=...` when you need an updated whole-project report and grades.

To implement findings selected in the Hub:

1. Upgrade the Hub and run its database migrations; see [Hub upgrade instructions](hub/README.md#upgrade-for-finding-implementation).
2. On **Findings**, click **Implement** on open items. **Cancel implementation** removes the flag. Queued items have an editable remediation on their details page. Rejected/inconclusive items are hidden by default; use **Show rejected and inconclusive** to include them.
3. Keep your existing Git authentication. GitHub/GitHub Enterprise additionally uses `gh`; Azure DevOps Server/Services uses the REST adapter and requires **neither gh nor Azure CLI**. See [Azure DevOps setup](#azure-devops-server-and-services). The checkout must be clean, on a named branch, and match its remote branch tip. Commit shared project settings from `/setup-review` so they exist on new worktrees.
4. Issue a repository-restricted Hub token with `findings:read` and `findings:implement` (optionally also `reviews:write` for publishing). Save it using the existing terminal login helper; never paste it into Copilot chat:

```text
<review-python> .github/graphify-review/scripts/publish_review.py login --repository .
```

Then use either:

```text
/implement-findings findings=ARCH-C001,COR-C005
/implement-findings
```

Without a filter, all queued open findings for this configured project are selected. Each runs sequentially in an isolated worktree on `feature/<short-ID>`, from the original checkout's commit. The remote branch is created before edits. A passing independent revalidation allows an explicit-file commit; a second revalidation of that clean commit precedes pushing and creating a PR to the original branch. Only then is the Hub finding resolved, unqueued and given a factual summary/PR link. **Resolved on the feature branch does not mean merged or deployed.** Nothing automatically merges a PR or resolves Jira.

Failures keep the finding open and queued with a reason when the Hub claim is still valid. Existing feature branches block rather than being overwritten. Cancellation, edited proposals and newer imported reviews invalidate stale workers. Worktrees/branches are retained for inspection; uncertain Hub writes retry the identical saved completion. See [recovery, permissions and API details](docs/finding_implementation_workflow.md).

### Azure DevOps Server and Services

Update the `.github` kit in the reviewed project and deploy/restart the updated Hub. This adapter adds no database migration beyond the existing implementation migration. Git still handles branches/commits/pushes with your configured credentials; only PR operations use the hosting API. The Hub does not receive Azure credentials.

For Azure DevOps Server 2020, configure this once in Copilot chat (replace the placeholder with the collection part of your Git remote, excluding `/Project/_git/repository`):

```text
/configure-review git-provider=azure-devops azure-api-version=6.0 azure-devops-url=https://ado.example.invalid/CollectionName azure-auth=windows
/doctor-review network=true
```

`/setup-review` can also guide this configuration. The terminal installer wizard offers these choices when a Hub is configured. Host, collection, project and repository names are not hard-coded; `git-provider=auto` detects Azure's `/_git/` URL structure. Shared provider/version settings belong to the project; trusted collection URL and authentication preference stay in the user's settings outside Git. Commit changed project settings before implementation.

The Windows API bridge uses the signed-in Windows identity through PowerShell/.NET, with no password/PAT needed when the server accepts that identity. Git's successful authentication alone does not prove API access. The explicit read-only check can also be run directly:

```powershell
.\.graphify-review-venv\Scripts\python.exe .github\graphify-review\scripts\azure_devops.py check --repository .
```

The bridge uses Windows certificate trust, saved/environment proxy and `NO_PROXY`; it does not import a PEM file into Windows, read Git credentials, enable proxy Windows credentials automatically, bypass script execution policy or disable TLS. Ask IT to provision trusted corporate certificates/approve the supplied `.ps1` if needed. For an internal host, configure proxy bypass only if your network permits direct access. A read-only success confirms repository access, not permission to push/create PRs.

If Windows authentication is unavailable, select `azure-auth=pat` and use your own terminal to run `azure_devops.py login` with the review Python interpreter. This saves a PAT in native OS credential storage for that collection only. Use least-privilege Code read/write access and repository PR permissions. Never paste it into chat or settings. PAT transport supports macOS/Linux too and uses the saved PEM/proxy configuration. For approved secret-manager use, `GRAPHIFY_AZURE_PAT` requires a matching `GRAPHIFY_AZURE_PAT_URL`; it does not reuse the Hub token. `azure-auth=auto` chooses Windows identity on Windows for on-premises hosts, PAT otherwise; authentication errors never silently switch modes.

Then use `/implement-findings` as usual. Azure creates an active PR to the original branch, verifies the exact source commit and repository, and never enables auto-completion. An uncertain create response is recovered using the same saved attempt; the adapter refuses to blindly POST a second PR. API 6.0 is the default for Server 2020 compatibility; 7.0/7.1 are optional for newer deployments. GitHub support is preserved; GitLab/Bitbucket and Azure SSH remotes are not yet supported by the PR adapter. See [workflow details](docs/finding_implementation_workflow.md#hosting-adapters).

## Guided setup (recommended)

You need Python 3.10+, VS Code with Copilot installed/signed in, and the project folder you want reviewed. This repository distributes a Copilot command kit, not a separately installed VS Code extension. You do not need to install or host Finding Hub to run reviews.

**Already have the `.github` kit?** Open the project in VS Code and run:

```text
/setup-review
```

The assistant asks for the project name, stable repository ID, default branch and profile, then any optional JFrog, Finding Hub and corporate-network settings. It detects existing JFrog server IDs and explains missing prerequisites. Credentials are never requested in chat. First-use review commands offer this setup flow when project settings are incomplete. If you decline, existing explicit-argument workflows remain available.

**Installing for the first time?** Ask your administrator for an approved local `company.json` preset, then run one command from the folder containing the installer:

```powershell
.\install-graphify-review.ps1 -TargetRepository 'C:\Projects\orders-api' -Preset '.\company.json'
```

```bash
python3 install-graphify-review-unix.py --target-repository '/path/to/orders-api' --preset './company.json'
```

The installers verify downloads and launch the same terminal wizard after installation. Without a preset, they ask for the approved kit URL and checksum when run interactively; your administrator should supply these. Use `-SkipSetup` / `--skip-setup` to defer the wizard until `/setup-review`. Automated installation uses `-NonInteractive` / `--non-interactive` and explicit parameters or a preset; it never waits for wizard answers. Presets fill missing saved values, while explicit installer options override preset download settings. A Java truststore download is optional: omit both its URL and checksum if it is unnecessary. Optional JFrog downloads are selected for the current OS/architecture.

### Change settings or diagnose a problem

```text
/configure-review
/configure-review hub-url=https://hub.company.example
/configure-review jfrog-server=corp-xray
/doctor-review
/doctor-review network=true
```

`/configure-review` lets you view/change/clear settings without editing JSON. `/doctor-review` performs local checks and explains next steps; `network=true` explicitly adds Artifactory ping and an unauthenticated Hub health check. It never runs audits, installs tools, modifies settings or uploads a report. A successful ping does not establish Xray licensing, a completed security audit, or Copilot authentication. Semgrep still needs an approved repository rule configuration.

After setup, repeat arguments are no longer necessary:

```text
/full-project-review graph=artifacts/graph.json
/check-vulnerability-upgrades severity=critical,high
/update-vulnerable-dependencies severity=critical,high
/export-review review=.github/graphify-review/output/runs/<run-id>/review.json
```

These commands use saved defaults unless explicitly overridden. Export still uses the historical source commit/branch, never today's checkout or the configured default branch. Saving a Hub URL never enables automatic transmission. To send a result explicitly, use [`/publish-review`](#publish-results-to-the-configured-hub); manual browser upload remains available without an API token.

### Where information is stored

| Location | Contents |
|---|---|
| `.github/graphify-review/settings.json` | Shareable project ID/name/default branch, profile, scanner choice and optional Artifactory repository keys |
| Windows: `%LOCALAPPDATA%\GraphifyReview\settings.json` | User connection and machine settings |
| macOS: `~/Library/Application Support/GraphifyReview/settings.json` | User connection and machine settings |
| Linux: `${XDG_CONFIG_HOME:-~/.config}/graphify-review/settings.json` | User connection and machine settings |

Commit project settings to share the repository ID and satisfy strict clean-worktree review checks. Never generate a different ID for the same project on each machine. User files remain outside Git; keep them private even though tokens/passwords are forbidden. Modified settings files get timestamped backups; identical saves do nothing. Installers preserve existing project settings and never copy the publisher's project identity from a downloaded bundle. Changing a setting does not change `policy.yaml` or weaken review gates.

Explicit command options override saved defaults. Configured user network values override inherited environment values **inside review child processes only**; unspecified values retain the inherited environment. The mapping is: `proxy_url` → both cases of HTTP(S)_PROXY; `no_proxy` → both cases of NO_PROXY; `ca_bundle` → PIP_CERT, REQUESTS_CA_BUNDLE and SSL_CERT_FILE; `pip_index_url` → PIP_INDEX_URL; `jfrog_server_id` → JFROG_CLI_SERVER_ID; `jfrog_cli_path` adds its parent to the child PATH. A configured Java truststore replaces only the trustStore/trustStoreType options in child MAVEN_OPTS, preserving unrelated options. Clearing a setting restores inherited behavior; it does not erase OS environment variables previously installed by an administrator or installer. The setup wizard itself does not write OS environment variables or shell profiles. Legacy installer PATH/MAVEN_OPTS behavior is retained when its optional tool/truststore artifacts are installed.

To create/update JFrog authentication, run this **in your own terminal**, not inside an agent tool or chat:

```text
python .github/graphify-review/scripts/setup_review.py jfrog-login --repository .
```

Use `python3` where appropriate. It invokes the native [JFrog configuration prompts](https://docs.jfrog.com/integrations/docs/jf-config-add); authentication stays in JFrog's existing credential store. Keep TLS verification enabled. Merely saving a new JFrog URL does not reconfigure an existing CLI server: use this helper, then `/doctor-review network=true`. Optional Hub publishing has its own terminal login below; Hub tokens never go in settings, presets or chat.

### Administrator presets and unattended configuration

Start from [corporate-preset.example.json](corporate-preset.example.json). Replace every placeholder URL/checksum, remove unused optional integrations/downloads, and distribute the preset through an approved trusted channel alongside the installers. Download hashes protect artifacts only if the preset itself is trusted. Neither installer fetches or executes a preset. Do not put tokens, passwords, arbitrary environment variables, executable commands, or a single shared repository ID for unrelated projects in it. `installer.jfrog_cli` is a platform map (`windows-amd64`, `windows-arm64`, `darwin-amd64`, `darwin-arm64`, `linux-amd64`, `linux-arm64`), with each entry containing `url` and `sha256`. Provide a supported binary for the machines you manage or rely on an existing CLI installation.

User settings may include absolute `ca_bundle` (PEM), `java_truststore` (Java cacerts), and `jfrog_cli_path` paths; these are machine-specific. Installed truststore/CLI paths become wizard defaults. Certificate trust for the initial download must already exist: Windows uses its certificate store; Unix accepts `--ca-bundle`. A downloaded Java truststore cannot bootstrap the trust needed to download itself. Proxy authentication, Maven repository/proxy settings.xml, and organization-approved Semgrep rules remain administrator responsibilities; HTTP_PROXY alone does not configure Maven's repository proxy settings.

The standard-library CLI works before the dedicated virtual environment exists:

```text
python .github/graphify-review/scripts/setup_review.py configure --repository .
python .github/graphify-review/scripts/setup_review.py configure --repository . --preset company.json --non-interactive
python .github/graphify-review/scripts/setup_review.py configure --repository . --non-interactive --set user.hub_url=https://hub.company.example
python .github/graphify-review/scripts/setup_review.py configure --repository . --non-interactive --unset user.proxy_url
python .github/graphify-review/scripts/setup_review.py show --repository .
python .github/graphify-review/scripts/setup_review.py doctor --repository . --network
```

Use `--set project.<key>=<value>` for project changes; `--set project.artifactory_repositories=repo-a,repo-b` accepts a comma-separated list. `--user-settings <absolute-file>` is available on the setup CLI for isolated/portable configuration and tests; normal review commands use the OS-specific user location above. Diagnostics exit `1` when attention is required; invalid settings/cancelled setup exit `2`. If installation succeeds but setup fails, the installed kit and backups remain: correct the issue and rerun `/setup-review`.

## Optional Finding Hub web application

The separate [Django Finding Hub](hub/README.md) in `hub/` provides customer workspaces, review uploads, findings/remediation history, scoped API tokens, optional Jira delivery, configurable subscriptions and enterprise branding. It supports PostgreSQL for hosting and SQLite for development. The existing kit installers do not install the Hub or change its database.

See [local setup and deployment](hub/README.md), the [updated SaaS/white-label specification](docs/graphify_finding_hub_spec.md), and the Hub's production launch checklist. Billing and Jira need operator configuration; native enterprise SSO/MFA and several operational extensions are explicitly not included in the initial pilot. The application remains AGPL-3.0-only, with no proprietary enterprise license assumed.

The kit keeps responsibilities deliberately separate:

- Graphify provides structural evidence and investigation targets.
- Deterministic tools establish objective facts.
- Copilot specialist agents interpret scoped evidence.
- Independent verification decides which candidates are supported.
- Policy-driven Python calculates confidence, technical-quality category scores, framework-profile caps, release hard gates, and release state.
- The synthesizer explains authoritative JSON without changing verified finding semantics; Python renders Markdown deterministically.

Missing evidence lowers assessment confidence, not project quality. A Graphify anomaly is not a finding until verified in source. A named CVE is never accepted without structured scanner/advisory evidence.

## Outputs

Review runs are immutable, versioned directories beneath `.github/graphify-review/output/runs/`:

```text
output/
└── runs/
    └── <timestamp>-<identity>/
        ├── run-context.json
        ├── evidence/
        │   ├── project-graph.json
        │   ├── profile.json
        │   ├── normalized-evidence.json
        │   ├── aspnet-anchors.json
        │   ├── spring-anchors.json
        │   ├── tool-results.json
        │   ├── samples.json
        │   ├── manifest.json
        │   ├── verified-findings.json
        │   ├── confidence.json
        │   └── score.json
        ├── review.json
        └── REVIEW.md
```

Current `review.json` and normalized evidence use schema version `2.1`. Earlier review JSON can be imported as a baseline but is not silently treated as current output.

## Workflow

```text
source snapshot + Graphify + deterministic audits + policy + resolved profile
                 │
                 ▼
        normalized evidence
                 │
                 ▼
 anchors + baseline + risk-directed + stable random scoping
                 │
                 ▼
       independent specialists
                 │
                 ▼
       adversarial verification
                 │
                 ▼
  production-impact classification
                 │
                 ▼
 immutable verified-findings.json
                 │
                 ▼
 deterministic confidence + scoring
                 │
                 ▼
       review.json → deterministic REVIEW.md
```

The manager orchestrates only. It does not perform specialist analysis, calculate scores, or choose release state.

## Install on Windows from Artifactory

[`install-graphify-review.ps1`](install-graphify-review.ps1) installs or updates the kit without administrator rights. Publish these artifacts to trusted HTTPS Artifactory locations:

1. A ZIP containing exactly one `.github` directory. Keep `.github/graphify-review/LICENSE`, `NOTICE`, and `THIRD_PARTY_NOTICES.md` in the archive. The installer validates these notices together with the manager, prompt, and kit version before merging it.
2. The corporate Java `cacerts` truststore.
3. Optionally, your approved `jf.exe` binary.

For example, create the kit archive from the source repository with:

```powershell
Compress-Archive -Path .\.github -DestinationPath .\graphify-review-github.zip -CompressionLevel Optimal
```

Record each artifact's SHA-256 value when publishing it:

```powershell
Get-FileHash .\graphify-review-github.zip -Algorithm SHA256
Get-FileHash .\cacerts -Algorithm SHA256
Get-FileHash .\jf.exe -Algorithm SHA256
```

Use a short-lived token through the current process environment so it does not appear in the script or PowerShell command history:

```powershell
$env:ARTIFACTORY_ACCESS_TOKEN = '<short-lived-access-token>'

try {
  .\install-graphify-review.ps1 `
    -TargetRepository 'C:\src\my-api' `
    -GithubBundleUrl 'https://artifactory.example.invalid/artifactory/REPLACE_ME/graphify-review-github.zip' `
    -GithubBundleSha256 '<64-character-sha256>' `
    -TrustStoreUrl 'https://artifactory.example.invalid/artifactory/REPLACE_ME/cacerts' `
    -TrustStoreSha256 '<64-character-sha256>'
}
finally {
  Remove-Item Env:\ARTIFACTORY_ACCESS_TOKEN -ErrorAction SilentlyContinue
}
```

If Artifactory uses Windows authentication, omit the token and add `-UseDefaultCredentials`. An explicit authenticated proxy can be selected with `-Proxy 'https://proxy.example.invalid:8443' -ProxyUseDefaultCredentials`; otherwise PowerShell uses the Windows proxy configuration. To install the approved JFrog CLI at the same time, add:

```powershell
-JfrogCliUrl 'https://artifactory.example.invalid/artifactory/REPLACE_ME/jf.exe' `
-JfrogCliSha256 '<64-character-sha256>'
```

The installer:

- refuses non-HTTPS artifact URLs, credentials embedded in URLs, placeholder URLs, missing hashes, and hash mismatches;
- backs up an existing repository `.github` directory below `%LOCALAPPDATA%\GraphifyReview\backups` before merging, so unrelated workflows and repository settings remain in place;
- installs the truststore as `%LOCALAPPDATA%\GraphifyReview\truststore\cacerts`;
- preserves existing user `MAVEN_OPTS` content while replacing the installer-owned `javax.net.ssl.trustStore` and `trustStoreType` options;
- optionally installs `jf.exe` under `%LOCALAPPDATA%\GraphifyReview\bin` and adds that directory to the user `PATH` idempotently.

Use `-TrustStoreType JKS` or `-TrustStoreType PKCS12` only when the published store requires an explicit type. The script intentionally does not persist a truststore password. After installation, close every VS Code window and restart VS Code so Copilot and its child processes inherit `MAVEN_OPTS` and `PATH`.

The HTTPS download itself relies on the Windows certificate store. If Windows does not yet trust the certificate presented by Artifactory, deploy the corporate root/intermediate CA to Windows first through your normal enterprise mechanism; the installer never bypasses TLS validation.

For corporate distribution, Authenticode-sign the final `.ps1` with your organization's code-signing certificate and publish that signed copy. This lets machines using an `AllSigned` execution policy run it without weakening PowerShell policy.

## Install on macOS and Linux from Artifactory

[`install-graphify-review-unix.py`](install-graphify-review-unix.py) is a standalone downloader/installer for macOS and Linux. It requires Python 3.10 or newer and uses only the standard library. Run it as your normal user; it needs neither `sudo`, Homebrew, nor additional Python packages.

Download this installer file and use the same `.github` ZIP and Java `cacerts` artifact as the Windows distribution. Provide their publisher-supplied SHA-256 hashes and your real Artifactory URLs:

```bash
python3 install-graphify-review-unix.py \
  --target-repository '/path/to/my-api' \
  --github-bundle-url 'https://artifactory.example.invalid/artifactory/REPLACE_ME/graphify-review-github.zip' \
  --github-bundle-sha256 '<64-character-sha256>' \
  --truststore-url 'https://artifactory.example.invalid/artifactory/REPLACE_ME/cacerts' \
  --truststore-sha256 '<64-character-sha256>'
```

For authenticated downloads, supply a short-lived bearer token through `ARTIFACTORY_ACCESS_TOKEN` in the process environment, preferably through your approved secret manager. Do not put tokens in the URLs or command arguments. Use `--token-env VARIABLE_NAME` to select another variable, and unset it after installation. Redirects are refused; use the final artifact download URLs. Windows integrated authentication is not supported by this installer.

To install an approved **raw Unix `jf` executable** (not `jf.exe`, a ZIP, or a tarball), add:

```bash
  --jfrog-cli-url 'https://artifactory.example.invalid/artifactory/REPLACE_ME/jfrog-cli/{os}-{arch}/jf' \
  --jfrog-cli-sha256 '<sha256-for-this-exact-platform-and-architecture-binary>'
```

The optional URL placeholders expand as follows. Adjust the surrounding path to match your Artifactory layout; a single universal checksum is not valid for different binaries.

| Machine | `{os}` | `{arch}` |
|---|---|---|
| macOS Apple Silicon | `darwin` | `arm64` |
| macOS Intel | `darwin` | `amd64` |
| Linux ARM64 | `linux` | `arm64` |
| Linux x86-64 | `linux` | `amd64` |

Architecture follows the Python process, so run a native Python on Apple Silicon when you want an ARM64 binary. JFrog installation is optional; an existing configured `jf` can be used without supplying these arguments. The installer never changes the JFrog server configuration.

By default the user installation lives under `~/.local/share/graphify-review/`, containing `truststore/cacerts`, `env.sh`, optional `bin/jf`, and unique `backups/` directories. `--install-root` selects another dedicated directory outside the target project. That path must contain no whitespace or shell metacharacters because Maven expands `MAVEN_OPTS`; the target project path may contain spaces.

All downloads and hashes are verified before installed files change. The installer validates ZIP paths and required license/notice files, backs up the existing `.github` tree and changed user files, merges the kit while retaining unrelated files, and backs out completed file writes if a later write fails. It rejects symlink destinations; users with symlink-managed shell profiles can use `--shell none` and add the environment source line themselves. Backups include a `manifest.json` mapping saved files to original locations. An identical rerun makes no file changes.

The generated Bash/Zsh-compatible `env.sh` preserves existing `MAVEN_OPTS` and appends the managed Java truststore options so they take precedence over earlier truststore settings. Repeated sourcing replaces its previous managed suffix. Optional `--truststore-type JKS` or `PKCS12` adds an explicit store type; no truststore password is persisted. It also adds the user `bin` directory to `PATH` when the installer manages a JFrog binary.

`--shell auto` uses `$SHELL`. For Bash it adds one managed source block to `.bashrc` and the first existing login file among `.bash_profile`, `.bash_login`, and `.profile` (creating `.profile` if none exists). For Zsh it uses `.zprofile` and `.zshrc`, respecting an absolute `ZDOTDIR`. Select `--shell bash` or `--shell zsh` explicitly if needed. Other shells require `--shell none` and their own equivalent environment setup.

After installation, activate the environment in a Bash/Zsh terminal:

```bash
. "$HOME/.local/share/graphify-review/env.sh"
```

Use the printed path if you selected a different install root. Fully quit all VS Code instances, then launch from that terminal with `code '/path/to/my-api'`. This ensures Copilot inherits the variables; launching from Finder, the Dock, or a desktop shortcut may not inherit shell settings. The installer cannot update its parent terminal's environment.

For a corporate proxy, use Python's normal proxy discovery (including `https_proxy`/`no_proxy`) or pass `--proxy 'http://proxy.example.invalid:8080'` with the real proxy host. For a private CA, add `--ca-bundle '/path/to/corporate-ca.pem'`. This must be an already trusted **PEM certificate bundle** for the HTTPS connection; the downloaded Java `cacerts` is a different format and cannot bootstrap its own download. TLS verification stays enabled. The post-install wizard can save an internal Python package index for review child processes. Maven repository/proxy settings remain in your existing corporate Maven configuration.

The review runtime remains `.graphify-review-venv`. It is bootstrapped manually or on the first Copilot command, as described below; an application's `.venv` is not modified by this installer.

## Corporate proxy configuration

Configure the proxy before running the installer or bootstrapping Graphify. Ask your IT team for the proxy host/port, any authentication requirements, the internal hosts that should bypass it, and the approved CA certificates. Replace every `.example.invalid` placeholder below. An `http://` proxy URL is common even for HTTPS destinations: it describes the connection to the proxy, which tunnels HTTPS traffic.

### Proxy environment for Python and JFrog CLI

On macOS/Linux, set these values in the Bash/Zsh terminal that will run the tools:

```bash
export http_proxy='http://proxy.example.invalid:8080'
export https_proxy="$http_proxy"
export no_proxy='localhost,127.0.0.1,artifactory.corp.example.invalid'
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"
export NO_PROXY="$no_proxy"
```

On Windows, use PowerShell process environment variables:

```powershell
$env:HTTP_PROXY = 'http://proxy.example.invalid:8080'
$env:HTTPS_PROXY = $env:HTTP_PROXY
$env:NO_PROXY = 'localhost,127.0.0.1,artifactory.corp.example.invalid'
```

Windows environment variable names are case-insensitive. On Unix, setting both cases consistently avoids differing tool preferences. `NO_PROXY` is a comma-separated list of hosts/domains; include your Artifactory host only when your network allows direct access to it. Avoid a blanket `*`. These examples configure the current process and its children. For persistence, place non-secret exports in your user shell configuration, or use Windows **Environment Variables → User variables**. See [pip proxy support](https://pip.pypa.io/en/stable/user_guide/#using-a-proxy-server) and [JFrog CLI proxy configuration](https://docs.jfrog.com/integrations/docs/configuring-the-cli#configure-proxy-support).

### Installer proxy options

For the Windows installer, append these options to the complete installation command above:

```powershell
-Proxy 'http://proxy.example.invalid:8080' -ProxyUseDefaultCredentials
```

Use `-ProxyUseDefaultCredentials` only when the proxy accepts your Windows identity; otherwise omit it. `-UseDefaultCredentials` authenticates to the artifact server and is a separate option. The installer's explicit proxy setting applies to its downloads; it does not configure pip, JFrog CLI, Maven, or VS Code globally.

The macOS/Linux installer uses proxy discovery, including the environment above. Alternatively append `--proxy 'http://proxy.example.invalid:8080'` to its installation command. Neither installer accepts credentials embedded in its explicit proxy URL. `ARTIFACTORY_ACCESS_TOKEN` authenticates artifact downloads, not the proxy. For NTLM/Kerberos or other authenticated corporate proxies, use an IT-approved authentication setup for each client; Windows integrated authentication does not automatically carry over to Python or JFrog CLI. Keep proxy passwords out of repository files, shell history, and shared diagnostic output.

### Certificates and the Graphify package download

The proxy route and certificate trust are separate settings. HTTPS inspection may require the corporate CA to be trusted by each client:

| Client | Certificate configuration |
|---|---|
| Windows PowerShell installer | Corporate CA in the Windows certificate store |
| macOS/Linux installer | Python's trusted CA store, or `--ca-bundle '/path/to/corporate-ca.pem'` |
| pip / Graphify bootstrap | Approved PEM CA bundle through `PIP_CERT`, when needed |
| Maven | The Java `cacerts` truststore configured by the installer in `MAVEN_OPTS` |
| JFrog CLI | Corporate CA in its supported trust configuration; see [JFrog TLS setup](https://docs.jfrog.com/integrations/docs/configuring-the-cli#tls-and-certificates) |

For Python package installation through an approved Artifactory PyPI repository, set the following before the first Graphify command. The PEM bundle must include the CA roots required by the package endpoints you use; it is not the binary Java `cacerts` file. [pip certificate configuration](https://pip.pypa.io/en/stable/topics/https-certificates/).

```bash
export PIP_CERT='/path/to/approved-ca-bundle.pem'
export PIP_INDEX_URL='https://artifactory.corp.example.invalid/artifactory/api/pypi/REPLACE_ME/simple'
python3 .github/graphify-review/scripts/bootstrap_environment.py
```

Windows equivalent:

```powershell
$env:PIP_CERT = 'C:\certificates\approved-ca-bundle.pem'
$env:PIP_INDEX_URL = 'https://artifactory.corp.example.invalid/artifactory/api/pypi/REPLACE_ME/simple'
py -3 .github/graphify-review/scripts/bootstrap_environment.py
```

Omit `PIP_CERT` if your Python/pip setup already trusts the corporate CA. Omit `PIP_INDEX_URL` if your existing pip configuration already selects the approved repository. Keep your organization's existing package authentication configuration; the installer's bearer token is not automatically supplied to pip. The bootstrapper's pip subprocess inherits these variables. TLS verification should remain enabled.

### Maven and other application package managers

Maven dependency resolution has its own proxy configuration. Merge the following into your user `~/.m2/settings.xml` (Windows: `%USERPROFILE%\.m2\settings.xml`), preserving existing mirrors, servers, and profiles:

```xml
<settings>
  <proxies>
    <proxy>
      <id>corporate-proxy</id>
      <active>true</active>
      <protocol>http</protocol>
      <host>proxy.example.invalid</host>
      <port>8080</port>
      <nonProxyHosts>localhost|127.0.0.1|artifactory.corp.example.invalid</nonProxyHosts>
    </proxy>
  </proxies>
</settings>
```

Maven uses `|` separators in `nonProxyHosts`, rather than the commas used by `NO_PROXY`. Adapt the bypass hosts to your approved network route. Proxy authentication, if required, belongs in your organization's secured Maven configuration. `MAVEN_OPTS` truststore settings do not configure the proxy itself. [Maven proxy documentation](https://maven.apache.org/guides/mini/guide-proxies.html).

For Gradle, configure the HTTP/HTTPS proxy system properties in the user's `~/.gradle/gradle.properties`; see [Gradle networking](https://docs.gradle.org/current/userguide/networking.html). For .NET restores, retain the corporate package-source and proxy settings in `NuGet.Config`; see [NuGet configuration](https://learn.microsoft.com/en-us/nuget/reference/nuget-config-file). A successful `jf rt ping` does not establish that Maven, Gradle, or NuGet can resolve dependencies during `jf audit`.

### VS Code and troubleshooting

Fully quit VS Code and launch it from a terminal with the configured environment so Copilot's child processes inherit it. Configure the editor's own network access according to [VS Code network settings](https://code.visualstudio.com/docs/setup/network); editor settings alone do not configure external package managers.

Check `jf rt ping --server-id=corp-xray` using your actual configured server ID, then run the Graphify bootstrap and the relevant application dependency resolution. An HTTP `407` indicates proxy authentication; `401`/`403` generally indicate endpoint authentication or authorization; certificate errors indicate trust configuration. If external JFrog extractor downloads are blocked, use the approved `JFROG_CLI_RELEASES_REPO` setup described in [JFrog Xray and secret scanning](#jfrog-xray-and-secret-scanning).

## Install and run in VS Code

Merge this `.github` directory into the repository being reviewed. In Copilot Chat:

### Review environment

The kit pins Graphify in `.github/graphify-review/requirements.txt`. You can install it manually before the first review:

```bash
python3 .github/graphify-review/scripts/bootstrap_environment.py
```

The command creates the dedicated `.graphify-review-venv` only when necessary, installs the pinned Graphify package, and prints the review Python and Graphify executable paths. An existing application `.venv` is never inspected, activated, modified, or replaced. If `.graphify-review-venv` already exists but is not a Python virtual environment, setup stops without modifying it.

Check without making changes:

```bash
python3 .github/graphify-review/scripts/bootstrap_environment.py --check --json
```

Automatic first-call setup is enabled by default with `runtime.auto_bootstrap: true` in `policy.yaml`. The Graphify Review manager runs:

```bash
python3 .github/graphify-review/scripts/bootstrap_environment.py --first-call --json
```

Set `runtime.auto_bootstrap: false` when package installation must be performed explicitly. In that mode, the first review only checks the environment and reports the manual setup command. For offline package caches, manual setup also supports `--offline`.

The commands below use `.graphify-review-venv/bin/python` on macOS/Linux. On Windows, use `.graphify-review-venv\Scripts\python.exe`. The Copilot manager uses the platform-correct path returned by the bootstrapper rather than assuming either layout.

### Start the review

In Copilot Chat:

```text
/full-project-review graph=path/to/graphify-output.json
```

Select an ASP.NET Core API or Spring Boot profile:

```text
/full-project-review graph=artifacts/graph.json profile=aspnet-core-api
/full-project-review graph=artifacts/graph.json profile=spring-boot
```

JFrog is selected automatically when its CLI is available, or can be required explicitly:

```text
/full-project-review graph=artifacts/graph.json profile=asp.net security-scanner=jfrog
/full-project-review graph=artifacts/graph.json profile=spring security-scanner=jfrog jfrog-server=corporate
```

Choose the run behavior explicitly when continuity matters:

```text
/full-project-review graph=artifacts/graph.json profile=asp.net mode=fresh
/full-project-review graph=artifacts/graph.json profile=asp.net mode=revalidate baseline=artifacts/previous-review.json
/full-project-review profile=asp.net mode=rescore baseline=artifacts/previous-review.json
```

- `fresh` discovers findings without a baseline.
- `revalidate` makes every previous supported or inconclusive finding mandatory and records it as `still_present`, `resolved`, `superseded`, or `not_reproduced`.
- `rescore` invokes no reviewer; it applies current deterministic grading to immutable baseline findings.

Strict runs require a clean worktree. `allow-dirty=true` is an explicit escape hatch recorded in the run context.

`profile=asp.net`, `profile=aspnet`, and `profile=dotnet` normalize to `aspnet-core-api`; `profile=spring` and `profile=springboot` normalize to `spring-boot`. `profile=auto` performs deterministic marker detection and stops when multiple profiles match. An unknown value is an error. When omitted, the profile is `generic`, preserving the framework-neutral workflow.

Optional scope can be combined with a profile:

```text
/full-project-review graph=artifacts/graph.json profile=asp.net scope=src/pricing
```

Or select **Graphify Review** from the Agents picker. If the graph path is omitted, the manager searches for plausible exports and records ambiguity.

## Framework profiles

Profiles live under `.github/graphify-review/profiles/` and are validated and hashed before evidence collection. The resolved canonical profile is written below the unique run directory; its ID, version, definition hash, and detection state are copied into normalized evidence, the reproducibility manifest, and final review.

- `aspnet-core-api` reviews .NET service lifetimes and dependency resolution, API boundaries, service/interface cohesion and dependency direction, async/cancellation, `HttpClient`, EF Core lifetime/query behavior, typed configuration, middleware ordering, nullable analysis, and analyzers.
- `spring-boot` reviews bean injection/scopes, API/application/persistence boundaries, component/interface cohesion and dependency direction, transactional proxy behavior, JPA query/lifecycle behavior, validation/error contracts, Spring Security, typed configuration, Actuator/health exposure, async executors, and build analysis.

Profiles do not make conventions mandatory. Missing MediatR, repositories, interfaces, a fixed package structure, or another fashionable pattern is not a finding by itself. Every profile finding needs the rule's stated source/configuration/tool evidence and counter-evidence review.

Resolve a profile manually:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/resolve_profile.py \
  --profile asp.net \
  --repository . \
  --output <run-dir>/evidence/profile.json
```

## JFrog Xray and secret scanning

The default `security-scanner=auto` requires no additional review-kit installation. When `jf` is visible on the `PATH` inherited by VS Code, the audit runner prefers JFrog Xray for dependency vulnerability evidence and JFrog Secrets for secret evidence. Restart VS Code after changing the Windows `PATH` so Copilot inherits it.

Before the first review, these PowerShell checks should succeed from VS Code's integrated terminal:

```powershell
Get-Command jf
jf --version
jf c show
jf rt ping
```

If more than one JFrog CLI configuration exists, either select its default with `jf c use <server-id>` or leave the default unchanged and pass `jfrog-server=<server-id>` to `/full-project-review`. Authentication stays in the JFrog CLI configuration; never put a URL, token, user name, or password in the review command.

| Review parameter | Dependency scan | Secret scan | Incomplete JFrog result |
|---|---|---|---|
| `security-scanner=auto` (default) | JFrog when `jf` is available; otherwise profile-native | JFrog Secrets when available; otherwise Gitleaks | Preserved, then native fallback where available |
| `security-scanner=jfrog` | JFrog required | JFrog Secrets required | Preserved as incomplete; no silent fallback |
| `security-scanner=native` | Profile-native audit | Gitleaks | JFrog is not invoked |

For ASP.NET Core, JFrog runs NuGet SCA from each detected solution/project root. For Spring Boot, it runs Maven or Gradle SCA and uses a repository wrapper when present. The generic profile supports common JFrog package managers when their manifests are detected. Each declared target must complete before dependency evidence is considered complete.

JFrog can replace Gitleaks only when the connected platform includes the JFrog Advanced Security Secrets scanner and the configured identity may use it. If that sub-scan is absent or fails, `auto` runs Gitleaks and records both attempts; strict `jfrog` mode leaves `secret_scan` incomplete. JFrog does not replace the repository-configured Semgrep SAST step, so `static_security_scan` remains incomplete until `.semgrep.yml` or `.semgrep.yaml` exists and `semgrep` is available.

The application SDK/package manager must also be available to JFrog, and dependencies/build descriptors must be resolvable through the corporate repositories. For Maven/Gradle behind a proxy, configure `user.jfrog_releases_repo=<server-id>/<repository-name>` with `/configure-review` (or set `JFROG_CLI_RELEASES_REPO` yourself) to an approved Artifactory remote repository that proxies `https://releases.jfrog.io`; JFrog uses it to obtain the required extractor resources. No JFrog credentials or raw detected secret values are written to review evidence.

You can smoke-test the two .NET scans directly from the solution directory:

```powershell
jf audit --nuget --sca --without-contextual-analysis --format=simple-json --fail=false --vuln
jf audit --secrets --format=simple-json --fail=false
```

The first command must include `scaScanStatusCode: 0`; the second must include `secretsScanStatusCode: 0`. Findings are allowed—the status code establishes that the scanner completed.

## Publish results to the configured Hub

Use `/publish-review` to send a completed review and its remediation plans. The Hub automatically creates the project (called a **repository** internally) if its stable repository ID does not exist in the authenticated workspace. Subsequent runs use that same project. You do not have to create the project manually.

When a Hub URL is configured, `/full-project-review` now includes local envelope preparation after successful validation for **fresh, revalidate and rescore** runs. Each run has its own `<run-dir>/hub/import-envelope.json`, including that run's complete reconciliation and source-bound remediation plans. Earlier envelopes are never overwritten. The final response explicitly reports Hub export readiness or the blocker; missing metadata/plans must not be mistaken for a ready envelope. No token or reachable Hub is needed for this local step, and nothing is uploaded automatically.

To complete an older/interrupted revalidation export, select the **new revalidation run**, not its baseline:

```text
/export-review review=.github/graphify-review/output/runs/<new-run-id>/review.json resume=true
```

This resumes or verifies that run's `hub/` export. It rejects stale/mismatched artifacts without replacing them. If all supported findings were resolved, it still builds an envelope with an empty remediation array and the explicit resolution reconciliation. Publishing requires the baseline history to exist in the Hub first.

### One-time connection

1. Run `/configure-review hub-url=https://hub.company.example` (or set the URL during installation/setup). For local development, `http://127.0.0.1:8000` is allowed.
2. Sign in to that Hub in your browser, choose or create your workspace, and open **API tokens** (workspace owner/admin only). Create a token with `reviews:write`, or ask your workspace administrator to provision one through your approved secret-sharing channel. If you restrict it to a repository, enter the exact stable ID saved during `/setup-review`. The kit does not create accounts or workspaces.
3. Bootstrap the dedicated environment if needed, then save the token through the hidden prompt in your **own terminal**, not Copilot chat:

Windows PowerShell:

```powershell
py -3 .github/graphify-review/scripts/bootstrap_environment.py
.graphify-review-venv\Scripts\python.exe .github/graphify-review/scripts/publish_review.py login
```

macOS/Linux:

```sh
python3 .github/graphify-review/scripts/bootstrap_environment.py
.graphify-review-venv/bin/python .github/graphify-review/scripts/publish_review.py login
```

Login saves a token locally; it does not contact the Hub or prove its permissions. Authentication is checked when publishing. The helper uses [keyring's native OS backends](https://keyring.readthedocs.io/en/latest/): macOS Keychain, Windows Credential Locker, or Linux Secret Service. Linux requires an unlocked desktop keyring and D-Bus session. There is no plaintext-file fallback. One token is saved per canonical Hub URL; log in again to switch workspace tokens or rotate an expired token. Changing the Hub URL never forwards the old token to the new destination.

### Publish from Copilot

Select a completed run explicitly; the command verifies/reuses its run-local envelope, or completes it when necessary without regenerating an already finalized envelope:

```text
/publish-review review=.github/graphify-review/output/runs/<run-id>/review.json
```

Or send an envelope you already exported:

```text
/publish-review envelope=.github/graphify-review/output/exports/<export-id>/import-envelope.json
```

Add `dry-run=true` to export/validate and display the destination without reading credentials or making a network request. The command uses saved project metadata; export options such as `repository-id`, `default-branch` and `remediations` apply only with `review`, not with an immutable existing envelope. Missing historical metadata or useful remediation context must be supplied before publishing. `REVIEW.md` and vulnerability-only reports are not accepted full-review inputs.

The command displays the destination and project identity before sending the complete envelope. Check findings/evidence for sensitive information before sharing; contract validation cannot guarantee prose contains no secrets. Publishing is always explicit: setup, reviews and `/export-review` never upload automatically. The response identifies a new import versus an already imported run, includes finding counts and (on updated Hub servers) project-creation status and a workspace link. A new `publish-receipt-*.json` is saved beside the unchanged envelope. Existing project Jira bindings can queue deliveries; importing does not enable Jira or guarantee delivery.

For a timeout, connection failure or server error, retry using **the exact same envelope**, not `review=` with regenerated plans. Exact retries return the original import without duplicates. A `409` means that repository/run already has different content; do not change its run ID to bypass the conflict. New reviews use new run IDs and the same repository ID. Revalidation requires its baseline history to have been imported; missing findings alone are never marked resolved. Token scope, repository restrictions, workspace subscription/quotas and archived-project protections still apply.

### Terminal publishing and troubleshooting

```sh
.graphify-review-venv/bin/python .github/graphify-review/scripts/publish_review.py publish --envelope path/to/import-envelope.json --dry-run
.graphify-review-venv/bin/python .github/graphify-review/scripts/publish_review.py publish --envelope path/to/import-envelope.json
.graphify-review-venv/bin/python .github/graphify-review/scripts/publish_review.py logout
```

On Windows substitute `.graphify-review-venv\Scripts\python.exe`. Run from the reviewed project root, or pass `--repository <project-root>`. Logout removes only the local token for the currently configured Hub; revoke it in the Hub to invalidate other copies. Existing browser uploads remain an option if native credential storage is unavailable.

For headless/CI use, an approved secret manager can inject `FINDING_HUB_TOKEN` and **matching** `FINDING_HUB_TOKEN_URL` into the process environment. Both are required together; this overrides a saved OS credential. No `--token` argument, credential URL, preset token or committed environment file is supported. Do not paste a token assignment into chat or shell history. To return to native credential storage, remove both injected variables.

Publishing uses the saved proxy, `NO_PROXY` and approved PEM CA bundle. Java `cacerts` is not a PEM bundle. TLS verification remains enabled and redirects are refused, even within the same host; configure the final Hub base URL. Authentication failures require token replacement, while `403` may also mean missing scope, repository restriction, an inactive workspace or a quota. Server errors are sanitized; ask your Hub operator to investigate server logs rather than enabling credential-bearing HTTP debug output.

## Export a previous review to Finding Hub

Use the Copilot slash command with an explicit final review or run directory:

```text
/export-review review=.github/graphify-review/output/runs/<run-id>/review.json repository-id=repo:orders-api default-branch=main
/export-review review=artifacts/previous-run repository-id=repo:orders-api repository-name="Orders API" default-branch=main remediations=artifacts/remediations.json
```

The command reads the original schema-2.1 `review.json`, generates proposed coding-agent remediation plans for every supported/partially-supported finding unless a plans file was supplied, and writes a validated `import-envelope.json` in a new `.github/graphify-review/output/exports/<export-id>/` directory. Upload that file through Finding Hub's **Import review** screen. It does not upload anything, require Hub/Jira credentials, rerun Graphify/Xray/tests, rescore, or edit the reviewed project. A Markdown report or vulnerability-command `check.json`/`apply.json` is not a full review and cannot be exported through this command.

Add `resume=true` to safely create/complete/verify the selected run's adjacent `hub/` directory instead (or an explicit `output`). The deterministic entry point for orchestration is `export_review.py ensure --review <run-dir>/review.json` using the dedicated review Python and its offline validator dependencies. `--if-hub-configured` skips automatic exports when no user Hub URL is saved. Exit 0 means `exported` or `not_configured` (inspect status), exit 3 means `needs_remediations`, and exit 2 means blocked. A pending request is not an import envelope. After the export agent supplies grounded plans, rerun `ensure` to build and verify; rerunning against a matching finished envelope is read-only. This validates the export contract, not the original review's policy/evidence again.

`repository-id` must remain stable across runs and machines. Repository ID/name/default branch use saved project settings unless explicitly overridden; the name falls back to the original review when no name is configured. Supply any values still missing, or run `/setup-review`. The default branch may differ from the reviewed source branch. Optional `clone-url` must be credential-free HTTPS. Optional `output` must name a new directory. Paths and values containing spaces must be quoted.

Source metadata comes from the original review or matching `run-context.json`, never current HEAD. Only when the original metadata is missing, supply `source-commit=<original-sha>` and/or `source-branch=<original-branch>`. Conflicting metadata is rejected. The review is embedded unchanged, including fingerprints, scores, source snapshot and revalidation reconciliation. If the original run included uncommitted changes, the exporter warns that its commit alone does not reproduce the reviewed source.

The script validates the same offline import contract used by Hub; it does not repeat historical policy/evidence validation or establish that remediation proposals are correct. Missing/invalid plans, unsupported review schemas, changed input files, credential-pattern matches and payload-limit violations block envelope creation. Preparation files are not importable envelopes. Review the proposed plans and check for sensitive content before sharing.

For retries, reuse the exact generated envelope. Its idempotency key is `<repository-id>:<original-run-id>`. Hub returns `409` if that repository/run has already been imported with different plans or metadata; exporting again is not a way to overwrite an import. Use a new review/revalidation run for updated findings.

The dedicated environment now includes pinned `jsonschema` and `referencing` dependencies. First-call bootstrap detects missing packages even in an existing `.graphify-review-venv`; manual setup remains available with `python .github/graphify-review/scripts/bootstrap_environment.py`. Corporate installations use the same configured pip proxy, trusted CA and Artifactory index as the rest of the kit.

### Manual export (without Copilot)

Use the Python executable returned by bootstrap as `<review-python>` (`.graphify-review-venv/Scripts/python.exe` on Windows, `.graphify-review-venv/bin/python` on macOS/Linux):

```text
<review-python> .github/graphify-review/scripts/export_review.py prepare --review "path/to/review.json" --repository-id repo:orders-api --repository-name "Orders API" --default-branch main --output-directory "artifacts/new-export"
```

Read `artifacts/new-export/export-request.json`, then author `artifacts/new-export/remediations.json` as a JSON array of source-bound plans using [the remediation contract](docs/graphify_finding_hub_spec.md#73-remediation-plan-requirements). With no supported findings, preparation creates an empty plans array automatically. Then run:

```text
<review-python> .github/graphify-review/scripts/export_review.py build --request "artifacts/new-export/export-request.json"
```

Alternatively pass `--remediations "path/to/existing-plans.json"` to `build`. Success prints the envelope path, idempotency key and payload hash. Blocked exports exit with code `2` and a sanitized error. Original inputs and existing exports are never overwritten.

## Vulnerability upgrade commands

Two independent Copilot slash commands turn completed JFrog Xray findings into conservative upgrade decisions:

```text
/check-vulnerability-upgrades
/check-vulnerability-upgrades severity=critical,high jfrog-server=corporate
/check-vulnerability-upgrades severity=high artifactory-repositories=maven-cache,nuget-cache

/update-vulnerable-dependencies severity=critical,high jfrog-server=corporate
```

`severity` defaults to `all`. A named value is an exact filter, so use `severity=critical,high` when both levels are wanted. Supported values are `critical`, `high`, `medium`, `low`, `informational`, and `unknown`.

`/check-vulnerability-upgrades` runs a fresh Xray SCA audit, selects an exact fixed version from Xray, and checks that exact artifact in the configured Artifactory instance. It does not modify dependency files. Its Markdown and JSON reports include a dedicated `missing_libraries` inventory for recommendations that are not stored or cached in the searched repositories.

`/update-vulnerable-dependencies` does not consume or depend on a prior check report. It independently reruns Xray, version selection, Artifactory lookup, and declaration discovery before editing. By default it requires a clean Git worktree; `allow-dirty=true` is an explicit escape hatch for a caller who has arranged another recovery mechanism. Original files are backed up in the command's unique output directory before replacement.

When `artifactory-repositories` is omitted, the command searches all repository keys visible to the configured JFrog identity. Supplying the option limits searches to the named local or remote-cache repository keys. A successful lookup means the exact artifact is currently stored or cached in Artifactory; the command never queries a public package registry.

The no-assumed-breaking-change rule is deliberately conservative: the candidate must be an exact numeric Xray fixed version greater than the installed version and in the same major version. For `0.x`, it must also remain in the same minor version. This reduces obvious compatibility risk but cannot guarantee source, binary, or behavioral compatibility. If any selected vulnerability for a dependency lacks such a fix, the dependency is reported for manual handling and is not updated.

Automatic edits are limited to recognizable direct declarations inside the Xray-affected target:

| Ecosystem | Automatically editable declarations |
|---|---|
| Maven | Literal dependency versions and a unique, unshared `${property}` in `pom.xml` |
| Gradle | Literal `group:artifact:version` string notation |
| Python | Exact unhashed `requirements*.txt`/`.in` pins; recognized PEP 508 arrays in `pyproject.toml`; Poetry/Pipfile exact, `^`, or `~` constraints |
| NuGet | Literal `PackageReference`, central `PackageVersion`, and `packages.config` versions |
| npm | Exact, `^`, or `~` versions in direct dependency, development-dependency, and optional-dependency sections |

Transitive dependencies, shared/ambiguous version properties, unsupported ranges, hashed Python requirements, comments, and generated lockfiles are not edited. The update report lists lockfiles that must be refreshed with the repository's configured package manager. After an update, refresh only those lockfiles, run the relevant restore/build/tests, and rerun Xray.

Each invocation writes immutable output beneath `.github/graphify-review/output/vulnerability-upgrades/<run-id>/`. Check runs produce `check.json` and `CHECK.md`; update runs produce `apply.json`, `APPLY.md`, and file backups when changes are made.

JFrog JSON output is decoded explicitly as UTF-8, including on Windows; no `PYTHONUTF8` setup is required. Empty or missing CVE IDs are tolerated when the finding has an Xray issue ID. Findings with neither identifier block automatic upgrade decisions. If scan execution, decoding, or normalization fails, both commands write `scan_incomplete` reports and exit nonzero before Artifactory queries or dependency edits. Unknown counts are JSON `null` / Markdown `unavailable`, not zero; the reports include sanitized scan failure details without raw CLI output or credentials. Failures before scanning (such as an invalid repository or a dirty-worktree safety check), or an unwritable output directory, may prevent report creation.

## Policy

`.github/graphify-review/policy.yaml` controls:

- automatic environment bootstrap, dedicated review-environment location, requirements file, and minimum Python version;
- project and deployment assumptions, including whether containers are required;
- security/test evidence expectations;
- category weights, severity/quality penalties, weakest-category aggregation, and hard gates;
- confidence weights and decision threshold;
- mandatory completed evidence required before any release decision;
- reproducible random sample size;
- clean-worktree enforcement, versioned output, and baseline disposition requirements;
- verification requirements by severity;
- accepted risks and expiry;
- task-complexity model routing.

Validate it before review:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/validate_policy.py \
  .github/graphify-review/policy.yaml
```

The scripts have no mandatory third-party dependency. They use PyYAML/jsonschema when available and otherwise apply their dependency-free parser and validations.

## Graphify and evidence

Prepare a unique run directory before writing evidence:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/reproducibility.py \
  --repository . --mode fresh --exclude-input path/to/graphify-output.json
```

The command prints `<run-dir>`. Generated Graphify input is excluded from the authored-source snapshot; the graph receives its own hash in the manifest. For revalidation/rescore, add `--mode revalidate|rescore --baseline path/to/review.json` (the baseline is excluded automatically).

The provided adapter preserves existing JSON, GraphML, and optional YAML input expectations:

```bash
.graphify-review-venv/bin/python .github/skills/graphify-evidence/scripts/graphify_adapter.py \
  --input path/to/graphify-output.json \
  --output <run-dir>/evidence/project-graph.json
```

Build the normalized evidence envelope before specialist review:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/run_audits.py \
  --repository . --profile-file <run-dir>/evidence/profile.json \
  --security-scanner auto \
  --output <run-dir>/evidence/tool-results.json
```

The manager also creates mandatory investigation anchors for the selected framework profile. These are stable signals that must receive a verifier disposition; they are never findings by themselves:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/aspnet_checks.py \
  --repository . --output <run-dir>/evidence/aspnet-anchors.json

.graphify-review-venv/bin/python .github/graphify-review/scripts/spring_checks.py \
  --repository . --output <run-dir>/evidence/spring-anchors.json
```

Run only the command for the resolved framework profile.

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/build_evidence.py \
  --repository . \
  --graph <run-dir>/evidence/project-graph.json \
  --profile-file <run-dir>/evidence/profile.json \
  --tool-results <run-dir>/evidence/tool-results.json \
  --run-context <run-dir>/run-context.json \
  --output <run-dir>/evidence/normalized-evidence.json
```

Validate it before scoping:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/validate_evidence.py \
  <run-dir>/evidence/normalized-evidence.json
```

Execution states are `not_run`, `passed`, `failed`, `unavailable`, and `not_applicable`; completion is separately recorded as `complete`, `partial`, `not_completed`, or `not_applicable`. Confidence is awarded only for completed evidence. A dependency scan is complete only when every declared project target and the command-output hash are recorded.

Create the reproducibility manifest after tool execution, then finalize it after the manager has recorded the exact executed agent/model routes in `model-records.json`:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/hash_inputs.py \
  --repository . \
  --graph <run-dir>/evidence/project-graph.json \
  --profile-file <run-dir>/evidence/profile.json \
  --run-context <run-dir>/run-context.json \
  --tools <run-dir>/evidence/tool-results.json \
  --models <run-dir>/evidence/model-records.json \
  --output <run-dir>/evidence/manifest.json
```

The manifest identity includes source, graph, policy, selected profile, review-kit version, baseline, tool results, and model routing. Its timestamp comes from the immutable run context, so rerunning manifest generation inside one run is stable.

Random-control sampling uses the authored source snapshot, sampling subsection, and profile hash. Scoring, confidence, and model-routing policy changes do not alter the sample:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/select_review_samples.py \
  --repository . \
  --graph <run-dir>/evidence/project-graph.json \
  --profile-file <run-dir>/evidence/profile.json \
  --output <run-dir>/evidence/samples.json
```

## Deterministic outcomes

Confidence is separate from quality:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/calculate_confidence.py \
  --evidence <run-dir>/evidence/normalized-evidence.json \
  --output <run-dir>/evidence/confidence.json
```

Each category starts at `10.0`. Policy penalties combine verified severity with `technical_quality_impact` (`systemic`, `material`, `localized`, or `none`), independently from `production_impact`. Supported non-informational findings must be at least `localized`, so a real finding with no production consequence still lowers its category.

The overall grade defaults to `70%` category-weighted average plus `30%` weakest-category score. This makes a weak maintainability or security score visible in the overall grade instead of diluting it among unrelated 10/10 categories. Both weights are configurable under `scoring.overall_aggregation`; `review.json` records the weighted component, weakest category, pre-gate score, and final score in `production_readiness.score_breakdown`.

Selected profiles can additionally apply deterministic category caps for verified systemic or repeated framework-rule findings. Release hard gates and action lists continue to use production impact. Accepted risk can waive release impact while preserving technical score impact.

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/calculate_score.py \
  --findings <run-dir>/evidence/verified-findings.json \
  --confidence-file <run-dir>/evidence/confidence.json \
  --profile-file <run-dir>/evidence/profile.json \
  --output <run-dir>/evidence/score.json
```

Release recommendations are:

- `GO`
- `GO_WITH_ACTIONS`
- `CONDITIONAL`
- `NO_GO`
- `INSUFFICIENT_EVIDENCE`

The synthesizer cannot override these results. Even when the numeric threshold is met, incomplete mandatory test/SCA/secret/SAST evidence forces `INSUFFICIENT_EVIDENCE`.

## Validate and test

Validate final JSON against policy, evidence, deterministic scoring, CVE provenance, verification thresholds, accepted-risk expiry, and immutable findings:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/validate_review.py \
  <run-dir>/review.json \
  --policy .github/graphify-review/policy.yaml \
  --profile-file <run-dir>/evidence/profile.json \
  --verified-findings <run-dir>/evidence/verified-findings.json \
  --evidence <run-dir>/evidence/normalized-evidence.json \
  --anchors <run-dir>/evidence/aspnet-anchors.json
```

Use the Spring anchor path for `spring-boot`; omit `--anchors` for `generic`. Validation requires exactly one evidence-backed disposition for every supplied anchor.

Validate the bundled example and run tests:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/validate_review.py \
  .github/graphify-review/examples/review.example.json

.graphify-review-venv/bin/python -m unittest discover \
  -s .github/graphify-review/tests \
  -v
```

Tests cover profile validation/aliases/detection, ASP.NET and Spring Boot deterministic anchors, JFrog parsing/provider fallback/secret redaction, stable finding fingerprints, baseline reconciliation (including inconclusive claims), versioned runs, source snapshots, non-production quality findings, weakest-category aggregation, mandatory confidence evidence, `INSUFFICIENT_EVIDENCE`, CVE safety, policy-isolated sampling, deterministic rendering, accepted-risk expiry, and synthetic end-to-end scenarios.

## HTML viewer

Open `.github/graphify-review/viewer/index.html` and select `review.json`, or serve the repository:

```bash
.graphify-review-venv/bin/python -m http.server 8000
```

Then open:

```text
Select a versioned `output/runs/<run-id>/review.json` in the viewer.
```

The viewer displays the selected run and profile, baseline reconciliation, quality and confidence separately, the weighted/weakest-category score breakdown, profile category caps, release controls, category scores, supported/rejected/inconclusive findings, accepted/expired risks, evidence provenance, tool execution/completion states, and reproducibility metadata. It filters technical-quality impact and production impact independently alongside the existing filters.

## Review governance

- Severity, technical-quality impact, production impact, likelihood, and remediation priority are independent.
- High/Critical findings require stronger policy-defined review and evidence.
- Critical findings require deterministic evidence when configured.
- Unsupported/inconclusive findings never affect scoring by default.
- Active risk acceptance remains visible and expirable; expired waivers do not affect release decisions.
- Standards mappings are evidence-based and do not increase severity by themselves.
- Model routing metadata records agent/model/role, never hidden reasoning.
- CI/CD is not a separate scoring category. Baseline revalidation and deterministic rescore are explicit run modes.

## License and attribution

This kit's original code, prompts, agent/skill instructions, configuration, and documentation are licensed under GNU AGPL version 3 only (`AGPL-3.0-only`). Commercial use and compliant redistribution are permitted. Redistributors must retain applicable legal notices, identify modifications, and satisfy the license's source-availability obligations. Operators of modified versions must also comply with section 13 where users interact with those versions remotely over a network. The full [license](LICENSE) governs these obligations.

The original concept and project creator is **De Jonckheere Stéphane (humblejok)**. Preserve the copyright and applicable attribution notices with the licensed material. There is no separate requirement to mention the author in every advertisement, and this license does not reserve ownership of an abstract idea or an independently implemented workflow.

Using the kit to inspect a proprietary application does not by itself place that application under the AGPL. Installing the kit into `.github` does not relicense unrelated repository files. Generated output is covered only when its content constitutes a covered work under section 2. Graphify and other third-party components retain their own terms; see [third-party acknowledgements](THIRD_PARTY_NOTICES.md).

When distributing just `.github`, retain the copies of the license and notices in `.github/graphify-review/`. Standalone source-file distributions must also carry the applicable notices and license. Update both root and packaged notice copies together. This project is provided without warranty, as described in the license.
