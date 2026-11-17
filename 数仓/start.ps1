param([switch]$NoBrowser, [int]$Port = 0)
$ErrorActionPreference = 'Stop'
if ($Port -eq 0) { $Port = if ($env:WH_PORT) { [int]$env:WH_PORT } else { 8765 } }
$expectedDb = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'warehouse.db'))
$url = "http://127.0.0.1:$Port"
function Get-ServiceHealth {
    try { return Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2 } catch { return $null }
}
$health = Get-ServiceHealth
if ($health) {
    if (-not $health.ok -or [IO.Path]::GetFullPath($health.db) -ne $expectedDb) {
        throw "Port $Port belongs to another project. Set WH_PORT to an unused port."
    }
} else {
    $candidates = @(
        $env:FINANCE_PYTHON,
        (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'),
        (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
        (Join-Path $env:USERPROFILE '.workbuddy\binaries\python\envs\default\Scripts\python.exe')
    )
    $candidates += @(Get-Command python.exe -All -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    $pythonPath = $null
    foreach ($candidate in ($candidates | Where-Object { $_ -and $_ -notlike '*\WindowsApps\*' } | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            & $candidate -c "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in ('sqlite3','openpyxl')) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { $pythonPath = $candidate; break }
        }
    }
    if (-not $pythonPath) { throw 'Python with openpyxl was not found. Set FINANCE_PYTHON to a working python.exe.' }
    $oldPort = $env:WH_PORT
    $oldEncoding = $env:PYTHONIOENCODING
    try {
        $env:WH_PORT = "$Port"; $env:PYTHONIOENCODING = 'utf-8'
        $service = Start-Process -FilePath $pythonPath -ArgumentList '-u', 'server.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot 'server.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'server.stderr.log')
    } finally { $env:WH_PORT = $oldPort; $env:PYTHONIOENCODING = $oldEncoding }
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $service.Refresh()
        if ($service.HasExited) { throw 'Server failed to start. See server.stderr.log.' }
        $health = Get-ServiceHealth
        if ($health -and $health.ok) { break }
        Start-Sleep -Milliseconds 200
    }
    if (-not $health -or [IO.Path]::GetFullPath($health.db) -ne $expectedDb) { throw 'Server health check failed. See server.stderr.log.' }
    $service.Id | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'server.pid') -Encoding Ascii
}
Write-Host "Ready: $url"
Write-Host "Database: $expectedDb"
if (-not $NoBrowser) { Start-Process $url }
