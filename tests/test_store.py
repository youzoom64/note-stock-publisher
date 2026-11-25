from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

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


def test_article_preview_preserves_schedule_and_is_invalidated_on_key_change(store):
    pid = post(store, status='scheduled', due=500)
    store.checkpoint(pid, key='n0123456789ab')
    before = store.get(pid)
    store.save_article_preview(pid, before['draft_key'], '読み込んだ本文', '<p>読み込んだ本文</p>')
    reopened = Store(store.root)
    assert reopened.get(pid) == before
    assert reopened.article_preview(pid)['body'] == '読み込んだ本文'
    reopened.checkpoint(pid, key='n123456789abc')
    assert reopened.article_preview(pid) is None
    with pytest.raises(ValueError):
        reopened.save_article_preview(pid, before['draft_key'], '古い本文', '')


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


def test_normal_startup_resumes_verified_account_after_expiring_old_reservations(store):
    store.set_setting('paused',True)
    store.set_setting('account','tester')
    store.set_setting('account_verified',True)
    pid=post(store,'scheduled',150)
    reopened=Store(store.root)
    assert pid in reopened.recover(200,start_automatic_posting=True)
    assert reopened.setting('paused') is False
    assert reopened.get(pid)['status']=='overdue'
    assert Scheduler(reopened,200).tick(201) is None


def test_startup_waits_for_first_account_verification_and_non_gui_recovery_keeps_pause(store):
    store.recover(200,start_automatic_posting=True)
    assert store.setting('paused') is True
    store.set_setting('account_verified',True)
    store.recover(201)
    assert store.setting('paused') is True


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


def test_recurring_override_moves_each_queued_post_one_daily_slot_later(store):
    ready(store)
    monday=datetime(2026,11,23,9,0,tzinfo=JST)
    a,b,priority=post(store),post(store),post(store)
    store.set_recurring_override(priority,'2026-11-23',stamp=monday.timestamp()-60)
    reopened=Store(store.root)
    assert reopened.recurring_override(priority)=='2026-11-23'
    for index,pid in enumerate((priority,a,b)):
        stamp=(monday+timedelta(days=index)).timestamp()
        scheduler=Scheduler(reopened,stamp-1)
        assert scheduler.tick(stamp)['id']==pid
        reopened.finish(pid,'published')
        assert scheduler.tick(stamp+1) is None
    assert reopened.recurring_override(priority) is None


def test_recurring_override_waits_for_its_day_and_rejects_duplicate_or_non_recurring_day(store):
    ready(store)
    store.set_setting('recurrence',{'days':[0,2],'time':'09:00'})
    monday=datetime(2026,11,23,9,0,tzinfo=JST).timestamp()
    priority,first=post(store),post(store)
    with pytest.raises(ValueError,match='定期投稿の日'):
        store.set_recurring_override(priority,'2026-11-24',stamp=monday-60)
    store.set_recurring_override(priority,'2026-11-25',stamp=monday-60)
    with pytest.raises(ValueError,match='別の記事'):
        store.set_recurring_override(first,'2026-11-25',stamp=monday-60)
    assert Scheduler(store,monday-1).tick(monday)['id']==first
    store.finish(first,'published')
    store.set_recurring_override(priority,None)
    stamp=datetime(2026,11,25,9,0,tzinfo=JST).timestamp()
    assert Scheduler(store,stamp-1).tick(stamp)['id']==priority


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
    store.set_setting('diagnostics', False)
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


def test_diagnostic_session_preserves_logs_until_next_start(store):
    from note_stock.diagnostics import DiagnosticSession

    first = DiagnosticSession(store)
    first.configure(True)
    error = RuntimeError('test credential must not appear')
    first.log.error('operation_failed',error,stage='startup')
    first.configure(False)
    path = store.root/'logs'/'diagnostics.jsonl'
    data = path.read_text(encoding='utf-8')
    assert 'operation_failed' in data and 'RuntimeError' in data
    assert 'test credential' not in data
    first.log.error('disabled_error',error)
    assert path.read_text(encoding='utf-8')==data
    second = DiagnosticSession(store)
    second.configure(True)
    try:
        assert path.read_text(encoding='utf-8')==''
        second.log.record('another_start')
        assert 'another_start' in path.read_text(encoding='utf-8')
    finally:
        second.configure(False)


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
