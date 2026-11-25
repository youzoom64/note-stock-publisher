"""Create a reviewable source archive. Never build or include executables."""
import ast
import hashlib
import json
import re
import sys
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from note_stock import __version__
entries=[ROOT/name for name in ('main.py','requirements.txt','setup.ps1','start.cmd','README.md','VALIDATION.md','THIRD_PARTY_NOTICES.md')]
entries.extend(path for path in (ROOT/'note_stock').rglob('*.py') if '__pycache__' not in path.parts)
entries.append(ROOT/'scripts'/'package_source.py')
activity_assets=tuple(ROOT/'extensions/remote_relogin/plugin/web'/name for name in ('index.html','style.css','app.js'))
entries.extend(activity_assets)
for path in entries:
    content=path.read_text(encoding='utf-8-sig')
    if path.suffix=='.py':
        ast.parse(content,filename=str(path))
    if path not in activity_assets and re.search(r'\b[A-Za-z]:[\\/]',content):
        raise RuntimeError('Fixed drive path in distribution: '+str(path.relative_to(ROOT)))
    if re.search(r'-WindowStyle\s+Hidden\b',content,re.I):
        raise RuntimeError('Hidden window launch in distribution: '+str(path.relative_to(ROOT)))
    if re.search(r'\bn(?=[a-z0-9]{12}\b)(?=[a-z0-9]*\d)[a-z0-9]{12}\b',content):
        raise RuntimeError('Hardcoded note article ID in distribution: '+str(path.relative_to(ROOT)))
    if path.is_relative_to(ROOT/'note_stock'):
        if any(name in content for name in ('NOTE_STOCK_TEST_CDP_FILE','NOTE_STOCK_BROWSER_EXE')):
            raise RuntimeError('Development browser override in runtime: '+str(path.relative_to(ROOT)))
out=ROOT/'release'
out.mkdir(exist_ok=True)
archive=out/f'NoteStockPublisher-{__version__}-source.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as target:
    for path in sorted(set(entries)):
        assert path.resolve().is_relative_to(ROOT)
        target.write(path,Path('NoteStockPublisher')/path.relative_to(ROOT))
with zipfile.ZipFile(archive) as target:
    names=target.namelist()
    assert target.testzip() is None
    assert not any(Path(name).suffix.lower() in {'.exe','.dll','.pyd','.sqlite3','.db'} for name in names)
    assert not any(part in {'data','artifacts','.venv','browser-profiles','tests'} for name in names for part in Path(name).parts)
    assert all(target.read(name)==(ROOT/Path(name).relative_to('NoteStockPublisher')).read_bytes() for name in names)
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
(out/(archive.name+'.sha256')).write_text(digest+'  '+archive.name+'\n',encoding='ascii')
report={'archive':str(archive),'bytes':archive.stat().st_size,'sha256':digest,'members':len(names),'no_executable_or_user_data':True,'python_syntax':'passed','archive_crc':'passed','source_matches':'passed','no_developer_tests_or_fixed_paths':True}
(ROOT/'artifacts').mkdir(exist_ok=True)
(ROOT/'artifacts'/'source-release.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False),flush=True)
