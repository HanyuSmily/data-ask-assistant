Set-Location "d:\TraeCN\data-ask-assistant\data-ask-assistant"
$outFile = "d:\TraeCN\data-ask-assistant\data-ask-assistant\run_output.log"
$errFile = "d:\TraeCN\data-ask-assistant\data-ask-assistant\run_error.log"

# Remove old logs
Remove-Item $outFile -Force -ErrorAction SilentlyContinue
Remove-Item $errFile -Force -ErrorAction SilentlyContinue

$proc = Start-Process -FilePath "C:\Users\smily\python-sdk\python3.13.2\python.exe" `
    -ArgumentList "-u", "app.py" `
    -WorkingDirectory "d:\TraeCN\data-ask-assistant\data-ask-assistant" `
    -RedirectStandardOutput $outFile `
    -RedirectStandardError $errFile `
    -PassThru -NoNewWindow

Write-Output "Started PID: $($proc.Id)"

# Wait up to 20 seconds for port 7860 or process exit
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    if ($proc.HasExited) {
        Write-Output "Process exited with code: $($proc.ExitCode)"
        break
    }
    $listening = netstat -ano | Select-String "LISTENING" | Select-String ":7860"
    if ($listening) {
        Write-Output "Port 7860 is LISTENING after $($i+1) seconds"
        break
    }
}

Write-Output "=== STDOUT ==="
Get-Content $outFile -ErrorAction SilentlyContinue
Write-Output "=== STDERR ==="
Get-Content $errFile -ErrorAction SilentlyContinue
