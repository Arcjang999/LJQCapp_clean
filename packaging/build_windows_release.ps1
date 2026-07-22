param(
    [string]$ZipPath = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ServiceSpecFile = Join-Path $RepoRoot "packaging\LJQCApp.spec"
$QtSpecFile = Join-Path $RepoRoot "packaging\LJQCAppQt.spec"
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$AssetWriter = Join-Path $RepoRoot "packaging\write_release_assets.py"
$ZipWriter = Join-Path $RepoRoot "packaging\create_release_zip.py"

if ([string]::IsNullOrWhiteSpace($ZipPath)) {
    $compatName = "LJQCApp_windows_$([char]0x517C)$([char]0x5BB9)$([char]0x7248).zip"
    $ZipPath = Join-Path $RepoRoot (Join-Path "release" $compatName)
}
$ZipPath = [System.IO.Path]::GetFullPath($ZipPath)
$ZipDirectory = Split-Path -Parent $ZipPath

$LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
if ([string]::IsNullOrWhiteSpace($LocalAppData)) {
    $LocalAppData = [System.IO.Path]::GetTempPath()
}
$WorkRoot = Join-Path $LocalAppData "LJQCApp\compat_release_packaging"
$ServiceDist = Join-Path $WorkRoot "service_dist"
$ServiceWork = Join-Path $WorkRoot "service_work"
$QtDist = Join-Path $WorkRoot "qt_dist"
$QtWork = Join-Path $WorkRoot "qt_work"
$ReleaseStaging = Join-Path $WorkRoot "LJQCApp_compat_release"

function Remove-TreeSafely {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TargetPath,
        [Parameter(Mandatory = $true)]
        [string[]]$AllowedRoots
    )

    if (-not (Test-Path -LiteralPath $TargetPath)) {
        return
    }

    $resolvedTarget = (Resolve-Path -LiteralPath $TargetPath).Path
    $isAllowed = $false
    foreach ($allowedRoot in $AllowedRoots) {
        if (-not (Test-Path -LiteralPath $allowedRoot)) {
            continue
        }
        $resolvedRoot = (Resolve-Path -LiteralPath $allowedRoot).Path.TrimEnd("\")
        if ($resolvedTarget.Equals($resolvedRoot, [StringComparison]::OrdinalIgnoreCase) -or
            $resolvedTarget.StartsWith("$resolvedRoot\", [StringComparison]::OrdinalIgnoreCase)) {
            $isAllowed = $true
            break
        }
    }

    if (-not $isAllowed) {
        throw "Refusing to remove path outside allowed roots: $resolvedTarget"
    }

    Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
}

function Remove-ItemWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TargetPath,
        [switch]$Recurse
    )

    if (-not (Test-Path -LiteralPath $TargetPath)) {
        return
    }

    $lastError = $null
    for ($attempt = 1; $attempt -le 8; $attempt += 1) {
        try {
            if ($Recurse) {
                Remove-Item -LiteralPath $TargetPath -Recurse -Force -ErrorAction Stop
            }
            else {
                Remove-Item -LiteralPath $TargetPath -Force -ErrorAction Stop
            }
            return
        }
        catch {
            $lastError = $_
            Start-Sleep -Seconds 3
        }
    }

    throw $lastError
}

function Ensure-CleanDirectory {
    param([string]$TargetPath)

    New-Item -ItemType Directory -Path (Split-Path -Parent $TargetPath) -Force | Out-Null
    if (Test-Path -LiteralPath $TargetPath) {
        $resolvedTarget = (Resolve-Path -LiteralPath $TargetPath).Path
        $isAllowed = $false
        foreach ($allowedRoot in @($WorkRoot, $ZipDirectory)) {
            if (-not (Test-Path -LiteralPath $allowedRoot)) {
                continue
            }
            $resolvedRoot = (Resolve-Path -LiteralPath $allowedRoot).Path.TrimEnd("\")
            if ($resolvedTarget.Equals($resolvedRoot, [StringComparison]::OrdinalIgnoreCase) -or
                $resolvedTarget.StartsWith("$resolvedRoot\", [StringComparison]::OrdinalIgnoreCase)) {
                $isAllowed = $true
                break
            }
        }

        if (-not $isAllowed) {
            throw "Refusing to remove path outside allowed roots: $resolvedTarget"
        }
        Remove-ItemWithRetry -TargetPath $resolvedTarget -Recurse
    }
    New-Item -ItemType Directory -Path $TargetPath -Force | Out-Null
}

function Invoke-PyInstallerBuild {
    param(
        [string]$SpecFile,
        [string]$DistPath,
        [string]$WorkPath,
        [string]$AppName,
        [string]$BundleMode,
        [string]$Console
    )

    Ensure-CleanDirectory -TargetPath $DistPath
    Ensure-CleanDirectory -TargetPath $WorkPath

    Push-Location $RepoRoot
    try {
        $env:LJQCAPP_BUNDLE_MODE = $BundleMode
        $env:LJQCAPP_APP_NAME = $AppName
        $env:LJQCAPP_QT_APP_NAME = $AppName
        $env:LJQCAPP_CONSOLE = $Console

        & $PythonExe -m PyInstaller --clean -y --distpath $DistPath --workpath $WorkPath $SpecFile
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller build failed for $AppName."
        }
    }
    finally {
        Remove-Item Env:LJQCAPP_BUNDLE_MODE -ErrorAction SilentlyContinue
        Remove-Item Env:LJQCAPP_APP_NAME -ErrorAction SilentlyContinue
        Remove-Item Env:LJQCAPP_QT_APP_NAME -ErrorAction SilentlyContinue
        Remove-Item Env:LJQCAPP_CONSOLE -ErrorAction SilentlyContinue
        Pop-Location
    }
}

function Copy-DirectoryMirror {
    param(
        [string]$SourcePath,
        [string]$TargetPath
    )

    New-Item -ItemType Directory -Path $TargetPath -Force | Out-Null
    robocopy $SourcePath $TargetPath /MIR | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "Failed to copy $SourcePath to $TargetPath."
    }
}

