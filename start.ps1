param([switch]$Demo)
# Starts StravaKiller on http://127.0.0.1:8765. With -Demo it uses made-up data only.
$ErrorActionPreference = 'Stop'
$data = Join-Path $env:LOCALAPPDATA 'GarminCoach'
$python = Join-Path $data 'venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $system = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe') -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -match '^Python31[2-9]$' } | Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $system) { throw 'Python 3.12+ not found. Install it with: winget install Python.Python.3.13 --scope user' }
    New-Item -ItemType Directory -Force $data | Out-Null
    Write-Host 'First run: installing dependencies, this takes a few minutes...'
    & $system.FullName -m venv (Join-Path $data 'venv')
    & $python -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt')
}
if ($Demo) { $env:GARMIN_COACH_DEMO = '1' } else { Remove-Item Env:GARMIN_COACH_DEMO -ErrorAction SilentlyContinue }
$env:NO_PROXY = '127.0.0.1,localhost'
Set-Location $PSScriptRoot
# Opens the browser only once the server is accepting connections.
$open = 'for($i=0;$i -lt 90;$i++){try{(New-Object Net.Sockets.TcpClient(''127.0.0.1'',8765)).Close();break}catch{Start-Sleep 1}};Start-Process ''http://127.0.0.1:8765'''
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $open
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
