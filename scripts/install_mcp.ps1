[CmdletBinding()]
param(
    [string]$PackageRoot = $PSScriptRoot,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\DaVinciGW"),
    [string]$CodexHome = (Join-Path $env:USERPROFILE ".codex"),
    [bool]$ConfigureCodex = $true,
    [ValidateSet('Codex', 'DeepSeekHarness', 'None')][string]$Client = 'Codex',
    [string]$DshHome,
    [switch]$Check
)

$ErrorActionPreference = "Stop"
function Resolve-SafeLocalDirectory([string]$Value, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Value)) { throw "$Label 不能为空" }
    $full = [IO.Path]::GetFullPath($Value)
    if ($full.StartsWith("\\")) { throw "$Label 必须是本地路径，禁止 UNC/设备路径" }
    $root = [IO.Path]::GetPathRoot($full)
    if ($full.TrimEnd('\') -eq $root.TrimEnd('\')) { throw "$Label 不得是文件系统根目录" }
    if ($root -and [IO.DriveInfo]::new($root).DriveType -eq [IO.DriveType]::Network) {
        throw "$Label 不得位于网络驱动器"
    }
    return $full
}

$package = Resolve-SafeLocalDirectory (Resolve-Path -LiteralPath $PackageRoot).Path "PackageRoot"
$sourceExe = Join-Path $package "davinci-gw-mcp.exe"
$sourceInternal = Join-Path $package "_internal"
$checksumFile = Join-Path $package "SHA256SUMS.txt"
$versionFile = Join-Path $package "VERSION"
if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) { throw "发布包缺少 davinci-gw-mcp.exe" }
if (-not (Test-Path -LiteralPath $sourceInternal -PathType Container)) { throw "发布包缺少 onedir _internal 目录" }
if (-not (Test-Path -LiteralPath $checksumFile -PathType Leaf)) { throw "发布包缺少 SHA256SUMS.txt" }
if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) { throw "发布包缺少 VERSION" }
$version = (Get-Content -LiteralPath $versionFile -Raw).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "VERSION 格式无效" }

