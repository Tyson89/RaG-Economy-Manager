param(
    [switch]$CheckArtifacts,
    [switch]$SkipBuildFiles
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

Push-Location $ProjectRoot
try {
    $Args = @("rag_release_checks.py")
    if ($CheckArtifacts) {
        $Args += "--check-artifacts"
    }
    if ($SkipBuildFiles) {
        $Args += "--skip-build-files"
    }

    python @Args
    if ($LASTEXITCODE -ne 0) {
        throw "Release readiness checks failed."
    }
}
finally {
    Pop-Location
}
