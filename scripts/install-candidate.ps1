[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$BundleRoot = ''
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrEmpty($BundleRoot)) { $BundleRoot = Split-Path -Parent $PSScriptRoot }

function Get-Sha256([string]$literalPath) {
    # Use the runtime directly: child Windows PowerShell can inherit Core module paths.
    $stream = [IO.File]::OpenRead($literalPath)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $hasher.Dispose() }
}

function Test-LocalAbsolutePath([string]$path) { return $path -match '^[A-Za-z]:[\\/]' }

function Assert-RegularAncestors([string]$existingPath) {
    $node = Get-Item -LiteralPath $existingPath -Force
    while ($null -ne $node) {
        if (($node.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Reparse-point paths are not supported; choose a physical local directory.'
        }
        $node = $node.Parent
    }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Windows is required.' }
if (-not (Test-LocalAbsolutePath $Python) -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Python must name an existing absolute CPython 3.13 executable.'
}
if (-not (Test-LocalAbsolutePath $Destination)) { throw 'Destination must be a fully qualified local absolute path.' }
$destinationPath = [IO.Path]::GetFullPath($Destination).TrimEnd('\', '/')
if (Test-Path -LiteralPath $destinationPath) { throw 'Destination already exists; choose a new venv directory.' }
if (-not (Test-Path -LiteralPath (Split-Path -Parent $destinationPath) -PathType Container)) {
    throw 'Destination parent must already exist.'
}
if (-not (Test-LocalAbsolutePath $BundleRoot)) { throw 'BundleRoot must be a fully qualified local absolute path.' }
$rootPath = [IO.Path]::GetFullPath($BundleRoot).TrimEnd('\', '/')
if (-not (Test-Path -LiteralPath $rootPath -PathType Container)) { throw 'BundleRoot must be a directory.' }
Assert-RegularAncestors $rootPath
Assert-RegularAncestors (Split-Path -Parent $destinationPath)
$rootPrefix = $rootPath + [IO.Path]::DirectorySeparatorChar
$destinationPrefix = $destinationPath + [IO.Path]::DirectorySeparatorChar
if ($destinationPrefix.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $rootPrefix.StartsWith($destinationPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The new venv and bundle must be separate directories.'
}

function Get-BundleFile([string]$relativePath) {
    if ([string]::IsNullOrWhiteSpace($relativePath) -or [IO.Path]::IsPathRooted($relativePath) -or
        $relativePath -match '(^|/)\.\.(/|$)|:|\\') { throw 'Invalid bundle-relative path.' }
    $fullPath = [IO.Path]::GetFullPath((Join-Path $rootPath $relativePath))
    if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Path escaped bundle.' }
    $node = Get-Item -LiteralPath $fullPath -Force
    if ($node.PSIsContainer) { throw 'Expected a bundle file.' }
    if (($node.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Bundle links are not supported.' }
    $node = $node.Directory
    while ($null -ne $node -and $node.FullName.Length -ge $rootPath.Length) {
        if (($node.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Bundle links are not supported.' }
        $node = $node.Parent
        if ($null -eq $node) { break }
    }
    return $fullPath
}

$manifestPath = Get-BundleFile 'candidate.json'
$manifestHashPath = Get-BundleFile 'candidate.sha256'
$expectedManifest = ((Get-Content -LiteralPath $manifestHashPath -Raw).Trim() -split '\s+')[0]
if ($expectedManifest -notmatch '^[a-f0-9]{64}$' -or
    (Get-Sha256 $manifestPath) -ne $expectedManifest) {
    throw 'Candidate manifest SHA mismatch.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema -ne 'pacttrail.windows-candidate.v1' -or $manifest.product_version -ne '0.1.0' -or
    $manifest.python -ne '3.13' -or $manifest.platform -ne 'win_amd64' -or
    $manifest.wheel -ne 'packages/pacttrail-0.1.0-py3-none-any.whl' -or
    $manifest.requirements -ne 'dependencies/production-requirements.txt' -or
    @($manifest.files).Count -eq 0 -or @($manifest.files).Count -gt 2048) { throw 'Unsupported candidate manifest.' }
$seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($entry in $manifest.files) {
    if ($entry.path -isnot [string] -or -not $seen.Add($entry.path) -or
        $entry.sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid candidate file entry.' }
    $filePath = Get-BundleFile $entry.path
    if ((Get-Item -LiteralPath $filePath).Length -ne $entry.bytes -or
        (Get-Sha256 $filePath) -ne $entry.sha256) {
        throw "Candidate file mismatch: $($entry.path)"
    }
}
if (-not $seen.Contains($manifest.wheel) -or -not $seen.Contains($manifest.requirements) -or
    -not $seen.Contains('scripts/install-candidate.ps1')) { throw 'Candidate install inputs are missing.' }
$wheelhousePath = Join-Path $rootPath 'wheelhouse'
$dependencyWheels = @(Get-ChildItem -LiteralPath $wheelhousePath -File -Force)
if ($dependencyWheels.Count -eq 0) { throw 'Dependency wheelhouse is empty.' }
foreach ($dependencyWheel in $dependencyWheels) {
    if ($dependencyWheel.Extension -ne '.whl' -or -not $seen.Contains('wheelhouse/' + $dependencyWheel.Name)) {
        throw 'Unlisted dependency wheelhouse file.'
    }
}

& $Python -I -c "import sys,sysconfig,struct; assert sys.platform == 'win32' and sys.implementation.name == 'cpython' and sys.version_info[:2] == (3,13) and struct.calcsize('P') == 8 and not sysconfig.get_config_var('Py_GIL_DISABLED'), 'Windows x64 CPython 3.13 required'"
if ($LASTEXITCODE -ne 0) { throw 'Use an existing Windows x64 CPython 3.13 interpreter.' }
& $Python -I -m venv $destinationPath
if ($LASTEXITCODE -ne 0) { throw 'venv creation failed; preserve the new directory for inspection.' }
$venvPython = Join-Path $destinationPath 'Scripts/python.exe'
$priorPipConfig = [Environment]::GetEnvironmentVariable('PIP_CONFIG_FILE', 'Process')
try {
    # pip documents os.devnull as disabling all global/user/site configuration.
    $env:PIP_CONFIG_FILE = 'nul'
    & $venvPython -I -m pip --isolated --disable-pip-version-check --no-input --no-cache-dir install --no-index --find-links $wheelhousePath --require-hashes --only-binary :all: -r (Join-Path $rootPath $manifest.requirements)
    if ($LASTEXITCODE -ne 0) { throw 'Offline dependency installation failed; preserve the new venv.' }
    & $venvPython -I -m pip --isolated --disable-pip-version-check --no-input --no-cache-dir install --no-index --no-deps (Join-Path $rootPath $manifest.wheel)
    if ($LASTEXITCODE -ne 0) { throw 'Candidate installation failed; preserve the new venv.' }
    & $venvPython -I -m pip --isolated --disable-pip-version-check check
    if ($LASTEXITCODE -ne 0) { throw 'Installed dependency check failed.' }
}
finally {
    [Environment]::SetEnvironmentVariable('PIP_CONFIG_FILE', $priorPipConfig, 'Process')
}
& $venvPython -I -c "import pathlib,sys,pacttrail; assert pacttrail.__version__ == '0.1.0'; assert pathlib.Path(pacttrail.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()); print(pacttrail.__file__)"
if ($LASTEXITCODE -ne 0) { throw 'Installed package identity check failed.' }
$entrypoint = Join-Path $destinationPath 'Scripts/pacttrail.exe'
& $entrypoint agent --help
if ($LASTEXITCODE -ne 0) { throw 'Installed agent entrypoint check failed.' }
Write-Output "INSTALL_OK candidate=$($manifest.candidate_id)"
Write-Output "Entrypoint: $entrypoint"
Write-Output 'No server/browser was started. Choose an explicit workspace when starting agent.'
