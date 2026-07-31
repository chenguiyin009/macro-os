# Daily macro wrapper for Windows Task Scheduler
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
if (-not $env:HTTPS_PROXY) { $env:HTTPS_PROXY = "http://127.0.0.1:7890" }
if (-not $env:HTTP_PROXY)  { $env:HTTP_PROXY  = "http://127.0.0.1:7890" }

$root = "C:\Users\chengy12005\Documents\tradingview"
# Pin to the managed venv Python: it has yfinance/pandas/numpy installed.
# Bare "python" under Task Scheduler can resolve to a system interpreter
# without these packages and fail silently.
$py   = "C:\Users\chengy12005\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
$script = Join-Path $root "macro-os\scripts\run_daily_macro.py"
$outDir = Join-Path $root "output\logs"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$log = Join-Path $outDir "scheduler_$stamp.log"

& $py $script --report-dir (Join-Path $root "output") *>> $log
$code = $LASTEXITCODE
"EXIT=$code" | Out-File -Append -Encoding utf8 $log
exit $code