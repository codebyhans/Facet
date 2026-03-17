"""Dark theme stylesheet and configuration for the application."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


class ThemeColors(NamedTuple):
    """Color palette for the application."""

    # Base colors
    background: str = "#1e1e1e"
    surface: str = "#252525"
    surface_light: str = "#2d2d2d"

    # Text colors
    text_primary: str = "#e0e0e0"
    text_secondary: str = "#a0a0a0"
    text_muted: str = "#707070"

    # Accent colors
    primary: str = "#0078d4"  # Windows blue
    primary_light: str = "#1084d7"
    primary_dark: str = "#005a9e"
    accent: str = "#7c3aed"  # Purple accent

    # Status colors
    success: str = "#21a34a"
    warning: str = "#f7b801"
    error: str = "#e81123"
    info: str = "#0078d4"

    # Border colors
    border: str = "#3f3f3f"
    border_light: str = "#4a4a4a"

    # Special
    selection: str = "#094771"  # Dark blue selection
    hover: str = "#333333"


def get_dark_stylesheet(colors: ThemeColors | None = None) -> str:
    """Generate dark theme stylesheet.

    Args:
        colors: ThemeColors instance or None to use defaults

    Returns:
        Qt stylesheet string
    """
    if colors is None:
        colors = ThemeColors()

    return f"""
/* Main Window */
QMainWindow {{
    background-color: {colors.background};
    color: {colors.text_primary};
}}

QWidget {{
    background-color: {colors.background};
    color: {colors.text_primary};
}}

/* Menu Bar */
QMenuBar {{
    background-color: {colors.surface};
    color: {colors.text_primary};
    border-bottom: 1px solid {colors.border};
    padding: 2px;
}}

QMenuBar::item:selected {{
    background-color: {colors.primary};
}}

QMenuBar::item:pressed {{
    background-color: {colors.primary_dark};
}}

/* Menu */
QMenu {{
    background-color: {colors.surface};
    color: {colors.text_primary};
    border: 1px solid {colors.border};
    margin: 2px;
    padding: 2px;
}}

QMenu::item:selected {{
    background-color: {colors.primary};
    padding-left: 8px;
}}

QMenu::separator {{
    background-color: {colors.border};
    height: 1px;
    margin: 4px 0px;
}}

/* Status Bar */
QStatusBar {{
    background-color: {colors.surface};
    color: {colors.text_primary};
    border-top: 1px solid {colors.border};
}}

/* Splitter */
QSplitter::handle {{
    background-color: {colors.border};
    margin: 0px;
}}

QSplitter::handle:hover {{
    background-color: {colors.border_light};
}}

QSplitter::handle:pressed {{
    background-color: {colors.primary};
}}

/* Tree Widget */
QTreeWidget {{
    background-color: {colors.surface};
    color: {colors.text_primary};
    border: 1px solid {colors.border};
    gridline-color: {colors.border};
}}

QTreeWidget::item:selected {{
    background-color: {colors.selection};
}}

QTreeWidget::item:hover {{
    background-color: {colors.hover};
}}

QTreeView {{
    background-color: {colors.surface};
    alternate-background-color: {colors.surface_light};
    color: {colors.text_primary};
}}

/* List Widget */
QListWidget {{
    background-color: {colors.surface};
    color: {colors.text_primary};
    border: 1px solid {colors.border};
    outline: none;
}}

QListWidget::item:selected {{
    background-color: {colors.selection};
}}

QListWidget::item:hover {{
    background-color: {colors.hover};
}}

/* Grid Widget */
QGridView {{
    background-color: {colors.surface};
    alternate-background-color: {colors.surface_light};
}}

/* Push Button */
QPushButton {{
    background-color: {colors.surface_light};
    color: {colors.text_primary};
    border: 1px solid {colors.border};
    border-radius: 3px;
    padding: 4px 8px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {colors.primary};
    border: 1px solid {colors.primary};
}}

QPushButton:pressed {{
    background-color: {colors.primary_dark};
    border: 1px solid {colors.primary_dark};
}}

QPushButton:disabled {{
    color: {colors.text_muted};
    background-color: {colors.surface};
}}

/* Line Edit */
QLineEdit {{
    background-color: {colors.surface_light};
    color: {colors.text_primary};
    border: 1px solid {colors.border};
    border-radius: 3px;
    padding: 4px;
    selection-background-color: {colors.selection};
}}

QLineEdit:focus {{
    border: 2px solid {colors.primary};
    padding: 3px;
}}

