param(
    [string]$Model = 'qwen3-vl:4b-instruct',
    [string]$Endpoint = 'http://localhost:11434',
    [string[]]$Images = @(
        'tools/hud-reader/fixtures/live/live-01.png',
        'tools/hud-reader/runs/sol-four-live-2309/frame-1790809793199-001.png'
    ),
    [string]$OutputDirectory = 'logs/ollama-qwen3-vl-20261001/instruct-brief'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$outputPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Save-Json($Value, $Path) {
    [System.IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 30), $utf8)
}

$prompt = @'
Describe this game screenshot in at most three short sentences. Identify the main scene, the visible space objects and their colors, and the selected target label if readable. Use only visible evidence. Do not propose actions.
'@
$version = Invoke-RestMethod -Uri "$Endpoint/api/version" -TimeoutSec 10
$inventory = Invoke-RestMethod -Uri "$Endpoint/api/tags" -TimeoutSec 10
$results = @()
$index = 0
foreach ($relativeImage in $Images) {
    $imagePath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $relativeImage))
    $bytes = [System.IO.File]::ReadAllBytes($imagePath)
    $request = @{
        model = $Model
        messages = @(@{ role = 'user'; content = $prompt; images = @([Convert]::ToBase64String($bytes)) })
        stream = $false
        think = $false
        keep_alive = '5m'
        options = @{ temperature = 0; num_ctx = 8192; num_predict = 256; repeat_penalty = 1.1 }
    }
    Write-Output "Running image $index : $relativeImage"
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-RestMethod -Method Post -Uri "$Endpoint/api/chat" -ContentType 'application/json' -Body ($request | ConvertTo-Json -Depth 20 -Compress) -TimeoutSec 300
    $timer.Stop()
    Save-Json $response (Join-Path $outputPath "response-$index.json")
    $state = Invoke-RestMethod -Uri "$Endpoint/api/ps" -TimeoutSec 10
    $gpu = & nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader
    if ($response.done_reason -eq 'length' -or [string]::IsNullOrWhiteSpace($response.message.content)) {
        throw "Image $index returned no complete answer; preserved raw response."
    }
    $result = @{
        image = $relativeImage
        image_sha256 = (Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash
        image_bytes = $bytes.Length
        wall_seconds = [Math]::Round($timer.Elapsed.TotalSeconds, 3)
        server_total_seconds = [Math]::Round($response.total_duration / 1e9, 3)
        load_seconds = [Math]::Round($response.load_duration / 1e9, 3)
        prompt_eval_count = $response.prompt_eval_count
        prompt_eval_cached_count = $response.prompt_eval_cached_count
        prompt_eval_seconds = [Math]::Round($response.prompt_eval_duration / 1e9, 3)
        output_tokens = $response.eval_count
        done_reason = $response.done_reason
        observation = $response.message.content
        running_models = $state
        gpu_after_request = $gpu
    }
    $results += $result
    Save-Json @{
        server_version = $version.version
        model = $Model
        model_inventory = $inventory
        prompt = $prompt
        response_format = 'freeform brief scene description'
        options = $request.options
        images_sent_unmodified = $true
        recorded_utc = [DateTime]::UtcNow.ToString('o')
        scope = 'Two saved-image smoke requests; not an accuracy or latency benchmark. No game input.'
        results = $results
    } (Join-Path $outputPath 'results.json')
    $result | ConvertTo-Json -Depth 20 -Compress | Write-Output
    $index++
}
