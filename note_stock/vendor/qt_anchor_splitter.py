from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal


def _import_qt():
    for binding in ("PySide6",):
        try:
            if binding == "PyQt6":
                from PyQt6.QtCore import QObject, QEvent, QTimer, Qt
                from PyQt6.QtWidgets import QFormLayout, QSizePolicy, QSplitter, QWidget
            elif binding == "PySide6":
                from PySide6.QtCore import QObject, QEvent, QTimer, Qt
                from PySide6.QtWidgets import QFormLayout, QSizePolicy, QSplitter, QWidget
            elif binding == "PyQt5":
                from PyQt5.QtCore import QObject, QEvent, QTimer, Qt
                from PyQt5.QtWidgets import QFormLayout, QSizePolicy, QSplitter, QWidget
            else:
                from PySide2.QtCore import QObject, QEvent, QTimer, Qt
                from PySide2.QtWidgets import QFormLayout, QSizePolicy, QSplitter, QWidget
            return binding, QObject, QEvent, QTimer, Qt, QFormLayout, QSizePolicy, QSplitter, QWidget
        except ImportError:
            continue
    raise ImportError("PyQt6, PySide6, PyQt5, or PySide2 is required")


(
    QT_BINDING,
    QObject,
    QEvent,
    QTimer,
    Qt,
    QFormLayout,
    QSizePolicy,
    QSplitter,
    QWidget,
) = _import_qt()


def _enum(owner: Any, group: str, name: str) -> Any:
    grouped = getattr(owner, group, None)
    if grouped is not None and hasattr(grouped, name):
        return getattr(grouped, name)
    return getattr(owner, name)


HORIZONTAL = _enum(Qt, "Orientation", "Horizontal")
VERTICAL = _enum(Qt, "Orientation", "Vertical")
RESIZE_EVENT = _enum(QEvent, "Type", "Resize")
PREFERRED_POLICY = _enum(QSizePolicy, "Policy", "Preferred")
FIXED_POLICY = _enum(QSizePolicy, "Policy", "Fixed")
EXPANDING_POLICY = _enum(QSizePolicy, "Policy", "Expanding")
FIELDS_STAY_AT_SIZE_HINT = _enum(
    QFormLayout,
    "FieldGrowthPolicy",
    "FieldsStayAtSizeHint",
)


OrientationText = Literal["horizontal", "vertical"]


def resolve_orientation(orientation: OrientationText | Any) -> Any:
    if orientation in ("horizontal", "x", "h"):
        return HORIZONTAL
    if orientation in ("vertical", "y", "v"):
        return VERTICAL
    return orientation


def _settings_value(settings: Any, key: str | None, default: Any) -> Any:
    if settings is None or not key:
        return default
    if hasattr(settings, "value"):
        return settings.value(key, default)
    if isinstance(settings, dict):
        return settings.get(key, default)
    return default


def _settings_set(settings: Any, key: str | None, value: Any) -> None:
    if settings is None or not key:
        return
    if hasattr(settings, "setValue"):
        settings.setValue(key, value)
    elif isinstance(settings, dict):
        settings[key] = value


def _as_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SplitterAnchorConfig:
    fixed_index: int = 0
    min_size: int = 120
    max_size: int | None = None
    initial_size: int | None = None
    persist_key: str | None = None
    preserve_on_resize: bool = True
    save_on_move: bool = True


