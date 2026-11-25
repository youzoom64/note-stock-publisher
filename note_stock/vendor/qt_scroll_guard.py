from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


def _import_qt():
    for binding in ("PySide6",):
        try:
            if binding == "PyQt6":
                from PyQt6.QtCore import QObject, QTimer
                from PyQt6.QtWidgets import QAbstractItemView, QAbstractScrollArea
            elif binding == "PySide6":
                from PySide6.QtCore import QObject, QTimer
                from PySide6.QtWidgets import QAbstractItemView, QAbstractScrollArea
            elif binding == "PyQt5":
                from PyQt5.QtCore import QObject, QTimer
                from PyQt5.QtWidgets import QAbstractItemView, QAbstractScrollArea
            else:
                from PySide2.QtCore import QObject, QTimer
                from PySide2.QtWidgets import QAbstractItemView, QAbstractScrollArea
            return binding, QObject, QTimer, QAbstractItemView, QAbstractScrollArea
        except ImportError:
            continue
    raise ImportError("PyQt6, PySide6, PyQt5, or PySide2 is required")


QT_BINDING, QObject, QTimer, QAbstractItemView, QAbstractScrollArea = _import_qt()


def _enum(owner: Any, group: str, name: str) -> Any:
    grouped = getattr(owner, group, None)
    if grouped is not None and hasattr(grouped, name):
        return getattr(grouped, name)
    return getattr(owner, name)


SCROLL_PER_PIXEL = _enum(QAbstractItemView, "ScrollMode", "ScrollPerPixel")


@dataclass(frozen=True)
class ScrollState:
    horizontal: int
    vertical: int
    was_at_bottom: bool
    horizontal_maximum: int
    vertical_maximum: int


def _horizontal_bar(target: Any) -> Any:
    if hasattr(target, "horizontalScrollBar"):
        return target.horizontalScrollBar()
    raise TypeError("target must have horizontalScrollBar()")


def _vertical_bar(target: Any) -> Any:
    if hasattr(target, "verticalScrollBar"):
        return target.verticalScrollBar()
    raise TypeError("target must have verticalScrollBar()")


def is_at_bottom(target: Any, *, threshold: int = 2) -> bool:
    vertical = _vertical_bar(target)
    return vertical.value() >= vertical.maximum() - threshold


def capture_scroll(target: Any, *, bottom_threshold: int = 2) -> ScrollState:
    horizontal = _horizontal_bar(target)
    vertical = _vertical_bar(target)
    return ScrollState(
        horizontal=horizontal.value(),
        vertical=vertical.value(),
        was_at_bottom=vertical.value() >= vertical.maximum() - bottom_threshold,
        horizontal_maximum=horizontal.maximum(),
        vertical_maximum=vertical.maximum(),
    )


def restore_scroll(
    target: Any,
    state: ScrollState,
    *,
    keep_bottom: bool = True,
    deferred: bool = True,
) -> None:
    horizontal = _horizontal_bar(target)
    vertical = _vertical_bar(target)
    vertical_value = vertical.maximum() if keep_bottom and state.was_at_bottom else state.vertical
    horizontal.setValue(state.horizontal)
    vertical.setValue(vertical_value)

    if not deferred:
        return
    if keep_bottom and state.was_at_bottom:
        QTimer.singleShot(0, lambda: _vertical_bar(target).setValue(_vertical_bar(target).maximum()))
    else:
        QTimer.singleShot(0, lambda value=state.vertical: _vertical_bar(target).setValue(value))
    QTimer.singleShot(0, lambda value=state.horizontal: _horizontal_bar(target).setValue(value))


def set_scroll_per_pixel(target: Any, *, single_step: int = 1) -> None:
    if hasattr(target, "setVerticalScrollMode"):
        target.setVerticalScrollMode(SCROLL_PER_PIXEL)
    if hasattr(target, "setHorizontalScrollMode"):
        target.setHorizontalScrollMode(SCROLL_PER_PIXEL)
    if hasattr(target, "verticalScrollBar"):
        target.verticalScrollBar().setSingleStep(single_step)
    if hasattr(target, "horizontalScrollBar"):
        target.horizontalScrollBar().setSingleStep(single_step)


class AutoScrollGuard(QObject):
    def __init__(self, target: Any, *, bottom_threshold: int = 2) -> None:
        super().__init__(target)
        self.target = target
        self.bottom_threshold = bottom_threshold
        self.follow_bottom = is_at_bottom(target, threshold=bottom_threshold)
        _vertical_bar(target).valueChanged.connect(self._value_changed)

    def _value_changed(self, _value: int) -> None:
        self.follow_bottom = is_at_bottom(self.target, threshold=self.bottom_threshold)

    def capture(self) -> ScrollState:
        state = capture_scroll(self.target, bottom_threshold=self.bottom_threshold)
        self.follow_bottom = state.was_at_bottom
        return state

    def restore(self, state: ScrollState, *, keep_bottom: bool | None = None) -> None:
        restore_scroll(
            self.target,
            state,
            keep_bottom=self.follow_bottom if keep_bottom is None else keep_bottom,
        )

    def scroll_to_bottom(self) -> None:
        _vertical_bar(self.target).setValue(_vertical_bar(self.target).maximum())
        QTimer.singleShot(0, lambda: _vertical_bar(self.target).setValue(_vertical_bar(self.target).maximum()))


def install_auto_scroll_guard(target: Any, *, bottom_threshold: int = 2) -> AutoScrollGuard:
    guard = AutoScrollGuard(target, bottom_threshold=bottom_threshold)
    guards = getattr(target, "_auto_scroll_guards", [])
    guards.append(guard)
    target._auto_scroll_guards = guards
    return guard


@contextmanager
def preserve_scroll(
    target: Any,
    *,
    keep_bottom: bool = True,
    bottom_threshold: int = 2,
) -> Iterator[ScrollState]:
    state = capture_scroll(target, bottom_threshold=bottom_threshold)
    try:
        yield state
    finally:
        restore_scroll(target, state, keep_bottom=keep_bottom)


__all__ = [
    "AutoScrollGuard",
    "QT_BINDING",
    "ScrollState",
    "capture_scroll",
    "install_auto_scroll_guard",
    "is_at_bottom",
    "preserve_scroll",
    "restore_scroll",
    "set_scroll_per_pixel",
]
