[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Codex', 'DeepSeekHarness')][string]$Client,
    [string]$Command,
    [string[]]$Arguments = @(),
    [string]$CodexHome = (Join-Path $env:USERPROFILE '.codex'),
    [string]$DshHome,
    [switch]$Check,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw '请使用 PowerShell 7' }
if ($Client -eq 'Codex') {
    $clientHome = $CodexHome
    $configName = 'config.toml'
} else {
    if (-not $DshHome) {
        $desktopHome = Join-Path $env:APPDATA 'dsh-desktop\harness'
        $DshHome = if ($env:DSH_HOME) { $env:DSH_HOME }
            elseif (Test-Path -LiteralPath $desktopHome -PathType Container) { $desktopHome }
            else { Join-Path $env:USERPROFILE '.dsh' }
    }
    $clientHome = $DshHome
    $configName = 'cordis.patch.yml'
}
$clientHome = [IO.Path]::GetFullPath($clientHome)
if ($clientHome.StartsWith('\\') -or $clientHome -eq [IO.Path]::GetPathRoot($clientHome)) {
    throw '客户端配置目录必须是本地非根目录'
}
$configPath = Join-Path $clientHome $configName
$begin = '# BEGIN DAVINCI_GW_MCP MANAGED BLOCK'
$end = '# END DAVINCI_GW_MCP MANAGED BLOCK'
$pattern = '(?ms)^' + [regex]::Escape($begin) + '.*?^' + [regex]::Escape($end) + '\r?\n?'
$existing = if (Test-Path -LiteralPath $configPath) { Get-Content -LiteralPath $configPath -Raw } else { '' }
if ($null -eq $existing) { $existing = '' }
$begins = ([regex]::Matches($existing, [regex]::Escape($begin))).Count
$ends = ([regex]::Matches($existing, [regex]::Escape($end))).Count
if ($begins -ne $ends -or $begins -gt 1 -or ([regex]::Matches($existing, $pattern)).Count -ne $begins) {
    throw '客户端托管标记残缺或重复，未修改配置'
}
$unmanaged = [regex]::Replace($existing, $pattern, '')
if ($Client -eq 'Codex') { $unmanaged = $unmanaged.TrimEnd() }
if (-not $Remove -and $unmanaged -match 'davinci_gateway|davinci-gateway-mcp') {
    throw '客户端已有非本安装器管理的网关 MCP 配置，未覆盖'
}
if ($Remove) {
    $updated = if ($Client -eq 'DeepSeekHarness') { $unmanaged }
        elseif ($unmanaged) { "$unmanaged`r`n" } else { '' }
} else {
    if (-not $Command -or -not [IO.Path]::IsPathFullyQualified($Command)) { throw 'Command 必须是绝对路径' }
    if (-not $Check -and -not (Test-Path -LiteralPath $Command -PathType Leaf)) { throw 'MCP 启动程序不存在' }
    $commandText = ConvertTo-Json -InputObject $Command -Compress
    $argumentText = ConvertTo-Json -InputObject @($Arguments) -Compress
    if ($Client -eq 'Codex') {
        $block = @"
$begin
[mcp_servers.davinci_gateway]
command = $commandText
args = $argumentText
startup_timeout_sec = 30
tool_timeout_sec = 3600
enabled = true
required = false
default_tools_approval_mode = "writes"

[mcp_servers.davinci_gateway.tools.generate_gateway_arxml]
approval_mode = "prompt"
[mcp_servers.davinci_gateway.tools.start_bug_repair]
approval_mode = "prompt"
[mcp_servers.davinci_gateway.tools.submit_bug_repair]
approval_mode = "prompt"
[mcp_servers.davinci_gateway.tools.cancel_bug_repair]
approval_mode = "prompt"
$end
"@
    } else {
        # DSH 使用补丁列表。已有空列表需要移除，其他用户条目保留原文。
        $contentLines = @($unmanaged -split '\r?\n' | Where-Object { $_.Trim() -and -not $_.TrimStart().StartsWith('#') })
        if ($contentLines.Count -eq 1 -and $contentLines[0].Trim() -eq '[]') {
            $unmanaged = [regex]::Replace($unmanaged, '(?m)^[ \t]*\[\][ \t]*\r?\n?', '')
            $contentLines = @()
        }
        if ($contentLines.Count -gt 0 -and ($unmanaged -notmatch '(?m)^-\s' -or $unmanaged -match '(?m)^\s*(---|\.\.\.)\s*$')) {
            throw 'DSH 配置不是单个补丁列表，未修改配置'
        }
        $block = @"
$begin
- insert:
    - id: davinci-gateway-mcp
      name: mcp-client
      config:
        transport: stdio
        serverName: davinci_gateway
        command: $commandText
        args: $argumentText
        toolCallTimeoutMs: 3600000
        failOnStartupError: false
$end
"@
    }
    if ($Client -eq 'DeepSeekHarness' -and $unmanaged) {
        $separator = if ($unmanaged.EndsWith("`n")) { '' } else { "`r`n" }
        $updated = "$unmanaged$separator$block`r`n"
    } else {
        $updated = if ($unmanaged) { "$unmanaged`r`n`r`n$block`r`n" } else { "$block`r`n" }
    }
}
$changed = $updated -ne $existing
if (-not $Check -and $changed) {
    New-Item -ItemType Directory -Path $clientHome -Force | Out-Null
    if (Test-Path -LiteralPath $configPath) {
        Copy-Item -LiteralPath $configPath -Destination "$configPath.$([DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff')).bak"
    }
    Set-Content -LiteralPath $configPath -Value $updated -Encoding utf8NoBOM -NoNewline
}
[pscustomobject]@{ Client = $Client; ConfigPath = $configPath; ConfigChanged = $changed; CheckOnly = [bool]$Check }
