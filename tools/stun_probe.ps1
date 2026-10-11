<#
.SYNOPSIS
    STUN probe (PowerShell) -- reports the NAT mapping each STUN server sees.

.DESCRIPTION
    Answers ONE question: is this network's NAT cone-type (endpoint-independent
    mapping) or symmetric (address/port-dependent mapping)?

    That single property decides whether peer-to-peer WebRTC can work:

        cone      -> a peer can aim at the port STUN reported -> P2P works
        symmetric -> each destination gets its own port, so the port STUN
                     reported is NOT the one a peer must use -> P2P fails,
                     a TURN relay is required

    Why not just use the browser's trickle-ice page: ICE de-duplicates
    srflx candidates that share the same mapped address, so "only one srflx
    candidate" cannot be told apart from "every server agreed", and those two
    cases mean opposite things.  This script asks every server separately and
    prints every answer.

    Requires nothing but Windows PowerShell.  No Python, no installs.
    It only sends UDP STUN binding requests; it reads and writes no files and
    changes no settings.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\stun_probe.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\stun_probe.ps1 stun.miwifi.com:3478 stun.cloudflare.com:3478
#>

param(
    [string[]]$Servers = @(
        "stun.l.google.com:19302",
        "stun.miwifi.com:3478",
        "stun.qq.com:3478",
        "stun.cloudflare.com:3478"
    )
)

$ErrorActionPreference = "Stop"

$MAGIC = 0x2112A442
$ATTR_MAPPED_ADDRESS = 0x0001
$ATTR_XOR_MAPPED_ADDRESS = 0x0020


function New-BindingRequest {
    $txid = New-Object byte[] 12
    $rng = New-Object System.Random
    $rng.NextBytes($txid)

    $msg = New-Object byte[] 20
    $msg[0] = 0x00; $msg[1] = 0x01        # Binding Request
    $msg[2] = 0x00; $msg[3] = 0x00        # message length 0
    $msg[4] = 0x21; $msg[5] = 0x12        # magic cookie
    $msg[6] = 0xA4; $msg[7] = 0x42
    [Array]::Copy($txid, 0, $msg, 8, 12)  # transaction id

    return @{ Message = $msg; TxId = $txid }
}


function Read-MappedAddress {
    param([byte[]]$Data)

    if ($null -eq $Data -or $Data.Length -lt 20) { return $null }

    $msgLength = ([int]$Data[2] -shl 8) + [int]$Data[3]
    $offset = 20
    $end = [Math]::Min(20 + $msgLength, $Data.Length)
    $result = $null

    while (($offset + 4) -le $end) {
        $attrType = ([int]$Data[$offset] -shl 8) + [int]$Data[$offset + 1]
        $attrLen = ([int]$Data[$offset + 2] -shl 8) + [int]$Data[$offset + 3]

        if ($attrType -eq $ATTR_XOR_MAPPED_ADDRESS -and $attrLen -ge 8) {
            $port = ((([int]$Data[$offset + 6] -shl 8) + [int]$Data[$offset + 7]) -bxor 0x2112)
            $a = ([int]$Data[$offset + 8]) -bxor 0x21
            $b = ([int]$Data[$offset + 9]) -bxor 0x12
            $c = ([int]$Data[$offset + 10]) -bxor 0xA4
            $d = ([int]$Data[$offset + 11]) -bxor 0x42
            $result = "$a.$b.$c.$d`:$port"
        }
        elseif ($attrType -eq $ATTR_MAPPED_ADDRESS -and $attrLen -ge 8) {
            $port = (([int]$Data[$offset + 6] -shl 8) + [int]$Data[$offset + 7])
            $a = [int]$Data[$offset + 8]
            $b = [int]$Data[$offset + 9]
            $c = [int]$Data[$offset + 10]
            $d = [int]$Data[$offset + 11]
            $result = "$a.$b.$c.$d`:$port"
        }

        $offset += 4 + $attrLen
        $offset += (4 - ($attrLen % 4)) % 4
    }

    return $result
}