$checksumRecords = @()
foreach ($line in Get-Content -LiteralPath $checksumFile) {
    if ($line -notmatch '^([0-9a-fA-F]{64})\s{2}(.+)$') { throw "校验和记录格式无效：$line" }
    $relative = $Matches[2].Replace('/', '\')
    $candidate = [IO.Path]::GetFullPath((Join-Path $package $relative))
    if (-not $candidate.StartsWith($package.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "校验和记录越出发布包：$relative"
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "发布包缺少校验文件：$relative" }
    $expected = $Matches[1].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "SHA-256 校验失败：$relative" }
    $checksumRecords += [pscustomobject]@{ relative = $relative; sha256 = $actual }
}
$actualPackageFiles = Get-ChildItem -LiteralPath $package -Recurse -File |
    Where-Object { $_.FullName -ne $checksumFile } |
    ForEach-Object { [IO.Path]::GetRelativePath($package, $_.FullName) } |
    Sort-Object
$recordedPackageFiles = $checksumRecords.relative | Sort-Object
if (Compare-Object $actualPackageFiles $recordedPackageFiles) {
    throw "发布包文件与 SHA256SUMS.txt 清单不一致"
}

$installDirectory = Resolve-SafeLocalDirectory $InstallRoot "InstallRoot"
$codexDirectory = Resolve-SafeLocalDirectory $CodexHome "CodexHome"
$versionsRoot = Join-Path $installDirectory "versions"
$runtimeDirectory = Join-Path $versionsRoot $version
$targetExe = Join-Path $runtimeDirectory "davinci-gw-mcp.exe"
$manifestPath = Join-Path $installDirectory "install-manifest.json"
$configPath = Join-Path $codexDirectory "config.toml"
$managedRuntimes = @()
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    try { $previousManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json } catch {
        throw "已有安装清单无法解析，拒绝覆盖"
    }
    if ($previousManifest.runtimes) {
        $managedRuntimes = @($previousManifest.runtimes)
    } elseif ($previousManifest.runtime_directory) {
        # 兼容 1.0 开发阶段写出的单运行时清单，升级后统一转成 runtimes 数组。
        $managedRuntimes = @([pscustomobject]@{
            version = [string]$previousManifest.version
            runtime_directory = [string]$previousManifest.runtime_directory
            executable = [string]$previousManifest.executable
            files = @($previousManifest.files)
        })
    } else {
        throw "已有安装清单不包含受管运行时，拒绝覆盖"
    }
    foreach ($managed in $managedRuntimes) {
        $managedPath = [IO.Path]::GetFullPath([string]$managed.runtime_directory)
        if (-not $managedPath.StartsWith($versionsRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "已有安装清单的运行目录越出 versions，拒绝升级"
        }
    }
}
$effectiveClient = if ($Client -eq 'Codex' -and -not $ConfigureCodex) { 'None' } else { $Client }
$configureScript = Join-Path $PSScriptRoot 'configure_mcp_client.ps1'
if ($effectiveClient -ne 'None') {
    & $configureScript -Client $effectiveClient -Command $targetExe -CodexHome $CodexHome -DshHome $DshHome -Check | Out-Null
}
if ($Check) {
    [pscustomobject]@{ PackageValidated = $true; Version = $version; Installed = $targetExe; Client = $effectiveClient }
    return
}

New-Item -ItemType Directory -Path $versionsRoot -Force | Out-Null
if (Test-Path -LiteralPath $runtimeDirectory) {
    $expectedExe = ($checksumRecords | Where-Object relative -eq "davinci-gw-mcp.exe").sha256
    if (-not (Test-Path -LiteralPath $targetExe -PathType Leaf) -or
        (Get-FileHash -LiteralPath $targetExe -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedExe) {
        throw "同版本安装目录已存在但内容不同，拒绝覆盖"
    }
} else {
    $temporary = Join-Path $versionsRoot ".installing-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $temporary | Out-Null
    try {
        Copy-Item -LiteralPath $sourceExe -Destination $temporary
        Copy-Item -LiteralPath $sourceInternal -Destination $temporary -Recurse
        Move-Item -LiteralPath $temporary -Destination $runtimeDirectory
    } catch {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
        throw
    }
}

foreach ($file in ($checksumRecords | Where-Object { $_.relative -eq "davinci-gw-mcp.exe" -or $_.relative.StartsWith("_internal\") })) {
    $installed = [IO.Path]::GetFullPath((Join-Path $runtimeDirectory $file.relative))
    if (-not $installed.StartsWith($runtimeDirectory.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $installed -PathType Leaf) -or
        (Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) {
        throw "安装后的运行文件校验失败：$($file.relative)"
    }
}

$configChanged = $false
if ($effectiveClient -ne 'None') {
    $clientConfig = & $configureScript -Client $effectiveClient -Command $targetExe -CodexHome $CodexHome -DshHome $DshHome
    $configPath = $clientConfig.ConfigPath
    $configChanged = $clientConfig.ConfigChanged
}

$runtimeFiles = $checksumRecords | Where-Object {
    $_.relative -eq "davinci-gw-mcp.exe" -or $_.relative.StartsWith("_internal\")
}
$currentRuntime = [pscustomobject]@{
    version = $version
    runtime_directory = $runtimeDirectory
    executable = $targetExe
    files = @($runtimeFiles)
}
$retainedRuntimes = @($managedRuntimes | Where-Object {
    [IO.Path]::GetFullPath([string]$_.runtime_directory) -ne $runtimeDirectory
})
[ordered]@{
    current_version = $version
    runtimes = @($retainedRuntimes) + @($currentRuntime)
    client = $effectiveClient
    codex_config = if ($effectiveClient -eq 'Codex') { $configPath } else { $null }
    dsh_config = if ($effectiveClient -eq 'DeepSeekHarness') { $configPath } else { $null }
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM

[pscustomobject]@{ Installed = $targetExe; Version = $version; Client = $effectiveClient; ClientConfig = $configPath; CodexConfig = $configPath; ConfigChanged = $configChanged }
