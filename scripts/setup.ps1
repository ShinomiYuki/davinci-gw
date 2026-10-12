[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Codex', 'DeepSeekHarness', 'None')][string]$Client,
    [ValidateSet('Package', 'Source')][string]$Mode = 'Package',
    [string]$PackageRoot,
    [string]$Python = 'python',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\DaVinciGW'),
    [string]$CodexHome = (Join-Path $env:USERPROFILE '.codex'),
    [string]$DshHome,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7 -or -not $IsWindows) { throw '此安装入口需要 Windows 和 PowerShell 7' }
$isPackaged = (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'VERSION')) -and
    (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'davinci-gw-mcp.exe'))
$projectRoot = if ($isPackaged) { $PSScriptRoot }
elseif (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\pyproject.toml')) {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
} else { $PSScriptRoot }
$scriptRoot = $PSScriptRoot
if (-not $isPackaged -and (Test-Path -LiteralPath (Join-Path $projectRoot 'pyproject.toml'))) {
    $metadata = Get-Content -LiteralPath (Join-Path $projectRoot 'pyproject.toml') -Raw
    if ($metadata -notmatch '(?m)^version\s*=\s*"(\d+\.\d+\.\d+)"') { throw '无法读取工程版本' }
    $version = $Matches[1]
} else {
    $version = (Get-Content -LiteralPath (Join-Path $projectRoot 'VERSION') -Raw).Trim()
    if ($version -notmatch '^\d+\.\d+\.\d+$') { throw '发布包版本无效' }
}
$arguments = @()
$download = $false
if ($Mode -eq 'Source') {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'pyproject.toml'))) { throw 'Source 模式需要完整工程源码' }
    $pythonCommand = Get-Command $Python -ErrorAction Stop
    if ($pythonCommand.Source -like '*\WindowsApps\*') { throw '请用 -Python 指定已安装的 Python 3.12 或更新版本，不能使用商店占位程序' }
    $pythonInfo = & $pythonCommand.Source -c 'import struct,sys; print(sys.version_info >= (3,12) and struct.calcsize("P")==8)'
    if ($LASTEXITCODE -ne 0 -or $pythonInfo.Trim() -ne 'True') { throw '源码安装需要 64 位 Python 3.12 或更新版本' }
    $runtimeCommand = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $arguments = @('-m', 'davinci_gw.mcp')
} else {
    if (-not $PackageRoot) {
        $localPackage = Join-Path $projectRoot "release\davinci-gw-mcp-$version-win-x64"
        if (Test-Path -LiteralPath (Join-Path $projectRoot 'davinci-gw-mcp.exe')) { $PackageRoot = $projectRoot }
        elseif (Test-Path -LiteralPath (Join-Path $localPackage 'davinci-gw-mcp.exe')) { $PackageRoot = $localPackage }
        else { $download = $true }
    }
    if ($PackageRoot) { $PackageRoot = (Resolve-Path -LiteralPath $PackageRoot -ErrorAction Stop).Path }
    $runtimeCommand = Join-Path ([IO.Path]::GetFullPath($InstallRoot)) "versions\$version\davinci-gw-mcp.exe"
}
$configuration = $null
if ($Client -ne 'None') {
    $configuration = & (Join-Path $scriptRoot 'configure_mcp_client.ps1') -Client $Client -Command $runtimeCommand -Arguments $arguments -CodexHome $CodexHome -DshHome $DshHome -Check
}
if ($Check) {
    [ordered]@{ Version=$version; Client=$Client; Mode=$Mode; Command=$runtimeCommand; Arguments=$arguments;
        ConfigPath=$configuration.ConfigPath; DownloadRequired=$download; PackageRoot=$PackageRoot;
        Release="https://github.com/ShinomiYuki/davinci-gw/releases/tag/v$version" } | ConvertTo-Json -Depth 4
    return
}
$temporary = $null
try {
    if ($Mode -eq 'Source') {
        if (-not (Test-Path -LiteralPath $runtimeCommand -PathType Leaf)) {
            & $pythonCommand.Source -m venv (Join-Path $projectRoot '.venv')
            if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败' }
        }
        & $runtimeCommand -m pip install "$projectRoot[mcp]"
        if ($LASTEXITCODE -ne 0) { throw '安装源码依赖失败；检查网络或 Python 包镜像设置后重试' }
    } else {
        if ($download) {
            $temporary = Join-Path ([IO.Path]::GetTempPath()) "davinci-gw-setup-$([guid]::NewGuid().ToString('N'))"
            New-Item -ItemType Directory -Path $temporary | Out-Null
            $archiveName = "davinci-gw-mcp-$version-win-x64.zip"
            $archive = Join-Path $temporary $archiveName
            $baseUrl = "https://github.com/ShinomiYuki/davinci-gw/releases/download/v$version"
            Invoke-WebRequest -Uri "$baseUrl/$archiveName" -OutFile $archive
            Invoke-WebRequest -Uri "$baseUrl/$archiveName.sha256" -OutFile "$archive.sha256"
            $checksum = (Get-Content -LiteralPath "$archive.sha256" -Raw).Trim()
            if ($checksum -notmatch '^([0-9a-fA-F]{64})\s+(.+)$' -or $Matches[2] -ne $archiveName) { throw '发布包校验文件无效' }
            if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $Matches[1]) { throw '发布 ZIP 校验失败' }
            $PackageRoot = Join-Path $temporary 'package'
            Expand-Archive -LiteralPath $archive -DestinationPath $PackageRoot
        }
        $packageVersion = (Get-Content -LiteralPath (Join-Path $PackageRoot 'VERSION') -Raw).Trim()
        if ($packageVersion -ne $version) { throw "发布包版本 $packageVersion 与工程 $version 不一致" }
        & (Join-Path $scriptRoot 'install_mcp.ps1') -PackageRoot $PackageRoot -InstallRoot $InstallRoot -Client $Client -CodexHome $CodexHome -DshHome $DshHome -Check | Out-Null
        # 校验发布包后检查服务能启动，再修改客户端配置。
        & (Join-Path $scriptRoot 'check_mcp.ps1') -Command (Join-Path $PackageRoot 'davinci-gw-mcp.exe') -ExpectedVersion $version | Out-Null
        $installed = & (Join-Path $scriptRoot 'install_mcp.ps1') -PackageRoot $PackageRoot -InstallRoot $InstallRoot -Client $Client -CodexHome $CodexHome -DshHome $DshHome
        $runtimeCommand = $installed.Installed
    }
    $health = & (Join-Path $scriptRoot 'check_mcp.ps1') -Command $runtimeCommand -Arguments $arguments -ExpectedVersion $version
    if ($Mode -eq 'Source' -and $Client -ne 'None') {
        $configuration = & (Join-Path $scriptRoot 'configure_mcp_client.ps1') -Client $Client -Command $runtimeCommand -Arguments $arguments -CodexHome $CodexHome -DshHome $DshHome
    }
    [ordered]@{ Status=$health.Status; Version=$version; ToolCount=$health.ToolCount; Client=$Client;
        Command=$runtimeCommand; ConfigPath=if ($Client -eq 'None') { $null } elseif ($Mode -eq 'Package') { $installed.ClientConfig } else { $configuration.ConfigPath };
        NextStep='重新加载 MCP 或重启当前客户端，然后调用 get_gateway_capabilities。' } | ConvertTo-Json -Depth 4
} finally {
    if ($temporary -and (Test-Path -LiteralPath $temporary)) {
        $absolute = [IO.Path]::GetFullPath($temporary)
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        if (-not $absolute.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetFileName($absolute) -notmatch '^davinci-gw-setup-[0-9a-f]{32}$') {
            throw '安装临时目录超出预期范围，未清理'
        }
        Remove-Item -LiteralPath $absolute -Recurse -Force
    }
}
