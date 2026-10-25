<#
.SYNOPSIS
  Make this PC the upstream of the board: a DHCP server and a NAT of its own,
  with the internet coming from the Wi-Fi.

.DESCRIPTION
  The bench has one cable, and it is in the board's LAN.  A lab router needs
  an upstream on one port and a client on another, so with one cable the only
  way to have both is for the PC to be the upstream: Windows' Internet
  Connection Sharing turns the Wi-Fi into the "public" side and the wired
  adapter into the "private" one, which gives that adapter the address
  192.168.137.1 and a DHCP server that hands out 192.168.137.x.

  The board's WAN is then the port the PC is in (the board is told so with
  /etc/labwan = pc, which S26nat reads), the board's LAN is the other jacks
  plus the veth client that S27labclient puts there, and the client's traffic
  goes out through the board's own NAT and this PC's ICS NAT to the internet.

  -Mode on     enable sharing (Wi-Fi public, Ethernet private)
  -Mode off    disable it and give the wired adapter its 192.168.9.25 address
               back, which is what the rest of this bench expects
  -Mode status print what is shared and what the wired adapter holds

  Nothing here writes to the board; tools/labnat.ps1 and the ssh helpers do
  that.
#>
param(
  [ValidateSet('on', 'off', 'status')]
  [string]$Mode = 'status',
  [string]$Wifi = 'WLAN',
  [string]$Wired = '以太网'
)

$ErrorActionPreference = 'Stop'

function Get-Share {
  $share = New-Object -ComObject HNetCfg.HNetShare
  $out = @()
  foreach ($conn in $share.EnumEveryConnection) {
    # Both of these are IDispatch methods with an argument, which PowerShell
    # will not bind as a normal call: they have to be invoked explicitly.
    $props = $share.NetConnectionProps.Invoke($conn)
    $cfg = $share.INetSharingConfigurationForINetConnection.Invoke($conn)
    $out += [pscustomobject]@{
      Name   = $props.Name
      Device = $props.DeviceName
      Share  = $cfg.SharingEnabled
      Type   = if ($null -eq $cfg.SharingConnectionType) { 'none' } else { [string]$cfg.SharingConnectionType }
      Cfg    = $cfg
      Conn   = $conn
    }
  }
  return $out
}

$conns = Get-Share
$w = $conns | Where-Object { $_.Device -eq $Wifi -or $_.Name -eq $Wifi } | Select-Object -First 1
$e = $conns | Where-Object { $_.Device -eq $Wired -or $_.Name -eq $Wired } | Select-Object -First 1

if (-not $e) {
  Write-Output "no wired adapter named '$Wired'; found:"
  $conns | ForEach-Object { Write-Output ("  {0} / {1}" -f $_.Name, $_.Device) }
  exit 1
}

switch ($Mode) {
  'status' {
    $conns | ForEach-Object {
      Write-Output ("{0,-20} device={1,-12} shared={2,-5} type={3}" -f $_.Name, $_.Device, $_.Share, $_.Type)
    }
    Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias $e.Name -ErrorAction SilentlyContinue |
      ForEach-Object { Write-Output ("  {0}: {1}/{2}" -f $e.Name, $_.IPAddress, $_.PrefixLength) }
  }

  'on' {
    if (-not $w) { Write-Output "no wifi adapter named '$Wifi'"; exit 1 }
    # EnableSharing is the way in: the type library exposes SharingEnabled
    # and SharingConnectionType as properties, but assigning to them in
    # PowerShell fails with "no overload for SharingEnabled with argument
    # count 1", and the method is what the API documents anyway.
    #   0 = ICSSHARINGTYPE_PUBLIC (the side with the internet)
    #   1 = ICSSHARINGTYPE_PRIVATE (the side that gets the NAT)
    # The public side goes first: ICS refuses the private side while
    # nothing public is shared.
    # Both calls are idempotent in practice, but the COM method throws when
    # the connection is already shared, and this script runs with
    # $ErrorActionPreference = 'Stop' - so a round that starts with the
    # sharing already on would die on the very first line of work.  The
    # state is read instead of assumed.
    try { $w.Cfg.EnableSharing(0) } catch { Write-Output "the public side is already shared" }
    Start-Sleep -Seconds 2
    try { $e.Cfg.EnableSharing(1) } catch { Write-Output "the private side is already shared" }
    Start-Sleep -Seconds 5
    Get-Share | ForEach-Object {
      Write-Output ("{0,-20} shared={1,-5} type={2}" -f $_.Name, $_.Share, $_.Type)
    }
    Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias $e.Name -ErrorAction SilentlyContinue |
      ForEach-Object { Write-Output ("  {0}: {1}/{2}" -f $e.Name, $_.IPAddress, $_.PrefixLength) }
  }

  'off' {
    foreach ($c in $conns) {
      if ($c.Share) { $c.Cfg.DisableSharing() }
    }
    Start-Sleep -Seconds 3
    # Put the bench's own address back: ICS takes the wired adapter to
    # 192.168.137.1 and nothing puts it back but us.
    $existing = Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias $e.Name -ErrorAction SilentlyContinue |
      Where-Object { $_.IPAddress -eq '192.168.137.1' }
    foreach ($x in $existing) { Remove-NetIPAddress -AddressFamily IPv4 -IPAddress $x.IPAddress -InterfaceIndex $x.InterfaceIndex -Confirm:$false }
    if (-not (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias $e.Name -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -eq '192.168.9.25' })) {
      New-NetIPAddress -AddressFamily IPv4 -IPAddress 192.168.9.25 -PrefixLength 24 -InterfaceAlias $e.Name | Out-Null
    }
    New-NetRoute -AddressFamily IPv4 -DestinationPrefix 0.0.0.0/0 -NextHop 192.168.9.1 `
      -InterfaceAlias $e.Name -RouteMetric 256 -ErrorAction SilentlyContinue | Out-Null
    Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias $e.Name -ErrorAction SilentlyContinue |
      ForEach-Object { Write-Output ("  {0}: {1}/{2}" -f $e.Name, $_.IPAddress, $_.PrefixLength) }
  }
}
