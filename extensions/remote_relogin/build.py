"""Build the optional source extension; never include owner configuration."""
import hashlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
plugin=ROOT/'plugin'
if not (plugin/'web/app.js').exists(): raise SystemExit('Build the client first; app.js is missing.')
files={p.relative_to(ROOT).as_posix():p for p in plugin.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
manifest={'id':'note.remote-relogin','name':'Discord内で再ログイン','version':'0.1.0','api':1,
          'files':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in sorted(files.items())}}
out=ROOT.parents[1]/'release';out.mkdir(exist_ok=True)
target=out/'NoteRemoteRelogin-0.1.0.noteext.zip'
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as package:
    package.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    for name,path in sorted(files.items()): package.write(path,name)
digest=hashlib.sha256(target.read_bytes()).hexdigest()
target.with_suffix(target.suffix+'.sha256').write_text(digest+'  '+target.name+'\n',encoding='ascii')
print(json.dumps({'file':str(target),'sha256':digest,'files':len(files),'bytes':target.stat().st_size},ensure_ascii=False))
