# Keep the bench's long-lived helpers alive.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools/bench-keep.ps1 -Minutes 480
#
# Why this exists
# ---------------
# Two things have to be running for this board to come back from a cold boot with a
# working data plane:
#
#   * tools/serrelay.ps1 - the only holder of COM3.  It exits on its own after
#     -Seconds (3300 s by default: it has to stop before the runtime's one hour
#     ceiling kills the task that started it).  When it is gone the next cold boot
#     is not caught at the U-Boot prompt, the vendor's PHY bring-up never runs, and
#     the board comes up with 1e:758d=0x0010 - no carrier, no data plane - and
#     nothing but another power cycle and a manual ping brings it back.
#
#   * tools/coldboot-watch.ps1 - it knocks at the countdown, sends the ping that
#     initialises the RTL8226, and runs bootcmd.  It lives for -Minutes (150).
#
# Both of them have been found dead after the fact: the relay once by a one hour
# ceiling and once by the runtime cancelling the task that started it, the watcher
# by the same.  This script is the answer to that: it checks every 30 s and starts
# whatever is missing.
#
# It never starts a second relay - COM3 admits one holder, and a second one fails
# with "COM3 access denied".
param(
    [int]$Minutes = 480,
    [string]$Repo = 'C:\UGit\openwrt\SK-D840N',
    [string]$Port = 'COM3',
    [int]$RelaySeconds = 3300,
    [int]$WatchMinutes = 150,
    [int]$EverySeconds = 30
)

$ErrorActionPreference = 'Continue'
$tools = Join-Path $Repo 'tools'
$log = Join-Path $Repo 'staging\ctl\bench-keep.log'
if (-not (Test-Path -LiteralPath (Split-Path -Parent $log))) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $log) -Force | Out-Null
}

function Get-Helpers {
    # The relay and the watcher may be powershell.exe or pwsh.exe: the bench has
    # used both, and a check that only looks at one of them reports "not running"
    # for a process that is running.
    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match '^(powershell|pwsh)\.exe$' } |
        Select-Object ProcessId, Name, CommandLine
}

function Find-Relay { param($Helpers) @($Helpers | Where-Object { $_.CommandLine -match 'serrelay\.ps1' }) }
function Find-Watch { param($Helpers) @($Helpers | Where-Object { $_.CommandLine -match 'coldboot-watch\.ps1' }) }

function Start-Hidden {
    param([string]$Script, [string[]]$Arguments)
    Start-Process -FilePath 'powershell' -WindowStyle Hidden -ArgumentList `
        (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Script) + $Arguments)
}

$end = (Get-Date).AddMinutes($Minutes)
Write-Output ("bench-keep: watching " + $Repo + " for " + $Minutes + " minutes")

while ((Get-Date) -lt $end) {
    try {
        $helpers = Get-Helpers
        $relay = Find-Relay $helpers
        if ($relay.Count -eq 0) {
            Write-Output ("bench-keep: the relay is not running - starting one (" + (Get-Date).ToString('HH:mm:ss') + ")")
            Start-Hidden (Join-Path $tools 'serrelay.ps1') @('-Port', $Port, '-Seconds', "$RelaySeconds")
            Start-Sleep -Seconds 5
        }
        $helpers = Get-Helpers
        $watch = Find-Watch $helpers
        if ($watch.Count -eq 0) {
            Write-Output ("bench-keep: the cold-boot watcher is not running - starting one (" + (Get-Date).ToString('HH:mm:ss') + ")")
            Start-Hidden (Join-Path $tools 'coldboot-watch.ps1') @('-Minutes', "$WatchMinutes")
            Start-Sleep -Seconds 5
        }
        $helpers = Get-Helpers
        ("{0}  relay={1}  watch={2}" -f (Get-Date).ToString('HH:mm:ss'),
            @(Find-Relay $helpers).Count, @(Find-Watch $helpers).Count) |
            Out-File -LiteralPath $log -Append -Encoding utf8
    } catch {
        Write-Output ("bench-keep: " + $_.Exception.Message)
    }
    Start-Sleep -Seconds $EverySeconds
}

Write-Output 'bench-keep: the watch window is over'
