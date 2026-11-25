"""Distribution boundary checks; no author identifiers or machine paths."""
import re
from pathlib import Path


def test_runtime_has_no_fixed_drive_paths_article_ids_or_developer_hooks():
    root=Path(__file__).resolve().parents[1]
    files=[root/'main.py',root/'setup.ps1',root/'start.cmd',*list((root/'note_stock').rglob('*.py'))]
    for path in files:
        text=path.read_text(encoding='utf-8-sig')
        assert not re.search(r'\b[A-Za-z]:[\\/]',text),str(path)
        assert not re.search(r'\bn(?=[a-z0-9]{12}\b)(?=[a-z0-9]*\d)[a-z0-9]{12}\b',text),str(path)
        assert 'NOTE_STOCK_TEST_CDP_FILE' not in text,str(path)
        assert 'NOTE_STOCK_BROWSER_EXE' not in text,str(path)
        assert not re.search(r'-WindowStyle\s+Hidden\b',text,re.I),str(path)
