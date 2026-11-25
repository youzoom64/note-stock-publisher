from note_stock.drafts import extract_notes
from note_stock.store import Store


def test_extract_drafts_keeps_status_and_untitled_records():
    data={'data':{'notes':[{'key':'n123456789abc','name':'','status':'draft','user':{'urlname':'tester'}},
                           {'key':'n123456789abd','name':'公開済み','status':'published'},
                           {'key':'n123456789abe','name':'予約済み','status':'reserved'}]}}
    rows=extract_notes(data)
    assert len(rows)==3
    assert rows[0]['title']==''
    assert [row['key'] for row in rows if row['status']=='draft']==['n123456789abc']


def test_live_management_schema_uses_note_draft_name_when_public_name_is_null():
    rows=extract_notes({'data':{'notes':[{'key':'n123456789abf','name':None,'status':'draft',
        'noteDraft':{'name':'下書きのタイトル','updatedAt':'2026-11-24T15:10:00+09:00'},
        'user':{'urlname':'tester'}}]}})
    assert rows==[{'key':'n123456789abf','title':'下書きのタイトル','status':'draft','owner':'tester','updated':'2026-11-24T15:10:00+09:00'}]


def test_note_import_deduplicates_by_remote_id_and_continues_after_invalid_item(tmp_path):
    store=Store(tmp_path)
    rows=[{'key':'n123456789abc','title':'既存の下書き','status':'draft','owner':'tester'},
          {'key':'n123456789abd','title':'公開済み','status':'published','owner':'tester'},
          {'key':'n123456789abe','title':'次の下書き','status':'draft','owner':'tester'}]
    results=store.link_note_drafts(rows)
    assert [row['ok'] for row in results]==[True,False,True]
    again=store.link_note_drafts(rows[:1])
    assert again[0]['id']==results[0]['id'] and not again[0]['new']
    assert len(store.posts())==2
    assert store.get(results[2]['id'])['draft_key']=='n123456789abe'
    assert store.get(results[0]['id'])['native_ready']==0


def test_draft_badge_is_not_treated_as_a_filter_button():
    import json
    from types import SimpleNamespace
    from playwright.sync_api import sync_playwright
    from note_stock.drafts import read_drafts
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page()
        def route(route):
            if '/api/' in route.request.url:
                route.fulfill(content_type='application/json',body=json.dumps({'data':{'notes':[
                    {'key':'n123456789abc','name':'下書き記事','status':'draft'}]}}))
            else:
                route.fulfill(content_type='text/html',body='''<div style="position:relative">
                    <span>下書き</span><button aria-label="下書き記事を編集" style="position:absolute;inset:0" onclick="window.badClick=true"></button>
                    </div><script>fetch('/api/v2/notes')</script>''')
        page.route('**/*',route)
        adapter=SimpleNamespace(page=page,CORE='https://note.com',check_account=lambda:None,cancelled=lambda:False,progress=lambda _:None)
        assert read_drafts(adapter)[0]['key']=='n123456789abc'
        assert page.evaluate('window.badClick') is None
        browser.close()
