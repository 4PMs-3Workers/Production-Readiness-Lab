[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 3000
)

$ErrorActionPreference = 'Stop'
$frontendRepositoryRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$frontendNodeCommand = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue
$frontendNodeExecutable = if ($frontendNodeCommand) { $frontendNodeCommand.Source } else { $null }

if (-not $frontendNodeExecutable) {
    $frontendNodeCandidates = @(
        (Join-Path $env:ProgramFiles 'nodejs/node.exe'),
        (Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
    )
    $frontendNodeExecutable = $frontendNodeCandidates |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
}

if (-not $frontendNodeExecutable) {
    throw 'Node.js was not found. Install Node.js 24 LTS, then run npm ci from the repository root.'
}

$frontendNodeVersion = [version]((& $frontendNodeExecutable --version).TrimStart('v'))
if ($frontendNodeVersion.Major -ne 24 -or $frontendNodeVersion.Minor -lt 15) {
    throw "Node.js 24.15+ is required; found $frontendNodeVersion."
}

$frontendNextCli = Join-Path $frontendRepositoryRoot 'node_modules/next/dist/bin/next'
if (-not (Test-Path -LiteralPath $frontendNextCli -PathType Leaf)) {
    throw 'Frontend dependencies are missing. Install Node.js/npm and run npm ci from the repository root.'
}

$frontendOriginalPath = $env:Path
Push-Location (Join-Path $frontendRepositoryRoot 'src/frontend')
try {
    # Child processes also need Node on PATH. This change lasts only for this script.
    $env:Path = "$(Split-Path $frontendNodeExecutable -Parent);$frontendOriginalPath"
    Write-Host "Using Node.js $frontendNodeVersion"
    & $frontendNodeExecutable $frontendNextCli dev --hostname 127.0.0.1 --port $Port
    $frontendExitCode = $LASTEXITCODE
}
finally {
    $env:Path = $frontendOriginalPath
    Pop-Location
}

exit $frontendExitCode
