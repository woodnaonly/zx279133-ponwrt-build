# Per-jack inbound-traffic sampler, used to find which RJ45 the running system can actually
# talk through. ReceivedBytes is the signal: on a point-to-point cable it only moves if the
# board transmits, which separates "cable in the wrong jack" from "kernel never brought up a
# netdev" without needing an IP answer from a system that may have no address.
param([int]$IntervalSec = 8, [int]$IfIndex = 19, [string]$Log = 'staging/ctl/rxsweep.log')
$prev = $null
while ($true) {
    $a = Get-NetAdapter -InterfaceIndex $IfIndex -ErrorAction SilentlyContinue
    if ($null -eq $a) { "$(Get-Date -Format T) no adapter ifindex=$IfIndex" | Add-Content $Log; Start-Sleep $IntervalSec; continue }
    $s = $a | Get-NetAdapterStatistics -ErrorAction SilentlyContinue
    $line = '{0} link={1} rxbytes={2} delta={3} pkts u/b/m={4}/{5}/{6}' -f (Get-Date -Format T), $a.Status,
        $s.ReceivedBytes, $(if ($null -ne $prev) { $s.ReceivedBytes - $prev } else { 0 }),
        $s.ReceivedUnicastPackets, $s.ReceivedBroadcastPackets, $s.ReceivedMulticastPackets
    if ($a.Status -ne $global:plast) { "$(Get-Date -Format T) >>> LINK $global:plast -> $($a.Status)" | Add-Content $Log; $global:plast = $a.Status }
    $line | Add-Content $Log
    $prev = $s.ReceivedBytes
    Start-Sleep $IntervalSec
}
