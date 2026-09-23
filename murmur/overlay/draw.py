"""Device-pixel snapping, colour math and font helpers shared by every
custom-painted surface. No dependency on the widget package, so the
waveform, needle and panel can all import it without cycles.

Windows hands Qt fractional scale factors (1.25 at 125 %, 1.5 at 150 %).
A 1 px line drawn at an integer logical coordinate then straddles two
device pixels and looks soft. Static lines are positioned in device pixels
and converted back to logical units so they occupy exactly one device
pixel at any scale.
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPen

from murmur.overlay.fonts import font_family


def dpx(n: float, dpr: float) -> float:
    """n device pixels expressed in logical units."""
    return n / dpr


def snap(v: float, dpr: float) -> float:
    """Snap a logical coordinate to a device-pixel edge (fills, even pens)."""
    return round(v * dpr) / dpr


def snap_c(v: float, dpr: float) -> float:
    """Snap a logical coordinate to a device-pixel centre (odd-width strokes)."""
    return (math.floor(v * dpr) + 0.5) / dpr


def hairline(color: QColor | str, dpr: float, n_dev: int = 1) -> QPen:
    """A pen exactly n_dev device pixels wide. Never a cosmetic 0-width pen."""
    pen = QPen(QColor(color), dpx(n_dev, dpr))
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    return pen


def stroke_rect(x: float, y: float, w: float, h: float, dpr: float, n_dev: int = 1) -> QRectF:
    """Rect whose n_dev-wide stroke lands on whole device pixels."""
    inset = dpx(n_dev, dpr) / 2
    return QRectF(
        snap(x, dpr) + inset,
        snap(y, dpr) + inset,
        snap(x + w, dpr) - snap(x, dpr) - dpx(n_dev, dpr),
        snap(y + h, dpr) - snap(y, dpr) - dpx(n_dev, dpr),
    )


def fill_rect(x: float, y: float, w: float, h: float, dpr: float) -> QRectF:
    x0, y0 = snap(x, dpr), snap(y, dpr)
    return QRectF(x0, y0, snap(x + w, dpr) - x0, snap(y + h, dpr) - y0)


def mix(a: QColor | str, b: QColor | str, t: float) -> QColor:
    a, b = QColor(a), QColor(b)
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def with_alpha(c: QColor | str, alpha: int) -> QColor:
    c = QColor(c)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


def smoothstep(e0: float, e1: float, x: float) -> float:
    if e1 == e0:
        return float(x >= e1)
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_in_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * t


def font(key: str, px: float, letter_spacing: float = 0.0, weight: Optional[int] = None) -> QFont:
    from murmur.overlay.fonts import FONT_WEIGHT

    f = QFont(font_family(key))
    f.setPixelSize(max(6, round(px)))
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if letter_spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    f.setWeight(QFont.Weight(weight if weight is not None else FONT_WEIGHT.get(key, 400)))
    return f