class AnchorSplitterBinding(QObject):
    """Keep one QSplitter side anchored while the opposite side absorbs resize."""

    def __init__(
        self,
        splitter: QSplitter,
        *,
        fixed_index: int = 0,
        settings: Any = None,
        persist_key: str | None = None,
        initial_size: int | None = None,
        min_size: int = 120,
        max_size: int | None = None,
        preserve_on_resize: bool = True,
        save_on_move: bool = True,
        no_ratio_stretch: bool = True,
    ) -> None:
        super().__init__(splitter)
        self.splitter = splitter
        self.settings = settings
        self.config = SplitterAnchorConfig(
            fixed_index=fixed_index,
            min_size=min_size,
            max_size=max_size,
            initial_size=initial_size,
            persist_key=persist_key,
            preserve_on_resize=preserve_on_resize,
            save_on_move=save_on_move,
        )
        self._applying = False
        self._scheduled = False
        self._anchor_size = _as_int(
            _settings_value(settings, persist_key, None),
            initial_size,
        )

        if no_ratio_stretch:
            set_splitter_no_ratio_stretch(splitter, fixed_index=fixed_index)

        splitter.splitterMoved.connect(self._splitter_moved)
        splitter.installEventFilter(self)
        self.schedule_apply()

    @property
    def anchor_size(self) -> int | None:
        return self._anchor_size

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if (
            watched is self.splitter
            and event.type() == RESIZE_EVENT
            and self.config.preserve_on_resize
            and not self._applying
        ):
            self.schedule_apply()
        return super().eventFilter(watched, event)

    def schedule_apply(self) -> None:
        if self._scheduled:
            return
        self._scheduled = True
        QTimer.singleShot(0, self.apply_anchor)

    def apply_anchor(self) -> None:
        self._scheduled = False
        if self.splitter.count() < 2:
            return

        sizes = list(self.splitter.sizes())
        total = sum(sizes)
        if total <= 0:
            return

        fixed_index = self._normalized_fixed_index()
        current = sizes[fixed_index] if fixed_index < len(sizes) else 0
        target = self._anchor_size if self._anchor_size is not None else current
        if target is None or target <= 0:
            target = self.config.initial_size or current or self.config.min_size
        target = self._clamp(target, total)
        self._anchor_size = target

        new_sizes = self._sizes_with_anchor(sizes, fixed_index, target)
        self._set_sizes(new_sizes)

    def refresh_anchor_from_current(self, *, save: bool = True) -> int | None:
        if self.splitter.count() < 2:
            return self._anchor_size
        sizes = list(self.splitter.sizes())
        fixed_index = self._normalized_fixed_index()
        if fixed_index >= len(sizes):
            return self._anchor_size
        self._anchor_size = self._clamp(sizes[fixed_index], sum(sizes))
        if save:
            _settings_set(self.settings, self.config.persist_key, self._anchor_size)
        return self._anchor_size

    def _splitter_moved(self, _pos: int, _index: int) -> None:
        if self._applying or not self.config.save_on_move:
            return
        self.refresh_anchor_from_current(save=True)

    def _normalized_fixed_index(self) -> int:
        count = max(1, self.splitter.count())
        index = self.config.fixed_index
        if index < 0:
            index = count + index
        return max(0, min(index, count - 1))

    def _clamp(self, value: int, total: int) -> int:
        minimum = max(0, int(self.config.min_size))
        maximum = self.config.max_size
        if maximum is None:
            maximum = max(minimum, total - 1) if self.splitter.count() > 1 else total
        maximum = max(minimum, min(int(maximum), total))
        return max(minimum, min(int(value), maximum))

    def _sizes_with_anchor(self, sizes: list[int], fixed_index: int, target: int) -> list[int]:
        count = len(sizes)
        if count <= 1:
            return sizes

        remaining = max(0, sum(sizes) - target)
        other_indexes = [index for index in range(count) if index != fixed_index]
        old_other_total = sum(max(0, sizes[index]) for index in other_indexes)

        new_sizes = list(sizes)
        new_sizes[fixed_index] = target
        if not other_indexes:
            return new_sizes

        if old_other_total <= 0:
            base = remaining // len(other_indexes)
            for index in other_indexes:
                new_sizes[index] = base
            new_sizes[other_indexes[-1]] += remaining - (base * len(other_indexes))
            return new_sizes

        used = 0
        for index in other_indexes[:-1]:
            size = int(round(remaining * (max(0, sizes[index]) / old_other_total)))
            new_sizes[index] = size
            used += size
        new_sizes[other_indexes[-1]] = max(0, remaining - used)
        return new_sizes

    def _set_sizes(self, sizes: list[int]) -> None:
        self._applying = True
        old_block = self.splitter.blockSignals(True)
        try:
            self.splitter.setSizes(sizes)
        finally:
            self.splitter.blockSignals(old_block)
            self._applying = False


def configure_splitter_boundary(
    splitter: QSplitter,
    *,
    handle_width: int = 8,
    children_collapsible: bool = False,
    opaque_resize: bool = True,
) -> QSplitter:
    splitter.setHandleWidth(handle_width)
    splitter.setChildrenCollapsible(children_collapsible)
    splitter.setOpaqueResize(opaque_resize)
    return splitter