function New-ReleaseFiles {
    Ensure-CleanDirectory -TargetPath $ReleaseStaging

    $qtPackage = Join-Path $QtDist "LJQCApp"
    $qtExe = Join-Path $qtPackage "LJQCApp.exe"
    if (-not (Test-Path -LiteralPath $qtExe)) {
        throw "Expected Qt launcher exe not found: $qtExe"
    }
    Copy-DirectoryMirror -SourcePath $qtPackage -TargetPath $ReleaseStaging

    $servicePackage = Join-Path $ServiceDist "LJQCAppService"
    $serviceExe = Join-Path $servicePackage "LJQCAppService.exe"
    if (-not (Test-Path -LiteralPath $serviceExe)) {
        throw "Expected service exe not found: $serviceExe"
    }
    Copy-DirectoryMirror -SourcePath $servicePackage -TargetPath (Join-Path $ReleaseStaging "_internal\app")

    $runtimeSource = Join-Path $RepoRoot "packaging\runtime"
    if (Test-Path -LiteralPath $runtimeSource) {
        Copy-DirectoryMirror -SourcePath $runtimeSource -TargetPath (Join-Path $ReleaseStaging "_internal\runtime")
    }

    $gitCommit = ""
    try {
        $gitCommit = (& git -C $RepoRoot rev-parse --short HEAD).Trim()
    }
    catch {
        $gitCommit = ""
    }
    $buildTime = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    & $PythonExe $AssetWriter --staging $ReleaseStaging --git-commit $gitCommit --build-time $buildTime
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to write release assets."
    }
}

function Compress-Release {
    New-Item -ItemType Directory -Path $ZipDirectory -Force | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $ZipDirectory -Force -ErrorAction SilentlyContinue) {
        Remove-ItemWithRetry -TargetPath $item.FullName -Recurse:($item.PSIsContainer)
    }

    & $PythonExe $ZipWriter --source $ReleaseStaging --output $ZipPath --work-dir $WorkRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create release zip."
    }
}

foreach ($requiredPath in @($PythonExe, $ServiceSpecFile, $QtSpecFile, $AssetWriter, $ZipWriter)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required build file not found: $requiredPath"
    }
}

New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null
Remove-TreeSafely -TargetPath (Join-Path $RepoRoot "build") -AllowedRoots @($RepoRoot)
Remove-TreeSafely -TargetPath (Join-Path $RepoRoot "dist") -AllowedRoots @($RepoRoot)
Remove-TreeSafely -TargetPath (Join-Path $RepoRoot "__pycache__") -AllowedRoots @($RepoRoot)
Remove-TreeSafely -TargetPath (Join-Path $RepoRoot ".pytest_cache") -AllowedRoots @($RepoRoot)
Remove-TreeSafely -TargetPath (Join-Path $RepoRoot "packaging\desktop_launcher\bin") -AllowedRoots @($RepoRoot)
Remove-TreeSafely -TargetPath (Join-Path $RepoRoot "packaging\desktop_launcher\obj") -AllowedRoots @($RepoRoot)

Invoke-PyInstallerBuild `
    -SpecFile $ServiceSpecFile `
    -DistPath $ServiceDist `
    -WorkPath $ServiceWork `
    -AppName "LJQCAppService" `
    -BundleMode "onedir" `
    -Console "true"

Invoke-PyInstallerBuild `
    -SpecFile $QtSpecFile `
    -DistPath $QtDist `
    -WorkPath $QtWork `
    -AppName "LJQCApp" `
    -BundleMode "onedir" `
    -Console "false"

New-ReleaseFiles
Compress-Release

Remove-TreeSafely -TargetPath (Join-Path $RepoRoot "build") -AllowedRoots @($RepoRoot)
Remove-TreeSafely -TargetPath (Join-Path $RepoRoot "dist") -AllowedRoots @($RepoRoot)
Remove-TreeSafely -TargetPath (Join-Path $RepoRoot "__pycache__") -AllowedRoots @($RepoRoot)
Remove-TreeSafely -TargetPath (Join-Path $RepoRoot ".pytest_cache") -AllowedRoots @($RepoRoot)
Remove-TreeSafely -TargetPath (Join-Path $RepoRoot "packaging\desktop_launcher\bin") -AllowedRoots @($RepoRoot)
Remove-TreeSafely -TargetPath (Join-Path $RepoRoot "packaging\desktop_launcher\obj") -AllowedRoots @($RepoRoot)

Write-Output "BUILD_OK"
Write-Output "ZIP_PATH=$ZipPath"
Write-Output "RELEASE_STAGING=$ReleaseStaging"
