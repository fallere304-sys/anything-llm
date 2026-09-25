<#
  Z4 MotionCam - 自宅ネットワーク診断（Windows PowerShell 5.1 / PowerShell 7）

  自宅の Wi-Fi / LAN につないだ PC で実行し、外出先から Z4 に接続できる環境かを調べます。
  何も変更しません（ルーターの設定も変えません）。調べる内容:
    - 外から見た公開 IPv4 / IPv6
    - ルーターの WAN 側 IPv4（UPnP で問い合わせ）
    - PC の IPv6 グローバルアドレス
    - インターネットまでの最初の数ホップ（二重ルーター / CGNAT の手がかり）

  実行方法（どちらか）:
    netcheck.bat をダブルクリック
    powershell -ExecutionPolicy Bypass -File .\netcheck.ps1

  このファイルは Windows PowerShell 5.1 が日本語を正しく読めるよう BOM 付き UTF-8 です。
  iex で実行する場合は先頭の BOM を取り除いてください: iex ((irm <URL>).TrimStart([char]0xFEFF))
#>

$ErrorActionPreference = 'Continue'
$Port = 8443
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

function Get-V4Bytes([string]$ip) {
    $addr = $null
    if (-not $ip -or -not [System.Net.IPAddress]::TryParse($ip, [ref]$addr)) { return $null }
    if ($addr.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { return $null }
    return $addr.GetAddressBytes()
}
function Test-PrivateV4([string]$ip) {
    $b = Get-V4Bytes $ip
    if (-not $b) { return $false }
    return ($b[0] -eq 10) -or ($b[0] -eq 172 -and ($b[1] -band 0xF0) -eq 16) -or ($b[0] -eq 192 -and $b[1] -eq 168)
}
function Test-CgnatV4([string]$ip) {
    $b = Get-V4Bytes $ip
    if (-not $b) { return $false }
    return ($b[0] -eq 100 -and ($b[1] -band 0xC0) -eq 64)
}
function Test-PublicV4([string]$ip) {
    $b = Get-V4Bytes $ip
    if (-not $b -or (Test-PrivateV4 $ip) -or (Test-CgnatV4 $ip)) { return $false }
    return ($b[0] -ne 0 -and $b[0] -ne 127 -and $b[0] -lt 224 -and -not ($b[0] -eq 169 -and $b[1] -eq 254))
}

# Without a text Content-Type header, Invoke-WebRequest returns the body as bytes.
function ConvertTo-Text($content) {
    if ($content -is [byte[]]) { return [Text.Encoding]::UTF8.GetString($content) }
    return [string]$content
}

function Get-HttpText([string]$url, [int]$timeout = 6) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeout
        return (ConvertTo-Text $r.Content).Trim()
    } catch { return $null }
}

function Get-PublicIp([string[]]$urls) {
    foreach ($u in $urls) {
        $t = Get-HttpText $u
        $a = $null
        if ($t -and [System.Net.IPAddress]::TryParse($t, [ref]$a)) { return $t }
    }
    return $null
}

# 送信せずに、OS が選ぶ送信元アドレスを調べる（UDP の connect はパケットを出さない）
function Get-SourceAddress([string]$target, [System.Net.Sockets.AddressFamily]$family) {
    $s = $null
    try {
        $s = New-Object System.Net.Sockets.Socket($family, [System.Net.Sockets.SocketType]::Dgram, [System.Net.Sockets.ProtocolType]::Udp)
        $s.Connect([System.Net.IPAddress]::Parse($target), 53)
        return $s.LocalEndPoint.Address.ToString()
    } catch {
        return $null  # e.g. no IPv6 on this PC
    } finally {
        if ($s) { $s.Close() }
    }
}

