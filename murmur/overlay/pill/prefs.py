from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

# Capsule sizes (logical px) per scale. The widget window is larger by a
# transparent margin that holds the drop shadow; see theme.Layout.
CAPSULE_SIZES = {
    "s": (204, 26),
    "m": (240, 32),
    "l": (288, 40),
}
SHADOW_MARGIN_RATIO = 10 / 32   # margin = capsule_h * ratio on every side


def widget_size_for(capsule: Tuple[int, int]) -> Tuple[int, int]:
    w, h = capsule
    m = int(round(h * SHADOW_MARGIN_RATIO))
    return w + 2 * m, h + 2 * m


@dataclass(frozen=True)
class PillPrefs:
    scale: str = "m"
    opacity: float = 1.0
    show_waveform: bool = True       # draw the resting voice line when idle
    bottom_margin: int = 80
    x: Optional[int] = None
    y: Optional[int] = None
    screen: str = ""

    @property
    def capsule_size(self) -> Tuple[int, int]:
        return CAPSULE_SIZES.get(self.scale, CAPSULE_SIZES["m"])

    @property
    def size(self) -> Tuple[int, int]:
        return widget_size_for(self.capsule_size)

    @classmethod
    def from_settings(cls, s) -> "PillPrefs":
        return cls(
            scale=(getattr(s, "pill_scale", "m") or "m").lower(),
            opacity=max(0.3, min(1.0, float(getattr(s, "pill_opacity", 1.0)))),
            show_waveform=bool(getattr(s, "pill_show_waveform", True)),
            bottom_margin=int(getattr(s, "pill_bottom_margin", 80)),
            x=getattr(s, "pill_x", None),
            y=getattr(s, "pill_y", None),
            screen=getattr(s, "pill_screen", "") or "",
        )
