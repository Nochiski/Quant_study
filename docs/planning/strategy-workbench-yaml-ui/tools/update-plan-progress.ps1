[CmdletBinding()]
param(
    [switch]$Check,
    [string]$PlanPath
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($PlanPath)) {
    $PlanPath = Join-Path (Split-Path $PSScriptRoot -Parent) "PLAN.md"
}

$resolvedPlanPath = (Resolve-Path -LiteralPath $PlanPath).Path
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$original = [System.IO.File]::ReadAllText($resolvedPlanPath, $utf8NoBom)

$prIdPattern = 'P\d+(?:\.\d+)?-\d{2}'
$rowPattern = "(?m)^\| \[(?<checked>[ xX])\] \| ``(?<id>$prIdPattern)`` \| (?<title>.*?) \| (?<dependency>.*?) \| ``(?<status>[A-Z_]+)`` \| (?<review>.*?) \|[ \t\r]*$"
$rowMatches = [regex]::Matches($original, $rowPattern)

if ($rowMatches.Count -eq 0) {
    throw "No PR tracker rows found in $resolvedPlanPath"
}

$rows = foreach ($match in $rowMatches) {
    [pscustomobject]@{
        Id = $match.Groups["id"].Value
        Phase = $match.Groups["id"].Value.Substring(
            0,
            $match.Groups["id"].Value.LastIndexOf("-")
        )
        Checked = $match.Groups["checked"].Value -match '[xX]'
        Dependency = $match.Groups["dependency"].Value
        Status = $match.Groups["status"].Value
    }
}

$duplicateIds = @($rows | Group-Object Id | Where-Object Count -gt 1)
if ($duplicateIds.Count -gt 0) {
    throw "Duplicate PR IDs: $($duplicateIds.Name -join ', ')"
}

$allowedStatuses = @(
    "PLANNED",
    "READY",
    "WAITING",
    "IN_PROGRESS",
    "SELF_CHECK",
    "IN_REVIEW",
    "CHANGES_REQUESTED",
    "APPROVED",
    "MERGED",
    "PAUSED"
)

$unknownStatuses = @($rows | Where-Object Status -notin $allowedStatuses)
if ($unknownStatuses.Count -gt 0) {
    throw "Unknown PR status: $(($unknownStatuses | ForEach-Object { "$($_.Id)=$($_.Status)" }) -join ', ')"
}

$checkboxMismatches = @(
    $rows | Where-Object {
        ($_.Checked -and $_.Status -ne "MERGED") -or
        (-not $_.Checked -and $_.Status -eq "MERGED")
    }
)
if ($checkboxMismatches.Count -gt 0) {
    throw "Checkbox/status mismatch: $($checkboxMismatches.Id -join ', ')"
}

$activeStatuses = @(
    "IN_PROGRESS",
    "SELF_CHECK",
    "IN_REVIEW",
    "CHANGES_REQUESTED",
    "APPROVED"
)

# An active stack is a concurrency set, not a priority-ordered list. Aggregate by explicit
# workflow severity so reordering parallel_window can never hide a requested change.
$activeStatusPriority = @(
    "CHANGES_REQUESTED",
    "IN_PROGRESS",
    "SELF_CHECK",
    "IN_REVIEW",
    "APPROVED"
)

function Get-AggregateActiveStatus {
    param([object[]]$CandidateRows)

    foreach ($status in $activeStatusPriority) {
        if (@($CandidateRows | Where-Object Status -eq $status).Count -gt 0) {
            return $status
        }
    }
    return $null
}

$activeRows = @($rows | Where-Object Status -in $activeStatuses)

$parallelMatch = [regex]::Match($original, '(?m)^parallel_window:\s*\[(?<ids>[^\]]*)\]\s*$')
if (-not $parallelMatch.Success) {
    throw "parallel_window frontmatter field is missing"
}
$parallelIds = @(
    $parallelMatch.Groups["ids"].Value.Split(",") |
        ForEach-Object { $_.Trim().Trim('"').Trim("'") } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)

# More than two active PRs are allowed only as a declared stack (parallel_window lists every one).
if ($activeRows.Count -gt 2 -and $parallelIds.Count -lt $activeRows.Count) {
    throw "At most two active PRs are allowed unless parallel_window declares the stack; found $($activeRows.Count)"
}
if ($activeRows.Count -gt 1) {
    $activeIds = @($activeRows.Id | Sort-Object)
    $declaredParallelIds = @($parallelIds | Sort-Object)
    if (($activeIds -join ",") -ne ($declaredParallelIds -join ",")) {
        throw "Multiple active PRs must exactly match parallel_window"
    }
}