def set_splitter_no_ratio_stretch(splitter: QSplitter, *, fixed_index: int = 0) -> QSplitter:
    count = splitter.count()
    if count <= 0:
        return splitter
    fixed_index = fixed_index if fixed_index >= 0 else count + fixed_index
    fixed_index = max(0, min(fixed_index, count - 1))
    for index in range(count):
        splitter.setStretchFactor(index, 0 if index == fixed_index else 1)
    return splitter


def set_form_fixed_growth(form_layout: QFormLayout) -> QFormLayout:
    form_layout.setFieldGrowthPolicy(FIELDS_STAY_AT_SIZE_HINT)
    return form_layout


def set_widget_no_auto_expand(
    widget: QWidget,
    *,
    horizontal: bool = True,
    vertical: bool = False,
) -> QWidget:
    policy = widget.sizePolicy()
    if horizontal:
        policy.setHorizontalPolicy(FIXED_POLICY)
    if vertical:
        policy.setVerticalPolicy(FIXED_POLICY)
    widget.setSizePolicy(policy)
    return widget


def set_widget_fixed_extent(
    widget: QWidget,
    *,
    width: int | None = None,
    height: int | None = None,
) -> QWidget:
    if width is not None:
        widget.setFixedWidth(width)
    if height is not None:
        widget.setFixedHeight(height)
    return widget


def set_widget_absorbs_resize(
    widget: QWidget,
    *,
    horizontal: bool = True,
    vertical: bool = False,
) -> QWidget:
    policy = widget.sizePolicy()
    if horizontal:
        policy.setHorizontalPolicy(EXPANDING_POLICY)
    if vertical:
        policy.setVerticalPolicy(EXPANDING_POLICY)
    widget.setSizePolicy(policy)
    return widget


def bind_splitter_anchor(
    splitter: QSplitter,
    *,
    fixed_index: int = 0,
    settings: Any = None,
    persist_key: str | None = None,
    initial_size: int | None = None,
    min_size: int = 120,
    max_size: int | None = None,
    handle_width: int = 8,
    children_collapsible: bool = False,
    opaque_resize: bool = True,
    preserve_on_resize: bool = True,
    save_on_move: bool = True,
    no_ratio_stretch: bool = True,
) -> AnchorSplitterBinding:
    configure_splitter_boundary(
        splitter,
        handle_width=handle_width,
        children_collapsible=children_collapsible,
        opaque_resize=opaque_resize,
    )
    binding = AnchorSplitterBinding(
        splitter,
        fixed_index=fixed_index,
        settings=settings,
        persist_key=persist_key,
        initial_size=initial_size,
        min_size=min_size,
        max_size=max_size,
        preserve_on_resize=preserve_on_resize,
        save_on_move=save_on_move,
        no_ratio_stretch=no_ratio_stretch,
    )
    guards = getattr(splitter, "_anchor_splitter_bindings", [])
    guards.append(binding)
    splitter._anchor_splitter_bindings = guards
    return binding


def create_anchor_splitter(
    parent: QWidget | None = None,
    *,
    orientation: OrientationText | Any = "horizontal",
    widgets: Iterable[QWidget] = (),
    fixed_index: int = 0,
    settings: Any = None,
    persist_key: str | None = None,
    initial_size: int | None = None,
    min_size: int = 120,
    max_size: int | None = None,
    handle_width: int = 8,
) -> tuple[QSplitter, AnchorSplitterBinding]:
    splitter = QSplitter(resolve_orientation(orientation), parent)
    for widget in widgets:
        splitter.addWidget(widget)
    binding = bind_splitter_anchor(
        splitter,
        fixed_index=fixed_index,
        settings=settings,
        persist_key=persist_key,
        initial_size=initial_size,
        min_size=min_size,
        max_size=max_size,
        handle_width=handle_width,
    )
    return splitter, binding


__all__ = [
    "AnchorSplitterBinding",
    "QT_BINDING",
    "SplitterAnchorConfig",
    "bind_splitter_anchor",
    "configure_splitter_boundary",
    "create_anchor_splitter",
    "resolve_orientation",
    "set_form_fixed_growth",
    "set_splitter_no_ratio_stretch",
    "set_widget_absorbs_resize",
    "set_widget_fixed_extent",
    "set_widget_no_auto_expand",
]
