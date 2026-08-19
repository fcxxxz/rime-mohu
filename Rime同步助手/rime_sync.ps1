# rime_sync.ps1 -- Rime 用户数据自动同步（Windows，无常驻进程）
#
# 形态：计划任务。登录时运行一次 + 按检查间隔被调度器拉起，
#       每次运行检查完就退出，平时系统里没有任何同步进程。
#
# 用法:
#   powershell -File rime_sync.ps1 install    注册计划任务（登录 + 定时）并立即运行一次
#   powershell -File rime_sync.ps1 uninstall  注销计划任务
#   powershell -File rime_sync.ps1 run        单次运行（由计划任务调用）
#   powershell -File rime_sync.ps1 status     查看设置和上次结果
#
# 开机同步：开机后第一次被拉起时同步一次（靠 sync-cursor.json 记录的开机时间判断）。
# 空闲同步：被拉起时读系统空闲时间，达到设定分钟数才同步；持续空闲只同步一次。
# 有更新才执行：真正同步前先做门卫检查——本地用户库和同步目录都没有比
#           「上次同步」更新的文件时直接跳过，不调用 WeaselDeployer。
# 没有关机同步：无常驻进程接不到关机信号；未同步的数据不会丢，下次开机同步会补上。
#
# 用户设置在同目录的 同步设置.conf 里编辑，改完保存即生效。
# 例外：「检查间隔分钟数」决定计划任务频率，改后需重新执行 install。

