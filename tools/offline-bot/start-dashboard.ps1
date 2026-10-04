$ErrorActionPreference = 'Stop'
$errorLog = $null
try {
    $workspacePath = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
    $pythonPath = Join-Path $workspacePath 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe'
    $logDirectory = Join-Path $workspacePath 'logs/mining/dashboard-launcher'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $launchId = [Guid]::NewGuid().ToString('N')
    $errorLog = Join-Path $logDirectory "$launchId.stderr.log"
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw "The project Python runtime is missing: $pythonPath. Open the project in Codex to repair it."
    }
    $env:PYTHONPATH = Join-Path $workspacePath 'tools/offline-bot'
    $helperProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'bsgo_offline.easy_launcher') -WorkingDirectory $workspacePath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "$launchId.stdout.log") -RedirectStandardError $errorLog -PassThru
    if ($helperProcess.WaitForExit(5000) -and $helperProcess.ExitCode -ne 0) {
        throw "The dashboard process exited with code $($helperProcess.ExitCode). Retry once; if it still fails, open the project in Codex."
    }
} catch {
    $startupError = $_.Exception.Message
    if ($errorLog) {
        try { Add-Content -LiteralPath $errorLog -Value $startupError } catch { }
    }
    $details = if ($errorLog) { "`nDetails: $errorLog" } else { "`nOpen this project in Codex to inspect the startup failure." }
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("$startupError$details", 'Mining dashboard startup') | Out-Null
    exit 1
}
