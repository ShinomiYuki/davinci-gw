[CmdletBinding()]
param(
    [string]$PackageRoot = $PSScriptRoot,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\DaVinciGW"),
    [string]$CodexHome = (Join-Path $env:USERPROFILE ".codex"),
    [bool]$ConfigureCodex = $true
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

$package = Resolve-SafeLocalDirectory (Resolve-Path -LiteralPath $PackageRoot).Path "PackageRoot"
$sourceExe = Join-Path $package "davinci-gw-mcp.exe"
$checksumFile = Join-Path $package "SHA256SUMS.txt"
$versionFile = Join-Path $package "VERSION"
if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) { throw "发布包缺少 davinci-gw-mcp.exe" }
if (-not (Test-Path -LiteralPath $checksumFile -PathType Leaf)) { throw "发布包缺少 SHA256SUMS.txt" }
if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) { throw "发布包缺少 VERSION" }

$checksumLine = (Get-Content -LiteralPath $checksumFile | Where-Object { $_ -match "davinci-gw-mcp\.exe$" } | Select-Object -First 1)
if ($checksumLine -notmatch '^([0-9a-fA-F]{64})\s{2}davinci-gw-mcp\.exe$') { throw "EXE 校验和记录格式无效" }
$expectedHash = $Matches[1].ToLowerInvariant()
$actualHash = (Get-FileHash -LiteralPath $sourceExe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw "EXE SHA-256 校验失败，拒绝安装" }

$installDirectory = Resolve-SafeLocalDirectory $InstallRoot "InstallRoot"
$codexDirectory = Resolve-SafeLocalDirectory $CodexHome "CodexHome"
$configPath = Join-Path $codexDirectory "config.toml"
$markerBegin = "# BEGIN DAVINCI_GW_MCP MANAGED BLOCK"
$markerEnd = "# END DAVINCI_GW_MCP MANAGED BLOCK"
$managedPattern = "(?ms)^" + [regex]::Escape($markerBegin) + ".*?^" + [regex]::Escape($markerEnd) + "\r?\n?"
$existing = ""
if ($ConfigureCodex -and (Test-Path -LiteralPath $configPath)) {
    $existing = Get-Content -LiteralPath $configPath -Raw
    $beginCount = ([regex]::Matches($existing, [regex]::Escape($markerBegin))).Count
    $endCount = ([regex]::Matches($existing, [regex]::Escape($markerEnd))).Count
    if ($beginCount -ne $endCount -or $beginCount -gt 1) {
        throw "Codex 配置中的 davinci_gateway 托管标记残缺或重复；为避免覆盖，请先手工处理"
    }
    $withoutManagedForCheck = [regex]::Replace($existing, $managedPattern, "")
    if ($withoutManagedForCheck -match 'davinci_gateway') {
        throw "Codex 配置已有非本安装器管理的 davinci_gateway 服务；为避免覆盖，请先手工处理"
    }
}

New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
$targetExe = Join-Path $installDirectory "davinci-gw-mcp.exe"
if (Test-Path -LiteralPath $targetExe) {
    $installedHash = (Get-FileHash -LiteralPath $targetExe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($installedHash -ne $actualHash) {
        $backupRoot = Join-Path $installDirectory "backups"
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
        $backupName = "davinci-gw-mcp.exe.$([DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff')).bak"
        Copy-Item -LiteralPath $targetExe -Destination (Join-Path $backupRoot $backupName)
    }
}
$temporaryExe = Join-Path $installDirectory ".davinci-gw-mcp.installing.exe"
Copy-Item -LiteralPath $sourceExe -Destination $temporaryExe -Force
$temporaryHash = (Get-FileHash -LiteralPath $temporaryExe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($temporaryHash -ne $expectedHash) {
    Remove-Item -LiteralPath $temporaryExe -Force
    throw "复制后的 EXE SHA-256 校验失败，拒绝安装"
}
Move-Item -LiteralPath $temporaryExe -Destination $targetExe -Force

$configChanged = $false
if ($ConfigureCodex) {
    New-Item -ItemType Directory -Path (Split-Path $configPath -Parent) -Force | Out-Null
    $commandToml = ConvertTo-Json -Compress $targetExe
    $block = @"
$markerBegin
[mcp_servers.davinci_gateway]
command = $commandToml
startup_timeout_sec = 30
tool_timeout_sec = 3600
enabled = true
required = false
default_tools_approval_mode = "writes"

[mcp_servers.davinci_gateway.tools.generate_gateway_arxml]
approval_mode = "prompt"
$markerEnd
"@
    $withoutManaged = [regex]::Replace($existing, $managedPattern, "").TrimEnd()
    $updated = if ($withoutManaged) { "$withoutManaged`r`n`r`n$block`r`n" } else { "$block`r`n" }
    if ($updated -ne $existing) {
        if (Test-Path -LiteralPath $configPath) {
            $backup = "$configPath.$([DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff')).bak"
            Copy-Item -LiteralPath $configPath -Destination $backup
        }
        Set-Content -LiteralPath $configPath -Value $updated -Encoding utf8NoBOM -NoNewline
        $configChanged = $true
    }
}

$manifest = [ordered]@{
    version = (Get-Content -LiteralPath $versionFile -Raw).Trim()
    executable = $targetExe
    sha256 = $actualHash
    codex_config = if ($ConfigureCodex) { $configPath } else { $null }
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $installDirectory "install-manifest.json") -Encoding utf8NoBOM
[pscustomobject]@{ Installed = $targetExe; Sha256 = $actualHash; CodexConfig = $configPath; ConfigChanged = $configChanged }
