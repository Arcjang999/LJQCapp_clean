param(
    [string]$OutputRoot = "D:\LJQCappdemo"
)

$ErrorActionPreference = "Stop"

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:PythonExe = "C:\Users\gao_h\AppData\Local\Python\bin\python.exe"
$script:SpecFile = Join-Path $RepoRoot "packaging\LJQCApp.spec"
$script:LauncherProject = Join-Path $RepoRoot "packaging\desktop_launcher\LJQCApp.Desktop.csproj"

$buildRoot = Join-Path $OutputRoot "build"
$distRoot = Join-Path $OutputRoot "dist"
$releaseRoot = Join-Path $OutputRoot "release"

$serviceOnedirDist = Join-Path $buildRoot "service_onedir_dist"
$serviceOnedirWork = Join-Path $buildRoot "service_onedir_work"
$serviceOnefileDist = Join-Path $buildRoot "service_onefile_dist"
$serviceOnefileWork = Join-Path $buildRoot "service_onefile_work"
$launcherObjRoot = Join-Path $buildRoot "launcher_obj"

$launcherOnedirOutput = Join-Path $distRoot "LJQCApp"
$launcherOnefileOutput = $releaseRoot
$rootEntryExe = Join-Path $OutputRoot "LJQCApp.exe"

function Remove-Safely {
    param([string]$TargetPath)

    if (-not (Test-Path -LiteralPath $TargetPath)) {
        return
    }

    $resolved = (Resolve-Path -LiteralPath $TargetPath).Path
    if (-not $resolved.StartsWith($OutputRoot)) {
        throw "Refusing to remove path outside output root: $resolved"
    }

    Remove-Item -LiteralPath $resolved -Recurse -Force
}

function Ensure-CleanDirectory {
    param([string]$TargetPath)

    Remove-Safely -TargetPath $TargetPath
    New-Item -ItemType Directory -Path $TargetPath | Out-Null
}

function Invoke-PyInstallerBuild {
    param(
        [string]$BundleMode,
        [string]$AppName,
        [string]$DistPath,
        [string]$WorkPath
    )

    Ensure-CleanDirectory -TargetPath $DistPath
    Ensure-CleanDirectory -TargetPath $WorkPath

    Push-Location $RepoRoot
    try {
        $env:LJQCAPP_BUNDLE_MODE = $BundleMode
        $env:LJQCAPP_APP_NAME = $AppName
        $env:LJQCAPP_CONSOLE = "false"

        & $PythonExe -m PyInstaller --clean -y --distpath $DistPath --workpath $WorkPath $SpecFile
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller build failed for $AppName ($BundleMode)."
        }
    }
    finally {
        Remove-Item Env:LJQCAPP_BUNDLE_MODE -ErrorAction SilentlyContinue
        Remove-Item Env:LJQCAPP_APP_NAME -ErrorAction SilentlyContinue
        Remove-Item Env:LJQCAPP_CONSOLE -ErrorAction SilentlyContinue
        Pop-Location
    }
}

function Invoke-DotnetPublish {
    param(
        [string]$OutputPath,
        [string]$IntermediatePath,
        [hashtable]$ExtraProperties
    )

    Ensure-CleanDirectory -TargetPath $OutputPath
    Ensure-CleanDirectory -TargetPath $IntermediatePath

    $propertyArgs = @(
        "-p:BaseOutputPath=$IntermediatePath\bin\"
        "-p:BaseIntermediateOutputPath=$IntermediatePath\"
        "-p:IntermediateOutputPath=$IntermediatePath\obj\"
        "-p:DebugSymbols=false"
        "-p:DebugType=None"
        "-p:PublishTrimmed=false"
    )
    foreach ($entry in $ExtraProperties.GetEnumerator()) {
        $propertyArgs += "-p:$($entry.Key)=$($entry.Value)"
    }

    & dotnet publish $LauncherProject `
        -c Release `
        -r win-x64 `
        --self-contained true `
        -o $OutputPath `
        @propertyArgs

    if ($LASTEXITCODE -ne 0) {
        throw "dotnet publish failed for $OutputPath"
    }
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

if (-not (Test-Path -LiteralPath $SpecFile)) {
    throw "Spec file not found: $SpecFile"
}

if (-not (Test-Path -LiteralPath $LauncherProject)) {
    throw "Launcher project not found: $LauncherProject"
}

Ensure-CleanDirectory -TargetPath $buildRoot
Ensure-CleanDirectory -TargetPath $distRoot
Ensure-CleanDirectory -TargetPath $releaseRoot

Invoke-PyInstallerBuild `
    -BundleMode "onedir" `
    -AppName "LJQCAppService" `
    -DistPath $serviceOnedirDist `
    -WorkPath $serviceOnedirWork

$serviceOnedirPackage = Join-Path $serviceOnedirDist "LJQCAppService"
if (-not (Test-Path -LiteralPath $serviceOnedirPackage)) {
    throw "Expected onedir service package not found: $serviceOnedirPackage"
}

Invoke-PyInstallerBuild `
    -BundleMode "onefile" `
    -AppName "LJQCAppService" `
    -DistPath $serviceOnefileDist `
    -WorkPath $serviceOnefileWork

$serviceOnefileExe = Join-Path $serviceOnefileDist "LJQCAppService.exe"
if (-not (Test-Path -LiteralPath $serviceOnefileExe)) {
    throw "Expected onefile service executable not found: $serviceOnefileExe"
}

Invoke-DotnetPublish `
    -OutputPath $launcherOnedirOutput `
    -IntermediatePath (Join-Path $launcherObjRoot "onedir") `
    -ExtraProperties @{
        PublishSingleFile = "false"
        IncludeNativeLibrariesForSelfExtract = "false"
    }

$serviceTargetDir = Join-Path $launcherOnedirOutput "service"
Ensure-CleanDirectory -TargetPath $serviceTargetDir
robocopy $serviceOnedirPackage $serviceTargetDir /MIR | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "Failed to copy onedir service package into launcher output."
}

Invoke-DotnetPublish `
    -OutputPath $launcherOnefileOutput `
    -IntermediatePath (Join-Path $launcherObjRoot "onefile") `
    -ExtraProperties @{
        PublishSingleFile = "true"
        IncludeNativeLibrariesForSelfExtract = "true"
        EmbeddedServicePath = $serviceOnefileExe
    }

Get-ChildItem -LiteralPath $launcherOnefileOutput -Filter "*.xml" -File -ErrorAction SilentlyContinue | Remove-Item -Force
Copy-Item -LiteralPath (Join-Path $launcherOnefileOutput "LJQCApp.exe") -Destination $rootEntryExe -Force
Remove-Safely -TargetPath (Join-Path $RepoRoot "packaging\desktop_launcher\bin")
Remove-Safely -TargetPath (Join-Path $RepoRoot "packaging\desktop_launcher\obj")

Write-Output "BUILD_OK"
Write-Output "OUTPUT_ROOT=$OutputRoot"
Write-Output "ONEDIR_EXE=$(Join-Path $launcherOnedirOutput 'LJQCApp.exe')"
Write-Output "ONEFILE_EXE=$(Join-Path $launcherOnefileOutput 'LJQCApp.exe')"
Write-Output "ROOT_ENTRY_EXE=$rootEntryExe"
