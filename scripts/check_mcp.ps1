[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Command,
    [string[]]$Arguments = @(),
    [string]$ExpectedVersion
)

$ErrorActionPreference = 'Stop'
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = $Command
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$start.StandardInputEncoding = [Text.UTF8Encoding]::new($false)
$start.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
$process = [Diagnostics.Process]::new()
$process.StartInfo = $start
$started = $false
function Read-Response([int]$Id) {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        $line = $process.StandardOutput.ReadLineAsync()
        $remaining = [Math]::Max(1, [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds)
        if (-not $line.Wait($remaining)) { throw 'MCP 响应超时' }
        if ($null -eq $line.Result) { throw 'MCP 在完成检查前退出' }
        $message = $line.Result | ConvertFrom-Json -AsHashtable
        if ($message.id -eq $Id) {
            if ($message.ContainsKey('error')) { throw "MCP 协议错误：$($message.error.code)" }
            return $message.result
        }
    }
    throw 'MCP 响应超时'
}
function Send-Request([hashtable]$Message) {
    $process.StandardInput.WriteLine(($Message | ConvertTo-Json -Depth 8 -Compress))
    $process.StandardInput.Flush()
}
try {
    if (-not $process.Start()) { throw '无法启动 MCP' }
    $started = $true
    $stderr = $process.StandardError.ReadToEndAsync()
    Send-Request @{ jsonrpc='2.0'; id=1; method='initialize'; params=@{
        protocolVersion='2025-03-26'; capabilities=@{}; clientInfo=@{ name='davinci-gw-setup'; version='1.0' }
    } }
    $initialized = Read-Response 1
    if ($initialized.serverInfo.name -ne 'davinci-gw-mcp') { throw '启动的服务不是网关 MCP' }
    Send-Request @{ jsonrpc='2.0'; method='notifications/initialized' }
    Send-Request @{ jsonrpc='2.0'; id=2; method='tools/list'; params=@{} }
    $listed = Read-Response 2
    foreach ($required in 'get_gateway_capabilities','validate_gateway_inputs','preview_gateway_update','generate_gateway_arxml') {
        if ($required -notin $listed.tools.name) { throw "MCP 缺少工具：$required" }
    }
    Send-Request @{ jsonrpc='2.0'; id=3; method='tools/call'; params=@{ name='get_gateway_capabilities'; arguments=@{} } }
    $capabilities = Read-Response 3
    if ($capabilities.isError -or $capabilities.structuredContent.status -ne 'SUCCESS') { throw 'MCP 能力查询失败' }
    $version = $capabilities.structuredContent.product.version
    if ($ExpectedVersion -and $version -ne $ExpectedVersion) { throw "MCP 版本不符：$version" }
    [pscustomobject]@{ Status='SUCCESS'; Version=$version; ToolCount=@($listed.tools).Count }
} finally {
    if ($started) {
        if (-not $process.HasExited) {
            $process.StandardInput.Close()
            if (-not $process.WaitForExit(5000)) { $process.Kill(); $process.WaitForExit() }
        }
    }
    $process.Dispose()
}
