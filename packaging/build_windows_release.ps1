param(
    [string]$PythonExe = "",
    [string]$OutputRoot = "",
    [string]$InnoCompiler = "",
    [string]$ChineseMessagesFile = "",
    [string]$ZipPath = "",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($env:OS -ne "Windows_NT") { throw "Build the Windows installer on a Windows x64 computer." }
. (Join-Path $PSScriptRoot "windows_compiler.ps1")
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $PythonExe) { $PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw "Python not found: $PythonExe" }
if (-not $OutputRoot) { $OutputRoot = Join-Path $RepoRoot "release" }
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)

# Each invocation uses new directories. Previous installers and work evidence stay intact.
$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
if (-not $LocalAppData) { throw "The current user's LocalApplicationData directory is unavailable." }
$WorkRoot = Join-Path $LocalAppData "LJQCAppBuild\$RunId"
New-Item -ItemType Directory -Path $WorkRoot | Out-Null
$MetadataPath = Join-Path $WorkRoot "build_info.json"
Push-Location $RepoRoot
try {
    & $PythonExe -c "import platform,sys; assert sys.platform == 'win32' and platform.architecture()[0] == '64bit' and platform.machine().lower() in ('amd64','x86_64'), 'Use Windows x64 Python'"
    if ($LASTEXITCODE -ne 0) { throw "A Windows x64 Python runtime is required." }
    & $PythonExe -m services.release_info --output $MetadataPath --target windows-x86_64
    if ($LASTEXITCODE -ne 0) { throw "Failed to capture the release source identity." }
} finally { Pop-Location }
$Metadata = Get-Content -LiteralPath $MetadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Metadata.version -notmatch '^\d+\.\d+\.\d+$' -or $Metadata.source_digest -notmatch '^[0-9a-f]{64}$' -or $Metadata.git_commit -notmatch '^[0-9a-f]{40}$') {
    throw "The canonical release metadata is incomplete."
}
$PayloadId = "$($Metadata.version)-$($Metadata.build_number)-$($Metadata.source_digest.Substring(0, 12))"
$ArtifactDir = Join-Path $OutputRoot "$($Metadata.version)\$($Metadata.build_number)\windows-x86_64\$RunId"
New-Item -ItemType Directory -Path $ArtifactDir | Out-Null
Copy-Item -LiteralPath $MetadataPath -Destination (Join-Path $ArtifactDir "build_info.json")
$ServiceDist = Join-Path $WorkRoot "service_dist"
$QtDist = Join-Path $WorkRoot "qt_dist"
$ReleaseStaging = Join-Path $WorkRoot "payload"

if (-not $SkipInstaller -and -not $InnoCompiler) {
    $FoundCompiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($FoundCompiler) { $InnoCompiler = $FoundCompiler.Source }
    if (-not $InnoCompiler) {
        foreach ($Candidate in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe")) {
            if (Test-Path -LiteralPath $Candidate -PathType Leaf) { $InnoCompiler = $Candidate; break }
        }
    }
}
if (-not $SkipInstaller -and (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf))) {
    throw "Install Inno Setup 6 from its official source, or pass -InnoCompiler PATH. No previous artifacts were removed."
}
if (-not $SkipInstaller) {
    $InnoCompiler = (Resolve-Path -LiteralPath $InnoCompiler).Path
    $CompilerVersion = Get-LJQCInnoCompilerVersion -CompilerPath $InnoCompiler -EvidenceDirectory (Join-Path $ArtifactDir "logs")
    if (-not $ChineseMessagesFile) { $ChineseMessagesFile = Join-Path (Split-Path -Parent $InnoCompiler) "Languages\ChineseSimplified.isl" }
    if (-not (Test-Path -LiteralPath $ChineseMessagesFile -PathType Leaf)) {
        throw "The Simplified Chinese language file is missing. In GitHub Actions run packaging/prepare_windows_ci.ps1 and pass its LJQC_CI_CHINESE_MESSAGES path with -ChineseMessagesFile."
    }
    $ChineseMessagesFile = (Resolve-Path -LiteralPath $ChineseMessagesFile).Path
}

