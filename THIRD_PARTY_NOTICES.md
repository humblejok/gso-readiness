# Third-party acknowledgements

## Graphify

This review kit integrates with Graphify, a separate project by Safi Shamsi and
the Graphify contributors. The kit's original concept credit is not a claim to
authorship of Graphify.

- Upstream: https://github.com/Graphify-Labs/graphify
- Python distribution: `graphifyy`, pinned to `0.9.46` in `requirements.txt`
- License declared by that distribution: Apache-2.0
- Its `NOTICE` identifies: Copyright 2026 Safi Shamsi and the Graphify contributors.
- Its `NOTICE` also records that portions contributed before relicensing remain
  available under MIT terms; the distribution retains `LICENSE-MIT`.

The bootstrapper installs this dependency separately. The kit's AGPL does not
replace Graphify's license. If you redistribute Graphify code, copied/adapted
skills or prompts, or an installed environment, preserve the applicable upstream
`LICENSE`, `NOTICE`, and `LICENSE-MIT` files and comply with their terms. Bundled
transitive dependencies also retain their own licenses and notices.

## GitHub Copilot, VS Code, and JFrog

GitHub Copilot and VS Code provide the editor/agent environment. JFrog CLI and
the configured Artifactory/Xray services provide optional security integrations.
Their software and service terms remain separate from this kit's license.
Users obtain and configure these products independently; publishing this kit
does not grant a subscription or service entitlement.

The PowerShell installer can download a separately supplied JFrog CLI binary.
A distributor providing that binary must preserve its applicable license and
notices. Corporate certificates, truststores, and application review data are
not licensed by this kit.

This is an independent project, not an official or endorsed product of
Graphify-Labs, GitHub, Microsoft, or JFrog. Product names identify integrations;
no rights to third-party trademarks are granted.

## Distribution

Keep this file with `LICENSE` and `NOTICE` in the review-kit distribution.
The copies inside `.github/graphify-review/` make these documents available
when the kit is distributed as a ZIP containing only `.github`.
