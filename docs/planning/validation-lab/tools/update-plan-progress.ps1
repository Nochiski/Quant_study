[CmdletBinding()]
param(
    [switch]$Check,
    [string]$PlanPath
)

$ErrorActionPreference = "Stop"

# 계산 정책은 공용 Python 도구 하나가 소유한다. 기존 Windows 호출 인자만 변환한다.
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "../../../..")).Path
if ([string]::IsNullOrWhiteSpace($PlanPath)) {
    $PlanPath = Join-Path (Split-Path $PSScriptRoot -Parent) "PLAN.md"
}
$arguments = @("run", "--locked", "--project", $repoRoot, "python", "-m", "quant_study_dev.plan_progress",
    "--profile", "validation-lab", "--plan-path", $PlanPath)
if ($Check) {
    $arguments += "--check"
}
& uv @arguments
exit $LASTEXITCODE
