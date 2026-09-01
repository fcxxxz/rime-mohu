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

foreach ($rel in @($schemaFile, "lua", "runtime", "data/$Scheme")) {
    if (-not (Test-Path (Join-Path $scriptDir $rel))) {
        Write-Error "missing package file: $rel"; exit 1
    }
}
if (-not (Test-Path $manifest)) { Write-Error "missing package manifest"; exit 1 }
if (-not (Test-Path (Join-Path $scriptDir "runtime/libtigerengine.dll"))) {
    Write-Error "missing runtime/libtigerengine.dll"; exit 1
}
if (-not (Test-Path (Join-Path $scriptDir "data/sentence-ngram-mobile.bin"))) {
    Write-Error "missing data/sentence-ngram-mobile.bin"; exit 1
}
$lexicon = Join-Path $scriptDir "data/$Scheme/mohu_llm_$Scheme.lexicon.txt"
if (-not (Test-Path $lexicon)) { Write-Error "missing lexicon: $lexicon"; exit 1 }

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
foreach ($needle in @('"package_type": "mohu_llm"', "`"scheme`": `"$Scheme`"", "`"schema_id`": `"$schemaId`"")) {
    if (-not $manifestText.Contains($needle)) { Write-Error "manifest mismatch: $needle"; exit 1 }
}

$targets = @(
    @{ Source = Join-Path $scriptDir $schemaFile; Destination = Join-Path $RimeDir $schemaFile },
    @{ Source = Join-Path $scriptDir "lua"; Destination = Join-Path $RimeDir "lua" },
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
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
            Copy-Item $_.FullName $destination -Force
        }
    }
}

# Register the schema in default.custom.yaml, preserving existing patches.
$custom = Join-Path $RimeDir "default.custom.yaml"
$entryLine = "    - schema: $schemaId"
if (Test-Path $custom) {
    $lines = Get-Content $custom -Encoding UTF8
    $body = ($lines | Where-Object { $_ -notmatch "^\s*#" }) -join "`n"
    if ($body -match "(?m)^[\s]*-[\s]*schema:[\s]*$schemaId([\s}]|,|$)" -or
        $body -match "(?m)[{,][\s]*schema:[\s]*$schemaId([\s}]|,|$)") {
        Write-Output "schema $schemaId already registered"
    } elseif ($lines -match "^\s*schema_list/\+:") {
        $listIndent = ($lines | Where-Object { $_ -match "^\s*schema_list/\+:" } | Select-Object -First 1) -replace "^(\s*).*", '$1'
        $insertAt = $lines.Count
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match "^\s*schema_list/\+:") {
                for ($j = $i + 1; $j -lt $lines.Count; $j++) {
                    $line = $lines[$j]
                    if ($line -match "^\s*#" -or $line.Trim() -eq "") { continue }
                    $indent = ($line -replace "^( *).*", '$1').Length
                    if ($indent -le $listIndent.Length -and $line -notmatch "^\s*-") { $insertAt = $j; break }
                    if ($line -match "^\s*-\s*schema:") { $insertAt = $j + 1; continue }
                    if ($indent -le $listIndent.Length) { break }
                    $insertAt = $j + 1
                }
                break
            }
        }
        $newLines = $lines[0..($insertAt - 1)] + $entryLine + $lines[$insertAt..($lines.Count - 1)]
        [System.IO.File]::WriteAllLines($custom, $newLines)
    } elseif ($lines -match "^patch:\s*\{") {
        if ($lines -match "schema_list/\+:") {
            $newLines = $lines | ForEach-Object {
                if ($_ -match "schema_list/\+:\s*\[\]") {
                    $_ -replace "schema_list/\+:\s*\[\]", "schema_list/+: [{schema: $schemaId}]"
                } elseif ($_ -match "schema_list/\+:") {
                    $_ -replace "\]\s*$", ", {schema: $schemaId}]"
                } else { $_ }
            }
            [System.IO.File]::WriteAllLines($custom, $newLines)
        } else {
            $updated = ($lines -join "`n") -replace "^(patch:\s*\{)(.*)\}(\s*(#.*)?)$", "`$1`$2, schema_list/+: [{schema: $schemaId}]`}`$3"
            [System.IO.File]::WriteAllText($custom, $updated)
        }
    } elseif ($lines -match "^patch:") {
        $newLines = @("patch:", "  schema_list/+:") + $entryLine + $lines[1..($lines.Count - 1)]
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
