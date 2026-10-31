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
$ctl = Join-Path $Repo 'staging\ctl'
$log = Join-Path $ctl 'bench-keep.log'
if (-not (Test-Path -LiteralPath (Split-Path -Parent $log))) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $log) -Force | Out-Null
}

# One keeper at a time.  Two of them would both start a relay when the relay is
# gone, and the second one fails with "COM3 access denied".
#
# The lock carries the owner's PID, and it is taken over when that PID is gone.
# A lock that only has a timestamp blocks its successor for two minutes, and the
# moment a keeper has to be restarted is usually the moment right before a power
# cut - on 2026-10-31 21:05 the replacement keeper walked away on a lock that the
# dead keeper had left behind, and the board would have gone through a cold boot
# with nobody knocking at the countdown.
$lock = Join-Path $Repo 'staging\ctl\bench-keep.lock'
if (Test-Path -LiteralPath $lock) {
    $age = ((Get-Date) - (Get-Item -LiteralPath $lock).LastWriteTime).TotalMinutes
    $owner = 0
    $raw = ''
    try { $raw = [string](Get-Content -LiteralPath $lock -Raw -ErrorAction Stop) } catch { }
    if ($raw -match '\|(\d+)\s*$') { $owner = [int]$Matches[1] }
    $ownerAlive = $false
    if ($owner -gt 0) {
        $ownerAlive = $null -ne (Get-Process -Id $owner -ErrorAction SilentlyContinue)
    }
    if ($age -lt 2 -and $ownerAlive) {
        Write-Output ("bench-keep: another keeper holds the lock (pid " + $owner + ", " + [int]$age + " min old) - this one leaves")
        exit 0
    }
    if ($age -lt 2) {
        Write-Output ("bench-keep: the lock is stale (pid " + $owner + " is gone) - this one takes it over")
    }
}
Set-Content -LiteralPath $lock -Value ((Get-Date).ToString('o') + '|' + $PID) -Encoding ascii

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
    # -WorkingDirectory is not optional here.  The keeper itself is started through
    # WMI (Invoke-CimMethod Win32_Process Create), and a process WMI creates has
    # C:\Windows\System32 as its working directory - not the repository.  A child
    # that resolves its paths against Get-Location therefore lands outside the
    # repository entirely, and the damage is silent: the relay opened COM3 and
    # kept it, but its log and its command inbox pointed at a directory that does
    # not exist, so nothing reached the board and nothing came back.
    # Measured 2026-10-31 21:01-21:05: the relay held the port and the keeper
    # reported relay=1, while serial.log stayed at 20:59:58 and the watcher's
    # 'u' never reached the UART - which is exactly how the next cold boot would
    # have gone past the countdown and lost the data plane.
    Start-Process -FilePath 'powershell' -WindowStyle Hidden -WorkingDirectory $Repo -ArgumentList `
        (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Script) + $Arguments)
}

$end = (Get-Date).AddMinutes($Minutes)
Write-Output ("bench-keep: watching " + $Repo + " for " + $Minutes + " minutes")

while ((Get-Date) -lt $end) {
    try {
        # Keep the lock fresh, so a second keeper can tell this one is alive.
        Set-Content -LiteralPath $lock -Value ((Get-Date).ToString('o') + '|' + $PID) -Encoding ascii
        $helpers = Get-Helpers
        $relay = Find-Relay $helpers
        if ($relay.Count -eq 0) {
            Write-Output ("bench-keep: the relay is not running - starting one (" + (Get-Date).ToString('HH:mm:ss') + ")")
            # The relay resolves its log and its inbox against Get-Location.  The paths are
            # passed explicitly so that the relay can never end up talking to another
            # directory than the watcher, whatever the working directory turns out to be.
            Start-Hidden (Join-Path $tools 'serrelay.ps1') @('-Port', $Port, '-Seconds', "$RelaySeconds",
                '-Log', (Join-Path $ctl 'serial.log'),
                '-CmdFile', (Join-Path $ctl 'sercmd.txt'),
                '-StopFile', (Join-Path $ctl 'serstop'),
                '-ResetFile', (Join-Path $ctl 'serreset'),
                '-NoStopFile', (Join-Path $ctl 'ser-nostop'))
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