function Invoke-StunProbe {
    param(
        [System.Net.Sockets.UdpClient]$Client,
        [string]$Server,
        [int]$TimeoutMs = 3000
    )

    $hostName = $Server
    $port = 3478
    if ($Server.Contains(":")) {
        $parts = $Server.Split(":")
        $hostName = $parts[0]
        $port = [int]$parts[1]
    }

    try {
        $addresses = [System.Net.Dns]::GetHostAddresses($hostName) |
            Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork }

        if (-not $addresses -or $addresses.Count -eq 0) {
            Write-Host ("    {0,-32} DNS: no IPv4 address" -f $Server)
            return $null
        }
        $address = $addresses[0]
    }
    catch {
        Write-Host ("    {0,-32} DNS failed" -f $Server)
        return $null
    }

    $request = New-BindingRequest
    $endpoint = New-Object System.Net.IPEndPoint($address, $port)

    try {
        [void]$Client.Send($request.Message, $request.Message.Length, $endpoint)
    }
    catch {
        Write-Host ("    {0,-32} send failed" -f $Server)
        return $null
    }

    $deadline = (Get-Date).AddMilliseconds($TimeoutMs)

    while ((Get-Date) -lt $deadline) {
        try {
            $remote = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
            $data = $Client.Receive([ref]$remote)

            # match the transaction id (bytes 8..19)
            if ($data.Length -ge 20) {
                $same = $true
                for ($i = 0; $i -lt 12; $i++) {
                    if ($data[8 + $i] -ne $request.TxId[$i]) { $same = $false; break }
                }
                if ($same) {
                    $mapped = Read-MappedAddress -Data $data
                    Write-Host ("    {0,-32} -> {1}" -f $Server, $mapped)
                    return $mapped
                }
            }
        }
        catch [System.Net.Sockets.SocketException] {
            Write-Host ("    {0,-32} timeout" -f $Server)
            return $null
        }
    }

    Write-Host ("    {0,-32} no matching response" -f $Server)
    return $null
}


Write-Host ""
Write-Host ("=" * 74)
Write-Host "NAT behaviour probe"
Write-Host ("=" * 74)
Write-Host ""
Write-Host ("Asking {0} STUN servers, all from ONE local UDP socket:" -f $Servers.Count)

$client = New-Object System.Net.Sockets.UdpClient(0)
$client.Client.ReceiveTimeout = 3000
$localPort = $client.Client.LocalEndPoint.Port
Write-Host ("  local port = {0}" -f $localPort)
Write-Host ""

$answers = @()
foreach ($server in $Servers) {
    $mapped = Invoke-StunProbe -Client $client -Server $server
    if ($mapped) { $answers += , @($server, $mapped) }
}

$client.Close()

Write-Host ""
Write-Host ("=" * 74)
Write-Host "result"
Write-Host ("=" * 74)

if ($answers.Count -lt 2) {
    Write-Host ""
    Write-Host ("  Only {0} server(s) answered -- NOT enough to judge the NAT." -f $answers.Count)
    Write-Host "  With a single answer, 'the others would have agreed' and 'the"
    Write-Host "  others were unreachable' look exactly the same."
    Write-Host ""
    Write-Host "  Try again, e.g.:"
    Write-Host "    powershell -ExecutionPolicy Bypass -File tools\stun_probe.ps1 ``"
    Write-Host "        stun.miwifi.com:3478 stun.cloudflare.com:3478"
    exit 2
}

$ports = $answers | ForEach-Object { ($_[1] -split ":")[1] } | Sort-Object -Unique

Write-Host ""
Write-Host ("  {0} servers answered, {1} distinct mapped port(s):" -f $answers.Count, $ports.Count)
Write-Host ""

foreach ($entry in $answers) {
    Write-Host ("    {0,-32} -> {1}" -f $entry[0], $entry[1])
}

Write-Host ""

if ($ports.Count -eq 1) {
    Write-Host "  ==> ENDPOINT-INDEPENDENT MAPPING (cone NAT)."
    Write-Host "      Every destination sees the same external port, which is"
    Write-Host "      exactly what hole punching needs."
    Write-Host "      LIKELY OUTCOME: direct peer-to-peer works, no TURN needed."
    $code = 0
}
else {
    Write-Host "  ==> ADDRESS/PORT-DEPENDENT MAPPING (symmetric NAT)."
    Write-Host "      Each destination gets its own external port, so the port a"
    Write-Host "      peer learns from STUN is not the port that peer must send to."
    Write-Host "      LIKELY OUTCOME: hole punching fails, a TURN relay is required."
    $code = 3
}

Write-Host ""
Write-Host "  Reminder: this describes only the network this command runs on."
Write-Host "  NAT behaviour differs per network, so run it at BOTH ends."
Write-Host ""

exit $code
