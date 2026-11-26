from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from note_stock.drafts import read_catalog
from note_stock.native import NativeNoteBrowser
from note_stock.publisher import VerificationFailed
from note_stock.store import Store


@pytest.mark.parametrize('other_tab',[False,True])
@pytest.mark.parametrize('outcome',['success','missing_response','cancelled'])
def test_catalog_reuses_startup_blank_without_creating_replacement(tmp_path,monkeypatch,other_tab,outcome):
    blank=Mock()
    blank.url='about:blank'
    blank.is_closed.return_value=False
    blank.get_by_role.return_value.count.return_value=0
    editor=Mock()
    editor.url='https://editor.note.com/notes/existing/edit/'
    editor.is_closed.return_value=False
    context=SimpleNamespace(pages=([editor] if other_tab else [])+[blank],new_page=Mock(),set_default_timeout=Mock())
    blank.context=context
    monkeypatch.setattr('note_stock.managed_browser.connect',lambda *args,**kwargs:('browser',context))
    adapter=NativeNoteBrowser(Store(tmp_path))
    adapter.open(None)
    assert adapter.page is blank
    adapter.check_account=Mock()
    adapter.cancelled=lambda:outcome=='cancelled'
    adapter.progress=Mock()
    response=SimpleNamespace(url='https://note.com/api/v2/notes',ok=True,json=lambda:{'data':{'notes':[], 'total_count':0,'isLastPage':True}})
    def navigate(url,**kwargs):
        blank.url=url
        if outcome=='success':
            blank.on.call_args.args[1](response)
    blank.goto.side_effect=navigate
    if outcome=='success':
        assert read_catalog(adapter)=={'items':[],'complete':True}
    elif outcome=='missing_response':
        with pytest.raises(VerificationFailed,match='記事一覧の取得応答'):
            read_catalog(adapter)
    else:
        with pytest.raises(RuntimeError,match='記事同期を中止'):
            read_catalog(adapter)
    blank.goto.assert_called_once_with(adapter.CORE+'/notes',wait_until='domcontentloaded')
    context.new_page.assert_not_called()
    if other_tab:
        blank.close.assert_called_once()
    else:
        blank.close.assert_not_called()
        assert blank.url==adapter.CORE+'/notes'
    editor.goto.assert_not_called()
    editor.close.assert_not_called()
    blank.remove_listener.assert_called_once()
