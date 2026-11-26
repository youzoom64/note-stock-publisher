param([switch]$NoLaunch)
$ErrorActionPreference = 'Stop'
$noteRoot = $PSScriptRoot
$setupLock = $null
try {
    Write-Host 'Pythonを確認し、noteストック専用の環境に必要なライブラリを入れます。'
    if (-not $NoLaunch) {
        Write-Host '準備が終わると、noteストックが開き、Discord連携を使うか選べます。'
        Write-Host '連携の設定を始めるとブラウザーでDiscordが開きます。必要に応じてログインや承認をしてください。'
        Write-Host '「今は使わない」を選んでも、PC上でnoteストックを使えます。'
    }
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
    if ($noteRoot.Length -gt 70) {
        Add-Type -AssemblyName System.Windows.Forms
        $picker = New-Object System.Windows.Forms.FolderBrowserDialog
        $picker.Description = 'Choose a short writable folder for NoteStockPublisher'
        if ($picker.ShowDialog() -ne 'OK') { throw 'Setup cancelled.' }
        $destination = Join-Path $picker.SelectedPath 'NoteStockPublisher'
        if ($destination.Length -gt 70 -or (Test-Path -LiteralPath $destination)) { throw 'Choose a shorter folder without an existing NoteStockPublisher folder.' }
        New-Item -ItemType Directory -Path $destination | Out-Null
        foreach ($name in @('main.py','requirements.txt','setup.ps1','start.cmd','README.md','README.txt','manual.html','VALIDATION.md','THIRD_PARTY_NOTICES.md')) {
            Copy-Item -LiteralPath (Join-Path $noteRoot $name) -Destination $destination
        }
        Copy-Item -LiteralPath (Join-Path $noteRoot 'note_stock') -Destination $destination -Recurse
        Copy-Item -LiteralPath (Join-Path $noteRoot 'guide') -Destination $destination -Recurse
        $activityAssets = 'extensions\remote_relogin\plugin\web'
        $activityDestination = Join-Path $destination $activityAssets
        New-Item -ItemType Directory -Path $activityDestination -Force | Out-Null
        foreach ($asset in @('index.html','style.css','app.js')) {
            Copy-Item -LiteralPath (Join-Path (Join-Path $noteRoot $activityAssets) $asset) -Destination $activityDestination
        }
        $noteRoot = $destination
    }
    Set-Location -LiteralPath $noteRoot
    $tempFolder = Join-Path $noteRoot 'data\temp'
    New-Item -ItemType Directory -Path $tempFolder -Force | Out-Null
    try {
        $setupLock = [IO.File]::Open((Join-Path $noteRoot 'data\runtime-setup.lock'), 'OpenOrCreate', 'Write', 'None')
    } catch [IO.IOException] {
        throw '別のウィンドウで初回準備が進行中です。そちらの完了をお待ちください。'
    }
    $env:TEMP = $tempFolder
    $env:TMP = $tempFolder
    $env:PIP_CACHE_DIR = Join-Path $noteRoot 'data\pip-cache'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $noteRoot 'data\browsers'
    Write-Host 'Checking Python and preparing the private runtime...'
    $notePython = $null
    $candidates = @((Join-Path $noteRoot '.venv\Scripts\python.exe'),(Join-Path $noteRoot 'runtime\python.exe'))
    foreach ($command in @('py','python')) {
        $resolved = Get-Command $command -ErrorAction SilentlyContinue
        if ($resolved -and $resolved.Source -notlike '*WindowsApps*') {
            try {
                if ($command -eq 'py') { $candidate = & py -3 -c 'import sys; print(sys.executable)' 2>$null }
                else { $candidate = & python -c 'import sys; print(sys.executable)' 2>$null }
                if ($LASTEXITCODE -eq 0) { $candidates += $candidate }
            } catch { }
        }
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            try {
                & $candidate -c "import sys,struct; sys.exit(0 if (3,10)<=sys.version_info[:2]<(3,15) and struct.calcsize('P')==8 else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) { $notePython = $candidate; break }
            } catch { }
        }
    }
    if (-not $notePython) {
        Write-Host 'Downloading signed official Python 3.14.10...'
        $installer = Join-Path $tempFolder 'python-3.14.10-amd64.exe'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-amd64.exe' -OutFile $installer
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
            throw 'Official Python installer signature verification failed.'
        }
        $runtime = Join-Path $noteRoot 'runtime'
        $process = Start-Process -FilePath $installer -ArgumentList @('/passive','InstallAllUsers=0',('TargetDir="'+$runtime+'"'),'PrependPath=0','AppendPath=0','Include_launcher=0','Include_test=0','Include_doc=0','Shortcuts=0','AssociateFiles=0','Include_pip=1') -WindowStyle Normal -Wait -PassThru
        if ($process.ExitCode -notin @(0,3010)) { throw ('Python setup failed: '+$process.ExitCode) }
        $notePython = Join-Path $runtime 'python.exe'
    }
    $venvPython = Join-Path $noteRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $notePython -m venv (Join-Path $noteRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    & $venvPython -m pip install --disable-pip-version-check -r (Join-Path $noteRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & $venvPython -c "import sys; from note_stock.managed_browser import installed_browser; sys.exit(0 if installed_browser('chrome') or installed_browser('edge') else 1)"
    if ($LASTEXITCODE -ne 0) {
        & $venvPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Browser installation failed.' }
    }
    Set-Content -LiteralPath (Join-Path $noteRoot 'data\runtime-ready') -Value 'ready' -Encoding Ascii
    Write-Host 'Runtime setup completed. Choose whether to use Discord in the app.'
    if (-not $NoLaunch) {
        Start-Process -FilePath (Join-Path $noteRoot '.venv\Scripts\pythonw.exe') -ArgumentList ('"'+(Join-Path $noteRoot 'main.py')+'"') -WorkingDirectory $noteRoot -WindowStyle Normal
    }
    exit 0
} catch {
    Write-Host ('Setup failed: '+$_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($setupLock) { $setupLock.Dispose() }
}
