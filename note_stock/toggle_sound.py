"""Play the approved sounds when automatic posting changes state."""
from pathlib import Path

import winsound

from PySide6.QtCore import QObject, QTimer


class ToggleSound(QObject):
    def __init__(self, store, parent):
        super().__init__(parent)
        self.store = store
        self.paused = bool(store.setting("paused", True))
        self.effects = {}
        for paused, name in ((False, "on"), (True, "off")):
            self.effects[paused] = str(
                Path(__file__).resolve().parent / "sounds" / f"auto_post_{name}.wav"
            )
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_state)
        self.timer.start(200)

    def check_state(self):
        paused = bool(self.store.setting("paused", True))
        if paused != self.paused:
            self.paused = paused
            winsound.PlaySound(
                self.effects[paused],
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
