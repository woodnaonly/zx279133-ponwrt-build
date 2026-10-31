# Cold-boot keeper for the SK-D840N on this bench.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools/coldboot-watch.ps1 -Minutes 240
#
# Why this exists
# ---------------
# The community U-Boot in this board's `boot` partition boots the flash like this:
#
#     bootcmd=mtd read kernel 0x88000000; mtd read dtb 0x88800000; bootm 0x88000000 - 0x88800000
#
# There is no step in that path that initialises the RTL8226 on the 2.5G jack.  The vendor
# bring-up of that PHY only runs when U-Boot's network stack is used - a `ping` is enough to
# trigger it (outerphy info init -> xmac0 is used -> mode_eth_1gbase_x_cfg -> uni serdes init
# succeed), and after that the lane carries code (1e:758d bit2 set, 0x801e).  Without it the
# PHY stays in its out-of-box class (1e:7580=0x9412, 1e:758d=0x0010, no code) and the board
# comes up with no carrier on the 2.5G jack.
#
# The smart plug cuts this board's power every ~20.5 minutes, so "type a ping at the U-Boot
# prompt by hand" is not a state the board can stay in.  This script does it for every cold
# boot, so the board comes back up with a working data plane.
#
# What it does, in one cycle
# ---------------------------
#   1. send 'u' into the console so U-Boot's one-second countdown is interrupted and the board
#      stops at its prompt instead of booting by itself.  The characters have to arrive during
#      the countdown, which starts ~3-5 s after the power comes back - the USB-TTL adapter is
#      re-enumerated at ~1.5-2 s, so a character stream that is already running wins that window.
#      That is why the stream is continuous while the period is still unknown, and why it is
#      bursty afterwards: a stream that runs for hours only makes the console log unreadable.
#   2. when the prompt shows up, send the ping that triggers the PHY bring-up, then `run bootcmd`
#      so the board boots the kernel out of the flash.
#   3. learn the plug's period from the boot banners it sees and aim the next burst at it.
#   4. keep knocking while the board does not answer a ping: a cold boot that is not caught
#      leaves the board up with no carrier, and nothing else would bring it back.
#
# It writes to the relay's inbox (staging/ctl/sercmd.txt) only when that file is not waiting
# to be sent, so anything else using the same inbox - the bench's own MCP tools - is not
# overwritten.
#
# The learned period and the last banner are kept in staging/ctl/watch-state.json, so a
# restarted instance does not flood the console with 'u' for hours.  That matters twice over:
# the log stays readable, and every 'u' is a fork+exec on the board, which is a real load on
# the CPU a measurement is trying to read.
param(
    [int]$Minutes = 240,
    [string]$Ctl = 'C:\UGit\openwrt\SK-D840N\staging\ctl',
    [string]$Log = 'C:\UGit\openwrt\SK-D840N\staging\ctl\serial.log',
    [string]$Board = '192.168.9.1',
    [string]$Pc = '192.168.9.25',
    [int]$DefaultPeriodMin = 21,
    [string]$StateFile = ''
)

if (-not $StateFile) { $StateFile = Join-Path $Ctl 'watch-state.json' }

$ErrorActionPreference = 'Stop'
$sercmd = Join-Path $Ctl 'sercmd.txt'
if (-not (Test-Path -LiteralPath $sercmd)) { New-Item -ItemType File -Path $sercmd | Out-Null }

function Send-Cmd {
    param([string]$Text)
    # The relay takes the inbox within ~30 ms.  Only write when it is free, and never overwrite
    # a command that is already queued: the bench's MCP tools send through the same file.
    for ($i = 0; $i -lt 40; $i++) {
        if (-not (Test-Path -LiteralPath $sercmd)) {
            Set-Content -LiteralPath $sercmd -Value $Text -NoNewline -Encoding ascii
            return $true
        }
        Start-Sleep -Milliseconds 50
    }
    Write-Output ("coldboot-watch: the inbox stayed busy, dropped: " + $Text)
    return $false
}

$pos = (Get-Item -LiteralPath $Log).Length
$end = (Get-Date).AddMinutes($Minutes)
$banners = New-Object System.Collections.ArrayList
$period = $DefaultPeriodMin * 60
$state = 'idle'          # idle | want-prompt | booting
$lastAction = ''

# The period and the last banner survive a restart of this script.  Without them a fresh
# instance knows no period, so it floods the console with 'u' for hours - which is both
# unreadable in the log and a real load on the board's CPU (every 'u' is a fork+exec).
if (Test-Path -LiteralPath $StateFile) {
    try {
        $st = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        if ($st.period -and [int]$st.period -gt 60) { $period = [int]$st.period }
        foreach ($b in @($st.banners)) {
            $t = [datetime]::Parse($b)
            if (((Get-Date) - $t).TotalHours -lt 6) { [void]$banners.Add($t) }
        }
        Write-Output ("coldboot-watch: restored a period of " + [int]($period / 60) +
                      " min and " + $banners.Count + " banner(s) from " + $StateFile)
    } catch {
        Write-Output ("coldboot-watch: cannot read the state file: " + $_.Exception.Message)
    }
}

function Save-State {
    $b = @($banners | ForEach-Object { $_.ToString('o') })
    @{ period = $period; banners = $b } | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding utf8
}

