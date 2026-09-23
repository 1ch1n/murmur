from __future__ import annotations

import os

import pytest

# Headless Qt for every test in this suite. Must be set before QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from murmur.overlay.fonts import register_fonts

    register_fonts()
    yield app
