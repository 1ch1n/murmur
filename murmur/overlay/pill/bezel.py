"""The static capsule: soft shadow, fill, hairline rim. Rendered once into a
pixmap at the real device pixel ratio and reused every frame."""
from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap

from murmur.overlay.draw import dpx, fill_rect, hairline, stroke_rect, with_alpha
from murmur.overlay.pill.theme import Layout
from murmur.overlay.styles import C


def structural_px(dpr: float) -> int:
    """Rim width in device pixels: one device pixel at every scale."""
    return 1


def capsule_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def paint_card(p: QPainter, rect: QRectF, radius: float, dpr: float, k: float,
               rim_alpha: int = 28) -> None:
    """Shared chrome for the capsule and the panel: shadow, fill, rim, top light."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)

    # drop shadow: three soft layers, offset down
    for grow, alpha in ((6 * k, 10), (3 * k, 20), (1 * k, 38)):
        r = rect.adjusted(-grow, -grow + 1.5 * k, grow, grow + 1.5 * k)
        p.setBrush(with_alpha("#000000", alpha))
        p.drawPath(capsule_path(r, radius + grow))

    # fill, snapped so its straight edges cover whole device pixels
    body = fill_rect(rect.x(), rect.y(), rect.width(), rect.height(), dpr)
    p.setBrush(with_alpha(C.CAPSULE, 242))
    p.drawPath(capsule_path(body, radius))

    # rim: one device pixel, snapped onto the fill's edge rows
    rim = stroke_rect(rect.x(), rect.y(), rect.width(), rect.height(), dpr, 1)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(hairline(with_alpha(C.TEXT_WARM, rim_alpha), dpr, 1))
    p.drawRoundedRect(rim, radius, radius)

    # a whisper of light along the top edge, inside the rim
    p.save()
    p.setClipPath(capsule_path(rect.adjusted(dpx(1, dpr), dpx(1, dpr), -dpx(1, dpr), -dpx(1, dpr)), radius))
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    y = rim.top() + dpx(1, dpr)
    p.setPen(hairline(with_alpha(C.TEXT_WARM, 10), dpr, 1))
    p.drawLine(QPointF(rect.left() + radius, y), QPointF(rect.right() - radius, y))
    p.restore()
    p.restore()


def render_bezel(L: Layout) -> QPixmap:
    dpr = L.dpr
    pm = QPixmap(int(round(L.w * dpr)), int(round(L.h * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_card(p, L.capsule, L.radius, dpr, L.k)
    p.end()
    return pm


class BezelCache:
    def __init__(self) -> None:
        self.key: Optional[Tuple] = None
        self.pixmap: Optional[QPixmap] = None

    def ensure(self, L: Layout) -> QPixmap:
        key = (round(L.w, 3), round(L.h, 3), round(L.dpr, 4))
        if key != self.key or self.pixmap is None:
            self.pixmap = render_bezel(L)
            self.key = key
        return self.pixmap

    def invalidate(self) -> None:
        self.key = None
