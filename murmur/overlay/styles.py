from __future__ import annotations


class C:
    BG = "#08080f"
    PANEL = "#0c0c16"
    SURFACE = "#10101c"
    BORDER = "#1a1a28"
    BORDER_HI = "#252538"

    AMBER = "#ff6b35"        # listening
    AMBER_DIM = "#b34a25"
    TEAL = "#00d4aa"         # processing
    TEAL_DIM = "#009977"
    PURPLE = "#a855f7"       # done / idle accent
    PURPLE_DIM = "#6d3aa1"
    RED = "#ff3333"          # error

    TEXT = "#c8c8d0"
    TEXT_DIM = "#505060"


PILL_STYLE = f"""
* {{ font-family: 'Consolas', 'Courier New', monospace; }}
QWidget {{ background: transparent; color: {C.TEXT}; }}
QLabel#status {{
    color: {C.TEXT_DIM};
    font-size: 10px;
    letter-spacing: 2px;
    font-weight: 600;
}}
"""
