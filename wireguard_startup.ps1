$ErrorActionPreference = 'Stop'
$services = @('WireGuardManager', 'WireGuardTunnel$hermes-laptop')
for ($attempt = 1; $attempt -le 10; $attempt++) {
    try {
        foreach ($name in $services) {
            $service = Get-Service -Name $name -ErrorAction Stop
            if ($service.Status -ne 'Running') {
                Start-Service -Name $name -ErrorAction Stop
            }
        }
        exit 0
    } catch {
        if ($attempt -eq 10) { exit 1 }
        Start-Sleep -Seconds 15
    }
}
