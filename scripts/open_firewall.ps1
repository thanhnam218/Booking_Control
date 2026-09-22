<#
    MO CONG CHO DONG NGHIEP TRONG MANG LAN TRUY CAP WEB
    ---------------------------------------------------
    Chay 1 lan tren MAY CHU, bang PowerShell "Run as administrator":

        .\scripts\open_firewall.ps1            # mo cong 8765 cho mang Private/Domain
        .\scripts\open_firewall.ps1 -Port 8800
        .\scripts\open_firewall.ps1 -Remove    # dong lai

    Chi mo cho mang noi bo (Private, Domain) - KHONG mo cho mang Public (wifi quan cafe...).
#>
param([int]$Port = 8765, [switch]$Remove)
$name = "Booking Control (TCP $Port)"

if ($Remove) {
    Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    Write-Host "Da dong cong $Port." -ForegroundColor Green
    exit 0
}

New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort $Port `
    -Action Allow -Profile Private,Domain | Out-Null
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } | Select-Object -First 1).IPAddress
Write-Host "Da mo cong $Port cho mang noi bo." -ForegroundColor Green
Write-Host "Nho dat  web.host: ""0.0.0.0""  trong config\config.yaml roi khoi dong lai."
Write-Host "Dong nghiep mo: http://$($ip):$Port"
