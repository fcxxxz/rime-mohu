Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$installerSource = Join-Path $root "tiger_sentence_native/install_mohu_llm_windows.ps1"
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("mohu-llm-windows-" + [guid]::NewGuid())
$utf8 = [Text.UTF8Encoding]::new($false)
$powerShell = (Get-Process -Id $PID).Path

# The installer must parse under the edition running this suite (pwsh 7 and
# Windows PowerShell 5.1 in CI); surface parser errors instead of opaque
# child failures.
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $installerSource, [ref]$null, [ref]$parseErrors) | Out-Null
if ($parseErrors -and $parseErrors.Count -gt 0) {
    throw ("install_mohu_llm_windows.ps1 does not parse under " + $PSVersionTable.PSVersion +
        ": " + (($parseErrors | ForEach-Object { "line $($_.Extent.StartLineNumber): $($_.Message)" }) -join "; "))
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Message)
    if ($Expected -ne $Actual) {
        throw "$Message`nExpected: <$Expected>`nActual:   <$Actual>"
    }
}

function Write-TestFile {
    param([string]$Path, [string]$Content)
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    [IO.File]::WriteAllText($Path, $Content, $utf8)
}

function New-TestPackage {
    param([string]$Parent, [string]$Scheme)
    $package = Join-Path $Parent "package-$Scheme"
    New-Item -ItemType Directory -Force -Path $package | Out-Null
    Copy-Item $installerSource (Join-Path $package "install_mohu_llm_windows.ps1")
    Write-TestFile (Join-Path $package "package.json") @"
{
  "package_type": "mohu_llm",
  "scheme": "$Scheme",
  "schema_id": "mohu_llm_$Scheme",
  "base_dir": "base"
}
"@
    Write-TestFile (Join-Path $package "mohu_llm_$Scheme.schema.yaml") "packaged llm schema`n"
    Write-TestFile (Join-Path $package "base/default.yaml") "packaged default`n"
    Write-TestFile (Join-Path $package "base/mohu_$Scheme.schema.yaml") "packaged standard schema`n"
    Write-TestFile (Join-Path $package "base/mohu.yaml") "packaged mohu config`n"
    Write-TestFile (Join-Path $package "base/squirrel.yaml") "packaged squirrel config`n"
    Write-TestFile (Join-Path $package "base/mohu_${Scheme}_custom_phrases.txt") "packaged custom phrases`n"
    Write-TestFile (Join-Path $package "base/mohu_$Scheme.extended.dict.yaml") "packaged extended dictionary`n"
    Write-TestFile (Join-Path $package "base/lua/mohu_processor.lua") "packaged base processor`n"
    Write-TestFile (Join-Path $package "base/lua/four_code_yield_pairs_$Scheme.txt") "packaged base yield pairs`n"
    Write-TestFile (Join-Path $package "lua/mohu_processor.lua") "packaged processor`n"
    Write-TestFile (Join-Path $package "lua/four_code_yield_pairs_$Scheme.txt") "packaged yield pairs`n"
    Write-TestFile (Join-Path $package "lua/mohu_llm_runtime.lua") "return {}`n"
    Write-TestFile (Join-Path $package "runtime/libtigerengine.dll") "test engine`n"
    Write-TestFile (Join-Path $package "runtime/lua54.dll") "test lua runtime`n"
    Write-TestFile (Join-Path $package "data/$Scheme/mohu_llm_$Scheme.lexicon.txt") "测试`tcode`n"
    Write-TestFile (Join-Path $package "data/sentence-ngram-mobile.bin") "test model`n"
    return $package
}

