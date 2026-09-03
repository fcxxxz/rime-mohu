# Shared installer for the mohu_llm scheme packages on Windows (Weasel).
# Windows ships the native v5 engine only; the optional Qwen neural reranker
# is macOS-only and intentionally not installed here.
param(
    [ValidateSet("zrm", "flypy")] [string]$Scheme = $(if ($env:MOHU_LLM_SCHEME) { $env:MOHU_LLM_SCHEME } else { "zrm" }),
    [string]$RimeDir = $(if ($env:MOHU_RIME_DIR) { $env:MOHU_RIME_DIR } else { Join-Path $env:APPDATA "Rime" })
)
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$schemaId = "mohu_llm_$Scheme"
$schemaFile = "$schemaId.schema.yaml"
$manifest = Join-Path $scriptDir "package.json"
if (-not (Test-Path $manifest)) { $manifest = Join-Path $scriptDir "mohu_llm_$Scheme.package.json" }

foreach ($rel in @(
    $schemaFile,
    "base",
    "base/default.yaml",
    "base/mohu_$Scheme.schema.yaml",
    "lua",
    "runtime",
    "runtime/libtigerengine.dll",
    "runtime/lua54.dll",
    "data/$Scheme",
    "data/sentence-ngram-mobile.bin"
)) {
    if (-not (Test-Path (Join-Path $scriptDir $rel))) {
        Write-Error "missing package file: $rel" -ErrorAction Continue; exit 1
    }
}
if (-not (Test-Path $manifest)) { Write-Error "missing package manifest" -ErrorAction Continue; exit 1 }
$lexicon = Join-Path $scriptDir "data/$Scheme/mohu_llm_$Scheme.lexicon.txt"
if (-not (Test-Path $lexicon)) { Write-Error "missing lexicon: $lexicon" -ErrorAction Continue; exit 1 }

# The native engine binds to the bundled runtime/lua54.dll while the host
# weasel's rime.dll embeds its own Lua; both must be the same 5.4.x version
# or strings returned by the engine are corrupted and the sentence translator
# silently falls back to dictionary candidates.
$bundledLua = $null
$weaselLua = $null
$bundledDll = Join-Path $scriptDir "runtime/lua54.dll"
if (Test-Path $bundledDll) {
    $m = [regex]::Match([Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($bundledDll)),
        '\$LuaVersion: Lua (5\.\d+\.\d+)')
    if ($m.Success) { $bundledLua = $m.Groups[1].Value }
}
$server = Get-Process WeaselServer -ErrorAction SilentlyContinue | Select-Object -First 1
$weaselRime = $null
if ($server -and $server.Path) {
    $candidate = Join-Path (Split-Path -Parent $server.Path) "rime.dll"
    if (Test-Path $candidate) { $weaselRime = $candidate }
}
if (-not $weaselRime) {
    $weaselRime = Get-ChildItem "$env:ProgramFiles\Rime", "${env:ProgramFiles(x86)}\Rime" `
        -Filter "rime.dll" -Recurse -Depth 2 -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if ($weaselRime) {
    $m = [regex]::Match([Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($weaselRime)),
        '\$LuaVersion: Lua (5\.\d+\.\d+)')
    if ($m.Success) { $weaselLua = $m.Groups[1].Value }
}
if ($bundledLua -and $weaselLua) {
    if ($bundledLua -ne $weaselLua) {
        Write-Warning ("Lua 版本不匹配: 安装包 lua54.dll=Lua {0}, 小狼毫 rime.dll=Lua {1}; " +
            "整句引擎将不可用,请升级小狼毫至内嵌 Lua {0} 的版本" -f $bundledLua, $weaselLua)
    } else {
        Write-Host "Lua runtime check: bundled=Lua $bundledLua weasel=Lua $weaselLua (matched)"
    }
}

$manifestText = Get-Content $manifest -Raw -Encoding UTF8
foreach ($needle in @('"package_type": "mohu_llm"', "`"scheme`": `"$Scheme`"", "`"schema_id`": `"$schemaId`"", '"base_dir": "base"')) {
    if (-not $manifestText.Contains($needle)) { Write-Error "manifest mismatch: $needle" -ErrorAction Continue; exit 1 }
}

$userMaintained = @{
    "default.yaml" = $true
    "mohu.yaml" = $true
    "mohu_${Scheme}_custom_phrases.txt" = $true
    "mohu_$Scheme.extended.dict.yaml" = $true
    "lua/mohu_processor.lua" = $true
    "lua/four_code_yield_pairs_$Scheme.txt" = $true
}
$targets = @(
    @{ Source = Join-Path $scriptDir "base"; Destination = $RimeDir; PreserveUserFiles = $true; RelativePrefix = "" },
    @{ Source = Join-Path $scriptDir $schemaFile; Destination = Join-Path $RimeDir $schemaFile },
    @{ Source = Join-Path $scriptDir "lua"; Destination = Join-Path $RimeDir "lua"; PreserveUserFiles = $true; RelativePrefix = "lua/" },
    @{ Source = Join-Path $scriptDir "runtime"; Destination = Join-Path $RimeDir "mohu_llm/runtime" },
    @{ Source = Join-Path $scriptDir "data/$Scheme"; Destination = Join-Path $RimeDir "mohu_llm/data/$Scheme" },
    @{ Source = Join-Path $scriptDir "data/sentence-ngram-mobile.bin"; Destination = Join-Path $RimeDir "mohu_llm/data/sentence-ngram-mobile.bin" }
)
$modelsDir = Join-Path $scriptDir "models"
if (Test-Path $modelsDir) {
    $targets += @{ Source = $modelsDir; Destination = Join-Path $RimeDir "mohu_llm/models" }
}

