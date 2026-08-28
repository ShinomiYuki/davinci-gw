[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\DaVinciGW"),
    [string]$CodexHome = (Join-Path $env:USERPROFILE ".codex"),
    [bool]$RemoveCodexConfig = $true
)

$ErrorActionPreference = "Stop"
function Resolve-SafeLocalDirectory([string]$Value, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Value)) { throw "$Label 不能为空" }
    $full = [IO.Path]::GetFullPath($Value)
    if ($full.StartsWith("\\")) { throw "$Label 必须是本地路径，禁止 UNC/设备路径" }
    $root = [IO.Path]::GetPathRoot($full)
    if ($full.TrimEnd('\') -eq $root.TrimEnd('\')) { throw "$Label 不得是文件系统根目录" }
    if ($root) {
        $drive = [IO.DriveInfo]::new($root)
        if ($drive.DriveType -eq [IO.DriveType]::Network) { throw "$Label 不得位于网络驱动器" }
    }
    return $full
}

$installDirectory = Resolve-SafeLocalDirectory $InstallRoot "InstallRoot"
$codexDirectory = Resolve-SafeLocalDirectory $CodexHome "CodexHome"
$targetExe = Join-Path $installDirectory "davinci-gw-mcp.exe"
$manifestPath = Join-Path $installDirectory "install-manifest.json"
$configPath = Join-Path $codexDirectory "config.toml"
$markerBegin = "# BEGIN DAVINCI_GW_MCP MANAGED BLOCK"
$markerEnd = "# END DAVINCI_GW_MCP MANAGED BLOCK"
$configChanged = $false

$manifest = $null
if (Test-Path -LiteralPath $targetExe) {
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "安装清单缺失；为避免删除非本产品文件，拒绝移除 EXE"
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    } catch {
        throw "安装清单无法解析；为避免误删，拒绝卸载"
    }
    $manifestExecutable = [IO.Path]::GetFullPath([string]$manifest.executable)
    if ($manifestExecutable -ne $targetExe) { throw "安装清单中的 EXE 路径与卸载目标不一致" }
    $installedHash = (Get-FileHash -LiteralPath $targetExe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($installedHash -ne ([string]$manifest.sha256).ToLowerInvariant()) {
        throw "已安装 EXE 与安装清单 SHA-256 不一致；为避免误删，拒绝卸载"
    }
}

if ($RemoveCodexConfig -and (Test-Path -LiteralPath $configPath)) {
    $existing = Get-Content -LiteralPath $configPath -Raw
    $beginCount = ([regex]::Matches($existing, [regex]::Escape($markerBegin))).Count
    $endCount = ([regex]::Matches($existing, [regex]::Escape($markerEnd))).Count
    if ($beginCount -ne $endCount -or $beginCount -gt 1) {
        throw "Codex 配置中的 davinci_gateway 托管标记残缺或重复；为避免误删，拒绝卸载"
    }
    $pattern = "(?ms)^" + [regex]::Escape($markerBegin) + ".*?^" + [regex]::Escape($markerEnd) + "\r?\n?"
    $updated = [regex]::Replace($existing, $pattern, "").TrimEnd()
    if ($updated -ne $existing.TrimEnd()) {
        $backup = "$configPath.$([DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff')).bak"
        Copy-Item -LiteralPath $configPath -Destination $backup
        if ($updated) {
            Set-Content -LiteralPath $configPath -Value "$updated`r`n" -Encoding utf8NoBOM -NoNewline
        } else {
            Set-Content -LiteralPath $configPath -Value "" -Encoding utf8NoBOM -NoNewline
        }
        $configChanged = $true
    }
}

if (Test-Path -LiteralPath $targetExe) { Remove-Item -LiteralPath $targetExe -Force }
if (Test-Path -LiteralPath $manifestPath) { Remove-Item -LiteralPath $manifestPath -Force }
[pscustomobject]@{ RemovedExecutable = -not (Test-Path -LiteralPath $targetExe); CodexConfig = $configPath; ConfigChanged = $configChanged }
