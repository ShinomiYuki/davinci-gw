[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Version = "0.2.5"
)

$ErrorActionPreference = "Stop"
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version 必须是纯数字的 major.minor.patch" }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$buildRoot = Join-Path $projectRoot "build\mcp"
$distRoot = Join-Path $projectRoot "dist\mcp"
$releaseRoot = Join-Path $projectRoot "release"
$stageRoot = Join-Path $releaseRoot "davinci-gw-mcp-$Version-win-x64"
$archivePath = Join-Path $releaseRoot "davinci-gw-mcp-$Version-win-x64.zip"
$archiveHashPath = "$archivePath.sha256"
$pythonCommand = Get-Command $Python -ErrorAction Stop
$pythonRoot = Split-Path $pythonCommand.Source -Parent
$condaLibraryBin = Join-Path $pythonRoot "Library\bin"
if (Test-Path -LiteralPath $condaLibraryBin -PathType Container) {
    # Conda 将 lxml 的 libxml2/libxslt 等依赖放在此处；PyInstaller 需在分析阶段找到它们。
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
    (Join-Path $projectRoot "packaging\davinci-gw-mcp.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败：$LASTEXITCODE" }

$exeSource = Join-Path $distRoot "davinci-gw-mcp.exe"
if (-not (Test-Path -LiteralPath $exeSource -PathType Leaf)) { throw "构建产物不存在：$exeSource" }

Copy-Item -LiteralPath $exeSource -Destination (Join-Path $stageRoot "davinci-gw-mcp.exe")
Copy-Item -LiteralPath (Join-Path $projectRoot "scripts\install_mcp.ps1") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $projectRoot "scripts\uninstall_mcp.ps1") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $projectRoot "packaging\codex-config.example.toml") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination (Join-Path $stageRoot "LICENSE.txt")
Copy-Item -LiteralPath (Join-Path $projectRoot "docs\MCP本地安装与使用.md") -Destination (Join-Path $stageRoot "README.md")
Set-Content -LiteralPath (Join-Path $stageRoot "VERSION") -Value $Version -Encoding utf8NoBOM

$exeHash = (Get-FileHash -LiteralPath (Join-Path $stageRoot "davinci-gw-mcp.exe") -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $stageRoot "SHA256SUMS.txt") `
    -Value "$exeHash  davinci-gw-mcp.exe" -Encoding ascii

if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
Compress-Archive -Path (Join-Path $stageRoot "*") -DestinationPath $archivePath -CompressionLevel Optimal
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $archiveHashPath -Value "$archiveHash  $(Split-Path $archivePath -Leaf)" -Encoding ascii

[pscustomobject]@{
    Version = $Version
    Executable = $exeSource
    ExecutableSha256 = $exeHash
    Archive = $archivePath
    ArchiveSha256 = $archiveHash
}
