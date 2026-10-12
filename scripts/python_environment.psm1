# 用户选择后的 Python 环境准备与国内镜像重试。

function Get-GatewayMirrors([string]$ProjectRoot) {
    $path = Join-Path $ProjectRoot 'packaging\python-mirrors.json'
    if (-not (Test-Path -LiteralPath $path)) { $path = Join-Path $ProjectRoot 'python-mirrors.json' }
    return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
}

function Test-GatewayPython([string]$Executable) {
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf) -or
        $Executable -like '*\WindowsApps\*') { return $false }
    $result = & $Executable -c 'import struct,sys; print(sys.version_info >= (3,12) and struct.calcsize("P")==8)' 2>$null
    return $LASTEXITCODE -eq 0 -and "$result".Trim() -eq 'True'
}

function Find-GatewayPython([string]$Requested, [string]$RuntimeRoot) {
    if ($Requested) {
        $found = Get-Command $Requested -ErrorAction SilentlyContinue
        if ($found -and (Test-GatewayPython $found.Source)) { return $found.Source }
        throw '指定的 Python 不是可用的 64 位 Python 3.12 或更新版本'
    }
    $localPython = Join-Path $RuntimeRoot 'Python\python.exe'
    if (Test-GatewayPython $localPython) { return $localPython }
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found -and (Test-GatewayPython $found.Source)) { return $found.Source }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        $candidate = & $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and (Test-GatewayPython "$candidate".Trim())) { return "$candidate".Trim() }
    }
    return $null
}

function Save-GatewayInstaller([string[]]$Urls, [string]$Destination, [string]$Publisher) {
    foreach ($url in $Urls) {
        try {
            Write-Host "下载安装程序：$([uri]::new($url).Host)"
            Invoke-WebRequest -Uri $url -Method Head -TimeoutSec 10 | Out-Null
            Invoke-WebRequest -Uri $url -OutFile $Destination -TimeoutSec 300
            $signature = Get-AuthenticodeSignature -LiteralPath $Destination
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $Publisher) {
                throw '安装程序签名或发布者不符'
            }
            return
        } catch {
            Write-Warning "该安装源未能提供可验证的安装程序：$([uri]::new($url).Host)"
            if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Force }
        }
    }
    throw '所有国内安装源均不可用。可先提供本地 Python/Miniconda 安装，再指定 -Python 或 -CondaRoot 重试。'
}

function Start-GatewayInstaller([string]$Installer, [string]$Arguments) {
    $process = Start-Process -FilePath $Installer -ArgumentList $Arguments -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw "环境安装失败，退出码 $($process.ExitCode)" }
}

