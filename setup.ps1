param([switch]$NoLaunch)
$ErrorActionPreference='Stop'
$noteRoot=$PSScriptRoot
$setupLock=$null
function Download-Checked {
    param([string]$Url,[string]$Path,[string]$Sha256='')
    if(-not(Test-Path -LiteralPath $Path)){Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Path}
    if($Sha256 -and (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256){throw ('Downloaded file checksum failed: '+[IO.Path]::GetFileName($Path))}
}
function Assert-Signed {
    param([string]$Path,[string]$Publisher)
    $signature=Get-AuthenticodeSignature -LiteralPath $Path
    if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $Publisher){throw ('Signature verification failed: '+[IO.Path]::GetFileName($Path))}
}
try {
    Write-Host 'noteストック 32bit版の実行環境を、このフォルダー内に準備します。'
    Set-Location -LiteralPath $noteRoot
    $tempFolder=Join-Path $noteRoot 'data\temp'
    $runtime=Join-Path $noteRoot 'runtime32'
    New-Item -ItemType Directory -Force -Path $tempFolder,$runtime | Out-Null
    $env:TEMP=$tempFolder
    $env:TMP=$tempFolder
    $env:PIP_CACHE_DIR=Join-Path $noteRoot 'data\pip-cache'
    $env:PYTHONDONTWRITEBYTECODE='1'
    try {$setupLock=[IO.File]::Open((Join-Path $tempFolder 'runtime-setup.lock'),'OpenOrCreate','Write','None')}
    catch [IO.IOException] {throw '別のウィンドウで32bit版の準備を実行しています。'}
    $pythonFolder=Join-Path $runtime 'python'
    $pythonExe=Join-Path $pythonFolder 'python.exe'
    if(-not(Test-Path -LiteralPath $pythonExe)){
        Write-Host '32bit版Pythonをダウンロードしています…'
        $archive=Join-Path $tempFolder 'python-3.9.13-embed-win32.zip'
        Download-Checked 'https://www.python.org/ftp/python/3.9.13/python-3.9.13-embed-win32.zip' $archive 'f8ed5e019d7bc6dba1d7dfa5d59052b5241c37e8eaa5293133c898ac7acedb98'
        Expand-Archive -LiteralPath $archive -DestinationPath $pythonFolder -Force
    }
    Assert-Signed $pythonExe 'Python Software Foundation'
    & $pythonExe -B -c "import struct,sys; assert struct.calcsize('P')==4 and sys.version_info[:2]==(3,9); print('32bit Python: OK')"
    if($LASTEXITCODE -ne 0){throw '32bit Pythonを実行できません。'}
    $site=Join-Path $pythonFolder 'Lib\site-packages'
    New-Item -ItemType Directory -Force -Path $site | Out-Null
    Set-Content -LiteralPath (Join-Path $pythonFolder 'python39._pth') -Encoding Ascii -Value @('python39.zip','.','Lib\site-packages','import site')
    Write-Host '32bit対応ライブラリを準備しています…'
    $pipMetadata=Invoke-RestMethod 'https://pypi.org/pypi/pip/25.3/json'
    $pipWheel=$pipMetadata.urls | Where-Object filename -like '*.whl' | Select-Object -First 1
    $pipPath=Join-Path $tempFolder $pipWheel.filename
    Download-Checked $pipWheel.url $pipPath $pipWheel.digests.sha256
    & $pythonExe -B -c "import sys; sys.path.insert(0,sys.argv[1]); from pip._internal.cli.main import main; raise SystemExit(main(sys.argv[2:]))" $pipPath install --disable-pip-version-check --only-binary=:all: --upgrade --target $site -r (Join-Path $noteRoot 'requirements.txt')
    if($LASTEXITCODE -ne 0){throw '32bit対応ライブラリのインストールに失敗しました。'}
    $nodeFolder=Join-Path $runtime 'node-v22.23.5-win-x86'
    $nodeExe=Join-Path $nodeFolder 'node.exe'
    if(-not(Test-Path -LiteralPath $nodeExe)){
        Write-Host '32bit版のブラウザー操作エンジンを準備しています…'
        $nodeZip=Join-Path $tempFolder 'node-v22.23.5-win-x86.zip'
        Download-Checked 'https://nodejs.org/dist/v22.23.5/node-v22.23.5-win-x86.zip' $nodeZip '9c01245683c87e3f81eedfbad82c1665611749627cd1b0b5a42921db9361b4a6'
        Expand-Archive -LiteralPath $nodeZip -DestinationPath $runtime -Force
    }
    Assert-Signed $nodeExe 'OpenJS Foundation'
    $env:PLAYWRIGHT_NODEJS_PATH=$nodeExe
    & $nodeExe -p 'process.arch'
    if($LASTEXITCODE -ne 0){throw '32bitブラウザー操作エンジンを実行できません。'}
    $browser=Join-Path $runtime 'chrome-win32\chrome.exe'
    if(-not(Test-Path -LiteralPath $browser)){
        Write-Host '32bit専用ブラウザーを準備しています…'
        $browserZip=Join-Path $tempFolder 'chrome-win32.zip'
        Download-Checked 'https://storage.googleapis.com/chrome-for-testing-public/158.0.8127.88/win32/chrome-win32.zip' $browserZip 'b994ff42d7b4b6c90bf66e13d7f4653ee4bb88dd5677e08241c2f3f01a61e877'
        Expand-Archive -LiteralPath $browserZip -DestinationPath $runtime -Force
    }
    # Chrome for Testing is unsigned; verify the pinned official archive instead.
    $browserZip=Join-Path $tempFolder 'chrome-win32.zip'
    Download-Checked 'https://storage.googleapis.com/chrome-for-testing-public/158.0.8127.88/win32/chrome-win32.zip' $browserZip 'b994ff42d7b4b6c90bf66e13d7f4653ee4bb88dd5677e08241c2f3f01a61e877'
    Expand-Archive -LiteralPath $browserZip -DestinationPath $runtime -Force
    & $pythonExe -B -c "import sys; sys.path.insert(0,sys.argv[1]); from note_stock.managed_browser import is_x86_executable; assert all(is_x86_executable(p) for p in sys.argv[2:]); from note_stock.qt import BINDING; assert BINDING=='PySide2'; print('Python / Node / Chrome: all 32bit')" $noteRoot $pythonExe $nodeExe $browser
    if($LASTEXITCODE -ne 0){throw '32bit実行環境の確認に失敗しました。'}
    Set-Content -LiteralPath (Join-Path $runtime 'ready') -Encoding Ascii -Value 'win32'
    Write-Host '32bit版の準備が完了しました。'
    if(-not $NoLaunch){Start-Process -FilePath (Join-Path $pythonFolder 'pythonw.exe') -ArgumentList ('-B "'+(Join-Path $noteRoot 'main.py')+'"') -WorkingDirectory $noteRoot -WindowStyle Normal}
    exit 0
} catch {
    Write-Host ('32bit版の準備に失敗しました: '+$_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {if($setupLock){$setupLock.Dispose()}}
