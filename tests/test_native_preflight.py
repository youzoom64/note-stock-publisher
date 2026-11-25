from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from note_stock.native import NativeNoteBrowser
from note_stock.publisher import VerificationFailed
from note_stock.store import Store


@pytest.mark.parametrize('title',['','  \u3000'])
def test_automatic_read_explains_missing_title_before_opening_publication(tmp_path,monkeypatch,title):
    store=Store(tmp_path)
    pid=store.create({'title':'','body':'テスト'})
    store.checkpoint(pid,key='n123456789abc')
    adapter=NativeNoteBrowser(store)
    adapter.check_account=Mock(return_value='tester')
    adapter.wait_draft=Mock(return_value='n123456789abc')
    adapter.editor_fields=Mock(return_value=(SimpleNamespace(input_value=lambda:title),Mock()))
    adapter.first_visible=Mock()
    adapter.context=SimpleNamespace(pages=[])
    adapter.page=Mock()
    adapter.page.url='https://editor.note.com/notes/n123456789abc/edit/'
    adapter.page.is_closed.return_value=False
    monkeypatch.setattr('note_stock.native.install_gate',Mock())
    with pytest.raises(VerificationFailed,match='記事タイトルが未入力'):
        adapter.edit(store.get(pid),automatic=True)
    adapter.first_visible.assert_not_called()
    adapter.page.close.assert_called_once()
    assert not store.get(pid)['native_ready']