param(
  [Parameter(Position = 0)][string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
$OutputEncoding = $Utf8NoBom
try { [Console]::OutputEncoding = $Utf8NoBom } catch {}

# 中文键名用码点构建，避免脚本以无 BOM UTF-8 保存时被 PowerShell 5.1 误读
function U {
  param([int[]]$CodePoints)
  return [System.String]::Concat(@($CodePoints | ForEach-Object { [System.Char]::ConvertFromUtf32($_) }))
}

$ConfFileName = U @(0x540C, 0x6B65, 0x8BBE, 0x7F6E) + '.conf'                    # 同步设置.conf
$StateFileName = U @(0x540C, 0x6B65, 0x72B6, 0x6001) + '.json'                   # 同步状态.json
$KeyIdle = U @(0x7A7A, 0x95F2, 0x540C, 0x6B65)                                   # 空闲同步
$KeyIdleMinutes = U @(0x7A7A, 0x95F2, 0x5206, 0x949F, 0x6570)                    # 空闲分钟数
$KeyStartup = U @(0x5F00, 0x673A, 0x540C, 0x6B65)                                # 开机同步
$KeyInterval = U @(0x68C0, 0x67E5, 0x95F4, 0x9694, 0x5206, 0x949F, 0x6570)       # 检查间隔分钟数
$ValueOn = U @(0x5F00)                                                           # 开

$TaskName = 'RimeMohuSync'
$ConfPath = Join-Path $PSScriptRoot $ConfFileName
$StatePath = Join-Path $PSScriptRoot $StateFileName
$CursorPath = Join-Path $PSScriptRoot 'sync-cursor.json'
$RimeRoot = Join-Path $env:APPDATA 'Rime'

function ConvertTo-JsonSafeString {
  param([string]$S)
  $S = $S.Replace('\', '\\').Replace('"', '\"').Replace("`b", '\b').Replace("`f", '\f').Replace("`n", '\n').Replace("`r", '\r').Replace("`t", '\t')
  return '"' + $S + '"'
}

function ConvertTo-JsonSafe {
  param($Value)
  if ($null -eq $Value) { return 'null' }
  if ($Value -is [bool]) { return $(if ($Value) { 'true' } else { 'false' }) }
  if ($Value -is [int] -or $Value -is [long] -or $Value -is [double]) { return [string]$Value }
  if ($Value -is [string]) { return ConvertTo-JsonSafeString $Value }
  if ($Value -is [System.Collections.IDictionary]) {
    $Parts = @()
    foreach ($K in $Value.Keys) { $Parts += (ConvertTo-JsonSafeString ([string]$K)) + ':' + (ConvertTo-JsonSafe $Value[$K]) }
    return '{' + ($Parts -join ',') + '}'
  }
  if ($Value -is [System.Collections.IEnumerable]) {
    $Parts = @()
    foreach ($Item in $Value) { $Parts += (ConvertTo-JsonSafe $Item) }
    return '[' + ($Parts -join ',') + ']'
  }
  if ($Value.PSObject.Properties.Count -gt 0) {
    $Parts = @()
    foreach ($Prop in $Value.PSObject.Properties) { $Parts += (ConvertTo-JsonSafeString $Prop.Name) + ':' + (ConvertTo-JsonSafe $Prop.Value) }
    return '{' + ($Parts -join ',') + '}'
  }
  return ConvertTo-JsonSafeString ([string]$Value)
}

function Test-Truthy {
  param([string]$Value)
  $Lower = $Value.ToLowerInvariant()
  return ($Value -eq $ValueOn) -or ($Lower -in @('on', 'true', '1', 'yes'))
}

function Read-SyncConf {
  $Settings = @{ idle = $true; startup = $true; idleMinutes = 10; intervalMinutes = 5 }
  if (-not (Test-Path $ConfPath -PathType Leaf)) { return $Settings }
  foreach ($Line in (Get-Content $ConfPath -Encoding UTF8)) {
    $Text = $Line
    $Hash = $Text.IndexOf('#')
    if ($Hash -ge 0) { $Text = $Text.Substring(0, $Hash) }
    $Text = $Text.Trim()
    if (-not $Text -or -not $Text.Contains(':')) { continue }
    $Colon = $Text.IndexOf(':')
    $Key = $Text.Substring(0, $Colon).Trim()
    $Value = $Text.Substring($Colon + 1).Trim()
    switch ($Key) {
      $KeyIdle { $Settings.idle = (Test-Truthy $Value) }
      $KeyStartup { $Settings.startup = (Test-Truthy $Value) }
      $KeyIdleMinutes {
        $Number = 0
        if ([int]::TryParse($Value, [ref]$Number) -and $Number -ge 1 -and $Number -le 1440) {
          $Settings.idleMinutes = $Number
        }
      }
      $KeyInterval {
        $Number = 0
        if ([int]::TryParse($Value, [ref]$Number) -and $Number -ge 1 -and $Number -le 1440) {
          $Settings.intervalMinutes = $Number
        }
      }
    }
  }
  return $Settings
}

function Read-SyncState {
  if (-not (Test-Path $StatePath -PathType Leaf)) { return $null }
  try { return Get-Content $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

function Write-SyncState {
  param($State)
  [System.IO.File]::WriteAllText($StatePath, (ConvertTo-JsonSafe $State) + [Environment]::NewLine, $Utf8NoBom)
}

function Read-Cursor {
  if (-not (Test-Path $CursorPath -PathType Leaf)) { return @{ armed = $true; boot = ''; last = [long]0 } }
  try {
    $Raw = Get-Content $CursorPath -Raw -Encoding UTF8 | ConvertFrom-Json
    return @{
      armed = if ($null -ne $Raw.armed) { [bool]$Raw.armed } else { $true }
      boot = [string]$Raw.boot
      last = if ($null -ne $Raw.last) { [long]$Raw.last } else { [long]0 }
    }
  } catch {
    return @{ armed = $true; boot = ''; last = [long]0 }
  }
}

function Write-Cursor {
  param([bool]$Armed, [string]$Boot, [long]$Last)
  [System.IO.File]::WriteAllText($CursorPath, (ConvertTo-JsonSafe @{ armed = $Armed; boot = $Boot; last = $Last }) + [Environment]::NewLine, $Utf8NoBom)
}

function Get-BootStamp {
  try {
    return (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('s')
  } catch {
    return ''
  }
}

function Get-SyncDir {
  $Yaml = Join-Path $RimeRoot 'installation.yaml'
  if (Test-Path $Yaml -PathType Leaf) {
    foreach ($Line in (Get-Content $Yaml -Encoding UTF8)) {
      if ($Line -match '^\s*sync_dir:\s*"([^"]+)"') { return $Matches[1] }
    }
  }
  return (Join-Path $RimeRoot 'sync')
}

# 有更新才执行门卫：本地用户库 + 同步目录任一文件比上次同步新，才算有新数据
function Get-DataNewest {
  $Newest = [long]0
  $SyncDir = Get-SyncDir
  $Files = @()
  $Files += Get-ChildItem -Path (Join-Path $RimeRoot '*.userdb\*') -File -ErrorAction SilentlyContinue
  $Files += Get-ChildItem -Path (Join-Path $SyncDir '*\*') -File -ErrorAction SilentlyContinue
  foreach ($F in $Files) {
    $Epoch = ([DateTimeOffset]$F.LastWriteTimeUtc).ToUnixTimeSeconds()
    if ($Epoch -gt $Newest) { $Newest = $Epoch }
  }
  return $Newest
}

function Get-SyncCommand {
  $SearchRoots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA) | Where-Object { $_ }
  foreach ($SearchRoot in $SearchRoots) {
    $RimeRoot = Join-Path $SearchRoot 'Rime'
    if (-not (Test-Path $RimeRoot -PathType Container)) { continue }
    $Executable = Get-ChildItem $RimeRoot -File -Filter 'WeaselDeployer.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Executable) { return @($Executable.FullName, '/sync') }
  }
  return $null
}

function Invoke-RimeSync {
  param([string]$Reason)
  $Command = @(Get-SyncCommand)
  $Stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
  if ($Command.Count -lt 2) {
    $State = @{ ok = $false; reason = $Reason; time = $Stamp; message = 'WeaselDeployer.exe not found.' }
  } else {
    try {
      $Process = Start-Process -FilePath $Command[0] -ArgumentList $Command[1] -WindowStyle Hidden -Wait -PassThru
      $Ok = ($Process.ExitCode -eq 0)
      $State = @{
        ok = $Ok
        reason = $Reason
        time = $Stamp
        message = if ($Ok) { 'sync ok' } else { "sync failed with exit code $($Process.ExitCode)." }
      }
    } catch {
      $State = @{ ok = $false; reason = $Reason; time = $Stamp; message = $_.Exception.Message }
    }
  }
  Write-SyncState $State
  Write-Host "[$Stamp] $Reason : $(if ($State.ok) { 'ok' } else { 'fail' }) $($State.message)"
  return $State
}

Add-Type -Namespace Win32 -Name IdleInput -MemberDefinition @'
[DllImport("user32.dll")]
public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
[DllImport("kernel32.dll")]
public static extern uint GetTickCount();
[StructLayout(LayoutKind.Sequential)]
public struct LASTINPUTINFO {
  public uint cbSize;
  public uint dwTime;
}
public static double GetIdleMilliseconds() {
  LASTINPUTINFO info = new LASTINPUTINFO();
  info.cbSize = (uint)System.Runtime.InteropServices.Marshal.SizeOf(typeof(LASTINPUTINFO));
  if (!GetLastInputInfo(ref info)) { return 0; }
  uint now = GetTickCount();
  uint elapsed = now - info.dwTime;
  if (now < info.dwTime) { elapsed = UInt32.MaxValue - info.dwTime + now + 1; }
  return (double)elapsed;
}
'@

function Get-SystemIdleSeconds {
  return [Math]::Max(0.0, [Win32.IdleInput]::GetIdleMilliseconds() / 1000.0)
}

function Invoke-Once {
  $Settings = Read-SyncConf
  $Boot = Get-BootStamp
  $Cursor = Read-Cursor
  $Armed = [bool]$Cursor.armed
  $Last = [long]$Cursor.last

  # 有更新才执行：本地用户库或同步目录出现新修改才真正同步
  $HasChanges = ($Last -eq 0) -or ((Get-DataNewest) -gt $Last)

  if ($Boot -and ($Cursor.boot -ne $Boot)) {
    if ($Settings.startup -and $HasChanges) {
      Invoke-RimeSync 'startup' | Out-Null
      Start-Sleep -Seconds 5
      $Last = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    }
    $Armed = $true
  }

  if ($Settings.idle) {
    $Idle = Get-SystemIdleSeconds
    if ($Idle -lt ([int]$Settings.idleMinutes) * 60) {
      $Armed = $true
    } elseif ($Armed) {
      if ($HasChanges) {
        $Armed = $false
        Invoke-RimeSync 'idle' | Out-Null
        Start-Sleep -Seconds 5
        $Last = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
      }
      # 没有新数据时保持武装：一出现新数据就同步
    }
  }

  Write-Cursor $Armed $Boot $Last
}

function Install-Sync {
  $Settings = Read-SyncConf
  $Self = (Resolve-Path $PSCommandPath).Path
  $TaskAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Self`" run"
  $LogonTrigger = New-ScheduledTaskTrigger -AtLogOn
  $TimerTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(30) `
    -RepetitionInterval (New-TimeSpan -Minutes ([int]$Settings.intervalMinutes))
  try { $TimerTrigger.RepetitionDuration = [TimeSpan]::MaxValue } catch { $TimerTrigger.RepetitionDuration = (New-TimeSpan -Days 3650) }
  Register-ScheduledTask -TaskName $TaskName -Action $TaskAction -Trigger @($LogonTrigger, $TimerTrigger) -Force | Out-Null
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "Rime sync registered as scheduled task (logon + every $($Settings.intervalMinutes) min, no resident process)."
  Write-Host "Settings file: $ConfPath (edit and save, takes effect next run)"
  Write-Host 'Note: changing the check interval requires running install again.'
}

function Uninstall-Sync {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host 'Rime sync uninstalled (scheduled task removed).'
}

function Show-SyncStatus {
  $Settings = Read-SyncConf
  $State = Read-SyncState
  $Registered = $false
  try { if (Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop) { $Registered = $true } } catch {}
  Write-Host "registered  : $(if ($Registered) { 'yes (scheduled task, no resident process)' } else { 'no' })"
  Write-Host "idle sync   : $(if ($Settings.idle) { 'on' } else { 'off' }) ($($Settings.idleMinutes) min, checked every $($Settings.intervalMinutes) min)"
  Write-Host "startup sync: $(if ($Settings.startup) { 'on' } else { 'off' })"
  Write-Host "sync tool   : $(if (Get-SyncCommand) { 'WeaselDeployer found' } else { 'WeaselDeployer not found' })"
  Write-Host "settings    : $ConfPath"
  if ($State) {
    Write-Host "last sync   : $($State.time) ($($State.reason)) $(if ($State.ok) { 'ok' } else { 'fail' }) $($State.message)"
  } else {
    Write-Host 'last sync   : none'
  }
}

switch ($Action) {
  'install' { Install-Sync }
  'uninstall' { Uninstall-Sync }
  'run' { Invoke-Once }
  'status' { Show-SyncStatus }
  default { throw "unknown action: $Action (install|uninstall|run|status)" }
}
