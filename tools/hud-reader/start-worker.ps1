param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}$')][string]$RunName,
    [ValidateRange(30,300)][int]$MaxSeconds = 120
)
$ErrorActionPreference = 'Stop'
$runDirectory = Join-Path $PSScriptRoot ('runs/' + $RunName)
if (Test-Path -LiteralPath $runDirectory) { throw 'Choose a fresh run name; this directory already exists.' }
$pythonPath = 'C:\Users\lmery\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Prepared Python runtime is missing.' }
$readerPath = Join-Path $PSScriptRoot 'hud_reader.py'
New-Item -ItemType Directory -Path $runDirectory | Out-Null
$worker = Start-Process -FilePath $pythonPath -ArgumentList @(
    ('"' + $readerPath + '"'), '--watch-directory', ('"' + $runDirectory + '"'),
    '--max-seconds', $MaxSeconds, '--profile', 'restart-20261001'
) -WindowStyle Hidden -PassThru
[PSCustomObject]@{ pid=$worker.Id; directory=$runDirectory; profile='restart-20261001'; maxSeconds=$MaxSeconds } | ConvertTo-Json -Compress
