param(
  [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
  [int]$Port = 0,
  [int]$IdleTimeoutMs = 60000,
  [switch]$NoIdleExit
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
$OutputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom

function U {
  param([int[]]$CodePoints)
  return [System.String]::Concat(@($CodePoints | ForEach-Object { [System.Char]::ConvertFromUtf32($_) }))
}

$BackupRoot = U @(0x76AE, 0x80A4, 0x7F16, 0x8F91, 0x5668, 0x5907, 0x4EFD)
$AccessDeniedMessage = U @(0x4E0D, 0x5141, 0x8BB8, 0x8BBF, 0x95EE, 0x8FD9, 0x4E2A, 0x8DEF, 0x5F84, 0x3002)
$PortUnavailableMessage = U @(0x65E0, 0x6CD5, 0x542F, 0x52A8, 0x672C, 0x5730, 0x670D, 0x52A1, 0xFF1A, 0x0031, 0x0037, 0x0038, 0x0039, 0x0030, 0x002D, 0x0031, 0x0037, 0x0039, 0x0032, 0x0030, 0x0020, 0x7AEF, 0x53E3, 0x90FD, 0x4E0D, 0x53EF, 0x7528, 0x3002)
$StartedMessage = U @(0x0052, 0x0069, 0x006D, 0x0065, 0x0020, 0x76AE, 0x80A4, 0x7F16, 0x8F91, 0x5668, 0x5DF2, 0x542F, 0x52A8, 0xFF1A)
$RootMessage = U @(0x914D, 0x7F6E, 0x76EE, 0x5F55, 0xFF1A)
$CloseMessage = U @(0x5173, 0x95ED, 0x8FD9, 0x4E2A, 0x7A97, 0x53E3, 0x5373, 0x53EF, 0x505C, 0x6B62, 0x672C, 0x5730, 0x670D, 0x52A1, 0x3002)
$InvalidTokenMessage = U @(0x8BBF, 0x95EE, 0x4EE4, 0x724C, 0x65E0, 0x6548, 0x3002)
$MissingFileMessage = U @(0x6587, 0x4EF6, 0x4E0D, 0x5B58, 0x5728, 0x3002)
$MissingApiMessage = U @(0x63A5, 0x53E3, 0x4E0D, 0x5B58, 0x5728, 0x3002)
$StaticRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$TokenBytes = New-Object byte[] 18
$TokenRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$TokenRng.GetBytes($TokenBytes)
$TokenRng.Dispose()
$Token = -join ($TokenBytes | ForEach-Object { $_.ToString('x2') })

function Resolve-AllowedPath {
  param([string]$RequestedPath, [switch]$Write)
  if ([string]::IsNullOrWhiteSpace($RequestedPath) -or $RequestedPath.StartsWith('/') -or $RequestedPath.StartsWith('\') -or $RequestedPath.Contains([char]0)) {
    throw $AccessDeniedMessage
  }
  $Parts = @($RequestedPath -replace '\\','/' -split '/' | Where-Object { $_ })
  if ($Parts -contains '..') { throw $AccessDeniedMessage }
  $IsFrontendFile = $Parts.Count -eq 1 -and @('squirrel.custom.yaml', 'weasel.custom.yaml', 'default.custom.yaml') -contains $Parts[0]
  $IsCustomFile = $Parts.Count -eq 1 -and $Parts[0].EndsWith('.custom.yaml')
  $IsYamlFile = $Parts.Count -eq 1 -and $Parts[0].EndsWith('.yaml')
  $IsBackupFile = $Parts.Count -ge 2 -and $Parts[0] -eq $BackupRoot
  $Readable = $IsFrontendFile -or $IsCustomFile -or $IsYamlFile -or $IsBackupFile
  $Writable = $IsFrontendFile -or $IsCustomFile -or $IsBackupFile
  if (-not $Readable -or ($Write -and -not $Writable)) { throw $AccessDeniedMessage }
  $TargetPath = $Root
  foreach ($Part in $Parts) {
    $TargetPath = Join-Path $TargetPath $Part
  }
  $Target = [System.IO.Path]::GetFullPath($TargetPath)
  $RootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
  if ($Target -ne $RootFull -and -not $Target.StartsWith($RootFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw $AccessDeniedMessage
  }
  return $Target
}

function Get-QueryValue {
  param($Request, [string]$Name)
  $Query = $Request.Url.Query
  if ($Query.StartsWith('?')) { $Query = $Query.Substring(1) }
  foreach ($Pair in $Query -split '&') {
    if ([string]::IsNullOrWhiteSpace($Pair)) { continue }
    $Parts = $Pair -split '=', 2
    $Key = [Uri]::UnescapeDataString($Parts[0].Replace('+', ' '))
    if ($Key -ne $Name) { continue }
    if ($Parts.Count -lt 2) { return '' }
    return [Uri]::UnescapeDataString($Parts[1].Replace('+', ' '))
  }
  return ''
}

function Write-JsonString {
  param([System.Text.StringBuilder]$Sb, [string]$S)
  [void]$Sb.Append('"')
  foreach ($ch in $S.ToCharArray()) {
    $code = [int]$ch
    switch ($ch) {
      '"'  { [void]$Sb.Append('\"'); continue }
      '\'  { [void]$Sb.Append('\\'); continue }
      "`b" { [void]$Sb.Append('\b'); continue }
      "`f" { [void]$Sb.Append('\f'); continue }
      "`n" { [void]$Sb.Append('\n'); continue }
      "`r" { [void]$Sb.Append('\r'); continue }
      "`t" { [void]$Sb.Append('\t'); continue }
      default {
        if ($code -lt 32) { [void]$Sb.Append('\u{0:x4}' -f $code) }
        else { [void]$Sb.Append($ch) }
      }
    }
  }
  [void]$Sb.Append('"')
}

function Write-JsonValue {
  param([System.Text.StringBuilder]$Sb, $Value)
  if ($null -eq $Value) { [void]$Sb.Append('null'); return }
  if ($Value -is [string]) { Write-JsonString $Sb $Value; return }
  if ($Value -is [bool]) { [void]$Sb.Append($(if ($Value) { 'true' } else { 'false' })); return }
  if ($Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal]) {
    [void]$Sb.Append([string]$Value); return
  }
  if ($Value -is [System.Collections.IDictionary]) {
    [void]$Sb.Append('{'); $First = $true
    foreach ($Key in $Value.Keys) {
      if (-not $First) { [void]$Sb.Append(',') }
      $First = $false
      Write-JsonString $Sb ([string]$Key); [void]$Sb.Append(':')
      Write-JsonValue $Sb $Value[$Key]
    }
    [void]$Sb.Append('}'); return
  }
  if ($Value -is [System.Management.Automation.PSCustomObject]) {
    [void]$Sb.Append('{'); $First = $true
    foreach ($Prop in $Value.PSObject.Properties) {
      if (-not $First) { [void]$Sb.Append(',') }
      $First = $false
      Write-JsonString $Sb $Prop.Name; [void]$Sb.Append(':')
      Write-JsonValue $Sb $Prop.Value
    }
    [void]$Sb.Append('}'); return
  }
  if ($Value -is [System.Collections.IEnumerable]) {
    [void]$Sb.Append('['); $First = $true
    foreach ($Item in $Value) {
      if (-not $First) { [void]$Sb.Append(',') }
      $First = $false
      Write-JsonValue $Sb $Item
    }
    [void]$Sb.Append(']'); return
  }
  Write-JsonString $Sb ([string]$Value)
}

# PowerShell 5.1 ConvertTo-Json recursively walks strings when -Depth is high,
# making serialization time and output explode. Use a small serializer instead.
function ConvertTo-JsonSafe {
  param($Value)
  $Sb = New-Object System.Text.StringBuilder
  Write-JsonValue $Sb $Value
  return $Sb.ToString()
}

function Send-Json {
  param($Response, [int]$Status, $Value)
  $Json = ConvertTo-JsonSafe $Value
  $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Json)
  $Response.StatusCode = $Status
  $Response.ContentType = 'application/json; charset=utf-8'
  $Response.Headers['cache-control'] = 'no-store'
  $Response.OutputStream.Write($Bytes, 0, $Bytes.Length)
  $Response.Close()
}

function Read-BodyJson {
  param($Request)
  $Reader = New-Object System.IO.StreamReader($Request.InputStream, [System.Text.Encoding]::UTF8)
  $Text = $Reader.ReadToEnd()
  if ([string]::IsNullOrWhiteSpace($Text)) { return @{} }
  return $Text | ConvertFrom-Json
}

function Get-ConfigSnapshot {
  $RawFiles = @{}
  $FileExists = @{}
  foreach ($Item in @(
    @('squirrel', 'squirrel.custom.yaml'),
    @('weasel', 'weasel.custom.yaml'),
    @('default', 'default.custom.yaml'),
    @('user', 'user.yaml')
  )) {
    $Path = Resolve-AllowedPath $Item[1]
    $Exists = Test-Path $Path -PathType Leaf
    $FileExists[$Item[0]] = $Exists
    $RawFiles[$Item[0]] = if ($Exists) { Get-Content $Path -Raw -Encoding UTF8 } else { '' }
  }
  return @{
    folderName = Split-Path $Root -Leaf
    rootPath = [System.IO.Path]::GetFullPath($Root)
    rawFiles = $RawFiles
    fileExists = $FileExists
    customFiles = @(Get-RootCustomFiles)
    yamlFiles = @(Get-RootYamlFiles)
    hasAnySchemaFile = [bool](Get-ChildItem $Root -File -Filter '*.schema.yaml' -ErrorAction SilentlyContinue | Select-Object -First 1)
    deploySupported = [bool](Get-DeployCommand)
  }
}

function Get-RootYamlFiles {
  $Items = @()
  foreach ($File in Get-ChildItem $Root -File -Filter '*.yaml' -ErrorAction SilentlyContinue | Sort-Object Name) {
    if ($File.Name.EndsWith('.dict.yaml') -or $File.Name.EndsWith('.custom.yaml') -or @('installation.yaml', 'user.yaml') -contains $File.Name) { continue }
    $Items += @{ name = $File.Name; text = Get-Content $File.FullName -Raw -Encoding UTF8 }
  }
  return $Items
}

function Get-DeployCommand {
  $SearchRoots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA) | Where-Object { $_ }
  foreach ($SearchRoot in $SearchRoots) {
    $RimeRoot = Join-Path $SearchRoot 'Rime'
    if (-not (Test-Path $RimeRoot -PathType Container)) { continue }
    $Executable = Get-ChildItem $RimeRoot -File -Filter 'WeaselDeployer.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Executable) { return @($Executable.FullName, '/deploy') }
  }
  return $null
}

function Invoke-RimeDeploy {
  $Command = @(Get-DeployCommand)
  if ($Command.Count -lt 2) { throw 'Rime deployer was not found.' }
  $Process = Start-Process -FilePath $Command[0] -ArgumentList $Command[1] -WorkingDirectory $Root -Wait -PassThru
  if ($Process.ExitCode -ne 0) { throw "Rime deploy failed with exit code $($Process.ExitCode)." }
  return @{ ok = $true }
}

function Get-RootCustomFiles {
  $FrontendFiles = @('squirrel.custom.yaml', 'weasel.custom.yaml', 'default.custom.yaml')
  $Items = @()
  foreach ($File in Get-ChildItem $Root -File -Filter '*.custom.yaml' -ErrorAction SilentlyContinue | Sort-Object Name) {
    if ($FrontendFiles -contains $File.Name) { continue }
    $Path = Resolve-AllowedPath $File.Name
    $Items += @{ name = $File.Name; text = Get-Content $Path -Raw -Encoding UTF8 }
  }
  return $Items
}

function Get-Backups {
  $BackupDir = Join-Path $Root $BackupRoot
  if (-not (Test-Path $BackupDir -PathType Container)) { return @() }
  $Items = @()
  foreach ($Dir in Get-ChildItem $BackupDir -Directory) {
    $Files = @(Get-ChildItem $Dir.FullName -File | ForEach-Object { $_.Name } | Sort-Object)
    $ManifestPath = Join-Path $Dir.FullName 'manifest.json'
    $Manifest = Read-Manifest $ManifestPath
    $Items += @{ name = $Dir.Name; manifest = $Manifest; availableFiles = $Files }
  }
  return @($Items | Sort-Object name -Descending)
}

function Remove-Backup {
  param([string]$Name)
  if ([string]::IsNullOrWhiteSpace($Name) -or $Name -eq '.' -or $Name -eq '..' -or $Name.Contains('/') -or $Name.Contains('\') -or $Name.Contains([char]0)) {
    throw $AccessDeniedMessage
  }
  $Marker = Resolve-AllowedPath "$BackupRoot/$Name/manifest.json"
  $BackupDir = Split-Path $Marker -Parent
  if (Test-Path $BackupDir -PathType Container) {
    Remove-Item $BackupDir -Recurse -Force
  }
}

function Read-Manifest {
  param([string]$ManifestPath)
  if (-not (Test-Path $ManifestPath -PathType Leaf)) { return $null }
  try {
    return Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  } catch {
    return $null
  }
}

$Listener = $null
$ActualPort = $Port
$PortsToTry = if ($Port -gt 0) { @($Port) } else { 17890..17920 }
foreach ($CandidatePort in $PortsToTry) {
  try {
    $Candidate = [System.Net.HttpListener]::new()
    $Candidate.Prefixes.Add("http://localhost:$CandidatePort/")
    $Candidate.Start()
    $Listener = $Candidate
    $ActualPort = $CandidatePort
    break
  } catch {
    if ($Candidate) { $Candidate.Close() }
  }
}
if (-not $Listener) {
  throw $PortUnavailableMessage
}
$Url = "http://localhost:$ActualPort/?token=$Token"
$LastSeen = Get-Date
Write-Host "$StartedMessage$Url"
Write-Host "$RootMessage$Root"
Write-Host $CloseMessage
Start-Process $Url

while ($Listener.IsListening) {
  $AsyncResult = $Listener.BeginGetContext($null, $null)
  while (-not $AsyncResult.AsyncWaitHandle.WaitOne(500)) {
    if (-not $NoIdleExit -and $IdleTimeoutMs -gt 0 -and ((Get-Date) - $LastSeen).TotalMilliseconds -ge $IdleTimeoutMs) {
      $Listener.Stop()
      break
    }
  }
  if (-not $Listener.IsListening) { break }
  $Context = $Listener.EndGetContext($AsyncResult)
  try {
    $Request = $Context.Request
    $Response = $Context.Response
    $Path = $Request.Url.AbsolutePath
    if ($Path.StartsWith('/api/')) {
      if ($Request.Headers['x-rime-editor-token'] -ne $Token) {
        $QueryToken = Get-QueryValue $Request 'token'
      } else {
        $QueryToken = $Token
      }
      if ($QueryToken -ne $Token) {
        Send-Json $Response 403 @{ error = $InvalidTokenMessage }
        continue
      }
      $LastSeen = Get-Date
      if ($Request.HttpMethod -eq 'POST' -and $Path -eq '/api/ping') {
        Send-Json $Response 200 @{ ok = $true }
        continue
      }
      if ($Request.HttpMethod -eq 'POST' -and $Path -eq '/api/close') {
        Send-Json $Response 200 @{ ok = $true }
        $Listener.Stop()
        break
      }
      if ($Request.HttpMethod -eq 'POST' -and $Path -eq '/api/deploy') {
        Send-Json $Response 200 (Invoke-RimeDeploy)
        continue
      }
      if ($Request.HttpMethod -eq 'GET' -and $Path -eq '/api/config') {
        Send-Json $Response 200 (Get-ConfigSnapshot)
        continue
      }
      if ($Request.HttpMethod -eq 'GET' -and $Path -eq '/api/backups') {
        Send-Json $Response 200 @{ backups = Get-Backups }
        continue
      }
      if ($Request.HttpMethod -eq 'DELETE' -and $Path -eq '/api/backup') {
        Remove-Backup (Get-QueryValue $Request 'name')
        Send-Json $Response 200 @{ ok = $true }
        continue
      }
      if ($Request.HttpMethod -eq 'GET' -and $Path -eq '/api/file') {
        $FilePath = Resolve-AllowedPath (Get-QueryValue $Request 'path')
        if (-not (Test-Path $FilePath -PathType Leaf)) {
          Send-Json $Response 404 @{ error = $MissingFileMessage }
          continue
        }
        Send-Json $Response 200 @{ exists = $true; text = Get-Content $FilePath -Raw -Encoding UTF8 }
        continue
      }
      if ($Request.HttpMethod -eq 'PUT' -and $Path -eq '/api/file') {
        $Body = Read-BodyJson $Request
        $FilePath = Resolve-AllowedPath $Body.path -Write
        New-Item -ItemType Directory -Force -Path (Split-Path $FilePath -Parent) | Out-Null
        Set-Content -Path $FilePath -Value ([string]$Body.text) -Encoding UTF8 -NoNewline
        Send-Json $Response 200 @{ ok = $true }
        continue
      }
      if ($Request.HttpMethod -eq 'DELETE' -and $Path -eq '/api/file') {
        $FilePath = Resolve-AllowedPath (Get-QueryValue $Request 'path') -Write
        if (Test-Path $FilePath -PathType Leaf) { Remove-Item $FilePath -Force }
        Send-Json $Response 200 @{ ok = $true }
        continue
      }
      if ($Request.HttpMethod -eq 'POST' -and $Path -eq '/api/mkdir') {
        $Body = Read-BodyJson $Request
        $Marker = Resolve-AllowedPath "$($Body.path)/manifest.json" -Write
        New-Item -ItemType Directory -Force -Path (Split-Path $Marker -Parent) | Out-Null
        Send-Json $Response 200 @{ ok = $true }
        continue
      }
      Send-Json $Response 404 @{ error = $MissingApiMessage }
      continue
    }

    $Relative = if ($Path -eq '/') { 'index.html' } else { [Uri]::UnescapeDataString($Path.TrimStart('/')) }
    $StaticPath = [System.IO.Path]::GetFullPath((Join-Path $StaticRoot $Relative))
    $StaticRootFull = [System.IO.Path]::GetFullPath($StaticRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    if (($StaticPath -ne $StaticRootFull -and -not $StaticPath.StartsWith($StaticRootFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) -or -not (Test-Path $StaticPath -PathType Leaf)) {
      $Response.StatusCode = 404
      $Response.Close()
      continue
    }
    $Bytes = [System.IO.File]::ReadAllBytes($StaticPath)
    $Response.Headers['cache-control'] = 'no-store'
    $Response.OutputStream.Write($Bytes, 0, $Bytes.Length)
    $Response.Close()
  } catch {
    Send-Json $Context.Response 400 @{ error = $_.Exception.Message }
  }
}
