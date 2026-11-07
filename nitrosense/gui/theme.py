"""Colours and stylesheet, taken from the NitroSense 3.1 look (dark + #E60000)."""
from .qt import QtGui

RED = "#E60000"
RED_BRIGHT = "#FF2A2A"
RED_DARK = "#5C0000"
ORANGE = "#FF8A00"
BG = "#0F0F0F"
FRAME = "#181818"
PANEL = "#151515"
PANEL_BORDER = "#3A3A3A"
TEXT = "#F0F0F0"
TEXT_DIM = "#8C8C8C"
TEXT_OFF = "#5E5E5E"
TEXT_DISABLED = "#383838"

BASE_COLORS = [
    ("Rouge", "#FF0000"), ("Orange", "#FF6A00"), ("Jaune", "#FFD800"),
    ("Vert", "#00FF00"), ("Cyan", "#00FFFF"), ("Bleu", "#0000FF"),
    ("Rose", "#FF00B4"), ("Violet", "#8000FF"), ("Blanc", "#FFFFFF"),
]

STYLESHEET = f"""
QWidget {{
    color: {TEXT};
    font-size: 10pt;
}}
QToolTip {{
    color: {TEXT};
    background: #222;
    border: 1px solid {PANEL_BORDER};
    padding: 4px;
}}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#warning {{ color: {RED}; }}
QLabel#status {{ color: {TEXT_DIM}; font-size: 9pt; }}
QLabel#statusError {{ color: {RED_BRIGHT}; font-size: 9pt; }}
QLabel#section {{ color: {TEXT}; font-size: 11pt; font-weight: 600; }}
QSlider::groove:horizontal {{
    height: 6px; background: #2B2B2B; border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {RED}; border-radius: 3px;
}}
QSlider::sub-page:horizontal:disabled {{ background: #3A3A3A; }}
QSlider::handle:horizontal {{
    width: 18px; height: 18px; margin: -7px 0; border-radius: 9px;
    background: {RED}; border: 2px solid #FF5A5A;
}}
QSlider::handle:horizontal:disabled {{ background: #444; border-color: #555; }}
QCheckBox, QRadioButton {{ spacing: 8px; color: {TEXT}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {TEXT_OFF}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; border: 1px solid #5A5A5A; background: #1E1E1E;
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {RED}; border: 1px solid #FF5A5A;
}}
QPushButton {{
    background: #232323; border: 1px solid {PANEL_BORDER}; border-radius: 2px;
    padding: 6px 14px; color: {TEXT};
}}
QPushButton:hover {{ border-color: {RED}; }}
QPushButton:checked {{ border-color: {RED}; color: {RED_BRIGHT}; }}
QPushButton:disabled {{ color: {TEXT_OFF}; border-color: #2A2A2A; }}
QPlainTextEdit {{
    background: #101010; border: 1px solid {PANEL_BORDER}; color: #CFCFCF;
    font-family: monospace; font-size: 9pt;
}}
QScrollBar:vertical {{ background: #151515; width: 10px; }}
QScrollBar::handle:vertical {{ background: #3A3A3A; min-height: 20px; }}
"""


def color(name: str) -> QtGui.QColor:
    return QtGui.QColor(name)


def font(size: float, weight: str = "normal", spacing: float = 0.0) -> QtGui.QFont:
    f = QtGui.QFont()
    f.setPointSizeF(size)
    weights = {"light": QtGui.QFont.Weight.Light, "normal": QtGui.QFont.Weight.Normal,
               "demibold": QtGui.QFont.Weight.DemiBold, "bold": QtGui.QFont.Weight.Bold}
    f.setWeight(weights[weight])
    if spacing:
        f.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, spacing)
    return f
