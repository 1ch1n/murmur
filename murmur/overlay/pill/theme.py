"""Geometry for the capsule widget. Snapping and colour helpers live in
murmur.overlay.draw and are re-exported here for convenience."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF

from murmur.overlay.draw import (  # noqa: F401  (re-exported)
    dpx,
    ease_in_cubic,
    ease_out_cubic,
    fill_rect,
    font,
    hairline,
    mix,
    smoothstep,
    snap,
    snap_c,
    stroke_rect,
    with_alpha,
)
from murmur.overlay.pill.prefs import SHADOW_MARGIN_RATIO

BASE_CAPSULE_H = 32.0


@dataclass(frozen=True)
class Layout:
    w: float                 # widget (window) size incl. shadow margin
    h: float
    dpr: float
    k: float                 # scale relative to the M capsule (32 px tall)
    margin: float
    capsule: QRectF
    radius: float
    lamp: QPointF
    lamp_r: float
    trace: QRectF            # where the voice line lives
    readout: QRectF          # right-aligned text
    line_y: float            # resting line centre

    @staticmethod
    def compute(w: float, h: float, dpr: float, show_line: bool = True) -> "Layout":
        margin_ratio = SHADOW_MARGIN_RATIO
        cap_h = h / (1 + 2 * margin_ratio)
        margin = cap_h * margin_ratio
        k = cap_h / BASE_CAPSULE_H
        capsule = QRectF(margin, margin, w - 2 * margin, cap_h)
        radius = cap_h / 2

        lamp_r = 3 * k
        lamp = QPointF(capsule.left() + 15 * k, capsule.center().y())

        readout_w = 58 * k
        readout = QRectF(capsule.right() - 13 * k - readout_w, capsule.top(), readout_w, cap_h)
        trace_left = capsule.left() + 28 * k
        trace = QRectF(trace_left, capsule.top() + 5 * k, readout.left() - 8 * k - trace_left, cap_h - 10 * k)
        return Layout(
            w=w, h=h, dpr=dpr, k=k, margin=margin, capsule=capsule, radius=radius,
            lamp=lamp, lamp_r=lamp_r, trace=trace, readout=readout, line_y=capsule.center().y(),
        )
