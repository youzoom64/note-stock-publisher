import json
from datetime import datetime, timezone


class Diagnostics:
    """Opt-in diagnostics; the operational history lives separately in SQLite."""
    def __init__(self, store):
        self.store = store

    def record(self, event, **fields):
        try:
            if not self.store.setting("diagnostics", False):
                return ""
            folder = self.store.root / "logs"
            folder.mkdir(exist_ok=True)
            # Callers supply identifiers/stages only: no titles, bodies, cookies or passwords.
            row = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
            with (folder / "diagnostics.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
            return ""
        except Exception as exc:
            return "診断ログを保存できません: " + str(exc)
