from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from note_stock.store import JST, Store, Scheduler, tags_from_text
from note_stock.diagnostics import Diagnostics


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def post(store, status='queued', due=None):
    pid = store.create({'editor_kind':'legacy'})
    store.save(pid, 'タイトル', '一行目\n\n二行目', ['日記', '旅行'], '', status, due, stamp=100)
    return pid


def test_roundtrip_duplicate_and_reorder(store):
    a = post(store)
    b = store.create(store.get(a))
    store.reorder(b, -1)
    reopened = Store(store.root)
    assert [p['id'] for p in reopened.posts()] == [b, a]
    assert reopened.get(a)['body'] == '一行目\n\n二行目'
    assert reopened.get(b)['tags'] == ['日記', '旅行']
    assert reopened.get(b)['status'] == 'draft'
    assert reopened.get(b)['draft_key'] == ''


def test_tags_normalized_and_deduplicated():
    assert tags_from_text('#日記,＃旅行\n日記、仕事') == ['日記', '旅行', '仕事']
    with pytest.raises(ValueError):
        tags_from_text('日記#旅行')


def test_no_duplicate_claims(store):
    a, b = post(store), post(store)
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(lambda _: store.claim(200, slot='slot'), range(2)))
    assert sum(item is not None for item in claimed) == 1
    assert store.get(a)['status'] == 'posting'
    assert store.get(b)['status'] == 'queued'


def test_crash_recovers_unknown_even_before_submit(store):
    pid = post(store)
    store.claim(200, post_id=pid)
    store.recover(201)
    assert store.get(pid)['status'] == 'unknown'
    with pytest.raises(ValueError):
        store.claim(202, post_id=pid)
    with pytest.raises(ValueError):
        store.save(pid, 'x', 'y', [], '', 'draft')


def test_submit_boundary_durable(store):
    pid = post(store)
    store.claim(200, post_id=pid)
    store.checkpoint(pid, key='n123456789abc', submitted=True)
    reopened = Store(store.root)
    reopened.recover(201)
    assert reopened.get(pid)['submitted'] == 1
    assert reopened.get(pid)['draft_key'] == 'n123456789abc'
    assert reopened.get(pid)['status'] == 'unknown'


def test_expired_startup_no_burst(store):
    pid = post(store, 'scheduled', 150)
    assert pid in store.recover(200)
    assert store.get(pid)['status'] == 'overdue'
    assert store.claim(201) is None


def ready(store):
    store.set_setting('account', 'tester')
    store.set_setting('paused', False)
    store.set_setting('recurrence', {'days': list(range(7)), 'time': '09:00'})


def test_recurring_once_per_slot_and_empty_slot(store):
    ready(store)
    t = datetime(2026, 11, 24, 9, 0, tzinfo=JST).timestamp()
    a, b = post(store), post(store)
    scheduler = Scheduler(store, t-1)
    assert scheduler.tick(t)['id'] == a
    store.finish(a, 'published', url='https://note.com/tester/n/n123456789abc')
    assert scheduler.tick(t+1) is None
    assert store.get(b)['status'] == 'queued'


def test_busy_slot_and_explicit_due_wait_instead_of_expiring(store):
    ready(store)
    t = datetime(2026, 11, 24, 9, 0, tzinfo=JST).timestamp()
    explicit = post(store, 'scheduled', t)
    periodic = post(store)
    scheduler = Scheduler(store, t-1)
    assert scheduler.tick(t, busy=True) is None
    assert store.get(explicit)['status'] == 'scheduled'
    assert scheduler.tick(t+1)['id'] == explicit
    store.finish(explicit, 'published')
    assert scheduler.tick(t+2)['id'] == periodic


def test_sleep_and_clock_changes(store):
    ready(store)
    pid = post(store, 'scheduled', 150)
    scheduler = Scheduler(store, 120)
    assert scheduler.tick(300) is None
    assert store.get(pid)['status'] == 'overdue'
    assert scheduler.tick(299) is None


def test_missing_image_preserves_old_content(store):
    pid = post(store)
    with pytest.raises(ValueError):
        store.save(pid, '壊さない', '本文', [], 'images/missing.png', 'queued')
    assert store.get(pid)['title'] == 'タイトル'
    with pytest.raises(ValueError):
        store.image_path('../secret.png')


def test_partial_settings_save_keeps_independent_success(store, monkeypatch):
    store.set_setting('old', 'keep')
    original = store.set_setting
    def save(key, value):
        if key == 'old':
            raise OSError('simulated disk error')
        original(key, value)
    monkeypatch.setattr(store, 'set_setting', save)
    result = store.settings_batch({'first': 1, 'old': 'replace', 'last': 2})
    assert result['old'] and not result['last']
    assert store.setting('old') == 'keep'
    assert store.setting('last') == 2


def test_diagnostics_opt_in(store):
    log = Diagnostics(store)
    log.record('off')
    assert not (store.root/'logs').exists()
    store.set_setting('diagnostics', True)
    log.record('on', post_id='test')
    file = store.root/'logs'/'diagnostics.jsonl'
    data = file.read_bytes()
    assert b'"event": "on"' in data
    store.set_setting('diagnostics', False)
    log.record('off_again')
    assert file.read_bytes() == data


@pytest.mark.parametrize('status', ['scheduled','queued','review'])
def test_native_reimport_preserves_posting_plan(store,status):
    from note_stock.store import now_ts
    pid=store.create({'editor_kind':'legacy','title':'検証','body':'本文'})
    due=now_ts()+3600 if status in {'scheduled','review'} else None
    store.save(pid,'検証','本文',[],'','scheduled' if status=='review' else status,due)
    if status=='review':
        with store.connect() as c:
            c.execute("UPDATE posts SET status='review' WHERE id=?",(pid,))
    edit=store.begin_native_edit(pid)
    template={'payload':{'name':'更新済み原稿','free_body':'<p>更新</p>'},'account':'tester'}
    snapshot={'document':{},'fingerprint':'test','title':'更新済み原稿','text':'更新'}
    store.capture_native(pid,template,snapshot,edit_state=edit)
    saved=store.get(pid)
    assert saved['status']==status and saved['due']==due
    assert saved['title']=='更新済み原稿' and saved['native_ready']


def test_native_edit_past_due_requires_rescheduling(store):
    from note_stock.store import now_ts
    pid=store.create({'editor_kind':'legacy','title':'検証','body':'本文'})
    store.save(pid,'検証','本文',[],'','scheduled',now_ts()+3600)
    edit=store.begin_native_edit(pid)
    edit['_previous_due']=now_ts()-1
    store.capture_native(pid,{'payload':{'name':'検証'},'account':'tester'},
                         {'document':{},'fingerprint':'test','title':'検証','text':'本文'},edit_state=edit)
    assert store.get(pid)['status']=='overdue'
    assert store.claim(now_ts()) is None
