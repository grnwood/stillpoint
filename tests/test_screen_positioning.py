from PySide6.QtCore import QPoint, QRect, QSize

from sp.app.ui import screen_positioning as pos


class _FakeScreen:
    def __init__(self, rect: QRect) -> None:
        self._rect = QRect(rect)

    def availableGeometry(self) -> QRect:
        return QRect(self._rect)


def test_clamp_popup_top_left_keeps_popup_inside_bounds() -> None:
    bounds = QRect(100, 100, 400, 300)
    size = QSize(200, 120)

    clamped = pos.clamp_popup_top_left(QPoint(450, 350), size, bounds)

    assert clamped == QPoint(300, 280)


def test_clamp_popup_top_left_respects_margin() -> None:
    bounds = QRect(0, 0, 500, 400)
    size = QSize(120, 80)

    clamped = pos.clamp_popup_top_left(QPoint(-10, -20), size, bounds, margin=12)

    assert clamped == QPoint(12, 12)


def test_fit_size_to_bounds_caps_both_dimensions() -> None:
    bounds = QRect(100, 50, 800, 600)

    fitted = pos.fit_size_to_bounds(QSize(1400, 800), bounds, margin=24)

    assert fitted == QSize(752, 552)


def test_fit_size_to_bounds_preserves_smaller_preferred_size() -> None:
    bounds = QRect(0, 0, 1920, 1080)

    fitted = pos.fit_size_to_bounds(QSize(620, 560), bounds, margin=24)

    assert fitted == QSize(620, 560)


def test_popup_available_geometry_prefers_anchor_screen(monkeypatch) -> None:
    anchor_screen = _FakeScreen(QRect(2000, 0, 1920, 1080))
    parent_screen = _FakeScreen(QRect(0, 0, 1920, 1080))

    class _FakeGuiApp:
        @staticmethod
        def screenAt(point: QPoint):
            if point == QPoint(2100, 50):
                return anchor_screen
            return None

        @staticmethod
        def primaryScreen():
            return _FakeScreen(QRect(10, 10, 100, 100))

    monkeypatch.setattr(pos, "QGuiApplication", _FakeGuiApp)
    monkeypatch.setattr(pos, "_screen_from_parent", lambda parent: parent_screen if parent is not None else None)

    geom = pos.popup_available_geometry(anchor=QPoint(2100, 50), parent=object())

    assert geom == QRect(2000, 0, 1920, 1080)


def test_popup_available_geometry_uses_parent_then_primary(monkeypatch) -> None:
    parent_screen = _FakeScreen(QRect(100, 100, 800, 600))
    primary_screen = _FakeScreen(QRect(0, 0, 1600, 900))

    class _FakeGuiApp:
        @staticmethod
        def screenAt(point: QPoint):
            return None

        @staticmethod
        def primaryScreen():
            return primary_screen

    monkeypatch.setattr(pos, "QGuiApplication", _FakeGuiApp)
    monkeypatch.setattr(pos, "QCursor", type("_Cursor", (), {"pos": staticmethod(lambda: QPoint(50, 50))}))
    monkeypatch.setattr(pos, "_screen_from_parent", lambda parent: parent_screen if parent is not None else None)

    parent_geom = pos.popup_available_geometry(anchor=None, parent=object())
    no_parent_geom = pos.popup_available_geometry(anchor=None, parent=None)

    assert parent_geom == QRect(100, 100, 800, 600)
    assert no_parent_geom == QRect(0, 0, 1600, 900)
