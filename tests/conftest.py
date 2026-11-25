import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT=Path(__file__).resolve().parents[1]
temporary=ROOT/'artifacts'/'test-temp'
temporary.mkdir(parents=True,exist_ok=True)
os.environ['TEMP']=os.environ['TMP']=str(temporary)
tempfile.tempdir=str(temporary)
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'artifacts'/'test-browsers'))
