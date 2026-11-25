import pytest

from note_stock.native import NativeNoteBrowser
from note_stock.store import Store


@pytest.mark.parametrize("tags", [["保存したタグ"], []])
def test_saved_tags_survive_automatic_recapture_and_restart(tmp_path,tags):
    store=Store(tmp_path)
    pid=store.create({"title":"記事","body":"本文"})
    template={"key":"n123456789abc","account":"tester",
              "payload":{"name":"記事","free_body":"本文","hashtags":["#note側の古いタグ"]}}
    snapshot={"title":"記事","text":"本文","document":{},"fingerprint":"same"}
    store.capture_native(pid,template,snapshot)
    store.save(pid,"記事","本文",tags,"","draft")
    previous=store.get(pid)
    store.capture_native(pid,template,snapshot,edit_state={**previous,"_preserve_app_tags":True})
    reopened=Store(tmp_path)
    assert reopened.get(pid)["tags"]==tags
    assert reopened.native_template(pid)["payload"]["hashtags"]==["#"+tag for tag in tags]
    adapter=NativeNoteBrowser(reopened)
    adapter.update_tags(pid,["遅れて返ってきた古い値"],use_current=True)
    assert reopened.get(pid)["tags"]==tags


def test_explicit_note_edit_can_import_its_new_tags(tmp_path):
    store=Store(tmp_path)
    pid=store.create({"title":"記事","body":"本文","tags":["以前"]})
    template={"key":"n123456789abc","account":"tester",
              "payload":{"name":"記事","free_body":"本文","hashtags":["#noteで編集"]}}
    store.capture_native(pid,template,{"title":"記事","text":"本文","document":{},"fingerprint":"same"})
    assert store.get(pid)["tags"]==["noteで編集"]
