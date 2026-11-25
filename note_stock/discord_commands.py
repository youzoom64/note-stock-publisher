"""User-configurable slash command names; stable action keys stay internal."""
import re

COMMANDS={
    'menu':('管理メニュー・検索','note'),
    'new':('新規下書き','note_new'),
    'list':('記事一覧','note_list'),
    'schedules':('予約一覧','note_schedules'),
    'status':('接続状態','note_status'),
    'sync':('noteと同期','note_sync'),
    'pause':('自動投稿を停止','note_pause'),
    'resume':('自動投稿を再開','note_resume'),
    'read':('本文・公開条件を読み込む','note_read'),
    'schedule':('予約・日時変更','note_schedule'),
    'cancel':('予約取消','note_cancel'),
    'publish':('今すぐ投稿・更新','note_publish'),
    'delete':('noteから削除','note_delete'),
    'tags':('ハッシュタグ変更','note_tags'),
    'help':('コマンド一覧','note_help'),
    'relogin':('Discord内でnoteへ再ログイン','note_relogin'),
}


def command_names(values=None):
    values=values or {}
    names={key:str(values.get(key,default)).strip().removeprefix('/') for key,(_,default) in COMMANDS.items()}
    for key,name in names.items():
        if not re.fullmatch(r'[a-z0-9_\-\u3041-\u3096\u30a1-\u30fa\u3400-\u9fff]{1,32}',name):
            raise ValueError(COMMANDS[key][0]+': コマンド名は小文字英数字・日本語・ハイフン・下線の1～32文字で入力してください。')
    if len(set(names.values()))!=len(names):
        raise ValueError('同じコマンド名を複数の機能に設定できません。')
    return names


def command_help(store):
    names=command_names(store.setting('discord_commands',{}))
    return '\n'.join('/'+names[key]+' — '+label for key,(label,_) in COMMANDS.items())