function Invoke-PackageBuild([string]$Spec, [string]$Dist, [string]$Work, [string]$Name, [string]$Console) {
    Push-Location $RepoRoot
    try {
        $env:LJQCAPP_BUNDLE_MODE = "onedir"
        $env:LJQCAPP_APP_NAME = $Name
        $env:LJQCAPP_QT_APP_NAME = $Name
        $env:LJQCAPP_CONSOLE = $Console
        $env:LJQCAPP_BUILD_METADATA = $MetadataPath
        & $PythonExe -m PyInstaller --clean -y --distpath $Dist --workpath $Work $Spec
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed: $Name" }
    } finally {
        foreach ($NameToClear in @("LJQCAPP_BUNDLE_MODE", "LJQCAPP_APP_NAME", "LJQCAPP_QT_APP_NAME", "LJQCAPP_CONSOLE", "LJQCAPP_BUILD_METADATA")) {
            Remove-Item "Env:\$NameToClear" -ErrorAction SilentlyContinue
        }
        Pop-Location
    }
}

function Copy-Package([string]$Source, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) { throw "Package not found: $Source" }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | Copy-Item -Destination $Destination -Recurse
}

Invoke-PackageBuild (Join-Path $PSScriptRoot "LJQCApp.spec") $ServiceDist (Join-Path $WorkRoot "service_work") "LJQCAppService" "true"
Invoke-PackageBuild (Join-Path $PSScriptRoot "LJQCAppQt.spec") $QtDist (Join-Path $WorkRoot "qt_work") "LJQCApp" "false"
Copy-Package (Join-Path $QtDist "LJQCApp") $ReleaseStaging
Copy-Package (Join-Path $ServiceDist "LJQCAppService") (Join-Path $ReleaseStaging "_internal\app")
foreach ($Executable in @((Join-Path $ReleaseStaging "LJQCApp.exe"), (Join-Path $ReleaseStaging "_internal\app\LJQCAppService.exe"))) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { throw "Assembled executable is missing: $Executable" }
}
& $PythonExe (Join-Path $PSScriptRoot "write_release_assets.py") --staging $ReleaseStaging --metadata $MetadataPath
if ($LASTEXITCODE -ne 0) { throw "Release resource and payload verification failed." }
Copy-Item -LiteralPath (Join-Path $ReleaseStaging "release_payload_manifest.json") -Destination (Join-Path $ArtifactDir "release_payload_manifest.json")
& $PythonExe -m pip freeze | Out-File -LiteralPath (Join-Path $ArtifactDir "windows-build-environment.txt") -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "Failed to record the Windows build environment." }

# Exercise both frozen entry points before declaring a successful build. The
# version command must not create a business database, even on a build account.
$OriginalDataRoot = $env:LJQC_DATA_ROOT
$OriginalPythonUtf8 = $env:PYTHONUTF8
$CliDataRoot = Join-Path $WorkRoot "cli-verification-data"
try {
    $env:LJQC_DATA_ROOT = $CliDataRoot
    $env:PYTHONUTF8 = "1"
    foreach ($Executable in @((Join-Path $ReleaseStaging "_internal\app\LJQCAppService.exe"), (Join-Path $ReleaseStaging "LJQCApp.exe"))) {
        $OutputPath = Join-Path $ArtifactDir ((Split-Path -Leaf $Executable) + "-version.txt")
        $ErrorPath = Join-Path $ArtifactDir ((Split-Path -Leaf $Executable) + "-version-error.txt")
        # The outer launcher is a GUI executable; '&' does not reliably wait for
        # it under PowerShell. A held process handle gives its real exit status.
        $VersionProcess = Start-Process -FilePath $Executable -ArgumentList @("--version") -Wait -PassThru -RedirectStandardOutput $OutputPath -RedirectStandardError $ErrorPath
        if ($VersionProcess.ExitCode -ne 0) {
            Write-Host "Frozen version stdout ($Executable):"
            Get-Content -LiteralPath $OutputPath -Encoding utf8 | Write-Host
            Write-Host "Frozen version stderr ($Executable):"
            Get-Content -LiteralPath $ErrorPath -Encoding utf8 | Write-Host
            throw "Frozen version command failed: $Executable (exit $($VersionProcess.ExitCode)). See $ErrorPath"
        }
    }
    if ((Test-Path -LiteralPath $CliDataRoot) -and (Get-ChildItem -LiteralPath $CliDataRoot -Recurse -File -Filter "*.db")) {
        throw "A frozen version command unexpectedly created a database."
    }
} finally {
    $DiagnosticDirectory = Join-Path $ArtifactDir "logs"
    New-Item -ItemType Directory -Path $DiagnosticDirectory -Force | Out-Null
    foreach ($VersionLog in (Get-ChildItem -LiteralPath $ArtifactDir -File -Filter "*-version*.txt")) {
        Copy-Item -LiteralPath $VersionLog.FullName -Destination (Join-Path $DiagnosticDirectory ($VersionLog.BaseName + ".log"))
    }
    $IsolatedLogs = Join-Path $CliDataRoot "logs"
    if (Test-Path -LiteralPath $IsolatedLogs -PathType Container) {
        foreach ($ApplicationLog in (Get-ChildItem -LiteralPath $IsolatedLogs -File -Filter "*.log")) {
            Copy-Item -LiteralPath $ApplicationLog.FullName -Destination (Join-Path $DiagnosticDirectory ("frozen-cli-" + $ApplicationLog.Name))
            Write-Host "Isolated frozen CLI log ($($ApplicationLog.Name)):"
            Get-Content -LiteralPath $ApplicationLog.FullName -Encoding utf8 | Write-Host
        }
    }
    if ($null -eq $OriginalDataRoot) { Remove-Item Env:\LJQC_DATA_ROOT -ErrorAction SilentlyContinue }
    else { $env:LJQC_DATA_ROOT = $OriginalDataRoot }
    if ($null -eq $OriginalPythonUtf8) { Remove-Item Env:\PYTHONUTF8 -ErrorAction SilentlyContinue }
    else { $env:PYTHONUTF8 = $OriginalPythonUtf8 }
}
$FinalMetadataPath = Join-Path $WorkRoot "source-after-build.json"
Push-Location $RepoRoot
try {
    & $PythonExe -m services.release_info --output $FinalMetadataPath --target windows-x86_64
    if ($LASTEXITCODE -ne 0) { throw "Failed to verify source identity after packaging." }
} finally { Pop-Location }
$FinalMetadata = Get-Content -LiteralPath $FinalMetadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($FinalMetadata.source_digest -ne $Metadata.source_digest -or $FinalMetadata.git_commit -ne $Metadata.git_commit) {
    throw "Source changed during packaging. This work directory is retained; no installer is accepted."
}
if (-not $ZipPath) { $ZipPath = Join-Path $ArtifactDir "LJQCApp-$PayloadId-windows-x64.zip" }
$ZipPath = [IO.Path]::GetFullPath($ZipPath)
if (Test-Path -LiteralPath $ZipPath) { throw "An existing output file must not be overwritten: $ZipPath" }
& $PythonExe (Join-Path $PSScriptRoot "create_release_zip.py") --source $ReleaseStaging --output $ZipPath --work-dir $WorkRoot
if ($LASTEXITCODE -ne 0) { throw "Release ZIP creation failed." }