foreach ($target in $targets) {
    if ($target.Source -like "*.*" -and (Test-Path $target.Source -PathType Leaf)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target.Destination) | Out-Null
        Copy-Item $target.Source $target.Destination -Force
    } else {
        Get-ChildItem $target.Source -Recurse -File | Where-Object {
            $_.FullName -notmatch "__pycache__" -and $_.Extension -ne ".pyc" -and $_.Extension -ne ".command"
        } | ForEach-Object {
            $relative = $_.FullName.Substring($target.Source.Length).TrimStart("\", "/")
            $destination = Join-Path $target.Destination $relative
            $managedRelative = (($target.RelativePrefix + $relative) -replace "\\", "/")
            $preserve = $target.PreserveUserFiles -and
                $userMaintained.ContainsKey($managedRelative) -and
                (Test-Path $destination)
            if (-not $preserve) {
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
                Copy-Item $_.FullName $destination -Force
            }
        }
    }
}

# Register the schema in default.custom.yaml, preserving existing patches.
$custom = Join-Path $RimeDir "default.custom.yaml"
$entryLine = "    - schema: $schemaId"
if (Test-Path $custom) {
    $lines = @(Get-Content $custom -Encoding UTF8)
    $body = ($lines | Where-Object { $_ -notmatch "^\s*#" }) -join "`n"
    if ($body -match "(?m)^[\s]*-[\s]*schema:[\s]*$schemaId([\s}]|,|$)" -or
        $body -match "(?m)[{,][\s]*schema:[\s]*$schemaId([\s}]|,|$)") {
        Write-Output "schema $schemaId already registered"
    } elseif ($lines -match "^\s*schema_list/\+:") {
        $listIndex = 0
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match "^\s*schema_list/\+:") { $listIndex = $i; break }
        }
        $listIndent = (($lines[$listIndex] -replace "^(\s*).*", '$1')).Length
        if ($lines[$listIndex] -match "schema_list/\+:\s*\[\s*\]") {
            $lines[$listIndex] = $lines[$listIndex] -replace
                "schema_list/\+:\s*\[\s*\]", "schema_list/+: [{schema: $schemaId}]"
            [System.IO.File]::WriteAllLines($custom, $lines)
        } elseif ($lines[$listIndex] -match "schema_list/\+:\s*\[") {
            $lines[$listIndex] = $lines[$listIndex] -replace
                "\](\s*(?:#.*)?)$", ", {schema: $schemaId}]`$1"
            [System.IO.File]::WriteAllLines($custom, $lines)
        } else {
            $insertAt = $lines.Count
            for ($j = $listIndex + 1; $j -lt $lines.Count; $j++) {
                $line = $lines[$j]
                if ($line -match "^\s*#" -or $line.Trim() -eq "") { continue }
                $indent = (($line -replace "^(\s*).*", '$1')).Length
                if ($indent -le $listIndent -and $line -notmatch "^\s*-") { $insertAt = $j; break }
                if ($line -match "^\s*-\s*schema:") { $insertAt = $j + 1; continue }
                if ($indent -le $listIndent) { $insertAt = $j; break }
                $insertAt = $j + 1
            }
            $newLines = New-Object System.Collections.Generic.List[string]
            for ($i = 0; $i -lt $insertAt; $i++) { $newLines.Add($lines[$i]) }
            $newLines.Add($entryLine)
            for ($i = $insertAt; $i -lt $lines.Count; $i++) { $newLines.Add($lines[$i]) }
            [System.IO.File]::WriteAllLines($custom, $newLines)
        }
    } elseif ($lines -match "^patch:\s*\{") {
        if ($lines -match "schema_list/\+:") {
            $newLines = $lines | ForEach-Object {
                if ($_ -match "schema_list/\+:\s*\[\s*\]") {
                    $_ -replace "schema_list/\+:\s*\[\s*\]", "schema_list/+: [{schema: $schemaId}]"
                } elseif ($_ -match "schema_list/\+:") {
                    $_ -replace "\](\s*\}\s*(?:#.*)?)$", ", {schema: $schemaId}]`$1"
                } else { $_ }
            }
            [System.IO.File]::WriteAllLines($custom, $newLines)
        } else {
            $newLines = $lines | ForEach-Object {
                if ($_ -match "^(\s*patch:\s*\{)(.*)\}(\s*(?:#.*)?)$") {
                    $prefix = $Matches[1]
                    $content = $Matches[2]
                    $suffix = $Matches[3]
                    $separator = if ($content.Trim()) { ", " } else { "" }
                    "$prefix$content${separator}schema_list/+: [{schema: $schemaId}]}$suffix"
                } else { $_ }
            }
            [System.IO.File]::WriteAllLines($custom, $newLines)
        }
    } elseif ($lines -match "^patch:\s*(?:#.*)?$") {
        $newLines = New-Object System.Collections.Generic.List[string]
        $added = $false
        foreach ($line in $lines) {
            $newLines.Add($line)
            if (-not $added -and $line -match "^patch:\s*(?:#.*)?$") {
                $newLines.Add("  schema_list/+:")
                $newLines.Add($entryLine)
                $added = $true
            }
        }
        [System.IO.File]::WriteAllLines($custom, $newLines)
    } else {
        Add-Content $custom "`npatch:`n  schema_list/+:`n$entryLine" -Encoding UTF8
    }
} else {
    New-Item -ItemType Directory -Force -Path $RimeDir | Out-Null
    [System.IO.File]::WriteAllLines($custom, @("patch:", "  schema_list/+:", $entryLine))
}

Write-Output "mohu_llm_$Scheme installed to $RimeDir"
Write-Output "Weasel: run 'Rime / IME settings' -> 'Re-deploy' (重新部署), then select the new schema."