function Get-LocalInfo {
    $info = [ordered]@{ LocalV4 = $null; Gateway = $null; GlobalV6 = @() }
    $info.LocalV4 = Get-SourceAddress '8.8.8.8' ([System.Net.Sockets.AddressFamily]::InterNetwork)
    if (Get-Command Get-NetRoute -ErrorAction SilentlyContinue) {
        $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1
        if ($route) {
            $info.Gateway = $route.NextHop
            $v6 = Get-NetIPAddress -AddressFamily IPv6 -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue |
                Where-Object { $_.IPAddress -match '^[23]' -and $_.AddressState -eq 'Preferred' }
            foreach ($a in $v6) {
                $kind = '固定'
                if ($a.SuffixOrigin -eq 'Random') { $kind = '一時' }
                $info.GlobalV6 += ('{0}（{1}）' -f $a.IPAddress, $kind)
            }
        }
    } else {
        $v6 = Get-SourceAddress '2001:4860:4860::8888' ([System.Net.Sockets.AddressFamily]::InterNetworkV6)
        if ($v6 -and $v6 -match '^[23]') { $info.GlobalV6 += $v6 }
    }
    return $info
}

function Find-UpnpLocations([int]$seconds = 3) {
    $targets = @(
        'urn:schemas-upnp-org:device:InternetGatewayDevice:1',
        'urn:schemas-upnp-org:device:InternetGatewayDevice:2',
        'urn:schemas-upnp-org:service:WANIPConnection:1',
        'urn:schemas-upnp-org:service:WANPPPConnection:1')
    $locations = @()
    $udp = New-Object System.Net.Sockets.UdpClient(0)
    try {
        $udp.Client.ReceiveTimeout = 500
        $group = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Parse('239.255.255.250'), 1900)
        foreach ($st in $targets) {
            $msg = "M-SEARCH * HTTP/1.1`r`nHOST: 239.255.255.250:1900`r`nMAN: `"ssdp:discover`"`r`nMX: 2`r`nST: $st`r`n`r`n"
            $bytes = [Text.Encoding]::ASCII.GetBytes($msg)
            [void]$udp.Send($bytes, $bytes.Length, $group)
        }
        $deadline = (Get-Date).AddSeconds($seconds)
        while ((Get-Date) -lt $deadline) {
            try {
                $remote = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
                $data = $udp.Receive([ref]$remote)
                $text = [Text.Encoding]::ASCII.GetString($data)
                if ($text -match '(?im)^location:\s*(\S+)') {
                    if ($locations -notcontains $Matches[1]) { $locations += $Matches[1] }
                }
            } catch { }
        }
    } finally { $udp.Close() }
    return $locations
}

function Get-XmlTag([string]$xml, [string]$name) {
    $m = [regex]::Match($xml, '(?is)<(?:\w+:)?' + $name + '(?:\s[^>]*)?>(.*?)</(?:\w+:)?' + $name + '>')
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return $null
}

function Get-Igd([string]$location) {
    $xml = Get-HttpText $location 5
    if (-not $xml) { return $null }
    $base = Get-XmlTag $xml 'URLBase'
    if (-not $base) { $base = $location }
    foreach ($m in [regex]::Matches($xml, '(?s)<(?:\w+:)?service>(.*?)</(?:\w+:)?service>')) {
        $svc = $m.Groups[1].Value
        $type = Get-XmlTag $svc 'serviceType'
        $control = Get-XmlTag $svc 'controlURL'
        if ($type -and $control -and ($type -match ':WAN(IP|PPP)Connection:')) {
            $url = (New-Object System.Uri((New-Object System.Uri($base)), $control)).AbsoluteUri
            return [pscustomobject]@{ ControlUrl = $url; ServiceType = $type; Location = $location; Xml = $xml }
        }
    }
    return $null
}

function Invoke-Soap($igd, [string]$action, [string]$argsXml) {
    $body = '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" ' +
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>' +
        "<u:$action xmlns:u=`"$($igd.ServiceType)`">$argsXml</u:$action></s:Body></s:Envelope>"
    $headers = @{ SOAPAction = ('"{0}#{1}"' -f $igd.ServiceType, $action) }
    $r = Invoke-WebRequest -Uri $igd.ControlUrl -Method Post -Body $body -ContentType 'text/xml; charset=utf-8' `
        -Headers $headers -UseBasicParsing -TimeoutSec 5
    return ConvertTo-Text $r.Content
}

function Get-FirstHops([int]$max = 4) {
    $hops = @()
    $onWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
    try {
        if ($onWindows) {
            $out = & tracert -d -h $max -w 800 8.8.8.8 2>$null
        } elseif (Get-Command traceroute -ErrorAction SilentlyContinue) {
            $out = & traceroute -n -m $max -q 1 -w 1 8.8.8.8 2>$null
        } else { return $hops }
        foreach ($line in $out) {
            if ($line -match '^\s*(\d+)\s') {
                $ip = [regex]::Match($line, '(\d{1,3}\.){3}\d{1,3}(?!.*(\d{1,3}\.){3}\d{1,3})')
                if ($ip.Success) { $hops += $ip.Value } else { $hops += '*' }
            }
        }
    } catch { }
    return $hops
}

# 判定（Z4 アプリの NetworkDiagnostics.classify と同じ基準）
function Get-Verdict([string]$publicV4, [bool]$upnp, [string]$routerWan) {
    if (-not $publicV4) { return 'OFFLINE' }
    if (-not $upnp) { return 'UNKNOWN' }
    if (Test-CgnatV4 $routerWan) { return 'CGNAT' }
    if (Test-PrivateV4 $routerWan) { return 'DOUBLE_NAT' }
    if ($routerWan -eq $publicV4 -and (Test-PublicV4 $routerWan)) { return 'GLOBAL' }
    return 'TUNNELED'
}

# ---------------------------------------------------------------------------------------------

Write-Host ''
Write-Host '=== Z4 MotionCam 自宅ネットワーク診断 ===' -ForegroundColor Cyan
Write-Host '調べています（20秒ほどかかります）...'

$local = Get-LocalInfo
$publicV4 = Get-PublicIp @('https://api.ipify.org', 'https://checkip.amazonaws.com')
$publicV6 = Get-PublicIp @('https://api6.ipify.org')

$upnp = $false
$routerWan = $null
$upnpNote = $null
$locations = @()
if ($env:Z4_UPNP_LOCATION) { $locations = @($env:Z4_UPNP_LOCATION) } else { $locations = Find-UpnpLocations 3 }
$igd = $null
foreach ($loc in $locations) { $igd = Get-Igd $loc; if ($igd) { break } }
$routerModel = $null
if ($igd) {
    $routerModel = (@((Get-XmlTag $igd.Xml 'manufacturer'), (Get-XmlTag $igd.Xml 'modelName')) | Where-Object { $_ }) -join ' '
    try {
        $routerWan = Get-XmlTag (Invoke-Soap $igd 'GetExternalIPAddress' '') 'NewExternalIPAddress'
        $upnp = $true
    } catch { $upnpNote = 'ルーターがWAN側IPの問い合わせに応答しません' }
} elseif ($locations.Count -gt 0) {
    $upnpNote = 'UPnP機器はありますが、ルーターのWAN接続サービスが見つかりません'
} else {
    $upnpNote = 'UPnP対応ルーターが見つかりません（ルーターのUPnPが無効の可能性）'
}

$hops = Get-FirstHops 4

$verdict = Get-Verdict $publicV4 $upnp $routerWan

# 経路からの手がかり（UPnP が使えない場合の補助）
$hopHint = $null
if ($hops.Count -ge 2) {
    $second = $hops[1]
    if (Test-CgnatV4 $second) { $hopHint = '2ホップ目が 100.64.0.0/10（CGNAT）のアドレスです。プロバイダーでIPv4を共有している可能性が高いです。' }
    elseif (Test-PrivateV4 $second) { $hopHint = '2ホップ目もプライベートアドレスです。ルーターが2段（二重ルーター）になっている可能性が高いです。' }
}

$show = {
    param($label, $value)
    if ($null -eq $value -or "$value" -eq '') { $value = '（なし）' }
    Write-Host ('  {0,-22} {1}' -f $label, $value)
}

Write-Host ''
Write-Host '--- 調査結果 ---' -ForegroundColor Cyan
& $show 'PCのLAN IPv4' $local.LocalV4
& $show 'ルーター（ゲートウェイ）' $local.Gateway
& $show '公開IPv4（外から見たIP）' $publicV4
& $show 'ルーター機種（UPnP）' $routerModel
if ($upnp) { & $show 'ルーターのWAN側IPv4' $(if ($routerWan) { $routerWan } else { '（なし）' }) }
else { & $show 'UPnP' $upnpNote }
& $show '公開IPv6' $publicV6
& $show 'PCのIPv6グローバル' ($local.GlobalV6 -join ', ')
& $show '最初の経路' ($hops -join ' → ')

Write-Host ''
Write-Host '--- 判定 ---' -ForegroundColor Cyan
switch ($verdict) {
    'GLOBAL' {
        Write-Host '◎ ルーターにグローバルIPv4アドレスが割り当てられています。外出先から視聴できます。' -ForegroundColor Green
        Write-Host "   Z4 の設定で「外出先からの視聴」を有効にしてください（TCP $Port 番をUPnPで自動開放します）。"
    }
    'DOUBLE_NAT' {
        Write-Host "△ ルーターのWAN側がプライベートアドレス（$routerWan）です。上位にもう1台ルーターがあります。" -ForegroundColor Yellow
        Write-Host "   上位ルーターでも TCP $Port を下位ルーターへ転送するか、下位ルーターをブリッジ（APモード）にしてください。"
    }
    'CGNAT' {
        Write-Host "× プロバイダーがIPv4アドレスを共有しています（CGNAT: $routerWan）。IPv4では外から接続できません。" -ForegroundColor Red
    }
    'TUNNELED' {
        $wanText = if ($routerWan) { $routerWan } else { 'なし' }
        Write-Host "△ ルーターのWAN側IPv4（$wanText）と公開IPv4（$publicV4）が一致しません。" -ForegroundColor Yellow
        Write-Host '   IPv6 IPoE（v6プラス / transix / クロスパス等）か上位NATの可能性が高く、IPv4での公開は制限されます。'
        Write-Host '   v6プラス等では、ルーター管理画面の「利用可能なポート」の範囲内なら開放できます。'
    }
    'UNKNOWN' {
        Write-Host '？ ルーターがUPnPに応答しないため、自動では判定できません。' -ForegroundColor Yellow
        $admin = if ($local.Gateway) { "http://$($local.Gateway)/" } else { 'ルーターの管理画面' }
        Write-Host "   $admin を開き、WAN側（インターネット側）IPv4 を確認してください。"
        Write-Host "   公開IPv4（$publicV4）と同じならグローバルIPです。"
    }
    default {
        Write-Host '× インターネットに接続できていないか、公開IPを取得できませんでした。' -ForegroundColor Red
    }
}
if ($hopHint) { Write-Host "   参考: $hopHint" }
if ($publicV6) {
    Write-Host "   IPv6 でインターネットに出られます。ルーターのIPv6フィルターで TCP $Port の着信を許可すれば、IPv6回線のスマホから接続できる可能性があります。"
}
if ($verdict -ne 'GLOBAL' -and -not $publicV6 -and $verdict -ne 'UNKNOWN') {
    Write-Host '   直接の公開が難しい環境です。ルーターのVPNサーバー機能で自宅に接続する方法を検討してください。'
}
Write-Host ''
Write-Host '※ この画面の内容（IPアドレスは一部を伏せても構いません）を送っていただければ、設定手順をご案内できます。'
Write-Host ''