function Measure-Period {
    # The period is the **median** of the intervals between the banners, not their mean.
    #
    # The plug misses a cycle now and then, and one missed cycle is an interval twice as
    # long.  A mean then places the next cut minutes late, and the 'u' stream is not running
    # when the power comes back: on 2026-10-31 18:47:36 the period had come out as 1532 s
    # instead of 1230 s, the stream was not up, U-Boot ran its one-second countdown out,
    # booted by itself, and the board came up with 1e:758d=0x0010 - no carrier, no data
    # plane, and nothing but a power cycle and a manual 'ping' could fix it.
    if ($banners.Count -lt 2) { return $false }
    $ivals = @()
    for ($i = 1; $i -lt $banners.Count; $i++) {
        $ivals += ($banners[$i] - $banners[$i - 1]).TotalSeconds
    }
    $ivals = $ivals | Sort-Object
    $m = $ivals[[int](($ivals.Count - 1) / 2)]
    # $script: - without it this is a function-local copy and the period never
    # leaves the function, which is how the 1538 s below survived a restart.
    if ($m -gt 60) { $script:period = [int]$m }
    return $true
}

Measure-Period | Out-Null

Write-Output ("coldboot-watch: watching " + $Log + " for " + $Minutes + " minutes")
Write-Output ("coldboot-watch: the plug's period is " + [int]($period / 60) + " minutes")

$iter = 0
$down = $false
$lastPing = Get-Date
$promptSeen = $false

while ((Get-Date) -lt $end) {
    # ---- read what the board said since last time
    $new = ''
    try {
        $fs = [IO.File]::Open($Log, 'Open', 'Read', 'ReadWrite')
        $len = $fs.Length
        if ($len -gt $pos) {
            $fs.Seek($pos, 'Begin') | Out-Null
            $buf = New-Object byte[] ($len - $pos)
            $fs.Read($buf, 0, $buf.Length) | Out-Null
            $new = [Text.Encoding]::UTF8.GetString($buf)
            $pos = $len
        }
        $fs.Close()
    } catch {
        Write-Output ("coldboot-watch: cannot read the log: " + $_.Exception.Message)
    }

    if ($new) {
        if ($new -match 'Hit any key to stop autoboot') {
            $now = Get-Date
            [void]$banners.Add($now)
            if ($banners.Count -gt 6) { $banners.RemoveAt(0) }
            Measure-Period | Out-Null
            Save-State
            $state = 'want-prompt'
            $promptSeen = $false
            Write-Output ("coldboot-watch: a boot banner at " + $now.ToString('HH:mm:ss') + ", the period is now " + [int]($period / 60) + " min")
        }
        if ($new -match 'Linux version') {
            if ($state -ne 'idle') { Write-Output ("coldboot-watch: the kernel is up") }
            $state = 'idle'
            $promptSeen = $false
        }
    }

    # ---- the loader is at its prompt: trigger the PHY bring-up, then boot the flash
    if ($state -eq 'want-prompt' -and $new -match '=> ') {
        Write-Output ("coldboot-watch: the loader is at its prompt - ping to init the RTL8226, then bootcmd")
        # From here on the knocking stops: the loader's prompt has to take the two
        # commands below clean.  A 'u' glued onto the end of them makes
        # "ping 192.168.9.25u" and "run bootcmdu", and both of those fail.
        $promptSeen = $true
        Send-Cmd "setenv ipaddr $Board; setenv serverip $Pc; ping $Pc" | Out-Null
        Start-Sleep -Seconds 10
        Send-Cmd 'run bootcmd' | Out-Null
        $state = 'booting'
    }

    # ---- is the board answering?  A board that does not is either in the middle of a cold
    # boot or up with no carrier, and the second case is the one that needs the 'u' stream
    # running right now: the next cold boot has to be caught, and the characters have to be
    # there before U-Boot's countdown is over.
    $iter++
    if (((Get-Date) - $lastPing).TotalSeconds -ge 10) {
        $lastPing = Get-Date
        $alive = $false
        try {
            $alive = [bool](Test-Connection -TargetName $Board -Count 1 -TimeoutSec 1 -Quiet -ErrorAction SilentlyContinue)
        } catch { $alive = $false }
        if ($alive -and $down) {
            Write-Output ("coldboot-watch: the board answers again at " + (Get-Date).ToString('HH:mm:ss'))
        }
        $down = -not $alive
        if ($down -and $state -eq 'idle') {
            # It went quiet while nothing was booting: knock, and keep knocking.
            $state = 'knocking'
        }
        if ($alive -and $state -eq 'knocking') { $state = 'idle' }
    }

    # ---- the character stream that stops the countdown
    # While the period is unknown the stream runs continuously.  Once it is known it is bursty:
    # only around the moment the plug is expected to cut the power, because the characters are
    # only useful in the few seconds after the power comes back.  The window is 90 s wide on
    # each side, because the period is only ever an estimate of when the plug will cut.
    $stream = $true
    if ($banners.Count -ge 2) {
        $sinceBanner = ((Get-Date) - $banners[$banners.Count - 1]).TotalSeconds
        $untilNext = $period - $sinceBanner
        $stream = ($sinceBanner -lt 90) -or ($untilNext -lt 90) -or ($untilNext -gt ($period - 60))
    }
    # The board is down: it may be a cold boot in progress, and the characters have
    # to be there before U-Boot's countdown is over.  But once the loader is at its
    # prompt the knocking stops, so the commands that go to that prompt arrive clean.
    if ($down) { $stream = -not $promptSeen }
    if ($stream -and -not (Test-Path -LiteralPath $sercmd)) {
        Set-Content -LiteralPath $sercmd -Value 'u' -NoNewline -Encoding ascii
    }

    Start-Sleep -Milliseconds 250
}

Write-Output 'coldboot-watch: the watch window is over'