function Invoke-TestInstaller {
    param([string]$Package, [string]$Scheme, [string]$RimeDir)
    $installer = Join-Path $Package "install_mohu_llm_windows.ps1"
    $stdout = Join-Path $Package "child-stdout.log"
    $stderr = Join-Path $Package "child-stderr.log"
    # File-based redirection avoids the 5.1 native stderr pipe/ErrorRecord
    # plumbing entirely, and the watchdog surfaces hangs with partial output
    # instead of stalling the job for hours.
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Scheme {1} -RimeDir "{2}"' -f $installer, $Scheme, $RimeDir
    $process = Start-Process -FilePath $powerShell -ArgumentList $arguments `
        -NoNewWindow -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    if (-not $process.WaitForExit(90000)) {
        $process.Kill()
        $process.WaitForExit()
        $partial = ""
        if (Test-Path $stdout) { $partial += Get-Content $stdout -Raw }
        if (Test-Path $stderr) { $partial += Get-Content $stderr -Raw }
        throw ("installer did not finish within 90s (scheme=$Scheme); partial output: <" + $partial + ">")
    }
    $output = ""
    if (Test-Path $stdout) { $output += (Get-Content $stdout -Raw -ErrorAction SilentlyContinue) }
    if (Test-Path $stderr) { $output += [Environment]::NewLine + (Get-Content $stderr -Raw -ErrorAction SilentlyContinue) }
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $output }
}

function Assert-RegisteredOnce {
    param([string]$Path, [string]$SchemaId)
    $content = Get-Content $Path -Raw -Encoding UTF8
    Assert-Equal 1 ([regex]::Matches($content, [regex]::Escape("schema: $SchemaId")).Count) "$SchemaId must be registered exactly once"
    Assert-Equal 1 ([regex]::Matches($content, [regex]::Escape("schema_list/+")).Count) "schema_list/+ must not be duplicated"
}

New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
try {
    $missingRuntimeRoot = Join-Path $testRoot "missing-runtime"
    $missingRuntimePackage = New-TestPackage $missingRuntimeRoot "zrm"
    Remove-Item (Join-Path $missingRuntimePackage "runtime/lua54.dll")
    Write-Host "case: missing-runtime"
    $missingResult = Invoke-TestInstaller $missingRuntimePackage "zrm" (Join-Path $missingRuntimeRoot "Rime")
    Assert-True ($missingResult.ExitCode -ne 0) ("installer must fail when runtime/lua54.dll is missing; exit=$($missingResult.ExitCode) output=<$($missingResult.Output)>")
    Assert-True ($missingResult.Output -match "lua54\.dll") ("missing-runtime error must name lua54.dll; output=<$($missingResult.Output)>")

    foreach ($scheme in @("zrm", "flypy")) {
        $upgradeRoot = Join-Path $testRoot "upgrade-$scheme"
        $package = New-TestPackage $upgradeRoot $scheme
        $rime = Join-Path $upgradeRoot "Rime"
        $preserved = [ordered]@{
            "default.yaml" = "user default`n"
            "mohu.yaml" = "user mohu config`n"
            "mohu_${scheme}_custom_phrases.txt" = "user custom phrases`n"
            "mohu_$scheme.extended.dict.yaml" = "user extended dictionary`n"
            "lua/mohu_processor.lua" = "user processor`n"
            "lua/four_code_yield_pairs_$scheme.txt" = "user yield pairs`n"
        }
        foreach ($relative in $preserved.Keys) {
            Write-TestFile (Join-Path $rime $relative) $preserved[$relative]
        }
        Write-TestFile (Join-Path $rime "mohu_$scheme.schema.yaml") "stale standard schema`n"
        Write-TestFile (Join-Path $rime "squirrel.yaml") "stale squirrel config`n"

        foreach ($attempt in 1..2) {
            Write-Host "case: upgrade-$scheme attempt=$attempt"
            $result = Invoke-TestInstaller $package $scheme $rime
            Assert-Equal 0 $result.ExitCode "$scheme upgrade attempt $attempt failed: $($result.Output)"
        }
        foreach ($relative in $preserved.Keys) {
            Assert-Equal $preserved[$relative] (Get-Content (Join-Path $rime $relative) -Raw -Encoding UTF8) "$scheme upgrade replaced user file $relative"
        }
        Assert-Equal "packaged standard schema`n" (Get-Content (Join-Path $rime "mohu_$scheme.schema.yaml") -Raw -Encoding UTF8) "$scheme standard schema was not updated"
        Assert-Equal "packaged squirrel config`n" (Get-Content (Join-Path $rime "squirrel.yaml") -Raw -Encoding UTF8) "$scheme squirrel.yaml was not updated"
        Assert-RegisteredOnce (Join-Path $rime "default.custom.yaml") "mohu_llm_$scheme"
    }

    $schemaCases = @(
        @{ Name = "one-line-patch"; Content = "patch:" },
        @{ Name = "empty-list-eof"; Content = "patch:`n  schema_list/+:" },
        @{ Name = "existing-item-eof"; Content = "patch:`n  schema_list/+:`n    - schema: existing" },
        @{ Name = "block-flow-empty"; Content = "patch:`n  schema_list/+: []" },
        @{ Name = "block-flow-spaced-empty"; Content = "patch:`n  schema_list/+: [ ]" },
        @{ Name = "block-flow-item-comment"; Content = "patch:`n  schema_list/+: [{schema: existing}] # keep" },
        @{ Name = "trailing-block-comment"; Content = "patch:`n  schema_list/+:`n    - schema: existing`n  menu: {page_size: 9} # keep" },
        @{ Name = "inline-empty-comment"; Content = "patch: {schema_list/+: []} # keep" },
        @{ Name = "inline-item-comment"; Content = "patch: {schema_list/+: [{schema: existing}]} # keep" },
        @{ Name = "inline-empty-map-comment"; Content = "patch: {} # keep" },
        @{ Name = "inline-other-key-comment"; Content = "patch: {menu: {page_size: 9}} # keep" }
    )
    foreach ($scheme in @("zrm", "flypy")) {
        foreach ($case in $schemaCases) {
            $caseRoot = Join-Path $testRoot "schema-$scheme-$($case.Name)"
            $package = New-TestPackage $caseRoot $scheme
            $rime = Join-Path $caseRoot "Rime"
            $custom = Join-Path $rime "default.custom.yaml"
            Write-TestFile $custom $case.Content
            foreach ($attempt in 1..2) {
                Write-Host "case: $scheme/$($case.Name) attempt=$attempt"
                $result = Invoke-TestInstaller $package $scheme $rime
                Assert-Equal 0 $result.ExitCode "$scheme/$($case.Name) attempt $attempt failed: $($result.Output)"
            }
            Assert-RegisteredOnce $custom "mohu_llm_$scheme"
            $updated = Get-Content $custom -Raw -Encoding UTF8
            Assert-True ($updated -notmatch "schema_list/\+:\s*\[\]\s*\r?\n\s+-\s+schema:") "$scheme/$($case.Name) left an empty flow list before a block item"
            Assert-True ($updated -notmatch "patch:\s*\{\s*,") "$scheme/$($case.Name) inserted a leading flow-map comma"
            if ($case.Content -match "schema: existing") {
                Assert-Equal 1 ([regex]::Matches($updated, "schema: existing").Count) "$scheme/$($case.Name) duplicated existing schema"
            }
            if ($case.Content -match "# keep") {
                Assert-True ($updated.Contains("# keep")) "$scheme/$($case.Name) dropped trailing comment"
            }
            if ($case.Content -match "menu:") {
                Assert-True ($updated.Contains("menu: {page_size: 9}")) "$scheme/$($case.Name) dropped existing patch"
            }
        }
    }

    Write-Output "WINDOWS_INSTALLER_TESTS_OK"
} finally {
    Remove-Item $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
