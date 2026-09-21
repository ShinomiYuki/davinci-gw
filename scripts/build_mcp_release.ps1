[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Version = "1.1.3"
)

$ErrorActionPreference = "Stop"
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version 必须是纯数字的 major.minor.patch" }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$versionSource = Get-Content -LiteralPath (Join-Path $projectRoot "src\davinci_gw\mcp\version.py") -Raw
if ($versionSource -notmatch 'MCP_VERSION\s*=\s*"([^\"]+)"' -or $Matches[1] -ne $Version) {
    throw "Version 与源码 MCP_VERSION 不一致"
}
$buildRoot = Join-Path $projectRoot "build\mcp"
$distRoot = Join-Path $projectRoot "dist\mcp"
$releaseRoot = Join-Path $projectRoot "release"
$stageRoot = Join-Path $releaseRoot "davinci-gw-mcp-$Version-win-x64"
$archivePath = Join-Path $releaseRoot "davinci-gw-mcp-$Version-win-x64.zip"
$archiveHashPath = "$archivePath.sha256"
$generatedRoot = Join-Path $projectRoot "build\mcp-metadata"
$buildInfoPath = Join-Path $generatedRoot "BUILD_INFO.json"
$pythonCommand = Get-Command $Python -ErrorAction Stop
$pythonRoot = Split-Path $pythonCommand.Source -Parent
$condaLibraryBin = Join-Path $pythonRoot "Library\bin"
if (Test-Path -LiteralPath $condaLibraryBin -PathType Container) {
    # Conda 将 lxml 的 libxml2/libxslt 等依赖放在此处；PyInstaller 需在分析阶段找到它们。
    $env:PATH = "$condaLibraryBin;$env:PATH"
}

foreach ($path in @($buildRoot, $distRoot, $stageRoot, $generatedRoot)) {
    $resolvedTarget = [IO.Path]::GetFullPath($path)
    if (-not $resolvedTarget.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "构建清理路径超出工程目录：$resolvedTarget"
    }
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}
New-Item -ItemType Directory -Path $buildRoot, $distRoot, $stageRoot, $generatedRoot -Force | Out-Null

$buildCommit = (& git -C $projectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $buildCommit -notmatch '^[0-9a-fA-F]{40}$') {
    throw "无法取得有效 Git 构建提交"
}
$buildDirty = [bool]((& git -C $projectRoot status --porcelain) -join "")
[ordered]@{
    version = $Version
    commit = $buildCommit
    dirty = $buildDirty
    built_at = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json | Set-Content -LiteralPath $buildInfoPath -Encoding utf8NoBOM
$env:DAVINCI_GW_BUILD_INFO = $buildInfoPath

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --workpath $buildRoot `
    --distpath $distRoot `
    (Join-Path $projectRoot "packaging\davinci-gw-mcp.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败：$LASTEXITCODE" }

$runtimeSource = Join-Path $distRoot "davinci-gw-mcp"
$exeSource = Join-Path $runtimeSource "davinci-gw-mcp.exe"
if (-not (Test-Path -LiteralPath $exeSource -PathType Leaf)) { throw "构建产物不存在：$exeSource" }
if (-not (Test-Path -LiteralPath (Join-Path $runtimeSource "_internal") -PathType Container)) {
    throw "构建产物缺少 onedir _internal 目录"
}

Get-ChildItem -LiteralPath $runtimeSource | Copy-Item -Destination $stageRoot -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot "scripts\install_mcp.ps1") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $projectRoot "scripts\uninstall_mcp.ps1") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $projectRoot "packaging\codex-config.example.toml") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination (Join-Path $stageRoot "LICENSE.txt")
Copy-Item -LiteralPath (Join-Path $projectRoot "docs\MCP本地安装与使用.md") -Destination (Join-Path $stageRoot "README.md")
Set-Content -LiteralPath (Join-Path $stageRoot "VERSION") -Value $Version -Encoding utf8NoBOM

$checksumLines = Get-ChildItem -LiteralPath $stageRoot -Recurse -File |
    Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
    Sort-Object FullName |
    ForEach-Object {
        $relative = [IO.Path]::GetRelativePath($stageRoot, $_.FullName).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $relative"
    }
Set-Content -LiteralPath (Join-Path $stageRoot "SHA256SUMS.txt") -Value $checksumLines -Encoding ascii
$exeHash = (Get-FileHash -LiteralPath (Join-Path $stageRoot "davinci-gw-mcp.exe") -Algorithm SHA256).Hash.ToLowerInvariant()

if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
Compress-Archive -Path (Join-Path $stageRoot "*") -DestinationPath $archivePath -CompressionLevel Optimal
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $archiveHashPath -Value "$archiveHash  $(Split-Path $archivePath -Leaf)" -Encoding ascii

[pscustomobject]@{
    Version = $Version
    Executable = $exeSource
    ExecutableSha256 = $exeHash
    BuildCommit = $buildCommit
    Archive = $archivePath
    ArchiveSha256 = $archiveHash
}
