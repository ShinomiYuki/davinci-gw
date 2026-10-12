[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\DaVinciGW"),
    [string]$CodexHome = (Join-Path $env:USERPROFILE ".codex"),
    [bool]$RemoveCodexConfig = $true,
    [ValidateSet('Codex', 'DeepSeekHarness')][string]$Client = 'Codex',
    [string]$DshHome
)

$ErrorActionPreference = "Stop"
function Resolve-SafeLocalDirectory([string]$Value, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Value)) { throw "$Label 不能为空" }
    $full = [IO.Path]::GetFullPath($Value)
    if ($full.StartsWith("\\")) { throw "$Label 必须是本地路径，禁止 UNC/设备路径" }
    $root = [IO.Path]::GetPathRoot($full)
    if ($full.TrimEnd('\') -eq $root.TrimEnd('\')) { throw "$Label 不得是文件系统根目录" }
    return $full
}

$installDirectory = Resolve-SafeLocalDirectory $InstallRoot "InstallRoot"
$codexDirectory = Resolve-SafeLocalDirectory $CodexHome "CodexHome"
$manifestPath = Join-Path $installDirectory "install-manifest.json"
$versionsRoot = [IO.Path]::GetFullPath((Join-Path $installDirectory "versions"))
$runtimeRecords = @()
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    try { $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json } catch {
        throw "安装清单无法解析"
    }
    if ($manifest.runtimes) {
        $runtimeRecords = @($manifest.runtimes)
    } elseif ($manifest.runtime_directory) {
        $runtimeRecords = @([pscustomobject]@{
            runtime_directory = [string]$manifest.runtime_directory
            executable = [string]$manifest.executable
            files = @($manifest.files)
        })
    } else {
        throw "安装清单不包含受管运行时"
    }
} elseif ((Test-Path -LiteralPath $versionsRoot) -and @(Get-ChildItem -LiteralPath $versionsRoot -Force).Count) {
    throw "安装清单缺失但 versions 中仍有文件，拒绝猜测删除"
}

$runtimeDirectories = @()
foreach ($runtime in $runtimeRecords) {
    $runtimeDirectory = [IO.Path]::GetFullPath([string]$runtime.runtime_directory)
    if (-not $runtimeDirectory.StartsWith($versionsRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "安装清单的运行目录越出 versions，拒绝卸载"
    }
    $targetExe = [IO.Path]::GetFullPath([string]$runtime.executable)
    if (-not $targetExe.StartsWith($runtimeDirectory.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "安装清单中的 EXE 不属于运行目录"
    }
    foreach ($file in $runtime.files) {
        $candidate = [IO.Path]::GetFullPath((Join-Path $runtimeDirectory ([string]$file.relative)))
        if (-not $candidate.StartsWith($runtimeDirectory.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "安装清单文件越出运行目录"
        }
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf) -or
            (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant() -ne ([string]$file.sha256).ToLowerInvariant()) {
            throw "已安装文件与清单不一致，拒绝卸载：$($file.relative)"
        }
    }
    $runtimeDirectories += $runtimeDirectory
}

$configPath = Join-Path $codexDirectory "config.toml"
$configChanged = $false
if ($Client -eq 'DeepSeekHarness' -or $RemoveCodexConfig) {
    $clientConfig = & (Join-Path $PSScriptRoot 'configure_mcp_client.ps1') -Client $Client -CodexHome $CodexHome -DshHome $DshHome -Remove
    $configPath = $clientConfig.ConfigPath
    $configChanged = $clientConfig.ConfigChanged
}

foreach ($runtimeDirectory in $runtimeDirectories) {
    if (Test-Path -LiteralPath $runtimeDirectory) { Remove-Item -LiteralPath $runtimeDirectory -Recurse -Force }
}
if (Test-Path -LiteralPath $manifestPath) { Remove-Item -LiteralPath $manifestPath -Force }
[pscustomobject]@{
    RemovedRuntimeCount = $runtimeDirectories.Count
    CodexConfig = $configPath
    ConfigChanged = $configChanged
}
