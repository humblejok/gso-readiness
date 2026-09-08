#requires -Version 5.1
# SPDX-FileCopyrightText: 2026 De Jonckheere Stéphane (humblejok)
# SPDX-License-Identifier: AGPL-3.0-only
# Original concept and project creator: De Jonckheere Stéphane (humblejok).
# See LICENSE and NOTICE supplied with the review kit. Provided without warranty.

<#
.SYNOPSIS
Installs or updates the Graphify Review .github kit in a Windows repository.

.DESCRIPTION
Downloads a ZIP containing exactly one .github directory and a Java cacerts
truststore from trusted HTTPS endpoints, verifies both with SHA-256, merges the
kit into the target repository, and configures the current Windows user's
MAVEN_OPTS to use the installed truststore.

An existing target .github directory is backed up outside the repository before
the merge. Existing MAVEN_OPTS values are preserved except for a previous
javax.net.ssl.trustStore/trustStoreType setting, which this installer owns.

JFrog CLI installation is optional. When supplied, jf.exe is also verified,
installed in a dedicated user directory, and that directory is added to the
user PATH. The script never configures a JFrog server or persists credentials.

.EXAMPLE
$env:ARTIFACTORY_ACCESS_TOKEN = '<short-lived-access-token>'
./install-graphify-review.ps1 `
  -TargetRepository 'C:\src\my-api' `
  -GithubBundleUrl 'https://artifactory.example.invalid/artifactory/tools/graphify-review-github.zip' `
  -GithubBundleSha256 '<64-character-sha256>' `
  -TrustStoreUrl 'https://artifactory.example.invalid/artifactory/tools/cacerts' `
  -TrustStoreSha256 '<64-character-sha256>'

.NOTES
This script deliberately does not disable TLS certificate validation. The
Artifactory TLS certificate must already be trusted by Windows because the Java
truststore cannot securely bootstrap the HTTPS connection used to download it.
#>

[CmdletBinding()]
param(
    [Parameter()]
    [string]$TargetRepository = (Get-Location).Path,

    [Parameter()]
    [string]$GithubBundleUrl = 'https://artifactory.example.invalid/artifactory/REPLACE_ME/graphify-review-github.zip',

    [Parameter()]
    [string]$GithubBundleSha256 = 'REPLACE_WITH_64_CHARACTER_SHA256',

    [Parameter()]
    [string]$TrustStoreUrl = '',

    [Parameter()]
    [string]$TrustStoreSha256 = '',

    [Parameter()]
    [ValidateSet('', 'JKS', 'PKCS12')]
    [string]$TrustStoreType = '',

    [Parameter()]
    [string]$JfrogCliUrl = '',

    [Parameter()]
    [string]$JfrogCliSha256 = '',

    [Parameter()]
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'GraphifyReview'),

    [Parameter()]
    [string]$ArtifactoryTokenEnvironmentVariable = 'ARTIFACTORY_ACCESS_TOKEN',

    [Parameter()]
    [switch]$UseDefaultCredentials,

    [Parameter()]
    [string]$Proxy = '',

    [Parameter()]
    [switch]$ProxyUseDefaultCredentials,

    [Parameter()]
    [string]$Preset = '',

    [Parameter()]
    [switch]$NonInteractive,

    [Parameter()]
    [switch]$SkipSetup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-HttpsUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    [Uri]$parsed = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$parsed)) {
        throw "$Name must be an absolute HTTPS URL."
    }
    if ($parsed.Scheme -ne [Uri]::UriSchemeHttps) {
        throw "$Name must use HTTPS."
    }
    if (-not [string]::IsNullOrWhiteSpace($parsed.UserInfo)) {
        throw "$Name must not contain credentials; use an environment token or Windows authentication."
    }
    if ($parsed.Host.EndsWith('.example.invalid', [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name still contains the example.invalid placeholder."
    }
}

function Assert-ProxyUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    [Uri]$parsed = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$parsed)) {
        throw 'Proxy must be an absolute HTTP or HTTPS URL.'
    }
    if ($parsed.Scheme -notin @([Uri]::UriSchemeHttp, [Uri]::UriSchemeHttps)) {
        throw 'Proxy must use HTTP or HTTPS.'
    }
    if (-not [string]::IsNullOrWhiteSpace($parsed.UserInfo)) {
        throw 'Proxy must not contain credentials; use -ProxyUseDefaultCredentials.'
    }
    if ($parsed.Host.EndsWith('.example.invalid', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Proxy still contains the example.invalid placeholder.'
    }
}

function Assert-Sha256 {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    if ($Value -notmatch '^[A-Fa-f0-9]{64}$') {
        throw "$Name must be the expected 64-character SHA-256 value."
    }
}

function Invoke-VerifiedDownload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedSha256,

        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    Assert-HttpsUrl -Name "$Name URL" -Value $Uri
    Assert-Sha256 -Name "$Name SHA-256" -Value $ExpectedSha256

    $token = [Environment]::GetEnvironmentVariable(
        $ArtifactoryTokenEnvironmentVariable,
        [EnvironmentVariableTarget]::Process
    )
    if ($UseDefaultCredentials -and -not [string]::IsNullOrWhiteSpace($token)) {
        throw 'Choose either -UseDefaultCredentials or an Artifactory token environment variable, not both.'
    }

    $request = @{
        Uri             = $Uri
        OutFile         = $Destination
        UseBasicParsing = $true
        ErrorAction     = 'Stop'
    }
    if ($UseDefaultCredentials) {
        $request['UseDefaultCredentials'] = $true
    }
    elseif (-not [string]::IsNullOrWhiteSpace($token)) {
        $request['Headers'] = @{ Authorization = "Bearer $token" }
    }
    if (-not [string]::IsNullOrWhiteSpace($Proxy)) {
        Assert-ProxyUrl -Value $Proxy
        $request['Proxy'] = $Proxy
        if ($ProxyUseDefaultCredentials) {
            $request['ProxyUseDefaultCredentials'] = $true
        }
    }
    elseif ($ProxyUseDefaultCredentials) {
        throw '-ProxyUseDefaultCredentials requires -Proxy.'
    }

    Write-Host "Downloading $Name..."
    Invoke-WebRequest @request

    if ((Get-Item -LiteralPath $Destination).Length -eq 0) {
        Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
        throw "$Name download was empty."
    }

    $actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
    if (-not $actual.Equals($ExpectedSha256, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
        throw "$Name failed SHA-256 verification. Expected $ExpectedSha256 but received $actual."
    }
}

function Find-GithubBundleRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExpandedArchive
    )

    $matches = @(
        Get-ChildItem -LiteralPath $ExpandedArchive -Directory -Force -Recurse |
            Where-Object { $_.Name -eq '.github' }
    )
    if ($matches.Count -ne 1) {
        throw "The kit ZIP must contain exactly one .github directory; found $($matches.Count)."
    }

    $githubRoot = $matches[0].FullName
    $requiredFiles = @(
        'graphify-review\VERSION',
        'graphify-review\LICENSE',
        'graphify-review\NOTICE',
        'graphify-review\THIRD_PARTY_NOTICES.md',
        'agents\review-manager.agent.md',
        'prompts\full-project-review.prompt.md'
    )
    foreach ($relativePath in $requiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $githubRoot $relativePath) -PathType Leaf)) {
            throw "The kit ZIP is invalid: .github\$relativePath is missing."
        }
    }
    return $githubRoot
}

function Remove-OwnedJavaOption {
    param(
        [AllowEmptyString()]
        [string]$Options,

        [Parameter(Mandatory = $true)]
        [string]$PropertyName
    )

    if ([string]::IsNullOrWhiteSpace($Options)) {
        return ''
    }
    $escapedName = [Regex]::Escape($PropertyName)
    $pattern = '(?i)(^|\s+)-D{0}=(?:"[^"]*"|\S+)' -f $escapedName
    return ([Regex]::Replace($Options, $pattern, ' ')).Trim()
}

function Set-MavenTrustStore {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TrustStorePath,

        [AllowEmptyString()]
        [string]$StoreType
    )

    $options = [Environment]::GetEnvironmentVariable(
        'MAVEN_OPTS',
        [EnvironmentVariableTarget]::User
    )
    $options = Remove-OwnedJavaOption -Options $options -PropertyName 'javax.net.ssl.trustStore'
    $options = Remove-OwnedJavaOption -Options $options -PropertyName 'javax.net.ssl.trustStoreType'

    $ownedOptions = @('-Djavax.net.ssl.trustStore="{0}"' -f $TrustStorePath)
    if (-not [string]::IsNullOrWhiteSpace($StoreType)) {
        $ownedOptions += '-Djavax.net.ssl.trustStoreType={0}' -f $StoreType
    }

    $newOptions = (($options, ($ownedOptions -join ' ')) |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) -join ' '

    [Environment]::SetEnvironmentVariable(
        'MAVEN_OPTS',
        $newOptions,
        [EnvironmentVariableTarget]::User
    )
    $env:MAVEN_OPTS = $newOptions
}

function Add-UserPathEntry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Entry
    )

    $normalizedEntry = [IO.Path]::GetFullPath($Entry).TrimEnd('\')
    $userPath = [Environment]::GetEnvironmentVariable(
        'Path',
        [EnvironmentVariableTarget]::User
    )
    $userEntries = @($userPath -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $alreadyPresent = @($userEntries | Where-Object {
        $_.Trim().TrimEnd('\').Equals($normalizedEntry, [StringComparison]::OrdinalIgnoreCase)
    }).Count -gt 0

    if (-not $alreadyPresent) {
        $newUserPath = (($userEntries + $normalizedEntry) -join ';')
        [Environment]::SetEnvironmentVariable(
            'Path',
            $newUserPath,
            [EnvironmentVariableTarget]::User
        )
    }

    $processEntries = @($env:Path -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $processHasEntry = @($processEntries | Where-Object {
        $_.Trim().TrimEnd('\').Equals($normalizedEntry, [StringComparison]::OrdinalIgnoreCase)
    }).Count -gt 0
    if (-not $processHasEntry) {
        $env:Path = (($processEntries + $normalizedEntry) -join ';')
    }
}

if ($env:OS -ne 'Windows_NT') {
    throw 'This installer supports Windows only.'
}
if ($Preset) {
    if ((Get-Item -LiteralPath $Preset).Length -gt 131072) { throw 'Preset exceeds 128 KiB.' }
    $presetData = Get-Content -LiteralPath $Preset -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($presetData.schema_version -ne '1.0') { throw 'Expected preset schema_version 1.0.' }
    foreach ($field in $presetData.PSObject.Properties.Name) {
        if ($field -notin @('schema_version', 'installer', 'project', 'user')) { throw 'Unknown preset section.' }
    }
    $presetInstaller = $presetData.PSObject.Properties['installer']
    $parameterMap = @{
        github_bundle_url = 'GithubBundleUrl'; github_bundle_sha256 = 'GithubBundleSha256'
        truststore_url = 'TrustStoreUrl'; truststore_sha256 = 'TrustStoreSha256'; truststore_type = 'TrustStoreType'
    }
    if ($presetInstaller) {
        foreach ($field in $presetInstaller.Value.PSObject.Properties) {
            if ($field.Name -eq 'jfrog_cli') { continue }
            if (-not $parameterMap.ContainsKey($field.Name)) { throw 'Unknown installer preset field.' }
            if ($field.Value -isnot [string]) { throw 'Installer preset values must be strings.' }
            $parameterName = $parameterMap[$field.Name]
            if (-not $PSBoundParameters.ContainsKey($parameterName)) {
                Set-Variable -Name $parameterName -Value $field.Value
            }
        }
        $artifacts = $presetInstaller.Value.PSObject.Properties['jfrog_cli']
        if ($artifacts) {
            $platformKey = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'windows-arm64' } else { 'windows-amd64' }
            $artifact = $artifacts.Value.PSObject.Properties[$platformKey]
            if ($artifact) {
                if (-not $PSBoundParameters.ContainsKey('JfrogCliUrl')) { $JfrogCliUrl = [string]$artifact.Value.url }
                if (-not $PSBoundParameters.ContainsKey('JfrogCliSha256')) { $JfrogCliSha256 = [string]$artifact.Value.sha256 }
            }
        }
    }
    $presetUser = $presetData.PSObject.Properties['user']
    if ($presetUser -and -not $PSBoundParameters.ContainsKey('Proxy')) {
        $proxyField = $presetUser.Value.PSObject.Properties['proxy_url']
        if ($proxyField) { $Proxy = [string]$proxyField.Value }
    }
}
if ($GithubBundleSha256 -eq 'REPLACE_WITH_64_CHARACTER_SHA256' -and -not $NonInteractive) {
    Write-Host 'Ask your administrator for the approved kit URL/checksum, or restart with -Preset company.json.'
    $GithubBundleUrl = Read-Host 'Kit ZIP HTTPS URL'
    $GithubBundleSha256 = Read-Host 'Approved SHA-256'
}
if ($TrustStoreType -notin @('', 'JKS', 'PKCS12')) { throw 'Truststore type must be JKS or PKCS12.' }
$installTrustStore = (-not [string]::IsNullOrWhiteSpace($TrustStoreUrl))
if ($installTrustStore -ne (-not [string]::IsNullOrWhiteSpace($TrustStoreSha256))) {
    throw 'Supply both truststore URL and SHA-256, or omit both when Java trust configuration is not needed.'
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'LOCALAPPDATA is not available for the current Windows user.'
}
if (-not (Test-Path -LiteralPath $TargetRepository -PathType Container)) {
    throw "Target repository does not exist: $TargetRepository"
}

$installJfrog = (-not [string]::IsNullOrWhiteSpace($JfrogCliUrl)) -or
    (-not [string]::IsNullOrWhiteSpace($JfrogCliSha256))
if ($installJfrog -and (
    [string]::IsNullOrWhiteSpace($JfrogCliUrl) -or
    [string]::IsNullOrWhiteSpace($JfrogCliSha256)
)) {
    throw 'Supply both -JfrogCliUrl and -JfrogCliSha256, or omit both.'
}

# Windows PowerShell 5.1 may otherwise negotiate an obsolete protocol. This does
# not weaken certificate or hostname validation.
[Net.ServicePointManager]::SecurityProtocol =
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$target = [IO.Path]::GetFullPath($TargetRepository)
$resolvedInstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("graphify-review-install-{0}" -f [Guid]::NewGuid())
$bundlePath = Join-Path $tempRoot 'graphify-review-github.zip'
$expandedPath = Join-Path $tempRoot 'expanded'
$downloadedTrustStore = Join-Path $tempRoot 'cacerts'
$downloadedJfrog = Join-Path $tempRoot 'jf.exe'
$timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$backupSuffix = '{0}-{1}' -f $timestamp, [Guid]::NewGuid().ToString('N')
$backupDirectory = $null
$trustStoreBackup = $null
$jfrogBackup = $null

try {
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

    # Complete and verify every network download before changing the repository
    # or the current user's environment.
    Invoke-VerifiedDownload `
        -Name '.github kit' `
        -Uri $GithubBundleUrl `
        -ExpectedSha256 $GithubBundleSha256 `
        -Destination $bundlePath
    if ($installTrustStore) {
        Invoke-VerifiedDownload `
            -Name 'Java truststore' `
            -Uri $TrustStoreUrl `
            -ExpectedSha256 $TrustStoreSha256 `
            -Destination $downloadedTrustStore
    }
    if ($installJfrog) {
        Invoke-VerifiedDownload `
            -Name 'JFrog CLI' `
            -Uri $JfrogCliUrl `
            -ExpectedSha256 $JfrogCliSha256 `
            -Destination $downloadedJfrog
    }

    New-Item -ItemType Directory -Path $expandedPath -Force | Out-Null
    Expand-Archive -LiteralPath $bundlePath -DestinationPath $expandedPath -Force
    $sourceGithub = Find-GithubBundleRoot -ExpandedArchive $expandedPath

    $destinationGithub = Join-Path $target '.github'
    if ((Test-Path -LiteralPath $destinationGithub) -and
        -not (Test-Path -LiteralPath $destinationGithub -PathType Container)) {
        throw "Cannot install because the target .github path is not a directory: $destinationGithub"
    }

    $trustStoreDirectory = Join-Path $resolvedInstallRoot 'truststore'
    $installedTrustStore = Join-Path $trustStoreDirectory 'cacerts'
    if ((Test-Path -LiteralPath $installedTrustStore) -and
        -not (Test-Path -LiteralPath $installedTrustStore -PathType Leaf)) {
        throw "Cannot install because the truststore path is not a file: $installedTrustStore"
    }

    $binDirectory = Join-Path $resolvedInstallRoot 'bin'
    $installedJfrog = Join-Path $binDirectory 'jf.exe'
    if ($installJfrog -and (Test-Path -LiteralPath $installedJfrog) -and
        -not (Test-Path -LiteralPath $installedJfrog -PathType Leaf)) {
        throw "Cannot install because the JFrog CLI path is not a file: $installedJfrog"
    }

    if (Test-Path -LiteralPath $destinationGithub) {
        $repositoryName = Split-Path -Leaf $target
        if ([string]::IsNullOrWhiteSpace($repositoryName)) {
            $repositoryName = 'repository'
        }
        $safeRepositoryName = $repositoryName -replace '[^A-Za-z0-9._-]', '_'
        $backupDirectory = Join-Path $resolvedInstallRoot (
            'backups\{0}-{1}' -f $safeRepositoryName, $backupSuffix
        )
        New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
        Copy-Item -LiteralPath $destinationGithub -Destination $backupDirectory -Recurse -Force
    }

    if ($installTrustStore) {
        New-Item -ItemType Directory -Path $trustStoreDirectory -Force | Out-Null
        if (Test-Path -LiteralPath $installedTrustStore -PathType Leaf) {
            $installedHash = (Get-FileHash -LiteralPath $installedTrustStore -Algorithm SHA256).Hash
            if (-not $installedHash.Equals($TrustStoreSha256, [StringComparison]::OrdinalIgnoreCase)) {
                $trustStoreBackup = "$installedTrustStore.$backupSuffix.bak"
                Copy-Item -LiteralPath $installedTrustStore -Destination $trustStoreBackup -Force
            }
        }
        Copy-Item -LiteralPath $downloadedTrustStore -Destination $installedTrustStore -Force
    }

    New-Item -ItemType Directory -Path $destinationGithub -Force | Out-Null
    Get-ChildItem -LiteralPath $sourceGithub -Recurse -File -Force | ForEach-Object {
        $relative = $_.FullName.Substring($sourceGithub.Length).TrimStart([char[]]'\/')
        $normalized = $relative.Replace('\', '/')
        if ($normalized -ne 'graphify-review/settings.json' -and -not $normalized.StartsWith('graphify-review/settings.json.bak-')) {
            $destination = Join-Path $destinationGithub $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
    }

    if ($installTrustStore) { Set-MavenTrustStore -TrustStorePath $installedTrustStore -StoreType $TrustStoreType }

    if ($installJfrog) {
        New-Item -ItemType Directory -Path $binDirectory -Force | Out-Null
        if (Test-Path -LiteralPath $installedJfrog -PathType Leaf) {
            $installedJfrogHash = (Get-FileHash -LiteralPath $installedJfrog -Algorithm SHA256).Hash
            if (-not $installedJfrogHash.Equals($JfrogCliSha256, [StringComparison]::OrdinalIgnoreCase)) {
                $jfrogBackup = "$installedJfrog.$backupSuffix.bak"
                Copy-Item -LiteralPath $installedJfrog -Destination $jfrogBackup -Force
            }
        }
        Copy-Item -LiteralPath $downloadedJfrog -Destination $installedJfrog -Force
        Add-UserPathEntry -Entry $binDirectory
    }
    else {
        $installedJfrog = $null
    }

    Write-Host ''
    Write-Host 'Graphify Review installation completed.' -ForegroundColor Green
    Write-Host "Repository kit: $destinationGithub"
    if ($installTrustStore) {
        Write-Host "Java truststore: $installedTrustStore"
        Write-Host 'User MAVEN_OPTS now points Maven at the installed truststore.'
    }
    if ($backupDirectory) {
        Write-Host "Previous .github backup: $backupDirectory"
    }
    if ($trustStoreBackup) {
        Write-Host "Previous truststore backup: $trustStoreBackup"
    }
    if ($installedJfrog) {
        Write-Host "JFrog CLI: $installedJfrog"
    }
    if ($jfrogBackup) {
        Write-Host "Previous JFrog CLI backup: $jfrogBackup"
    }
    Write-Host ''
    Write-Host 'Close every VS Code window and start VS Code again so Copilot inherits the updated user environment.' -ForegroundColor Yellow
    $setupScript = Join-Path $destinationGithub 'graphify-review\scripts\setup_review.py'
    if (-not $SkipSetup -and (Test-Path -LiteralPath $setupScript -PathType Leaf)) {
        $python = Get-Command py -ErrorAction SilentlyContinue
        $pythonPrefix = @('-3')
        if (-not $python) {
            $python = Get-Command python -ErrorAction SilentlyContinue
            $pythonPrefix = @()
        }
        if ($python) {
            $setupArguments = @($setupScript, 'configure', '--repository', $target, '--installed-root', $resolvedInstallRoot)
            if ($Preset) { $setupArguments += @('--preset', [IO.Path]::GetFullPath($Preset)) }
            if ($Proxy) { $setupArguments += @('--default', "user.proxy_url=$Proxy") }
            if ($TrustStoreType) { $setupArguments += @('--default', "user.java_truststore_type=$TrustStoreType") }
            if ($NonInteractive) { $setupArguments += '--non-interactive' }
            & $python.Source @pythonPrefix @setupArguments
            if ($LASTEXITCODE -ne 0) {
                Write-Warning 'Kit installed, but setup needs attention. Run /setup-review to finish; installed files and backups are retained.'
                exit $LASTEXITCODE
            }
        }
        else { Write-Warning 'Install Python 3.10+ or ask your administrator, then run /setup-review. The kit is installed.' }
    }
    else { Write-Host 'Run /setup-review in Copilot when ready to configure the project.' }
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
