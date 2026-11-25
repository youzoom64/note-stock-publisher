import pytest

from note_stock.discord_bot import article_text, operation_embed
from note_stock.store import Store
from note_stock.ui import MainWindow


URL = "https://note.com/tester/n/n123456789abc"


def test_discord_published_article_and_result_include_public_url(tmp_path):
    store = Store(tmp_path)
    store.set_setting("account", "tester")
    post = store.merge_catalog([
        {"key": "n123456789abc", "title": "公開済み記事", "status": "published"}
    ], True)[0]
    assert "公開URL: " + URL in article_text(store, store.get(post["id"]))
    result = operation_embed(store, "result", {
        "ok": True, "message": "投稿しました。", "url": URL, "action": "publish_note",
    })
    assert URL in result.description


@pytest.mark.parametrize("status", ["published", "draft", "native_reserved"])
def test_gui_shows_clickable_url_only_for_published_article(tmp_path, qt_application, status):
    store = Store(tmp_path)
    pid = store.create({"title": "URL表示テスト", "body": "本文"})
    if status != "draft":
        store.finish(pid, status, url=URL)
    window = MainWindow(store, offline=True)
    try:
        window.show()
        window.load(pid)
        qt_application.processEvents()
        if status == "published":
            assert not window.public_url_label.isHidden()
            assert f'href="{URL}"' in window.public_url_label.text()
            assert window.public_url_label.openExternalLinks()
            window.worker_result("published", "投稿しました。", URL)
            assert URL in window.statusBar().currentMessage()
        else:
            assert window.public_url_label.isHidden()
        window.load(None)
        assert window.public_url_label.isHidden()
    finally:
        window.quitting = True
        window.close()