$phaseGoals = [ordered]@{
    "P0" = "Contract, product direction, tool choices"
    "P1" = "Backend Authoring Contract"
    "P1.5" = "Backtest Correctness Gate"
    "P2" = "App Shell and visual foundation"
    "P3" = "YAML Editor MVP"
    "P4" = "Outline, Contract, Projections"
    "P5" = "Truthful Trace UI"
    "P6" = "Professional release and migration"
}

$unknownPhases = @($rows | Where-Object Phase -notin $phaseGoals.Keys)
if ($unknownPhases.Count -gt 0) {
    throw "Unknown phases in PR tracker: $(($unknownPhases | ForEach-Object { "$($_.Id)=$($_.Phase)" }) -join ', ')"
}

$rowById = @{}
foreach ($row in $rows) {
    $rowById[$row.Id] = $row
}

foreach ($row in $rows) {
    $dependencyIds = @(
        [regex]::Matches($row.Dependency, $prIdPattern) |
            ForEach-Object { $_.Value } |
            Select-Object -Unique
    )
    $missingDependencyIds = @($dependencyIds | Where-Object { -not $rowById.ContainsKey($_) })
    if ($missingDependencyIds.Count -gt 0) {
        throw "$($row.Id) has unknown dependencies: $($missingDependencyIds -join ', ')"
    }
    if ($row.Status -eq "READY") {
        $unmergedDependencyIds = @(
            $dependencyIds | Where-Object { $rowById[$_].Status -ne "MERGED" }
        )
        if ($unmergedDependencyIds.Count -gt 0) {
            throw "$($row.Id) is READY but dependencies are not MERGED: $($unmergedDependencyIds -join ', ')"
        }
    }
}

$total = $rows.Count
$merged = @($rows | Where-Object Checked).Count
$approved = @($rows | Where-Object Status -in @("APPROVED", "MERGED")).Count
$progress = if ($total -eq 0) { 0 } else { [math]::Round(($merged * 100.0) / $total) }
$orderedActiveRows = @(
    foreach ($id in $parallelIds) {
        if ($rowById.ContainsKey($id) -and $rowById[$id].Status -in $activeStatuses) {
            $rowById[$id]
        }
    }
)
$aggregateActiveStatus = Get-AggregateActiveStatus -CandidateRows $activeRows

if ($merged -eq $total) {
    $projectStatus = "COMPLETE"
} elseif ($null -ne $aggregateActiveStatus) {
    $projectStatus = $aggregateActiveStatus
} elseif (@($rows | Where-Object Status -eq "READY").Count -gt 0) {
    $projectStatus = "READY"
} elseif (@($rows | Where-Object Status -eq "PAUSED").Count -gt 0) {
    $projectStatus = "PAUSED"
} else {
    $projectStatus = "WAITING"
}

$currentRows = if ($orderedActiveRows.Count -gt 0) {
    $orderedActiveRows
} elseif ($activeRows.Count -gt 0) {
    $activeRows
} else {
    $readyRows = @($rows | Where-Object Status -eq "READY")
    if ($readyRows.Count -gt 0) {
        @($readyRows[0])
    } else {
        @($rows | Where-Object Status -ne "MERGED" | Select-Object -First 1)
    }
}

$currentPr = if ($currentRows.Count -eq 0) { "none" } else { $currentRows.Id -join "," }
$currentPhase = if ($currentRows.Count -eq 0) {
    "complete"
} else {
    @($currentRows.Phase | Select-Object -Unique) -join ","
}
$markdownTick = [char]96
$activePrText = if ($activeRows.Count -eq 0) {
    "none"
} else {
    "$markdownTick$($activeRows.Id -join ', ')$markdownTick"
}

$timestampMatch = [regex]::Match($original, '(?m)^last_updated:\s*(?<value>\S+)\s*$')
if (-not $timestampMatch.Success) {
    throw "last_updated frontmatter field is missing"
}

if ($Check) {
    $isoTimestamp = $timestampMatch.Groups["value"].Value
    $timestamp = [DateTimeOffset]::Parse($isoTimestamp)
} else {
    try {
        $koreaTimeZone = [TimeZoneInfo]::FindSystemTimeZoneById("Korea Standard Time")
    } catch {
        $koreaTimeZone = [TimeZoneInfo]::FindSystemTimeZoneById("Asia/Seoul")
    }
    $koreaNow = [TimeZoneInfo]::ConvertTime([DateTimeOffset]::UtcNow, $koreaTimeZone)
    $timestamp = $koreaNow
    $isoTimestamp = $koreaNow.ToString("yyyy-MM-ddTHH:mm:sszzz")
}
$displayTimestamp = $timestamp.ToString("yyyy-MM-dd HH:mm 'KST'")

