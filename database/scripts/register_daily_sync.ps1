# 매일 한 번 `ledger_sync sync` 를 도는 Windows 작업 스케줄러 항목을 등록·해제한다.
#
#   database\scripts\register_daily_sync.ps1                 # 등록 (기본 10:00 KST — 아침 확정판 m_ 09:20 뒤)
#   database\scripts\register_daily_sync.ps1 -At 23:30       # 저녁 잠정판(22:30 KST 전후)도 따라가려면 하나 더
#   database\scripts\register_daily_sync.ps1 -Remove
#
# 서버는 아침 확정판을 00:19 UTC(09:19 KST) 전후, 저녁 잠정판을 13:30 UTC(22:30 KST) 전후에 커밋한다
# (ledger_sync/__main__.py BUILD_WINDOWS_UTC). 그 창을 피해 기본 10:00 으로 둔다.
# 결과는 `<root>\equity\_sync\last_run.json`, 로그는 `<root>\equity\_sync\logs\`. 확인은
# `ledger_sync.ps1 status --remote`.
param(
    [string]$At = "10:00",
    [string]$TaskName = "QuantLedgerSync",
    [switch]$Remove
)
$ErrorActionPreference = "Stop"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "removed scheduled task $TaskName"
    exit 0
}
$wrapper = Join-Path $PSScriptRoot "ledger_sync.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`" sync" `
    -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "카엘 서버 equity 층 일일 동기화 (ledger_sync sync)" -Force | Out-Null
Write-Host "registered scheduled task $TaskName daily at $At -> $wrapper sync"