$InstallerPath = $null
if (-not $SkipInstaller) {
    $InstallerBase = "LJQCApp-$PayloadId-windows-x64-Setup"
    $CompilerArgs = @("/DSourceDir=$ReleaseStaging", "/DOutputDir=$ArtifactDir", "/DOutputBase=$InstallerBase", "/DAppVersion=$($Metadata.version)", "/DBuildNumber=$($Metadata.build_number)", "/DPayloadId=$PayloadId", "/DChineseMessagesFile=$ChineseMessagesFile", (Join-Path $PSScriptRoot "windows_installer.iss"))
    & $InnoCompiler @CompilerArgs
    if ($LASTEXITCODE -ne 0) { throw "Windows installer compilation failed." }
    $InstallerPath = Join-Path $ArtifactDir "$InstallerBase.exe"
    if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) { throw "The compiled installer was not found." }
}
$Artifacts = @([ordered]@{path=$ZipPath; sha256=(Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()})
if ($InstallerPath) { $Artifacts += [ordered]@{path=$InstallerPath; sha256=(Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()} }
$Compiler = if ($SkipInstaller) { $null } else { [ordered]@{path=$InnoCompiler; version=$CompilerVersion; chinese_messages_sha256=(Get-FileHash -LiteralPath $ChineseMessagesFile -Algorithm SHA256).Hash.ToLowerInvariant()} }
$Receipt = [ordered]@{status=$(if ($SkipInstaller) {"payload_built"} else {"installer_built"}); release=$Metadata; payload_id=$PayloadId; staging=$ReleaseStaging; artifacts=$Artifacts; compiler=$Compiler; frozen_version_commands="passed"; native_installation_validation="pending"; code_signing="not_configured"; mutable_data_included=$false}
$Receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $ArtifactDir "build_receipt.json") -Encoding UTF8
Write-Output "BUILD_OK"
Write-Output "INSTALLER_PATH=$InstallerPath"
Write-Output "ZIP_PATH=$ZipPath"
Write-Output "RELEASE_STAGING=$ReleaseStaging"
Write-Output "BUILD_RECEIPT=$(Join-Path $ArtifactDir 'build_receipt.json')"
