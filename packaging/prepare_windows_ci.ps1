param(
    [string]$InnoCompiler = "",
    [string]$PreparationRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($env:OS -ne "Windows_NT") { throw "Windows CI preparation must run on Windows." }
. (Join-Path $PSScriptRoot "windows_compiler.ps1")

# GitHub's windows-2022 image already includes Inno Setup 6. Prefer it to an
# unbounded package-manager upgrade, and fail rather than silently skip Setup.
if (-not $InnoCompiler) {
    $FoundCompiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($FoundCompiler) { $InnoCompiler = $FoundCompiler.Source }
    foreach ($Candidate in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe")) {
        if (-not $InnoCompiler -and (Test-Path -LiteralPath $Candidate -PathType Leaf)) { $InnoCompiler = $Candidate }
    }
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) {
    throw "Inno Setup 6.5+ was not found. Use the supported windows-2022 runner image or provide -InnoCompiler."
}
$InnoCompiler = (Resolve-Path -LiteralPath $InnoCompiler).Path

if (-not $PreparationRoot) {
    $TemporaryRoot = if ($env:GITHUB_ACTIONS -eq "true") {
        Join-Path (Split-Path -Parent $PSScriptRoot) "output\ci-verification\windows-compiler"
    } elseif ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
    $PreparationRoot = Join-Path $TemporaryRoot ("ljqc-windows-ci-" + [guid]::NewGuid().ToString("N"))
}
$PreparationRoot = [IO.Path]::GetFullPath($PreparationRoot)
if (Test-Path -LiteralPath $PreparationRoot) { throw "CI preparation output must be a new directory: $PreparationRoot" }
New-Item -ItemType Directory -Path $PreparationRoot | Out-Null
$CompilerVersion = Get-LJQCInnoCompilerVersion -CompilerPath $InnoCompiler -EvidenceDirectory $PreparationRoot

$CompilerDirectory = Split-Path -Parent $InnoCompiler
$ChineseMessagesFile = Join-Path $CompilerDirectory "Languages\ChineseSimplified.isl"
$LanguageSource = "runner-image"
if (-not (Test-Path -LiteralPath $ChineseMessagesFile -PathType Leaf)) {
    # Chinese became an official bundled translation after older Inno images.
    # This fixed upstream version is compatible with Inno 6.5+; its digest makes
    # a moved tag, failed download, or unexpected response a hard build failure.
    $LanguageSource = "https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_1/Files/Languages/Unofficial/ChineseSimplified.isl"
    $ExpectedLanguageHash = "7d544b9bb1d142cfa11f2e5d3cc8abe2e55f8e066c5124e3772675aa236e1278"
    $ChineseMessagesFile = Join-Path $PreparationRoot "ChineseSimplified.isl"
    Invoke-WebRequest -Uri $LanguageSource -OutFile $ChineseMessagesFile -TimeoutSec 60
    $ActualLanguageHash = (Get-FileHash -LiteralPath $ChineseMessagesFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualLanguageHash -ne $ExpectedLanguageHash) { throw "The official Inno Chinese language file failed SHA-256 verification." }
}
$LanguageHash = (Get-FileHash -LiteralPath $ChineseMessagesFile -Algorithm SHA256).Hash.ToLowerInvariant()
$ReceiptPath = Join-Path $PreparationRoot "windows-ci-preparation.json"
[ordered]@{
    status = "ready"
    compiler_path = $InnoCompiler
    compiler_version = $CompilerVersion
    compiler_version_source = "ISPP Ver probe"
    chinese_messages_file = $ChineseMessagesFile
    chinese_messages_sha256 = $LanguageHash
    chinese_messages_source = $LanguageSource
    prepared_at = (Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json | Set-Content -LiteralPath $ReceiptPath -Encoding utf8

$env:LJQC_CI_INNO_COMPILER = $InnoCompiler
$env:LJQC_CI_CHINESE_MESSAGES = $ChineseMessagesFile
$env:LJQC_CI_PREPARATION_RECEIPT = $ReceiptPath
if ($env:GITHUB_ENV) {
    foreach ($Entry in @("LJQC_CI_INNO_COMPILER=$InnoCompiler", "LJQC_CI_CHINESE_MESSAGES=$ChineseMessagesFile", "LJQC_CI_PREPARATION_RECEIPT=$ReceiptPath")) {
        Add-Content -LiteralPath $env:GITHUB_ENV -Value $Entry -Encoding utf8
    }
}
Write-Output "WINDOWS_CI_READY"
Write-Output "INNO_VERSION=$CompilerVersion"
Write-Output "PREPARATION_RECEIPT=$ReceiptPath"