$activeFrontmatter = if ($activeRows.Count -eq 0) {
    "[]"
} else {
    "[$($activeRows.Id -join ', ')]"
}

$updated = $original
$frontmatterValues = [ordered]@{
    "project_status" = $projectStatus
    "current_phase" = $currentPhase
    "current_pr" = $currentPr
    "active_prs" = $activeFrontmatter
    "last_updated" = $isoTimestamp
    "planned_prs" = $total
    "merged_prs" = $merged
    "approved_prs" = $approved
    "progress_percent" = $progress
}

foreach ($entry in $frontmatterValues.GetEnumerator()) {
    $pattern = "(?m)^$([regex]::Escape($entry.Key)):\s*.*$"
    if (-not [regex]::IsMatch($updated, $pattern)) {
        throw "$($entry.Key) frontmatter field is missing"
    }
    $updated = [regex]::Replace(
        $updated,
        $pattern,
        "$($entry.Key): $($entry.Value)",
        1
    )
}

$summaryLines = @(
    "<!-- PLAN:SUMMARY:START -->",
    "| Field | Value |",
    "|---|---|",
    "| Project status | ``$projectStatus`` |",
    "| Current phase | ``$currentPhase`` |",
    "| Current/next PR | ``$currentPr`` |",
    "| Active PR | $activePrText |",
    "| Progress | ``$merged / $total merged ($progress%)`` |",
    "| Approved | ``$approved / $total`` |",
    "| Aggregated at | ``$displayTimestamp`` |",
    "<!-- PLAN:SUMMARY:END -->"
)
$summary = $summaryLines -join "`n"
$summaryPattern = '(?s)<!-- PLAN:SUMMARY:START -->.*?<!-- PLAN:SUMMARY:END -->'
if (-not [regex]::IsMatch($updated, $summaryPattern)) {
    throw "Summary markers are missing"
}
$updated = [regex]::Replace($updated, $summaryPattern, $summary, 1)

$phaseLines = [System.Collections.Generic.List[string]]::new()
$phaseLines.Add("<!-- PLAN:PHASES:START -->")
$phaseLines.Add("| Phase | Goal | PR | Merged | Status |")
$phaseLines.Add("|---|---|---:|---:|---|")

foreach ($phase in $phaseGoals.Keys) {
    $phaseRows = @($rows | Where-Object Phase -eq $phase)
    if ($phaseRows.Count -eq 0) {
        throw "No tracker rows found for phase $phase"
    }
    $phaseMerged = @($phaseRows | Where-Object Checked).Count
    $phaseActive = @($phaseRows | Where-Object Status -in $activeStatuses)
    if ($phaseMerged -eq $phaseRows.Count) {
        $phaseStatus = "MERGED"
    } elseif ($phaseActive.Count -gt 0) {
        $phaseStatus = Get-AggregateActiveStatus -CandidateRows $phaseActive
    } elseif (@($phaseRows | Where-Object Status -eq "READY").Count -gt 0) {
        $phaseStatus = "READY"
    } elseif (@($phaseRows | Where-Object Status -eq "PAUSED").Count -gt 0) {
        $phaseStatus = "PAUSED"
    } else {
        $phaseStatus = "WAITING"
    }
    $phaseLines.Add("| $phase | $($phaseGoals[$phase]) | $($phaseRows.Count) | $phaseMerged | ``$phaseStatus`` |")
}

$phaseLines.Add("| **Total** |  | **$total** | **$merged** | **$progress%** |")
$phaseLines.Add("<!-- PLAN:PHASES:END -->")
$phaseSummary = $phaseLines -join "`n"
$phasePattern = '(?s)<!-- PLAN:PHASES:START -->.*?<!-- PLAN:PHASES:END -->'
if (-not [regex]::IsMatch($updated, $phasePattern)) {
    throw "Phase summary markers are missing"
}
$updated = [regex]::Replace($updated, $phasePattern, $phaseSummary, 1)

if ($Check) {
    if ($updated -cne $original) {
        throw "PLAN.md aggregates are stale. Run update-plan-progress.ps1 without -Check."
    }
    Write-Output "PLAN.md is consistent: $merged/$total merged, $approved approved."
    exit 0
}

if ($updated -cne $original) {
    [System.IO.File]::WriteAllText($resolvedPlanPath, $updated, $utf8NoBom)
}

Write-Output "Updated PLAN.md: $merged/$total merged, $approved approved, status=$projectStatus."
