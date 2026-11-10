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
$manifestClients = @()
$manifestConfigPaths = @{}
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
    # 清单记录安装器写过哪些客户端的配置块：既可能是历史累计的 clients，
    # 也可能只有单个 client（早期清单）。卸载必须按记录逐个清理，
    # 否则装到 DeepSeekHarness 后用默认参数卸载会留下指向已删除 EXE 的托管块。
    if ($manifest.PSObject.Properties.Name -contains 'clients' -and $manifest.clients) {
        $manifestClients = @($manifest.clients | ForEach-Object { [string]$_ })
    } elseif ($manifest.PSObject.Properties.Name -contains 'client' -and $manifest.client) {
        $manifestClients = @([string]$manifest.client)
    }
    foreach ($pair in @(@('codex_config', 'Codex'), @('dsh_config', 'DeepSeekHarness'))) {
        $property = $pair[0]
        $clientName = $pair[1]
        if ($manifest.PSObject.Properties.Name -contains $property -and $manifest.$property) {
            $manifestConfigPaths[$clientName] = [string]$manifest.$property
        }
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

# 清理客户端配置块：按安装清单记录的客户端逐个处理，并叠加用户显式指定的客户端。
# 某个客户端的托管块残缺或配置被手工改坏时，不能因此中断卸载——
# 运行时目录必须照样删掉，否则会留下指向已删除 EXE 的悬空配置。
$configPaths = @()
$configChanged = $false
$configWarnings = @()
if ($RemoveCodexConfig) {
    $clientsToClean = @($manifestClients)
    if ($PSBoundParameters.ContainsKey('Client')) { $clientsToClean += $Client }
    $clientsToClean = @($clientsToClean | Where-Object { $_ -and $_ -ne 'None' } | Select-Object -Unique)
    foreach ($clientName in $clientsToClean) {
        if ($clientName -notin @('Codex', 'DeepSeekHarness')) {
            $configWarnings += "安装清单记录的客户端 $clientName 不受支持，已跳过配置清理"
            continue
        }
        $clientHome = $CodexHome
        if ($clientName -eq 'DeepSeekHarness') {
            $clientHome = $DshHome
            if (-not $clientHome -and $manifestConfigPaths.ContainsKey('DeepSeekHarness')) {
                # 清单记录了当时写入的配置文件位置，用它反推 DSH 配置目录，
                # 避免环境变量或默认路径变化后清理到别的文件。
                $clientHome = Split-Path -Parent $manifestConfigPaths['DeepSeekHarness']
            }
        }
        try {
            $clientConfig = & (Join-Path $PSScriptRoot 'configure_mcp_client.ps1') -Client $clientName -CodexHome $CodexHome -DshHome $clientHome -Remove
            $configPaths += [string]$clientConfig.ConfigPath
            if ($clientConfig.ConfigChanged) { $configChanged = $true }
        } catch {
            $configWarnings += "清理 $clientName 配置块失败：$($_.Exception.Message)"
        }
    }
}

foreach ($runtimeDirectory in $runtimeDirectories) {
    if (Test-Path -LiteralPath $runtimeDirectory) { Remove-Item -LiteralPath $runtimeDirectory -Recurse -Force }
}
if (Test-Path -LiteralPath $manifestPath) { Remove-Item -LiteralPath $manifestPath -Force }
foreach ($warning in $configWarnings) { Write-Warning $warning }
[pscustomobject]@{
    RemovedRuntimeCount = $runtimeDirectories.Count
    CodexConfig = if ($configPaths.Count) { $configPaths -join '; ' } else { $null }
    ConfigChanged = $configChanged
    ConfigWarnings = @($configWarnings)
}
