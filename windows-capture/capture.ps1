param([string]$Out, [int]$Seconds = 100, [string]$Dev = '\\.\USBPcap1')
$Host.UI.RawUI.WindowTitle = 'RECORDING Kraken - do your Synapse steps now'
$p = Start-Process -FilePath 'C:\Program Files\USBPcap\USBPcapCMD.exe' -ArgumentList @('-d', $Dev, '-o', "`"$Out`"", '-A', '--inject-descriptors') -PassThru -WindowStyle Hidden
Write-Host "RECORDING to $Out" -ForegroundColor Green
Write-Host "Do your Synapse steps now. Wait 5 seconds between each change." -ForegroundColor Yellow
for ($i = $Seconds; $i -gt 0; $i--) {
    Write-Host ("`rTime left: {0,3} s   " -f $i) -NoNewline
    Start-Sleep -Seconds 1
}
Write-Host ""
Stop-Process -Id $p.Id -Force
Write-Host "STOPPED. You can close this window and tell Claude 'done'." -ForegroundColor Green
Start-Sleep -Seconds 5