/* Text Edit */
QTextEdit {{
    background-color: {colors.surface_light};
    color: {colors.text_primary};
    border: 1px solid {colors.border};
    border-radius: 3px;
    padding: 4px;
    selection-background-color: {colors.selection};
}}

QTextEdit:focus {{
    border: 2px solid {colors.primary};
}}

/* Combo Box */
QComboBox {{
    background-color: {colors.surface_light};
    color: {colors.text_primary};
    border: 1px solid {colors.border};
    border-radius: 3px;
    padding: 4px;
}}

QComboBox:hover {{
    border: 1px solid {colors.primary};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid {colors.border};
}}

QComboBox::down-arrow {{
    image: none;
    background-color: {colors.text_secondary};
}}

QComboBox QAbstractItemView {{
    background-color: {colors.surface};
    color: {colors.text_primary};
    selection-background-color: {colors.selection};
    border: 1px solid {colors.border};
}}

/* Spin Box */
QSpinBox, QDoubleSpinBox {{
    background-color: {colors.surface_light};
    color: {colors.text_primary};
    border: 1px solid {colors.border};
    border-radius: 3px;
    padding: 4px;
}}

QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 2px solid {colors.primary};
}}

/* Slider */
QSlider::groove:horizontal {{
    background-color: {colors.surface_light};
    border: 1px solid {colors.border};
    height: 6px;
    margin: 2px 0;
    border-radius: 3px;
}}

QSlider::handle:horizontal {{
    background-color: {colors.primary};
    border: 1px solid {colors.primary};
    width: 12px;
    margin: -3px 0;
    border-radius: 6px;
}}

QSlider::handle:horizontal:hover {{
    background-color: {colors.primary_light};
}}

/* Scroll Bar */
QScrollBar:vertical {{
    background-color: {colors.surface};
    width: 12px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background-color: {colors.border_light};
    border-radius: 6px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {colors.border};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background-color: {colors.surface};
    height: 12px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background-color: {colors.border_light};
    border-radius: 6px;
    min-width: 20px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {colors.border};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    background: none;
}}

/* Group Box */
QGroupBox {{
    color: {colors.text_primary};
    border: 1px solid {colors.border};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 8px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 3px;
}}

/* Tab Widget */
QTabWidget {{
    background-color: {colors.background};
    color: {colors.text_primary};
    border: none;
}}

QTabBar::tab {{
    background-color: {colors.surface_light};
    color: {colors.text_primary};
    border: 1px solid {colors.border};
    padding: 4px 12px;
    margin-right: 1px;
}}

QTabBar::tab:selected {{
    background-color: {colors.primary};
    border: 1px solid {colors.primary};
}}

QTabBar::tab:hover {{
    background-color: {colors.hover};
}}

/* Label */
QLabel {{
    color: {colors.text_primary};
    background-color: transparent;
}}

/* Dialog */
QDialog {{
    background-color: {colors.background};
    color: {colors.text_primary};
}}

/* Check Box */
QCheckBox {{
    color: {colors.text_primary};
    spacing: 5px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    background-color: {colors.surface_light};
    border: 1px solid {colors.border};
    border-radius: 2px;
}}

QCheckBox::indicator:hover {{
    background-color: {colors.hover};
    border: 1px solid {colors.primary};
}}

QCheckBox::indicator:checked {{
    background-color: {colors.primary};
    border: 1px solid {colors.primary};
}}

/* Radio Button */
QRadioButton {{
    color: {colors.text_primary};
    spacing: 5px;
}}

QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    background-color: {colors.surface_light};
    border: 1px solid {colors.border};
    border-radius: 8px;
}}

QRadioButton::indicator:hover {{
    background-color: {colors.hover};
    border: 1px solid {colors.primary};
}}

QRadioButton::indicator:checked {{
    background-color: {colors.primary};
    border: 1px solid {colors.primary};
}}

/* Tooltip */
QToolTip {{
    background-color: {colors.surface};
    color: {colors.text_primary};
    border: 1px solid {colors.border};
    padding: 2px;
    border-radius: 3px;
}}

/* Input Dialog */
QInputDialog {{
    background-color: {colors.background};
}}

/* Message Box */
QMessageBox {{
    background-color: {colors.background};
}}

QMessageBox QLabel {{
    color: {colors.text_primary};
}}

QMessageBox QPushButton {{
    min-width: 60px;
}}
"""


def apply_theme(app: QApplication, colors: ThemeColors | None = None) -> None:
    """Apply dark theme to application.

    Args:
        app: QApplication instance
        colors: Optional custom ThemeColors
    """
    stylesheet = get_dark_stylesheet(colors)
    app.setStyleSheet(stylesheet)