function Prepare-GatewayEnvironment {
    param([string]$ProjectRoot, [ValidateSet('Direct','Miniconda')][string]$Environment,
          [string]$Python, [string]$RuntimeRoot, [string]$CondaRoot, [string]$CondaChannel)
    $mirrors = Get-GatewayMirrors $ProjectRoot
    $RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
    if ($RuntimeRoot.StartsWith('\\') -or $RuntimeRoot -eq [IO.Path]::GetPathRoot($RuntimeRoot)) { throw 'RuntimeRoot 必须是本地非根目录' }
    $temporary = $null
    try {
        if ($Environment -eq 'Direct') {
            $existingPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
            if (Test-GatewayPython $existingPython) { return $existingPython }
            $basePython = Find-GatewayPython $Python $RuntimeRoot
            if (-not $basePython) {
                $pythonRoot = Join-Path $RuntimeRoot 'Python'
                if (Test-Path -LiteralPath $pythonRoot) { throw 'Python 目录已存在但解释器不可用，未覆盖' }
                $temporary = Join-Path ([IO.Path]::GetTempPath()) "davinci-gw-python-$([guid]::NewGuid().ToString('N'))"
                New-Item -ItemType Directory -Path $temporary | Out-Null
                $installer = Join-Path $temporary 'python.exe'
                Save-GatewayInstaller $mirrors.python $installer 'Python Software Foundation'
                Start-GatewayInstaller $installer "/quiet /norestart InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0 TargetDir=`"$pythonRoot`""
                $basePython = Join-Path $pythonRoot 'python.exe'
                if (-not (Test-GatewayPython $basePython)) { throw 'Python 安装后检查失败' }
            }
            $environmentPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
            if (-not (Test-GatewayPython $environmentPython)) {
                if (Test-Path -LiteralPath (Join-Path $ProjectRoot '.venv')) { throw '工程 .venv 已存在但不可用，未覆盖' }
                & $basePython -m venv (Join-Path $ProjectRoot '.venv') | Out-Host
                if ($LASTEXITCODE -ne 0) { throw '创建工程虚拟环境失败' }
            }
        } else {
            if (-not $CondaRoot) { $CondaRoot = Join-Path $RuntimeRoot 'Miniconda3' }
            $CondaRoot = [IO.Path]::GetFullPath($CondaRoot)
            if ($CondaRoot.StartsWith('\\') -or $CondaRoot -eq [IO.Path]::GetPathRoot($CondaRoot)) { throw 'CondaRoot 必须是本地非根目录' }
            $conda = Join-Path $CondaRoot 'Scripts\conda.exe'
            if (-not (Test-Path -LiteralPath $conda)) {
                if (Test-Path -LiteralPath $CondaRoot) { throw 'Miniconda 目录已存在但不可用，未覆盖' }
                $temporary = Join-Path ([IO.Path]::GetTempPath()) "davinci-gw-python-$([guid]::NewGuid().ToString('N'))"
                New-Item -ItemType Directory -Path $temporary | Out-Null
                $installer = Join-Path $temporary 'miniconda.exe'
                Save-GatewayInstaller $mirrors.miniconda $installer 'Anaconda'
                Start-GatewayInstaller $installer "/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=$CondaRoot"
                if (-not (Test-Path -LiteralPath $conda)) { throw 'Miniconda 安装后检查失败' }
            }
            $environmentRoot = Join-Path $CondaRoot 'envs\davinci-gw'
            $environmentPython = Join-Path $environmentRoot 'python.exe'
            if (-not (Test-GatewayPython $environmentPython)) {
                if (Test-Path -LiteralPath $environmentRoot) { throw 'davinci-gw 环境已存在但不可用，未覆盖' }
                $channels = if ($CondaChannel) { @($CondaChannel) } else { @($mirrors.conda) }
                $created = $false
                foreach ($channel in $channels) {
                    & $conda create --prefix $environmentRoot --no-default-packages --override-channels --channel $channel 'python=3.12' pip -y | Out-Host
                    if ($LASTEXITCODE -eq 0 -and (Test-GatewayPython $environmentPython)) { $created=$true; break }
                    Write-Warning '该国内 Conda 源未完成环境创建，尝试下一个源'
                    if (Test-Path -LiteralPath $environmentRoot) {
                        $absolute = [IO.Path]::GetFullPath($environmentRoot)
                        $parent = [IO.Path]::GetFullPath((Join-Path $CondaRoot 'envs')).TrimEnd('\') + '\'
                        if (-not $absolute.StartsWith($parent,[StringComparison]::OrdinalIgnoreCase) -or
                            [IO.Path]::GetFileName($absolute) -ne 'davinci-gw') { throw '本次未完成的环境目录越出预期范围' }
                        Remove-Item -LiteralPath $absolute -Recurse -Force
                    }
                }
                if (-not $created) { throw '所有 Conda 源均未完成 davinci-gw 环境创建' }
            }
        }
        if (-not (Test-GatewayPython $environmentPython)) { throw '工程 Python 环境检查失败' }
        return $environmentPython
    } finally {
        if ($temporary -and (Test-Path -LiteralPath $temporary)) {
            $absolute = [IO.Path]::GetFullPath($temporary)
            $root = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
            if (-not $absolute.StartsWith($root,[StringComparison]::OrdinalIgnoreCase) -or
                [IO.Path]::GetFileName($absolute) -notmatch '^davinci-gw-python-[0-9a-f]{32}$') { throw '环境临时目录越出预期范围' }
            Remove-Item -LiteralPath $absolute -Recurse -Force
        }
    }
}

function Install-GatewayDependencies {
    param([string]$Python, [string]$ProjectRoot, [string]$PipIndexUrl, [switch]$Build)
    $mirrors = Get-GatewayMirrors $ProjectRoot
    $indexes = if ($PipIndexUrl) { @($PipIndexUrl) } else { @($mirrors.pip) }
    $requirements = @("$ProjectRoot[mcp]")
    if ($Build) { $requirements += 'pyinstaller>=6.15,<7' }
    # 读取现有连接设置，隔离包源时继续使用企业代理和证书。
    $readConnection = @'
import json
from pip._internal.configuration import Configuration
from pip._internal.exceptions import ConfigurationError
configuration = Configuration(isolated=False)
configuration.load()
values = {}
for name in ("proxy", "cert", "client-cert"):
    for section in ("install", "global"):
        try:
            value = configuration.get_value(section + "." + name)
        except ConfigurationError:
            continue
        if value:
            values[name] = value
            break
print(json.dumps(values))
'@
    $connectionJson = & $Python -c $readConnection
    if ($LASTEXITCODE -ne 0) { throw '读取 pip 连接配置失败' }
    $connection = "$connectionJson" | ConvertFrom-Json -AsHashtable
    $connectionArguments = @()
    foreach ($name in $connection.Keys) { $connectionArguments += @("--$name",[string]$connection[$name]) }
    $saved = @{}
    foreach ($name in 'PIP_CONFIG_FILE','PIP_INDEX_URL','PIP_EXTRA_INDEX_URL','PIP_FIND_LINKS','PIP_NO_INDEX') {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name,'Process')
        [Environment]::SetEnvironmentVariable($name,$null,'Process')
    }
    try {
        # 本次安装只使用选定镜像，不继承其他索引或链接，不改写用户配置。
        $env:PIP_CONFIG_FILE = 'nul'
        foreach ($index in $indexes | Select-Object -Unique) {
            & $Python -m pip install --index-url $index --timeout 15 --retries 1 @connectionArguments @requirements | Out-Host
            if ($LASTEXITCODE -eq 0) { return }
            Write-Warning '该 Python 包源未完成依赖安装，尝试下一个源'
        }
        throw '所有 Python 包源均未完成依赖安装。请提供可访问的内网 -PipIndexUrl 后重试。'
    } finally {
        foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name,$saved[$name],'Process') }
    }
}

Export-ModuleMember -Function Get-GatewayMirrors,Test-GatewayPython,Find-GatewayPython,Save-GatewayInstaller,Prepare-GatewayEnvironment,Install-GatewayDependencies
