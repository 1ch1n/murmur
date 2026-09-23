"""Bundled font registration.

Geist Sans and Geist Mono (Vercel, SIL Open Font License) ship in
murmur/assets/fonts. They are registered with Qt once a QApplication
exists; everything degrades to Segoe UI / Consolas if the files are
missing or registration fails.
"""
from __future__ import annotations

import logging
from importlib import resources
from typing import Dict

log = logging.getLogger(__name__)

_FALLBACK = {
    "ui": "Segoe UI",
    "ui_medium": "Segoe UI",
    "display": "Segoe UI",
    "mono": "Consolas",
    "mono_medium": "Consolas",
}

# key -> font file
_FONT_FILES = {
    "ui": "Geist-Regular.ttf",
    "ui_medium": "Geist-Medium.ttf",
    "display": "Geist-SemiBold.ttf",
    "mono": "GeistMono-Regular.ttf",
    "mono_medium": "GeistMono-Medium.ttf",
}

# Qt exposes weights of one family under the same family name; we keep the
# requested weight alongside so font() can ask for it.
FONT_WEIGHT = {
    "ui": 400,
    "ui_medium": 500,
    "display": 600,
    "mono": 400,
    "mono_medium": 500,
}

_registered: Dict[str, str] = {}
_attempted = False


def register_fonts() -> Dict[str, str]:
    """Register bundled fonts with QFontDatabase. Requires a QGuiApplication.
    Safe to call more than once. Returns {key: family}."""
    global _attempted
    if _attempted:
        return dict(_registered)
    _attempted = True

    from PySide6.QtGui import QFontDatabase

    try:
        root = resources.files("murmur.assets") / "fonts"
    except Exception:
        log.warning("font assets package not found; using system fallback")
        return dict(_registered)

    for key, fname in _FONT_FILES.items():
        try:
            with resources.as_file(root / fname) as real:
                if not real.is_file():
                    log.warning("font missing: %s", fname)
                    continue
                fid = QFontDatabase.addApplicationFont(str(real))
            if fid < 0:
                log.warning("font failed to register: %s", fname)
                continue
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                _registered[key] = fams[0]
        except Exception:
            log.exception("font registration error: %s", fname)

    if _registered:
        log.info("fonts registered: %s", ", ".join(sorted(set(_registered.values()))))
    return dict(_registered)


def font_family(key: str) -> str:
    """Resolve a font family by role. Never raises, never needs a QApplication."""
    return _registered.get(key) or _FALLBACK.get(key, "Segoe UI")


def is_bundled(key: str) -> bool:
    return key in _registered
