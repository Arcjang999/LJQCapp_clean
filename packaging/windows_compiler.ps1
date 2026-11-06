# Dot-source this file to inspect the actual Inno compiler engine. ISCC.exe may
# deliberately carry a 0.0.0.0 Windows file-version resource.
function Get-LJQCInnoCompilerVersion {
    param(
        [Parameter(Mandatory = $true)][string]$CompilerPath,
        [Parameter(Mandatory = $true)][string]$EvidenceDirectory
    )
    New-Item -ItemType Directory -Path $EvidenceDirectory -Force | Out-Null
    $ProbePath = Join-Path $EvidenceDirectory "inno-compiler-probe.iss"
    $StdoutPath = Join-Path $EvidenceDirectory "inno-compiler-probe-stdout.log"
    $StderrPath = Join-Path $EvidenceDirectory "inno-compiler-probe-stderr.log"
    foreach ($Path in @($ProbePath, $StdoutPath, $StderrPath)) {
        if (Test-Path -LiteralPath $Path) { throw "Compiler probe evidence already exists: $Path" }
    }
    # Ver is the compiler's own 32-bit version, exposed by its preprocessor.
    # Output=no checks a minimal script without producing an installer.
    $Probe = @'
#if Ver < EncodeVer(6, 5, 0) || Ver >= EncodeVer(7, 0, 0)
  #error LJQC requires Inno Setup 6.5 or newer within version 6.
#endif
#pragma message "LJQC_INNO_VERSION=" + DecodeVer(Ver)
[Setup]
AppName=LJQC Compiler Probe
AppVersion=1
CreateAppDir=no
Uninstallable=no
Output=no
'@
    [IO.File]::WriteAllText($ProbePath, $Probe, [Text.UTF8Encoding]::new($false))
    $ProbeProcess = Start-Process -FilePath $CompilerPath -ArgumentList @('"' + $ProbePath + '"') -Wait -PassThru -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath
    $Output = [IO.File]::ReadAllText($StdoutPath) + "`n" + [IO.File]::ReadAllText($StderrPath)
    if ($ProbeProcess.ExitCode -ne 0) {
        throw "Inno compiler version probe failed (exit $($ProbeProcess.ExitCode)). See $StdoutPath and $StderrPath."
    }
    $VersionMatch = [regex]::Match($Output, 'LJQC_INNO_VERSION=(\d+\.\d+\.\d+(?:\.\d+)?)')
    if (-not $VersionMatch.Success) { throw "Inno compiler did not report its version. See $StdoutPath and $StderrPath." }
    $Version = [version]$VersionMatch.Groups[1].Value
    if ($Version.Major -ne 6 -or $Version.Minor -lt 5) {
        throw "Inno Setup 6.5 or newer within version 6 is required; found $Version."
    }
    return $Version.ToString()
}
