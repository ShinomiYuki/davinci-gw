[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Version = "0.3.3"
)

$ErrorActionPreference = "Stop"
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version 必须是纯数字的 major.minor.patch" }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$buildRoot = Join-Path $projectRoot "build\gui"
$distRoot = Join-Path $projectRoot "dist\gui"
$releaseRoot = Join-Path $projectRoot "release"
$stageRoot = Join-Path $releaseRoot "davinci-gw-gui-$Version-win-x64"
$archivePath = Join-Path $releaseRoot "davinci-gw-gui-$Version-win-x64.zip"
$archiveHashPath = "$archivePath.sha256"
$pythonCommand = Get-Command $Python -ErrorAction Stop
$pythonRoot = Split-Path $pythonCommand.Source -Parent
$condaLibraryBin = Join-Path $pythonRoot "Library\bin"
if (Test-Path -LiteralPath $condaLibraryBin -PathType Container) {
    $env:PATH = "$condaLibraryBin;$env:PATH"
}

foreach ($path in @($buildRoot, $distRoot, $stageRoot)) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}
New-Item -ItemType Directory -Path $buildRoot, $distRoot, $stageRoot -Force | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --workpath $buildRoot `
    --distpath $distRoot `
    (Join-Path $projectRoot "packaging\davinci-gw-gui.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败：$LASTEXITCODE" }

$bundleSource = Join-Path $distRoot "davinci-gw-gui"
if (-not (Test-Path -LiteralPath (Join-Path $bundleSource "davinci-gw-gui.exe") -PathType Leaf)) {
    throw "构建产物不存在：$bundleSource"
}
Copy-Item -Path (Join-Path $bundleSource "*") -Destination $stageRoot -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination (Join-Path $stageRoot "LICENSE.txt")
Copy-Item -LiteralPath (Join-Path $projectRoot "THIRD_PARTY_NOTICES.md") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $projectRoot "docs\GUI.md") -Destination (Join-Path $stageRoot "README.md")

$licenseRoot = Join-Path $stageRoot "third-party-licenses"
New-Item -ItemType Directory -Path $licenseRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "licenses\LGPL-3.0.txt") -Destination $licenseRoot
Copy-Item -LiteralPath (Join-Path $projectRoot "licenses\lxml-LICENSES.txt") -Destination $licenseRoot
Copy-Item -LiteralPath (Join-Path $projectRoot "licenses\OpenSSL-LICENSE.txt") -Destination $licenseRoot
$pythonLicense = @("LICENSE_PYTHON.txt", "LICENSE.txt") |
    ForEach-Object { Join-Path $pythonRoot $_ } |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if (-not $pythonLicense) { throw "Python runtime license was not found." }
Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $licenseRoot "Python-LICENSE.txt")
$pyInstallerLicense = & $Python -c "from importlib.metadata import distribution; d=distribution('pyinstaller'); print(next(d.locate_file(f) for f in d.files if f.name == 'COPYING.txt'))"
$openpyxlLicense = & $Python -c "from importlib.metadata import distribution; d=distribution('openpyxl'); print(next(d.locate_file(f) for f in d.files if f.name == 'LICENCE.rst'))"
$etXmlfileLicense = & $Python -c "from importlib.metadata import distribution; d=distribution('et_xmlfile'); print(next(d.locate_file(f) for f in d.files if f.name == 'LICENCE.rst'))"
Copy-Item -LiteralPath $pyInstallerLicense `
    -Destination (Join-Path $licenseRoot "PyInstaller-COPYING.txt")
Copy-Item -LiteralPath $openpyxlLicense `
    -Destination (Join-Path $licenseRoot "openpyxl-LICENSE.rst")
Copy-Item -LiteralPath $etXmlfileLicense `
    -Destination (Join-Path $licenseRoot "et_xmlfile-LICENSE.rst")
Set-Content -LiteralPath (Join-Path $stageRoot "VERSION") -Value $Version -Encoding UTF8

$exePath = Join-Path $stageRoot "davinci-gw-gui.exe"
$exeHash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $stageRoot "SHA256SUMS.txt") `
    -Value "$exeHash  davinci-gw-gui.exe" -Encoding ascii

if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
Compress-Archive -Path (Join-Path $stageRoot "*") -DestinationPath $archivePath -CompressionLevel Optimal
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $archiveHashPath -Value "$archiveHash  $(Split-Path $archivePath -Leaf)" -Encoding ascii

[pscustomobject]@{
    Version = $Version
    Executable = $exePath
    ExecutableSha256 = $exeHash
    Archive = $archivePath
    ArchiveSha256 = $archiveHash
}
