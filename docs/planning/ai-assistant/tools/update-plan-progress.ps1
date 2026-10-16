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
# PATH의 Python 하나만 사용한다. 패키지 설치나 다른 실행기로 폴백하지 않는다.
& python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 'Python 3.11+ is required; got ' + sys.version.split()[0])"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
$scriptPath = Join-Path $repoRoot "tools/quant_study_dev/plan_progress.py"
$arguments = @("-X", "utf8", $scriptPath,
    "--profile", "ai-assistant", "--plan-path", $PlanPath)
if ($Check) {
    $arguments += "--check"
}
& python @arguments
exit $LASTEXITCODE
