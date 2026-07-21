import os
# Bypass system proxy for all urllib requests — ModSync connects directly
for _pv in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY",
            "all_proxy", "ALL_PROXY"):
    os.environ.pop(_pv, None)

import modsync_v2

import json
import shutil
import subprocess
import sys
import threading
import time
import zipfile
import urllib.parse
import urllib.request
import socket
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Optional
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer, QPoint
from PyQt6.QtGui import QIcon, QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QMessageBox,
    QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QComboBox, QSystemTrayIcon, QMenu, QFrame, QProgressBar, QTabWidget,
    QGraphicsOpacityEffect
)

APP_VERSION = "2.0.0"
GITHUB_REPO = "Leo111444/ModSync"

GLASS_STYLE = """
QWidget {
    background-color: #0e1016;
    color: #dde3f0;
    font-family: 'Segoe UI', 'SF Pro Display', Arial, sans-serif;
    font-size: 13px;
}
/* ── Cards ── */
QFrame#glass_card {
    background: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 14px;
}
/* ── Inputs ── */
QLineEdit {
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 8px;
    padding: 6px 10px;
    color: #dde3f0;
    selection-background-color: rgba(99,102,241,0.40);
}
QLineEdit:focus {
    border-color: rgba(99,102,241,0.65);
    background: rgba(99,102,241,0.06);
}
QLineEdit:read-only {
    color: rgba(255,255,255,0.35);
    border-color: rgba(255,255,255,0.05);
}
QLineEdit:disabled {
    color: rgba(255,255,255,0.25);
    border-color: rgba(255,255,255,0.04);
}
/* ── ComboBox ── */
QComboBox {
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 8px;
    padding: 0px 10px;
    color: #dde3f0;
    min-height: 28px;
    max-height: 28px;
}
QComboBox:hover {
    background: rgba(255,255,255,0.09);
    border-color: rgba(99,102,241,0.50);
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox::down-arrow {
    width: 8px;
    height: 8px;
    border-left: 2px solid rgba(99,102,241,0.70);
    border-bottom: 2px solid rgba(99,102,241,0.70);
}
QComboBox QAbstractItemView {
    background: #1a1d2a;
    border: 1px solid rgba(99,102,241,0.40);
    border-radius: 8px;
    color: #dde3f0;
    selection-background-color: rgba(99,102,241,0.22);
    outline: none;
}
/* ── Buttons base ── */
QPushButton {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px;
    padding: 0px 12px;
    color: #dde3f0;
    min-height: 28px;
    max-height: 28px;
    min-width: 32px;
    font-weight: 500;
}
QPushButton:hover {
    background: rgba(255,255,255,0.11);
    border-color: rgba(99,102,241,0.50);
    color: #ffffff;
}
QPushButton:pressed {
    background: rgba(255,255,255,0.03);
}
QPushButton:focus { outline: none; }
QPushButton:disabled {
    background: rgba(255,255,255,0.02);
    border-color: rgba(255,255,255,0.04);
    color: rgba(255,255,255,0.18);
}
QPushButton:checked {
    background: rgba(99,102,241,0.18);
    border: 1.5px solid rgba(99,102,241,0.65);
    color: #a5b4fc;
    font-weight: 600;
}
/* ── Check updates (accent blue) ── */
QPushButton#check_btn {
    background: rgba(56,189,248,0.10);
    border-color: rgba(56,189,248,0.30);
    color: #7dd3fc;
    font-weight: 600;
}
QPushButton#check_btn:hover {
    background: rgba(56,189,248,0.18);
    border-color: #7dd3fc;
}
QPushButton#check_btn:disabled {
    background: rgba(56,189,248,0.03);
    border-color: rgba(56,189,248,0.08);
    color: rgba(125,211,252,0.20);
}
/* ── Download / Update mods (green) ── */
QPushButton#download_btn {
    background: rgba(34,197,94,0.12);
    border-color: rgba(34,197,94,0.40);
    color: #4ade80;
    font-weight: 700;
}
QPushButton#download_btn:hover {
    background: rgba(34,197,94,0.20);
    border-color: #4ade80;
}
QPushButton#download_btn:disabled {
    background: rgba(34,197,94,0.03);
    border-color: rgba(34,197,94,0.08);
    color: rgba(74,222,128,0.18);
}
/* ── Fix extras (orange) ── */
QPushButton#fix_btn {
    background: rgba(251,146,60,0.10);
    border-color: rgba(251,146,60,0.30);
    color: #fb923c;
}
QPushButton#fix_btn:hover {
    background: rgba(251,146,60,0.18);
    border-color: #fb923c;
}
QPushButton#fix_btn:disabled {
    background: rgba(251,146,60,0.03);
    border-color: rgba(251,146,60,0.08);
    color: rgba(251,146,60,0.20);
}
/* ── P2P toggle ── */
QPushButton#p2p_btn {
    background: rgba(99,102,241,0.10);
    border-color: rgba(99,102,241,0.30);
    color: #a5b4fc;
    min-width: 90px;
}
QPushButton#p2p_btn:hover {
    background: rgba(99,102,241,0.18);
    border-color: #a5b4fc;
}
QPushButton#p2p_btn:checked {
    background: rgba(99,102,241,0.20);
    border: 1.5px solid rgba(99,102,241,0.65);
    color: #c7d2fe;
    font-weight: 600;
}
/* ── Progress bar ── */
QProgressBar {
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 5px;
    text-align: center;
    font-size: 11px;
    max-height: 8px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #6366f1, stop:1 #4ade80);
    border-radius: 4px;
}
/* ── Text/Log ── */
QTextEdit {
    background: rgba(0,0,0,0.30);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    color: #8898b8;
    font-family: 'Consolas','Cascadia Mono',monospace;
    font-size: 12px;
    padding: 6px;
}
/* ── Tables ── */
QTableWidget {
    background: rgba(0,0,0,0.20);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    gridline-color: rgba(255,255,255,0.04);
    color: #dde3f0;
    selection-background-color: rgba(99,102,241,0.22);
    alternate-background-color: rgba(255,255,255,0.02);
}
QTableWidget::item { padding: 5px 8px; }
QTableWidget::item:selected {
    background: rgba(99,102,241,0.28);
    color: #ffffff;
}
QTableWidget::item:focus { outline: none; border: none; }
QTableCornerButton::section {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.06);
}
QHeaderView::section {
    background: rgba(255,255,255,0.04);
    color: #5a6070;
    border: none;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    padding: 7px 10px;
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.5px;
}
/* ── Scrollbars ── */
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.15);
    border-radius: 3px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.25); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: transparent;
    height: 6px;
    border-radius: 3px;
}
QScrollBar::handle:horizontal {
    background: rgba(255,255,255,0.15);
    border-radius: 3px;
    min-width: 20px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
/* ── Tooltips ── */
QToolTip {
    background: #1a1d2a;
    border: 1px solid rgba(99,102,241,0.40);
    border-radius: 8px;
    color: #dde3f0;
    padding: 7px 11px;
    font-size: 12px;
    opacity: 240;
}
/* ── Labels ── */
QLabel { background: transparent; }
QLabel#section_label {
    background: transparent;
    color: #6366f1;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.8px;
    padding-bottom: 6px;
    border-bottom: 1px solid rgba(99,102,241,0.20);
    margin-bottom: 4px;
}
QLabel#status_ok   { color: #4ade80; font-weight: 600; }
QLabel#status_stop { color: #f87171; font-weight: 600; }
QLabel#game_running_lbl { color: #fb923c; font-weight: 600; font-size: 12px; }
QLabel#speed_lbl { color: #7dd3fc; font-weight: 600; }
/* ── Status bar ── */
QFrame#statusbar {
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 0px;
    min-height: 28px;
    max-height: 28px;
}
/* ── Status bar buttons ── */
QPushButton#sb_btn {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px;
    color: #5a6070;
    font-size: 11px;
    min-height: 20px;
    max-height: 20px;
    padding: 0 8px;
    min-width: 0;
}
QPushButton#sb_btn:hover {
    background: rgba(255,255,255,0.10);
    border-color: rgba(99,102,241,0.40);
    color: #dde3f0;
}
/* ── MessageBox ── */
QMessageBox {
    background-color: #0e1016;
    color: #dde3f0;
}
QMessageBox QPushButton {
    min-width: 80px;
    padding: 6px 16px;
}
/* ── Menu ── */
QMenu {
    background-color: #1a1d2a;
    color: #dde3f0;
    border: 1px solid rgba(99,102,241,0.30);
    border-radius: 8px;
    padding: 4px;
}
QMenu::item:selected {
    background-color: rgba(99,102,241,0.22);
    border-radius: 4px;
    color: #a5b4fc;
}
/* ── Tabs ── */
QTabWidget::pane {
    border: none;
    background: transparent;
}
QTabWidget > QStackedWidget { background: transparent; }
QTabWidget > QStackedWidget > QWidget { background: transparent; }
QTabBar { background: transparent; }
QTabBar::tab {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 7px 18px;
    color: #5a6070;
    margin: 4px 2px 0 2px;
    font-weight: 500;
}
QTabBar::tab:selected {
    background: rgba(99,102,241,0.15);
    color: #a5b4fc;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background: rgba(255,255,255,0.05);
    color: #dde3f0;
}
"""

def _make_card(title: str = "") -> tuple["QFrame", "QVBoxLayout", "QLabel | None"]:
    """Returns (card_frame, inner_layout, title_label_or_None)."""
    from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel
    card = QFrame()
    card.setObjectName("glass_card")
    outer = QVBoxLayout(card)
    outer.setContentsMargins(16, 14, 16, 14)
    outer.setSpacing(0)
    title_lbl = None
    if title:
        title_lbl = QLabel(title.upper())
        title_lbl.setObjectName("section_label")
        title_lbl.setFixedHeight(26)
        outer.addWidget(title_lbl)
    inner = QVBoxLayout()
    inner.setSpacing(8)
    inner.setContentsMargins(0, 6, 0, 0)
    outer.addLayout(inner)
    outer.addStretch(1)
    return card, inner, title_lbl


class ToggleSwitch(QWidget):
    """iOS-style toggle switch."""
    toggled = pyqtSignal(bool)

    def __init__(self, parent=None, checked: bool = False):
        super().__init__(parent)
        self._checked = checked
        self._anim = 1.0 if checked else 0.0
        self.setFixedSize(46, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._timer = QTimer(self)
        self._timer.setInterval(14)
        self._timer.timeout.connect(self._step)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, val: bool, emit: bool = True):
        if self._checked == val:
            return
        self._checked = val
        self._timer.start()
        if emit:
            self.toggled.emit(val)

    def _step(self):
        target = 1.0 if self._checked else 0.0
        diff = target - self._anim
        if abs(diff) < 0.08:
            self._anim = target
            self._timer.stop()
        else:
            self._anim += diff * 0.35
        self.update()

    def mousePressEvent(self, event):
        self.setChecked(not self._checked)

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._anim
        w, h = self.width(), self.height()
        r = int(0x2c + (0x22 - 0x2c) * t)
        g = int(0x30 + (0xc5 - 0x30) * t)
        b = int(0x44 + (0x5e - 0x44) * t)
        p.setBrush(QColor(r, g, b))
        p.setPen(Qt.PenStyle.NoPen)
        track_h = h - 4
        p.drawRoundedRect(0, 2, w, track_h, track_h // 2, track_h // 2)
        knob_d = track_h - 4
        knob_y = (h - knob_d) // 2
        knob_x = int(2 + t * (w - 4 - knob_d))
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(knob_x, knob_y, knob_d, knob_d)
        p.end()


# -----------------------------
# Paths / Config
# -----------------------------

APPDATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "ModSyncClient"
CONFIG_PATH = APPDATA_DIR / "config.json"

DEFAULT_GAME_EXE_STEAM = r"C:\Program Files (x86)\Steam\steamapps\common\7 Days to Die\7DaysToDie.exe"

CHECK_INTERVAL_OPTIONS_MIN = [10, 30, 60]
DEFAULT_CHECK_INTERVAL_MIN = 30

MASTER_SERVER_URL = "https://bar7dtd.ru"

# =====================================================================
#  LOCALISATION  (EN / RU)
# =====================================================================

TRANSLATIONS = {
    "en": {
        # Window
        "window_title":             "MODSYNC  //  7 DAYS TO DIE",
        "tray_tooltip":             "ModSync Client",
        "tray_show":                "Show",
        "tray_quit":                "Quit",
        # Labels
        "lbl_server_url":           "Server URL:",
        "lbl_steamid":              "SteamID:",
        "lbl_game_exe":             "Game EXE:",
        "lbl_mods_dir":             "Mods dir:",
        "lbl_autocheck":            "Auto-check:",
        "lbl_mod_comparison":       "▸ MOD COMPARISON",
        "lbl_game_offline":         "GAME: ● OFFLINE",
        "lbl_game_running":         "GAME: ⚠ RUNNING  [apply locked]",
        "lbl_status_idle":          "ожидание",
        # Placeholders
        "ph_server_url":            "e.g. http://192.168.0.11:8765",
        "ph_steamid":               "Click 'Login with Steam' or enter manually",
        # Buttons
        "btn_ping":                 "Ping",
        "btn_test_toast":           "Test toast",
        "btn_steam_login":          "⚙ Login via Steam",
        "btn_steam_change":         "Change Account",
        "btn_save":                 "Save",
        "btn_find_game":            "Find game",
        "btn_autocheck_on":         "Enabled",
        "btn_autocheck_off":        "Disabled",
        "btn_automode_on":          "AUTO MODE: ON",
        "btn_automode_off":         "AUTO MODE: OFF",
        "btn_show_rules":           "Show rules",
        "btn_reset_rules":          "Reset rules",
        "btn_check":                "Check updates",
        "btn_download":             "Update mods",
        "btn_apply":                "Apply update",
        "btn_fix":                  "Fix extras (move to disabled)",
        "btn_lang":                 "RU",
        # Table headers
        "th_mod":                   "Mod",
        "th_server_hash":           "Server hash",
        "th_local_hash":            "Local hash",
        "th_status":                "Status",
        "th_action":                "Action",
        # Row status values
        "rs_ok":                    "OK",
        "rs_outdated":              "OUTDATED",
        "rs_missing":               "MISSING",
        "rs_extra":                 "EXTRA (local)",
        "ra_update":                "UPDATE/DOWNLOAD",
        "ra_move":                  "MOVE TO disabled",
        "ra_none":                  "NONE",
        # Rules dialog
        "rules_title":              "Rules",
        "rules_text": (
            "Welcome to ModSync v2 — mod manager for 7 Days to Die servers.\n"
            "Please read before using:\n\n"

            "── WHAT THIS PROGRAM DOES ──────────────────────\n"
            "ModSync keeps your Mods folder in sync with the server.\n"
            "It downloads missing or outdated mods via BitTorrent (P2P)\n"
            "and moves extras out of the way — so you always join\n"
            "with exactly the right mods.\n\n"

            "── HOW DOWNLOAD WORKS (v2) ─────────────────────\n"
            "• Mods are distributed via BitTorrent — fast P2P transfer.\n"
            "• Files sync automatically; there is no separate Apply step.\n"
            "• Your client may upload (seed) mod data to other players\n"
            "  while idle. This uses a small amount of bandwidth.\n"
            "• You can turn P2P off in settings (server-only download).\n\n"

            "── CONNECTING TO THE SERVER ────────────────────\n"
            "• Enter the server address in the 'Server URL' field.\n"
            "  Usually it's the same IP as the game server, port 8765.\n"
            "  Example: http://192.168.1.10:8765\n"
            "• Ask your server admin if you're unsure of the address.\n"
            "• Use the Ping button to check the connection.\n\n"

            "── STEAM LOGIN ─────────────────────────────────\n"
            "• Login via Steam opens a browser page — standard Steam\n"
            "  OpenID, the same method used by many game sites.\n"
            "• We only read your SteamID (a public numeric ID).\n"
            "• No passwords, no tokens, no personal data is stored.\n"
            "• SteamID is used only if the server runs a whitelist.\n"
            "• You can clear it anytime with 'Change Account'.\n\n"

            "── YOUR MODS ARE SAFE ──────────────────────────\n"
            "• ModSync NEVER deletes your files.\n"
            "• Extra or outdated mods are moved to Mods\\disabled_mods\n"
            "  with a timestamp — you can restore them any time.\n\n"

            "── AUTO MODE ───────────────────────────────────\n"
            "• When enabled, ModSync checks and syncs updates\n"
            "  automatically in the background.\n\n"

            "By clicking YES you confirm that you have read this,\n"
            "agree to P2P seeding while ModSync is running,\n"
            "and allow ModSync to manage your Mods folder.\n\n"
            "Accept?"
        ),
        # Dialogs
        "dlg_error_title":          "Error",
        "dlg_wrong_file_title":     "Wrong file",
        "dlg_wrong_file_text":      "Please select 7DaysToDie.exe",
        # Errors / warnings (on_error)
        "err_server_empty":         "Server URL is empty",
        "err_steamid_empty":        "SteamID is empty (required by server)",
        "err_mods_dir_empty":       "Mods dir is not set",
        "err_game_running_apply":   "Game is running. Close the game before applying update.",
        "err_game_running_fix":     "Game is running. Close the game before moving extras.",
        "err_no_bundle":            "No downloaded bundle. Click Download first.",
        "err_no_diff":              "No diff. Click Check updates first.",
        "err_nothing_to_dl":        "Nothing to download",
        "err_missing_server_url":   "Missing Server URL / SteamID / Mods dir",
        # Toasts
        "toast_title":              "ModSync",
        "toast_test":               "Test notification",
        "toast_steam_ok":           "Steam login OK ✅",
        "toast_steam_cleared":      "SteamID cleared. Please login again.",
        "toast_steam_opening":      "Steam login opened in browser…",
        "toast_up_to_date":         "Up-to-date ✅",
        "toast_updates_available":  "Updates available: +{dl} / move {rm}",
        "toast_extras_detected":    "Extras detected. Close the game to move them.",
        "toast_extras_moved":       "Extras moved to disabled ✅",
        "toast_downloaded":         "Downloaded. Close the game to apply.",
        "toast_update_applied":     "Update applied ✅",
        "toast_verify_failed":      "Verify failed (still differs)",
        "toast_auto_update":        "Auto update started...",
        "toast_auto_pending":       "Updates found. Close the game to apply.",
        "toast_auto_available":     "Updates are available. Click 'Check updates'.",
        "toast_auto_uptodate":      "Auto check: up-to-date ✅",
        "p2p_dlg_title":            "P2P sharing",
        "p2p_dlg_text":             (
            "While downloading, ModSync also seeds mods to other players on the same server.\n\n"
            "This speeds up downloads for everyone and reduces load on the server.\n"
            "A small amount of upload bandwidth is used while the app is open.\n\n"
            "Enable P2P sharing?"
        ),
        "p2p_dlg_enable":           "Enable P2P",
        "p2p_dlg_server_only":      "Server only",
        # Card group titles
        "grp_connection":           "Connection",
        "grp_local":                "Local",
        "grp_sync":                 "Sync",
        "lbl_log_title":            "Log",
        # Labels
        "lbl_automode":             "Auto mode:",
        "lbl_automode_tip":         "When ON: after checking, auto-downloads and applies updates if game is closed",
        "lbl_p2p_share":            "P2P share:",
        "lbl_p2p_tip":              "Seeds mods to other players — speeds up downloads for everyone",
        # Tabs
        "tab_mods":                 "Mods",
        "tab_log":                  "Log",
        # Buttons
        "btn_refresh_srv":          "Refresh",
        "btn_manual_srv":           "Manual",
        "btn_appdata":              "AppData",
        # GitHub status
        "gh_checking":              "GitHub: …",
        "gh_uptodate":              "GitHub: ✓ up to date",
        "gh_update":                "GitHub: update {v}",
        # Extra buttons
        "btn_browse":               "Browse",
        # Runtime status messages
        "st_ping":                  "ping…",
        "st_ping_ok":               "ping: ok",
        "st_get_build":             "fetching build info…",
        "st_get_manifest":          "fetching manifest…",
        "st_scan_files":            "scanning local files…",
        "st_update_ready":          "ready to update",
        "st_up_to_date":            "up-to-date",
        "st_get_torrent":           "fetching torrent…",
        "st_checking":              "checking / downloading…",
        "st_verify":                "verifying… {pct}%",
        "st_downloading":           "downloading {pct}% · {spd} MB/s · {peers} peers",
        "st_applying":              "applying…",
        "st_verify_failed":         "needs update (verify failed)",
        "st_p2p_idle":              "P2P · waiting",
        "st_p2p_seeding":           "P2P  ↑{spd} MB/s · {peers} peer{s}",
    },
    "ru": {
        # Window
        "window_title":             "MODSYNC  //  7 DAYS TO DIE",
        "tray_tooltip":             "ModSync Клиент",
        "tray_show":                "Показать",
        "tray_quit":                "Выйти",
        # Labels
        "lbl_server_url":           "Адрес сервера:",
        "lbl_steamid":              "SteamID:",
        "lbl_game_exe":             "Игра (EXE):",
        "lbl_mods_dir":             "Папка модов:",
        "lbl_autocheck":            "Автопроверка:",
        "lbl_mod_comparison":       "▸ СРАВНЕНИЕ МОДОВ",
        "lbl_game_offline":         "ИГРА: ● ВЫКЛЮЧЕНА",
        "lbl_game_running":         "ИГРА: ⚠ ЗАПУЩЕНА  [применение заблокировано]",
        "lbl_status_idle":          "ожидание",
        # Placeholders
        "ph_server_url":            "например http://192.168.0.11:8765",
        "ph_steamid":               "Нажмите 'Войти через Steam' или введите вручную",
        # Buttons
        "btn_ping":                 "Пинг",
        "btn_test_toast":           "Тест уведомления",
        "btn_steam_login":          "⚙ Войти через Steam",
        "btn_steam_change":         "Сменить аккаунт",
        "btn_save":                 "Сохранить",
        "btn_find_game":            "Найти игру",
        "btn_autocheck_on":         "Включена",
        "btn_autocheck_off":        "Выключена",
        "btn_automode_on":          "АВТОРЕЖИМ: ВКЛ",
        "btn_automode_off":         "АВТОРЕЖИМ: ВЫКЛ",
        "btn_show_rules":           "Правила",
        "btn_reset_rules":          "Сбросить правила",
        "btn_check":                "Проверить обновления",
        "btn_download":             "Обновить моды",
        "btn_apply":                "Применить обновление",
        "btn_fix":                  "Убрать лишние моды",
        "btn_lang":                 "EN",
        # Table headers
        "th_mod":                   "Мод",
        "th_server_hash":           "Хэш сервера",
        "th_local_hash":            "Локальный хэш",
        "th_status":                "Статус",
        "th_action":                "Действие",
        # Row status values
        "rs_ok":                    "OK",
        "rs_outdated":              "УСТАРЕЛ",
        "rs_missing":               "ОТСУТСТВУЕТ",
        "rs_extra":                 "ЛИШНИЙ (локальный)",
        "ra_update":                "ОБНОВИТЬ/СКАЧАТЬ",
        "ra_move":                  "УБРАТЬ в disabled",
        "ra_none":                  "НЕТ",
        # Rules dialog
        "rules_title":              "Правила",
        "rules_text": (
            "Добро пожаловать в ModSync v2 — менеджер модов для серверов 7 Days to Die.\n"
            "Пожалуйста, прочитайте перед использованием:\n\n"

            "── ЧТО ДЕЛАЕТ ЭТА ПРОГРАММА ────────────────────\n"
            "ModSync синхронизирует вашу папку Mods с сервером.\n"
            "Программа скачивает отсутствующие или устаревшие моды\n"
            "через BitTorrent (P2P), а лишние убирает в сторону —\n"
            "чтобы вы всегда заходили с нужными модами.\n\n"

            "── КАК РАБОТАЕТ ЗАГРУЗКА (v2) ──────────────────\n"
            "• Моды распространяются через BitTorrent — быстрая P2P-раздача.\n"
            "• Файлы синхронизируются автоматически; кнопки «Применить» нет.\n"
            "• Пока ModSync запущен, ваш клиент может раздавать (сидировать)\n"
            "  моды другим игрокам. Расходуется небольшой трафик.\n"
            "• P2P можно отключить в настройках (только скачивание с сервера).\n\n"

            "── ПОДКЛЮЧЕНИЕ К СЕРВЕРУ ────────────────────────\n"
            "• Введите адрес сервера в поле «Адрес сервера».\n"
            "  Обычно это тот же IP, что и у игрового сервера, порт 8765.\n"
            "  Пример: http://192.168.1.10:8765\n"
            "• Если не знаете адрес — спросите у администратора сервера.\n"
            "• Нажмите Пинг, чтобы проверить соединение.\n\n"

            "── ВХОД ЧЕРЕЗ STEAM ─────────────────────────────\n"
            "• Кнопка «Войти через Steam» открывает страницу в браузере —\n"
            "  стандартный Steam OpenID, как на многих игровых сайтах.\n"
            "• Программа получает только ваш SteamID (публичный числовой ID).\n"
            "• Пароли, токены и личные данные не запрашиваются и не хранятся.\n"
            "• SteamID нужен только если сервер работает в режиме белого списка.\n"
            "• Вы можете сбросить его в любой момент через «Сменить аккаунт».\n\n"

            "── ВАШИ МОДЫ В БЕЗОПАСНОСТИ ─────────────────────\n"
            "• ModSync НИКОГДА не удаляет ваши файлы.\n"
            "• Лишние и устаревшие моды перемещаются в папку Mods\\disabled_mods\n"
            "  с меткой времени — вы всегда сможете их восстановить вручную.\n\n"

            "── АВТОРЕЖИМ ────────────────────────────────────\n"
            "• При включённом авторежиме ModSync самостоятельно проверяет\n"
            "  и синхронизирует обновления в фоне.\n\n"

            "Нажимая ДА, вы подтверждаете, что прочитали это,\n"
            "соглашаетесь с P2P-раздачей пока ModSync запущен,\n"
            "и разрешаете ModSync управлять вашей папкой Mods.\n\n"
            "Принять?"
        ),
        # Dialogs
        "dlg_error_title":          "Ошибка",
        "dlg_wrong_file_title":     "Неверный файл",
        "dlg_wrong_file_text":      "Выберите файл 7DaysToDie.exe",
        # Errors / warnings
        "err_server_empty":         "Адрес сервера не указан",
        "err_steamid_empty":        "SteamID не указан (требуется сервером)",
        "err_mods_dir_empty":       "Папка модов не задана",
        "err_game_running_apply":   "Игра запущена. Закройте игру перед применением обновления.",
        "err_game_running_fix":     "Игра запущена. Закройте игру перед перемещением модов.",
        "err_no_bundle":            "Пакет не скачан. Сначала нажмите «Скачать».",
        "err_no_diff":              "Нет данных сравнения. Сначала нажмите «Проверить обновления».",
        "err_nothing_to_dl":        "Нечего скачивать",
        "err_missing_server_url":   "Не указан адрес сервера / SteamID / папка модов",
        # Toasts
        "toast_title":              "ModSync",
        "toast_test":               "Тестовое уведомление",
        "toast_steam_ok":           "Вход через Steam выполнен ✅",
        "toast_steam_cleared":      "SteamID сброшен. Войдите снова.",
        "toast_steam_opening":      "Браузер открыт для входа в Steam…",
        "toast_up_to_date":         "Моды актуальны ✅",
        "toast_updates_available":  "Доступны обновления: +{dl} / убрать {rm}",
        "toast_extras_detected":    "Лишние моды обнаружены. Закройте игру чтобы убрать их.",
        "toast_extras_moved":       "Лишние моды перемещены ✅",
        "toast_downloaded":         "Скачано. Закройте игру чтобы применить.",
        "toast_update_applied":     "Обновление применено ✅",
        "toast_verify_failed":      "Проверка не прошла (файлы отличаются)",
        "toast_auto_update":        "Запуск автообновления...",
        "toast_auto_pending":       "Найдены обновления. Закройте игру для применения.",
        "toast_auto_available":     "Доступны обновления. Нажмите «Проверить обновления».",
        "toast_auto_uptodate":      "Автопроверка: моды актуальны ✅",
        "toast_automode_on":         "Авторежим включён — будет обновлять автоматически",
        "toast_automode_off":        "Авторежим отключён",
        "p2p_dlg_title":            "P2P раздача",
        "p2p_dlg_text":             (
            "При загрузке ModSync раздаёт моды другим игрокам на том же сервере.\n\n"
            "Это ускоряет загрузку для всех и снижает нагрузку на сервер.\n"
            "Расходуется небольшой исходящий трафик пока приложение открыто.\n\n"
            "Включить P2P раздачу?"
        ),
        "p2p_dlg_enable":           "Включить P2P",
        "p2p_dlg_server_only":      "Только сервер",
        # Card group titles
        "grp_connection":           "Подключение",
        "grp_local":                "Локально",
        "grp_sync":                 "Синхронизация",
        "lbl_log_title":            "Лог",
        # Labels
        "lbl_automode":             "Авторежим:",
        "lbl_automode_tip":         "Если ВКЛ: автоматически скачивает и применяет обновления когда игра закрыта",
        "lbl_p2p_share":            "P2P раздача:",
        "lbl_p2p_tip":              "Раздача модов другим игрокам — ускоряет загрузку для всех",
        # Tabs
        "tab_mods":                 "Моды",
        "tab_log":                  "Лог",
        # Buttons
        "btn_refresh_srv":          "Обновить",
        "btn_manual_srv":           "Вручную",
        "btn_appdata":              "AppData",
        # GitHub status
        "gh_checking":              "GitHub: …",
        "gh_uptodate":              "GitHub: ✓ актуально",
        "gh_update":                "GitHub: обновление {v}",
        # Extra buttons
        "btn_browse":               "Выбрать",
        # Runtime status messages
        "st_ping":                  "пинг…",
        "st_ping_ok":               "пинг: ок",
        "st_get_build":             "запрос сборки…",
        "st_get_manifest":          "получение манифеста…",
        "st_scan_files":            "сканирование файлов…",
        "st_update_ready":          "готово к обновлению",
        "st_up_to_date":            "актуально",
        "st_get_torrent":           "получение торрента…",
        "st_checking":              "проверка / загрузка…",
        "st_verify":                "сверка файлов… {pct}%",
        "st_downloading":           "загрузка {pct}% · {spd} МБ/с · пиров {peers}",
        "st_applying":              "применение…",
        "st_verify_failed":         "нужно обновление (ошибка проверки)",
        "st_p2p_idle":              "P2P · ожидание",
        "st_p2p_seeding":           "P2P  ↑{spd} МБ/с · {peers} пир{s}",
    },
}

# -----------------------------
# Steam OpenID (client-only auth)
# -----------------------------

STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"


def _build_steam_openid_url(return_to: str, realm: str) -> str:
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return STEAM_OPENID_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _extract_steamid_from_claimed_id(claimed_id: str) -> str:
    if not claimed_id:
        return ""
    parts = claimed_id.strip("/").split("/")
    if parts and parts[-1].isdigit():
        return parts[-1]
    return ""


def _verify_steam_openid_response(query_params: dict) -> bool:
    """POST check_authentication back to Steam to verify the login."""
    data = dict(query_params)
    data["openid.mode"] = "check_authentication"
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(STEAM_OPENID_ENDPOINT, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        txt = resp.read().decode("utf-8", errors="replace")
    return "is_valid:true" in txt


def _pick_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ensure_dirs():
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)

def resource_path(rel: str) -> Path:
    """
    Работает и в режиме .py, и в режиме PyInstaller .exe
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / rel
    return Path(__file__).resolve().parent / rel

def read_config() -> dict:
    ensure_dirs()
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def write_config(cfg: dict) -> None:
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# -----------------------------
# Utilities
# -----------------------------

def is_game_running() -> bool:
    """
    Проверка запущенной игры без мигающих консольных окон.
    Быстро: tasklist с фильтром по имени процесса.
    """
    targets = ["7DaysToDie.exe", "7DaysToDie_EAC.exe"]

    creationflags = 0
    startupinfo = None

    # Windows-only: спрятать консольное окно
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

    try:
        for t in targets:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {t}"],
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                startupinfo=startupinfo
            )
            # Если процесс найден, tasklist вернёт строку с именем процесса
            if t.lower() in out.lower():
                return True
        return False
    except Exception:
        return False


def ensure_disabled_dir(mods_root: Path) -> Path:
    d = mods_root / "disabled_mods"
    d.mkdir(parents=True, exist_ok=True)
    return d


def move_to_disabled(mods_root: Path, src: Path, reason: str, log_cb):
    if not src.exists():
        return
    disabled = ensure_disabled_dir(mods_root)
    dst = disabled / src.name
    if dst.exists():
        ts = time.strftime("%Y%m%d_%H%M%S")
        dst = disabled / f"{src.name}_{ts}"
    log_cb(f"[MOVE] {src.name} -> disabled_mods")
    try:
        shutil.move(str(src), str(dst))
    except Exception as e:
        raise RuntimeError(f"Failed to move '{src}' to disabled_mods: {e}")


def strict_cleanup_to_disabled(mods_root: Path, server_mod_ids: set[str], log_cb):
    """
    Новая логика:
    - лишние папки/моды НЕ удаляем
    - всё, что:
        a) папка без ModInfo.xml (кроме disabled_mods)
        b) мод с ModInfo.xml, но его нет в server_mod_ids
      -> переносим в disabled_mods
    """
    if not mods_root.exists():
        return

    for entry in mods_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.lower() == "disabled_mods":
            continue

        modinfo = entry / "ModInfo.xml"
        if not modinfo.exists():
            # не мод
            move_to_disabled(mods_root, entry, "nonmod", log_cb)
            continue

        # это мод, но возможно лишний
        if entry.name not in server_mod_ids:
            move_to_disabled(mods_root, entry, "extra", log_cb)




def human_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(max(n, 0))
    i = 0
    while x >= 1024 and i < len(units) - 1:
        x /= 1024
        i += 1
    if i == 0:
        return f"{int(x)} {units[i]}"
    return f"{x:.2f} {units[i]}"


# -----------------------------
# Worker signals
# -----------------------------

class UiBridge(QObject):
    log = pyqtSignal(str)
    status = pyqtSignal(str)
    diff_ready = pyqtSignal(dict)
    servermods_ready = pyqtSignal(dict)
    error = pyqtSignal(str)
    set_busy = pyqtSignal(bool)
    toast = pyqtSignal(str, str)  # title, message
    steamid_ready = pyqtSignal(str)  # steamid from Steam OpenID login
    download_progress = pyqtSignal(int, float)  # pct 0-100, speed MB/s

    start_check = pyqtSignal(str)  # context: "manual"/"auto"
@dataclass
class DiffResult:
    server_manifest_hash: str
    download: list
    delete: list

# -----------------------------
# Toast app
# -----------------------------
class OverlayToast(QWidget):
    """Уведомление в правом нижнем углу с заголовком и телом."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(380)

        self._title = QLabel("")
        self._title.setWordWrap(True)
        self._title.setStyleSheet(
            "background: transparent; border: none;"
            "font-family: 'Segoe UI', sans-serif;"
            "font-size: 13px; font-weight: 700; color: #a5b4fc;"
        )

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(99,102,241,0.30); border: none;")

        self._msg = QLabel("")
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet(
            "background: transparent; border: none;"
            "font-family: 'Segoe UI', sans-serif;"
            "font-size: 12px; color: #8898b8;"
        )

        # left accent strip + content side by side
        accent = QFrame()
        accent.setFixedWidth(3)
        accent.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #6366f1,stop:1 #4ade80); border: none; border-radius: 2px;"
        )

        body = QVBoxLayout()
        body.setSpacing(6)
        body.addWidget(self._title)
        body.addWidget(sep)
        body.addWidget(self._msg)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(accent)
        row.addLayout(body, 1)

        card = QWidget()
        card.setObjectName("toast_card")
        card.setLayout(row)
        card.setContentsMargins(14, 12, 14, 12)
        card.setStyleSheet(
            "QWidget#toast_card { background: rgba(14,16,22,245);"
            "border-radius: 12px; border: none; }"
        )

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(card)
        self.setLayout(root)

        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(1.0)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_toast(self, title: str, message: str, duration_ms: int = 4000):
        self._title.setVisible(bool(title))
        self._title.setText(title)
        self._msg.setText(message)
        self.adjustSize()
        self._move_to_bottom_right()
        self._opacity.setOpacity(1.0)
        self.show()
        self.raise_()
        self._hide_timer.start(duration_ms)

    def _move_to_bottom_right(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        margin = 20
        x = geo.x() + geo.width() - self.width() - margin
        y = geo.y() + geo.height() - self.height() - margin
        self.move(QPoint(x, y))

# -----------------------------
# Client app
# -----------------------------

class ClientWindow(QWidget):
    _ms_servers_signal = pyqtSignal(object)  # list[dict]
    _gh_status_signal  = pyqtSignal(str)     # GitHub check result
    _p2p_status_signal = pyqtSignal(str)     # P2P upload status

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MODSYNC  //  7 DAYS TO DIE")
        self.resize(1200, 760)
        self.setMinimumWidth(900)
        self.setStyleSheet(GLASS_STYLE)

        ensure_dirs()
        self.cfg = read_config()
        self._lang = self.cfg.get("lang", "ru")  # current language

        # ── V2 (torrent) state ──
        self.v2_engine: modsync_v2.ClientEngine | None = None
        self.v2_manifest: dict | None = None
        self.v2_build: dict | None = None
        self.v2_updating: bool = False
        self._v2_poll_timer = QTimer(self)
        self._v2_poll_timer.setInterval(1000)
        self._v2_poll_timer.timeout.connect(self._v2_poll)

        self._init_content()

    def tr(self, key: str, **kwargs) -> str:
        """Return translated string for current language."""
        lang = getattr(self, "_lang", "en")
        text = TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, key)
        return text.format(**kwargs) if kwargs else text

    def _init_content(self):





        # flags / dedupe
        self.fixing_extras = False
        self._last_extras_fix_hash = ""
        self._next_check_context = "manual"  # used by on_check_updates_full
        self._last_toast = ("", 0.0)  # (message, ts)

        # state
        self.last_diff: Optional[DiffResult] = None
        self.server_mods: list[dict] = []
        self.server_manifest_hash: str = ""
        self.local_mods_map: dict[str, str] = {}

        # auto-pipeline guards (prevent double auto download/apply)
        self._auto_pipeline_running = False
        self._auto_target_hash = ""
        self._auto_download_started = False
        self._auto_last_start_ts = 0.0

        # bridge
        self.bridge = UiBridge()
        self.bridge.log.connect(self.append_log)
        self.bridge.status.connect(self.set_status)
        self.bridge.diff_ready.connect(self.on_diff_ready)
        self.bridge.servermods_ready.connect(self.on_servermods_ready)
        self.bridge.error.connect(self.on_error)
        self.bridge.set_busy.connect(self.set_busy)
        self.bridge.toast.connect(self.show_toast)
        self.bridge.steamid_ready.connect(self.on_steamid_ready)
        self.bridge.download_progress.connect(self.on_download_progress)

        self._ping_last = 0.0  # 1s debounce for ping
        self._ms_servers_signal.connect(self._populate_server_combo)
        self.bridge.start_check.connect(self._start_check)

        # LAN discovery disabled (conflicts with libtorrent WSAStartup on Windows)
        self._lan_discovery = None

        # --- tray for notifications
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("ModSync Client")
        icon_path = resource_path("favicon.ico")
        icon = QIcon(str(icon_path)) if icon_path.exists() else QApplication.style().standardIcon(QApplication.style().StandardPixmap.SP_ComputerIcon)
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(icon)
        tray_menu = QMenu()
        act_show = tray_menu.addAction("Show")
        act_show.triggered.connect(self.showNormal)
        act_quit = tray_menu.addAction("Quit")
        act_quit.triggered.connect(QApplication.quit)
        self.tray.setContextMenu(tray_menu)
        self.tray.show()
        self.tray.activated.connect(self.on_tray_activated)
        self.overlay_toast = OverlayToast()
        
        # ---------- UI ----------
        # layout: top_row (Connection|Sync) → Local → tabs (Mods|Log) → statusbar

        def _lbl(text="", w=None):
            l = QLabel(text)
            l.setStyleSheet("color: #5a6070; font-size: 13px; background: transparent;")
            l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if w:
                l.setFixedWidth(w)
            return l

        # ─── Card: Connection ────────────────────────────────
        card_conn = QFrame(); card_conn.setObjectName("glass_card")
        conn_l = QVBoxLayout(card_conn)
        conn_l.setContentsMargins(16, 14, 16, 14); conn_l.setSpacing(8)
        self._conn_title_lbl = QLabel(); self._conn_title_lbl.setObjectName("section_label")
        conn_l.addWidget(self._conn_title_lbl)

        # server row
        r0 = QHBoxLayout(); r0.setSpacing(6)
        self.lbl_server_url = _lbl(w=110)
        r0.addWidget(self.lbl_server_url)
        self.server_combo = QComboBox()
        self.server_combo.setMinimumWidth(80)
        self.server_combo.setToolTip("Выбери сервер из списка")
        self.server_combo.currentIndexChanged.connect(self._on_server_combo_changed)
        r0.addWidget(self.server_combo, 1)
        self.server_edit = QLineEdit(self.cfg.get("server_url", ""))
        self.server_edit.setVisible(False)
        r0.addWidget(self.server_edit, 1)
        self.refresh_servers_btn = QPushButton()
        self.refresh_servers_btn.setToolTip("Обновить список серверов")
        self.refresh_servers_btn.clicked.connect(self._fetch_server_list)
        r0.addWidget(self.refresh_servers_btn)
        self.ping_btn = QPushButton(); self.ping_btn.setVisible(False)
        self.ping_btn.clicked.connect(self.on_ping)
        r0.addWidget(self.ping_btn)
        self.save_btn = QPushButton(); self.save_btn.setVisible(False)
        self.save_btn.clicked.connect(self.on_save)
        r0.addWidget(self.save_btn)
        self.manual_server_btn = QPushButton()
        self.manual_server_btn.setCheckable(True)
        self.manual_server_btn.setToolTip("Ввести адрес сервера вручную")
        self.manual_server_btn.clicked.connect(self._on_toggle_manual_server)
        r0.addWidget(self.manual_server_btn)
        conn_l.addLayout(r0)

        # SteamID row
        r1 = QHBoxLayout(); r1.setSpacing(6)
        self.lbl_steamid = _lbl(w=80)
        r1.addWidget(self.lbl_steamid)
        _saved_sid = self.cfg.get("steamid", "")
        if not _saved_sid:
            try:
                _accs = modsync_v2.read_steam_accounts()
                if _accs:
                    _saved_sid = _accs[0]["steamid"]
                    self.cfg["steamid"] = _saved_sid
                    write_config(self.cfg)
            except Exception:
                pass
        self.steam_edit = QLineEdit(_saved_sid)
        if self.cfg.get("steamid", "").strip():
            self.steam_edit.setReadOnly(True)
        r1.addWidget(self.steam_edit, 1)
        self.steam_login_btn = QPushButton()
        self.steam_login_btn.clicked.connect(self.on_steam_login)
        if self.cfg.get("steamid", "").strip():
            self.steam_login_btn.setEnabled(False)
        r1.addWidget(self.steam_login_btn)
        self.steam_change_btn = QPushButton()
        self.steam_change_btn.clicked.connect(self.on_change_account)
        r1.addWidget(self.steam_change_btn)
        conn_l.addLayout(r1)
        conn_l.addStretch(1)

        # ─── Card: Sync ─────────────────────────────────────
        card_sync = QFrame(); card_sync.setObjectName("glass_card")
        sync_l = QVBoxLayout(card_sync)
        sync_l.setContentsMargins(16, 14, 16, 14); sync_l.setSpacing(8)
        self._sync_title_lbl = QLabel(); self._sync_title_lbl.setObjectName("section_label")
        sync_l.addWidget(self._sync_title_lbl)

        # autocheck row: label | ToggleSwitch | combo
        ra = QHBoxLayout(); ra.setSpacing(8)
        self.lbl_autocheck = _lbl(w=100)
        ra.addWidget(self.lbl_autocheck)
        self.autocheck_btn = ToggleSwitch(checked=bool(self.cfg.get("auto_check_enabled", True)))
        self.autocheck_btn.toggled.connect(self.on_toggle_autocheck)
        ra.addWidget(self.autocheck_btn)
        self.autocheck_combo = QComboBox(); self.autocheck_combo.setFixedWidth(80)
        for m in CHECK_INTERVAL_OPTIONS_MIN:
            self.autocheck_combo.addItem(f"{m} min", m)
        _saved_iv = int(self.cfg.get("check_interval_min", DEFAULT_CHECK_INTERVAL_MIN))
        if _saved_iv not in CHECK_INTERVAL_OPTIONS_MIN:
            _saved_iv = DEFAULT_CHECK_INTERVAL_MIN
        self.autocheck_combo.setCurrentIndex(CHECK_INTERVAL_OPTIONS_MIN.index(_saved_iv))
        ra.addWidget(self.autocheck_combo)
        ra.addStretch(1)
        sync_l.addLayout(ra)

        # automode row: label | ToggleSwitch | tip
        rb = QHBoxLayout(); rb.setSpacing(8)
        self.lbl_automode = _lbl(w=100)
        rb.addWidget(self.lbl_automode)
        self.automode_btn = ToggleSwitch(checked=bool(self.cfg.get("auto_mode", False)))
        self.automode_btn.toggled.connect(self.on_toggle_automode)
        rb.addWidget(self.automode_btn)
        self._automode_tip = QLabel()
        self._automode_tip.setStyleSheet("color: #3d4060; font-size: 10px; background: transparent;")
        self._automode_tip.setWordWrap(True)
        rb.addWidget(self._automode_tip, 1)
        sync_l.addLayout(rb)

        # P2P row: label | ToggleSwitch | tip
        rc = QHBoxLayout(); rc.setSpacing(8)
        self.lbl_p2p = _lbl(w=100)
        rc.addWidget(self.lbl_p2p)
        self.share_btn = ToggleSwitch(checked=bool(self.cfg.get("p2p_share", True)))
        self.share_btn.toggled.connect(self.on_toggle_share)
        rc.addWidget(self.share_btn)
        self._p2p_tip = QLabel()
        self._p2p_tip.setStyleSheet("color: #3d4060; font-size: 10px; background: transparent;")
        rc.addWidget(self._p2p_tip, 1)
        sync_l.addLayout(rc)

        # game status row (stays in sync card)
        rg = QHBoxLayout(); rg.setSpacing(8)
        self.game_status_lbl = QLabel()
        self.game_status_lbl.setStyleSheet("color: #fb923c; font-size: 12px; font-weight: 600; background: transparent;")
        rg.addWidget(self.game_status_lbl)
        rg.addStretch(1)
        sync_l.addLayout(rg)

        # top row: Connection | Sync
        top_row = QHBoxLayout(); top_row.setSpacing(8)
        top_row.addWidget(card_conn, 1)
        top_row.addWidget(card_sync, 1)

        # ─── Card: Local (full width, horizontal) ───────────
        card_local = QFrame(); card_local.setObjectName("glass_card")
        local_l = QVBoxLayout(card_local)
        local_l.setContentsMargins(16, 12, 16, 12); local_l.setSpacing(6)
        self._local_title_lbl = QLabel(); self._local_title_lbl.setObjectName("section_label")
        local_l.addWidget(self._local_title_lbl)

        local_row = QHBoxLayout(); local_row.setSpacing(10)
        self.lbl_game_exe = _lbl(w=100)
        local_row.addWidget(self.lbl_game_exe)
        self.game_edit = QLineEdit(self.cfg.get("game_exe", "")); self.game_edit.setReadOnly(True)
        local_row.addWidget(self.game_edit, 1)
        self.find_game_btn = QPushButton()
        self.find_game_btn.clicked.connect(self.on_find_game)
        local_row.addWidget(self.find_game_btn)

        local_row.addSpacing(16)

        self.lbl_mods_dir = _lbl(w=100)
        local_row.addWidget(self.lbl_mods_dir)
        self.mods_edit = QLineEdit(self.cfg.get("mods_dir", "")); self.mods_edit.setReadOnly(True)
        local_row.addWidget(self.mods_edit, 1)
        self.browse_mods_btn = QPushButton()
        self.browse_mods_btn.setToolTip("Выбрать папку Mods вручную")
        self.browse_mods_btn.clicked.connect(self.on_browse_mods)
        local_row.addWidget(self.browse_mods_btn)
        local_l.addLayout(local_row)

        # ─── Card: Actions ──────────────────────────────────
        card_actions = QFrame(); card_actions.setObjectName("glass_card")
        actions_l = QVBoxLayout(card_actions)
        actions_l.setContentsMargins(16, 12, 16, 12); actions_l.setSpacing(6)

        # row 1: action buttons + P2P status
        rd = QHBoxLayout(); rd.setSpacing(6)
        self.check_btn = QPushButton(); self.check_btn.setObjectName("check_btn")
        self.check_btn.clicked.connect(self.on_check_updates_full)
        rd.addWidget(self.check_btn)
        self.download_btn = QPushButton(); self.download_btn.setObjectName("download_btn")
        self.download_btn.clicked.connect(self.on_download_bundle)
        self.download_btn.setEnabled(False)
        rd.addWidget(self.download_btn)
        self.fix_btn = QPushButton(); self.fix_btn.setObjectName("fix_btn")
        self.fix_btn.clicked.connect(self.on_fix_extras)
        self.fix_btn.setEnabled(False)
        rd.addWidget(self.fix_btn)
        self.rules_btn = QPushButton()
        self.rules_btn.clicked.connect(lambda: self.show_rules_dialog(force=True))
        rd.addWidget(self.rules_btn)
        rd.addStretch(1)
        _p2p_sep = QFrame()
        _p2p_sep.setFrameShape(QFrame.Shape.VLine)
        _p2p_sep.setFrameShadow(QFrame.Shadow.Plain)
        _p2p_sep.setFixedWidth(1)
        _p2p_sep.setStyleSheet("background: rgba(255,255,255,0.12); border: none;")
        rd.addWidget(_p2p_sep)
        self._p2p_info_lbl = QLabel()
        self._p2p_info_lbl.setStyleSheet(
            "color: #5a6070; font-size: 12px; background: transparent; padding-left: 8px;")
        self._p2p_info_lbl.setMinimumWidth(180)
        rd.addWidget(self._p2p_info_lbl)
        actions_l.addLayout(rd)

        # row 2: status + progress
        re = QHBoxLayout(); re.setSpacing(8)
        self.status_lbl = QLabel()
        self.status_lbl.setObjectName("status_ok")
        re.addWidget(self.status_lbl)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False); self.progress_bar.setVisible(False)
        re.addWidget(self.progress_bar, 1)
        self.progress_pct_lbl = QLabel("")
        self.progress_pct_lbl.setObjectName("speed_lbl"); self.progress_pct_lbl.setFixedWidth(42)
        self.progress_pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.progress_pct_lbl.setVisible(False)
        re.addWidget(self.progress_pct_lbl)
        self.speed_lbl = QLabel("")
        self.speed_lbl.setObjectName("speed_lbl"); self.speed_lbl.setFixedWidth(90)
        self.speed_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.speed_lbl.setVisible(False)
        re.addWidget(self.speed_lbl)
        actions_l.addLayout(re)

        # ─── Tab widget: Mods | Log ──────────────────────────
        tab_card = QFrame(); tab_card.setObjectName("glass_card")
        tab_card_l = QVBoxLayout(tab_card)
        tab_card_l.setContentsMargins(8, 8, 8, 8); tab_card_l.setSpacing(0)

        self.main_tabs = QTabWidget()
        tab_card_l.addWidget(self.main_tabs)

        # Mods tab
        mods_tab = QWidget()
        mods_tab_l = QVBoxLayout(mods_tab)
        mods_tab_l.setContentsMargins(0, 6, 0, 0); mods_tab_l.setSpacing(4)
        self.lbl_compare = QLabel(); self.lbl_compare.setObjectName("section_label")
        mods_tab_l.addWidget(self.lbl_compare)
        self.compare_table = QTableWidget(0, 5)
        self.compare_table.setHorizontalHeaderLabels(["Mod", "Server hash", "Local hash", "Status", "Action"])
        hdr = self.compare_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.compare_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.compare_table.setAlternatingRowColors(True)
        self.compare_table.cellDoubleClicked.connect(self.on_table_double_click)
        mods_tab_l.addWidget(self.compare_table, 1)

        # Log tab
        log_tab = QWidget()
        log_tab_l = QVBoxLayout(log_tab)
        log_tab_l.setContentsMargins(0, 6, 0, 0); log_tab_l.setSpacing(0)
        self._log_title_lbl = QLabel(); self._log_title_lbl.setObjectName("section_label")
        log_tab_l.addWidget(self._log_title_lbl)
        self.log_view = QTextEdit(); self.log_view.setReadOnly(True)
        log_tab_l.addWidget(self.log_view, 1)

        self.main_tabs.addTab(mods_tab, "")   # text set in retranslate_ui
        self.main_tabs.addTab(log_tab, "")

        # ── Status bar ────────────────────────────────────────
        sb_frame = QFrame(); sb_frame.setObjectName("statusbar")
        sb_l = QHBoxLayout(sb_frame)
        sb_l.setContentsMargins(12, 0, 12, 0); sb_l.setSpacing(8)

        def _sb_lbl(text, color="#3d4458", url=None):
            lbl = QLabel()
            lbl.setStyleSheet(f"color: {color}; font-size: 11px; background: transparent;")
            if url:
                lbl.setOpenExternalLinks(True)
                lbl.setText(f'<a href="{url}" style="color:{color};text-decoration:none;">{text}</a>')
            else:
                lbl.setText(text)
            return lbl

        sb_l.addWidget(_sb_lbl(f"ModSync Client  v{APP_VERSION}"))
        sb_l.addWidget(_sb_lbl("  ·  ", "#2a2d3a"))
        sb_l.addWidget(_sb_lbl("by SkyLett & AI", "#4a5060"))
        sb_l.addWidget(_sb_lbl("  ·  ", "#2a2d3a"))
        sb_l.addWidget(_sb_lbl("bar7dtd.ru", "#4a7060", "http://bar7dtd.ru"))
        sb_l.addWidget(_sb_lbl("  ·  ", "#2a2d3a"))
        sb_l.addWidget(_sb_lbl("Discord", "#504870", "https://discord.gg/B3zN2h7Ukf"))
        sb_l.addWidget(_sb_lbl("  ·  ", "#2a2d3a"))
        self._gh_status_lbl = _sb_lbl("GitHub: …", "#3d5060")
        sb_l.addWidget(self._gh_status_lbl)
        sb_l.addStretch(1)

        self.open_temp_btn = QPushButton()
        self.open_temp_btn.setObjectName("sb_btn")
        self.open_temp_btn.setFixedWidth(70)
        self.open_temp_btn.setToolTip("Открыть папку модов")
        self.open_temp_btn.clicked.connect(self._open_mods_folder)
        sb_l.addWidget(self.open_temp_btn)

        self.lang_btn = QPushButton()
        self.lang_btn.setObjectName("sb_btn")
        self.lang_btn.setFixedWidth(50)
        self.lang_btn.setToolTip("Switch language / Сменить язык")
        self.lang_btn.clicked.connect(self.on_toggle_lang)
        sb_l.addWidget(self.lang_btn)

        # Hidden debug buttons (not in layout)
        self.test_toast_btn = QPushButton("Test toast")
        self.test_toast_btn.clicked.connect(lambda: self.show_toast(self.tr("toast_title"), self.tr("toast_test")))
        self.test_toast_btn.setVisible(False)
        self.reset_rules_btn = QPushButton("Reset rules")
        self.reset_rules_btn.clicked.connect(self.on_reset_rules)
        self.reset_rules_btn.setVisible(False)

        # ── Root layout ───────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 0); root.setSpacing(8)
        root.addLayout(top_row)
        root.addWidget(card_local)
        root.addWidget(card_actions)
        root.addWidget(tab_card, 1)
        root.addWidget(sb_frame)

        # ── Start background tasks ─────────────────────────
        threading.Thread(target=self._fetch_server_list, daemon=True).start()
        self._gh_status_signal.connect(self._apply_gh_status)
        self._p2p_status_signal.connect(self._apply_p2p_status)
        threading.Thread(target=self._update_check_bg, daemon=True).start()

        # timers
        self.game_timer = QTimer(self)
        self.game_timer.setInterval(10000)
        self.game_timer.timeout.connect(self.refresh_game_status)
        self.game_timer.start()

        self.autocheck_timer = QTimer(self)
        self.autocheck_timer.timeout.connect(self.on_autocheck_tick)

        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(60_000)
        self._heartbeat_timer.timeout.connect(self._send_heartbeat)
        self._heartbeat_timer.start()

        self.refresh_game_status()

        # first-run rules: show if not accepted
        if not bool(self.cfg.get("accepted_rules", False)):
            ok = self.show_rules_dialog(force=True)
            if not ok:
                sys.exit(0)
            self.cfg["accepted_rules"] = True
            write_config(self.cfg)

        # auto-find game if needed
        if not self.game_edit.text().strip() or not Path(self.game_edit.text().strip()).exists():
            self.auto_find_game()

        # start auto-check timer
        self.apply_autocheck_settings()

        self.append_log(f"[APP] Config: {CONFIG_PATH}")

        # Apply translations (handles initial lang from config)
        self.retranslate_ui()

        # Start seed engine on launch if share was already enabled
        if bool(self.cfg.get("p2p_share", True)):
            QTimer.singleShot(3000, self._try_start_seed_engine)

    # ---------------- Rules ----------------

    def show_rules_dialog(self, force: bool = False) -> bool:
        if not force and bool(self.cfg.get("accepted_rules", False)):
            return True
        text = self.tr("rules_text")
        r = QMessageBox.question(self, self.tr("rules_title"), text,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return r == QMessageBox.StandardButton.Yes

    def on_table_double_click(self, row: int, col: int):
        """Open mod folder in Explorer on double-click."""
        item = self.compare_table.item(row, 0)
        if not item:
            return
        mod_id = item.text()
        mods_dir = self.cfg.get("mods_dir", "")
        if not mods_dir:
            return
        mod_path = Path(mods_dir) / mod_id
        if mod_path.exists():
            subprocess.Popen(f'explorer "{mod_path}"')
        else:
            self.append_log(f"[INFO] Folder not found: {mod_path}")

    def on_reset_rules(self):
        self.cfg["accepted_rules"] = False
        write_config(self.cfg)
        self.append_log("[CFG] accepted_rules reset (restart app to see rules on start)")

    # ---------------- Language ----------------

    def on_toggle_lang(self):
        self._lang = "ru" if self._lang == "en" else "en"
        self.cfg["lang"] = self._lang
        write_config(self.cfg)
        self.retranslate_ui()

    def retranslate_ui(self):
        """Update all UI text to current language without rebuilding widgets."""
        t = self.tr
        # Window & tray
        self.setWindowTitle(t("window_title"))
        self.tray.setToolTip(t("tray_tooltip"))
        # Card titles
        self._conn_title_lbl.setText(t("grp_connection").upper())
        self._local_title_lbl.setText(t("grp_local").upper())
        self._sync_title_lbl.setText(t("grp_sync").upper())
        self._log_title_lbl.setText(t("lbl_log_title").upper())
        # Row labels
        self.lbl_server_url.setText(t("lbl_server_url"))
        self.lbl_steamid.setText(t("lbl_steamid"))
        self.lbl_game_exe.setText(t("lbl_game_exe"))
        self.lbl_mods_dir.setText(t("lbl_mods_dir"))
        self.lbl_autocheck.setText(t("lbl_autocheck"))
        self.lbl_automode.setText(t("lbl_automode"))
        self.lbl_p2p.setText(t("lbl_p2p_share"))
        self.lbl_compare.setText(t("lbl_mod_comparison").upper())
        # Tip labels
        self._automode_tip.setText(t("lbl_automode_tip"))
        self._p2p_tip.setText(t("lbl_p2p_tip"))
        # Placeholders
        self.server_edit.setPlaceholderText(t("ph_server_url"))
        self.steam_edit.setPlaceholderText(t("ph_steamid"))
        # Buttons
        self.refresh_servers_btn.setText(t("btn_refresh_srv"))
        self.manual_server_btn.setText(t("btn_manual_srv"))
        self.ping_btn.setText(t("btn_ping"))
        self.save_btn.setText(t("btn_save"))
        self.test_toast_btn.setText(t("btn_test_toast"))
        self.steam_login_btn.setText(t("btn_steam_login"))
        self.steam_change_btn.setText(t("btn_steam_change"))
        self.find_game_btn.setText(t("btn_find_game"))
        self.browse_mods_btn.setText(t("btn_browse"))
        self.rules_btn.setText(t("btn_show_rules"))
        self.reset_rules_btn.setText(t("btn_reset_rules"))
        self.check_btn.setText(t("btn_check"))
        self.download_btn.setText(t("btn_download"))
        self.fix_btn.setText(t("btn_fix"))
        self.open_temp_btn.setText(t("btn_appdata"))
        self.lang_btn.setText(t("btn_lang"))
        # Tabs
        self.main_tabs.setTabText(0, t("tab_mods"))
        self.main_tabs.setTabText(1, t("tab_log"))
        # Status / game labels
        running = is_game_running()
        self.game_status_lbl.setText(t("lbl_game_running") if running else t("lbl_game_offline"))
        if not self.status_lbl.text() or self.status_lbl.text() in ("ожидание", "idle"):
            self.status_lbl.setText(t("lbl_status_idle"))
        if not self.v2_engine or not self.v2_engine.handle:
            self._p2p_info_lbl.setText(t("st_p2p_idle"))
        # Table headers
        self.compare_table.setHorizontalHeaderLabels([
            t("th_mod"), t("th_server_hash"), t("th_local_hash"), t("th_status"), t("th_action")
        ])

    # ---------------- Steam Auth ----------------

    def on_steamid_ready(self, steamid: str):
        steamid = (steamid or "").strip()
        if not steamid:
            return
        self.steam_edit.setText(steamid)
        self.steam_edit.setReadOnly(True)
        self.steam_login_btn.setEnabled(False)
        self.cfg["steamid"] = steamid
        write_config(self.cfg)
        self.append_log(f"[AUTH] SteamID set: {steamid}")
        self.show_toast(self.tr("toast_title"), self.tr("toast_steam_ok"))

    def on_change_account(self):
        self.cfg["steamid"] = ""
        write_config(self.cfg)
        self.steam_edit.setReadOnly(False)
        self.steam_edit.setText("")
        self.steam_login_btn.setEnabled(True)
        self.append_log("[AUTH] SteamID cleared. Login again if needed.")
        self.show_toast(self.tr("toast_title"), self.tr("toast_steam_cleared"))

    def on_steam_login(self):
        port = _pick_free_local_port()
        return_to = f"http://127.0.0.1:{port}/steam/callback"
        realm = f"http://127.0.0.1:{port}"
        auth_url = _build_steam_openid_url(return_to=return_to, realm=realm)

        self.append_log(f"[AUTH] Opening Steam login in browser...")
        done_flag = {"done": False}
        result = {"steamid": "", "error": ""}
        window = self

        class SteamCallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return  # suppress server logs

            def do_GET(self):
                try:
                    parsed = urllib.parse.urlparse(self.path)
                    if parsed.path != "/steam/callback":
                        self.send_response(404)
                        self.end_headers()
                        return

                    qs = urllib.parse.parse_qs(parsed.query)
                    qp = {k: (v[0] if isinstance(v, list) and v else "") for k, v in qs.items()}

                    if not _verify_steam_openid_response(qp):
                        result["error"] = "Steam OpenID verification failed"
                        html = "<html><body><h3>ModSync</h3><p>Verification failed. Close this tab.</p></body></html>"
                    else:
                        claimed = qp.get("openid.claimed_id", "")
                        steamid = _extract_steamid_from_claimed_id(claimed)
                        if not steamid:
                            result["error"] = "SteamID not found in response"
                            html = "<html><body><h3>ModSync</h3><p>SteamID not found. Close this tab.</p></body></html>"
                        else:
                            result["steamid"] = steamid
                            html = (
                                f"<html><body style='font-family:monospace;background:#111;color:#d4c5a0;padding:40px'>"
                                f"<h2 style='color:#FF4500'>☣ ModSync</h2>"
                                f"<p style='color:#6a8a50'>Steam login successful ✅</p>"
                                f"<p>SteamID: <b style='color:#FF8C00'>{steamid}</b></p>"
                                f"<p>You can close this tab.</p></body></html>"
                            )

                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(html.encode("utf-8"))

                except Exception as e:
                    result["error"] = str(e)
                    try:
                        self.send_response(500)
                        self.end_headers()
                    except Exception:
                        pass
                finally:
                    done_flag["done"] = True

        httpd = ThreadingHTTPServer(("127.0.0.1", port), SteamCallbackHandler)

        def server_thread():
            deadline = time.time() + 180  # 3 min timeout
            while time.time() < deadline and not done_flag["done"]:
                httpd.handle_request()
            httpd.server_close()
            if result["steamid"]:
                window.bridge.steamid_ready.emit(result["steamid"])
            else:
                err = result["error"] or "Login timeout (3 min)"
                window.bridge.error.emit(f"Steam login failed: {err}")

        threading.Thread(target=server_thread, daemon=True).start()
        webbrowser.open(auth_url)
        self.show_toast(self.tr("toast_title"), self.tr("toast_steam_opening"))

    # ---------------- Toast ----------------

    def show_toast(self, title: str, message: str):
        # Deduplicate identical toasts within 3 seconds
        now = time.time()
        last_msg, last_ts = self._last_toast
        key = f"{title}||{message}"
        if key == last_msg and (now - last_ts) < 3.0:
            return
        self._last_toast = (key, now)
        self.overlay_toast.show_toast(title, message, duration_ms=8000)

    # ---------------- UI helpers ----------------

    _LOG_COLORS = {
        "APP":    "#8b949e",
        "AUTH":   "#cba6f7",
        "UPnP":   "#89b4fa",
        "V2":     "#a6e3a1",
        "MS":     "#89dceb",
        "SCAN":   "#f9e2af",
        "AUTO":   "#f9e2af",
        "MOVE":   "#fb923c",
        "CFG":    "#8b949e",
        "INFO":   "#8b949e",
        "ERROR":  "#f38ba8",
        "WARN":   "#fab387",
    }
    _LOG_DEFAULT_COLOR = "#cdd6f4"

    def append_log(self, msg: str):
        import html as _html, re as _re
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)  # goes to client.log via Tee
        color = self._LOG_DEFAULT_COLOR
        m = _re.match(r"\[([A-Za-z0-9_\-]+)\]", msg)
        if m:
            tag = m.group(1).upper()
            color = self._LOG_COLORS.get(tag, self._LOG_DEFAULT_COLOR)
        if "error" in msg.lower() or "❌" in msg:
            color = self._LOG_COLORS["ERROR"]
        elif "warn" in msg.lower() or "⚠" in msg:
            color = self._LOG_COLORS["WARN"]
        ts_html = f'<span style="color:#4a5568">[{ts}]</span>'
        msg_html = f'<span style="color:{color}">{_html.escape(msg)}</span>'
        self.log_view.append(f'{ts_html} {msg_html}')

    def set_status(self, msg: str):
        self.status_lbl.setText(msg)

    def set_busy(self, busy: bool):
        self._is_busy = busy
        # During operation — lock everything
        lock_all = [self.ping_btn, self.save_btn, self.find_game_btn,
                    self.check_btn, self.download_btn, self.fix_btn]
        if busy:
            for w in lock_all:
                w.setEnabled(False)
        self.progress_bar.setVisible(busy)
        self.progress_pct_lbl.setVisible(busy)
        self.speed_lbl.setVisible(busy)
        if not busy:
            self.progress_bar.setValue(0)
            self.progress_pct_lbl.setText("")
            self.speed_lbl.setText("")
            self.update_action_buttons()

    def on_download_progress(self, pct: int, speed_mbs: float):
        self.progress_bar.setValue(pct)
        self.progress_pct_lbl.setText(f"{pct}%")
        if speed_mbs >= 1.0:
            self.speed_lbl.setText(f"{speed_mbs:.1f} MB/s")
        else:
            self.speed_lbl.setText(f"{speed_mbs * 1024:.0f} KB/s")

    def on_table_double_click(self, row: int, col: int):
        item = self.compare_table.item(row, 0)
        if not item:
            return
        mod_name = item.text().strip()
        mods_dir = self.cfg.get("mods_dir", "").strip()
        if not mods_dir or not mod_name:
            return
        mod_path = Path(mods_dir) / mod_name
        if mod_path.exists():
            subprocess.Popen(f'explorer "{mod_path}"')
        else:
            QMessageBox.information(self, "Mod folder",
                f"Folder not found:\n{mod_path}")

    def on_error(self, msg: str):
        self.append_log(f"[ERROR] {msg}")
        QMessageBox.warning(self, self.tr("dlg_error_title"), msg)
        self.set_status("error")

    def update_action_buttons(self):
        """
        State machine for action buttons:
          check_btn  — always available after cooldown (re-check resets pipeline)
          download   — available if diff has updates and no bundle yet
          apply      — available if bundle exists and game not running
          fix        — available if extras exist and game not running
        """
        busy = getattr(self, "_is_busy", False)
        if busy:
            return

        has_diff      = self.last_diff is not None
        has_download  = has_diff and len(self.last_diff.download) > 0
        has_delete    = has_diff and len(self.last_diff.delete) > 0
        game_running  = is_game_running()

        self.check_btn.setEnabled(True)
        self.download_btn.setEnabled(
            has_download and not game_running and not self.v2_updating)
        self.fix_btn.setEnabled(has_delete and not game_running)

    def _reset_pipeline(self):
        self.last_diff = None
        self.update_action_buttons()

    def on_toggle_automode(self):
        self.cfg["auto_mode"] = bool(self.automode_btn.isChecked())
        write_config(self.cfg)
        self.append_log(f"[AUTO] auto_mode={'ON' if self.cfg['auto_mode'] else 'OFF'}")
        if self.cfg["auto_mode"]:
            self.show_toast("ModSync", self.tr("toast_automode_on"))
        else:
            self.show_toast("ModSync", self.tr("toast_automode_off"))
            
    def on_tray_activated(self, reason):
        # ЛКМ/двойной клик по иконке — показать/спрятать
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible():
                self.hide()
            else:
                self.show_window_from_tray()


    def show_window_from_tray(self):
        # корректно вернуть окно
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        """
        Вместо закрытия — прячем в трей.
        Выход только через Quit в меню трея.
        """
        # Если трея нет (очень редкий случай) — тогда закрываем
        if not hasattr(self, "tray") or self.tray is None:
            event.accept()
            return

        event.ignore()
        self.hide()

        # Можно показать подсказку один раз
        if not self.cfg.get("tray_hint_shown", False):
            self.cfg["tray_hint_shown"] = True
            write_config(self.cfg)
            self.show_toast("ModSync", "Свернуто в трей. Выход — через Quit.")

    # ---------------- Game path ----------------

    def auto_find_game(self):
        """Search for 7DaysToDie.exe across common Steam install locations."""
        # Standard Steam on C:
        candidates = [
            Path(r"C:\Program Files (x86)\Steam\steamapps\common\7 Days to Die\7DaysToDie.exe"),
            Path(r"C:\Program Files\Steam\steamapps\common\7 Days to Die\7DaysToDie.exe"),
        ]
        # D, E, F drives — default steamapps + SteamLibrary subfolder
        for drive in ["D", "E", "F"]:
            candidates += [
                Path(f"{drive}:\\Steam\\steamapps\\common\\7 Days to Die\\7DaysToDie.exe"),
                Path(f"{drive}:\\SteamLibrary\\steamapps\\common\\7 Days to Die\\7DaysToDie.exe"),
                Path(f"{drive}:\\Games\\Steam\\steamapps\\common\\7 Days to Die\\7DaysToDie.exe"),
                Path(f"{drive}:\\Games\\SteamLibrary\\steamapps\\common\\7 Days to Die\\7DaysToDie.exe"),
                Path(f"{drive}:\\steamapps\\common\\7 Days to Die\\7DaysToDie.exe"),
            ]
        for p in candidates:
            if p.exists():
                self.set_game_path(p)
                self.append_log(f"[GAME] Auto-detected: {p}")
                return
        # Nothing found — open file dialog
        self.on_find_game()

    def on_find_game(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Select game EXE", "", "Executable (*.exe)")
        if not fn:
            return
        self.set_game_path(Path(fn))

    def set_game_path(self, exe_path: Path):
        self.game_edit.setText(str(exe_path))
        mods_dir = exe_path.parent / "Mods"
        self.mods_edit.setText(str(mods_dir))

        self.cfg["game_exe"] = str(exe_path)
        self.cfg["mods_dir"] = str(mods_dir)
        write_config(self.cfg)

        self.append_log(f"[GAME] exe={exe_path}")
        self.append_log(f"[GAME] mods={mods_dir}")

    def on_browse_mods(self):
        """Выбор папки Mods вручную (п.4)."""
        start = self.cfg.get("mods_dir", "") or self.cfg.get("game_exe", "")
        d = QFileDialog.getExistingDirectory(self, "Выбрать папку Mods", start)
        if not d:
            return
        self.mods_edit.setText(d)
        self.cfg["mods_dir"] = d
        write_config(self.cfg)
        self.append_log(f"[GAME] mods={d}")

    def _on_toggle_manual_server(self):
        """Переключение между списком серверов и ручным вводом."""
        manual = self.manual_server_btn.isChecked()
        self.server_combo.setVisible(not manual)
        self.refresh_servers_btn.setVisible(not manual)
        self.server_edit.setVisible(manual)
        self.ping_btn.setVisible(manual)
        self.save_btn.setVisible(manual)
        if manual and not self.server_edit.text().strip():
            # предзаполнить из текущего выбора combo
            url = self.server_combo.currentData() or ""
            self.server_edit.setText(url)

    def _on_server_combo_changed(self, idx: int):
        """При смене сервера в combo — сохраняем URL в config."""
        url = self.server_combo.itemData(idx)
        if url:
            self.cfg["server_url"] = url
            write_config(self.cfg)

    def _fetch_server_list(self):
        """Загружает список серверов с мастер-сервера (вызывается в фоне)."""
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(
                MASTER_SERVER_URL + "/ms/servers",
                headers={"User-Agent": "ModSync-Client/2.0"})
            with opener.open(req, timeout=8) as resp:
                raw = json.loads(resp.read())
            # API может вернуть список напрямую или {"servers": [...]}
            if isinstance(raw, list):
                servers = raw
            elif isinstance(raw, dict):
                servers = raw.get("servers", [])
            else:
                servers = []
            self.bridge.log.emit(f"[MS] Got {len(servers)} servers from master")
            self._ms_servers_signal.emit(servers)
        except Exception as e:
            self.bridge.log.emit(f"[MS] Server list fetch failed: {e}")
            self._ms_servers_signal.emit([])

    def _populate_server_combo(self, servers: list):
        """Заполняет combo списком серверов (вызывается в главном потоке)."""
        self.server_combo.blockSignals(True)
        self.server_combo.clear()
        saved_url = self.cfg.get("server_url", "").strip()
        best_idx = 0
        for i, s in enumerate(servers):
            host = s.get("host", "")
            port = s.get("port", 8765)
            url = f"http://{host}:{port}"
            name = s.get("server_name") or host
            online = s.get("online", 0)
            label = f"{'● ' if online else '○ '}{name}  [{host}:{port}]"
            self.server_combo.addItem(label, url)
            if url == saved_url:
                best_idx = i
        if not servers:
            self.server_combo.addItem("Нет серверов — введите вручную", "")
        self.server_combo.blockSignals(False)
        self.server_combo.setCurrentIndex(best_idx)
        # Обновляем server_url: точное совпадение ИЛИ совпадение по хосту (порт мог смениться)
        if servers:
            url = self.server_combo.itemData(best_idx)
            if url:
                self.cfg["server_url"] = url
                write_config(self.cfg)
                # Автозапуск проверки обновлений при первом получении списка серверов
                if not getattr(self, "_startup_check_done", False):
                    self._startup_check_done = True
                    QTimer.singleShot(500, lambda: self.bridge.start_check.emit("auto"))

    # ---------------- Status timers ----------------

    def refresh_game_status(self):
        running = is_game_running()
        self.game_status_lbl.setText(self.tr("lbl_game_running") if running else self.tr("lbl_game_offline"))
        self.update_action_buttons()
        # если игра закрылась и есть pending update -> запускаем пайплайн
        if (not running) and bool(self.cfg.get("auto_mode", False)) and bool(self.cfg.get("pending_update", False)):
            pending_hash = str(self.cfg.get("pending_update_hash", "")).strip()
            if pending_hash:
                self.append_log(f"[AUTO] game closed -> resume pending update {pending_hash[:8]}")
                # чтобы не стартовать много раз таймером
                self.cfg["pending_update"] = False
                write_config(self.cfg)
                self._start_check("auto")
                
    # ---------------- Config ----------------

    def on_save(self):
        if self.server_edit.isVisible():
            # ручной режим — берём из поля ввода
            self.cfg["server_url"] = self.server_edit.text().strip()
        self.cfg["steamid"] = self.steam_edit.text().strip()

        self.cfg["check_interval_min"] = int(self.autocheck_combo.currentData())
        self.cfg["auto_check_enabled"] = bool(self.autocheck_btn.isChecked())

        write_config(self.cfg)
        self.append_log("[CFG] saved")

    # ---------------- Auto-check ----------------

    def on_toggle_autocheck(self):
        self.on_save()
        self.apply_autocheck_settings()

    def apply_autocheck_settings(self):
        enabled = bool(self.cfg.get("auto_check_enabled", True))
        interval_min = int(self.cfg.get("check_interval_min", DEFAULT_CHECK_INTERVAL_MIN))
        if interval_min not in CHECK_INTERVAL_OPTIONS_MIN:
            interval_min = DEFAULT_CHECK_INTERVAL_MIN

        if enabled:
            self.autocheck_timer.start(interval_min * 60 * 1000)
            self.append_log(f"[AUTO] enabled, interval={interval_min} min")
        else:
            self.autocheck_timer.stop()
            self.append_log("[AUTO] disabled")

    def on_autocheck_tick(self):
        if getattr(self, "v2_updating", False):
            return
        self.on_save()
        if not self.cfg.get("server_url") or not self.cfg.get("steamid"):
            return

        def worker():
            try:
                base = self._resolve_base(self.cfg["server_url"]).rstrip("/")
                data = modsync_v2.fetch_json(base + "/api/v2/build")
                if not data.get("ok") or not data.get("build_id"):
                    return
                new_hash = str(data.get("build_id", "")).strip()
                old_hash = str(self.cfg.get("last_server_manifest_hash", "")).strip()

                if new_hash and new_hash != old_hash:
                    auto_mode = bool(self.cfg.get("auto_mode", False))

                    if auto_mode and is_game_running():
                        self.cfg["pending_update"] = True
                        self.cfg["pending_update_hash"] = new_hash
                        write_config(self.cfg)
                        self.bridge.log.emit(f"[AUTO] pending update (game running): {new_hash[:8]}")
                        self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_auto_pending"))
                        return

                    if not auto_mode:
                        self.cfg["last_seen_server_manifest_hash"] = new_hash
                        write_config(self.cfg)
                        self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_auto_available"))
                        return

                    self.cfg["pending_update"] = True
                    self.cfg["pending_update_hash"] = new_hash
                    write_config(self.cfg)
                    self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_auto_update"))
                    self.bridge.log.emit("[AUTO] Starting full update pipeline")
                    self.bridge.start_check.emit("auto")
                else:
                    self.bridge.log.emit("[AUTO] no changes")
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Auto disable ----------------

    def on_fix_extras(self):
        if getattr(self, "fixing_extras", False):
            self.append_log("[AUTO] Extras fix already running -> skip")
            return
        self.fixing_extras = True
        if is_game_running():
            self.on_error(self.tr("err_game_running_fix"))
            return
        if not self.last_diff or not self.last_diff.delete:
            return
        if not self.v2_manifest:
            self.on_error("Нет данных сборки — выполни проверку")
            return

        mods_dir = self.cfg.get("mods_dir", "").strip()
        if not mods_dir:
            self.on_error(self.tr("err_missing_server_url"))
            return

        def worker():
            self.bridge.set_busy.emit(True)
            try:
                mods_root = Path(mods_dir)
                server_ids = set(m.get("id") for m in self.server_mods if isinstance(m, dict) and m.get("id"))

                for mod_id in self.last_diff.delete:
                    p = mods_root / str(mod_id)
                    if p.exists():
                        move_to_disabled(mods_root, p, "server_removed", self.bridge.log.emit)

                strict_cleanup_to_disabled(mods_root, server_ids, self.bridge.log.emit)

                qd = modsync_v2.quick_local_diff(self.v2_manifest, mods_root)
                from hashlib import sha256 as _sha
                mod_fp = {}
                for mod in self.v2_manifest.get("mods", []):
                    fp = _sha("".join(
                        f["root"] for f in sorted(mod.get("files", []),
                                                  key=lambda x: x["path"].casefold())
                    ).encode()).hexdigest()
                    mod_fp[mod["name"]] = fp

                bad_mods = set(qd["by_mod_missing"]) | set(qd["by_mod_changed"])
                orphan_tops = sorted({p.split("/", 1)[0] for p in qd["orphans"]
                                      if p.split("/", 1)[0] not in mod_fp})
                build_id = (self.v2_build or {}).get("build_id", "")
                diff2 = {
                    "ok": True,
                    "server_manifest_hash": build_id,
                    "download": [{"id": m} for m in sorted(bad_mods, key=str.casefold)],
                    "delete": orphan_tops,
                    "_context": "internal",
                }
                self.bridge.diff_ready.emit(diff2)

                if not bad_mods and not orphan_tops:
                    self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_extras_moved"))
            except Exception as e:
                self.bridge.error.emit(str(e))
            finally:
                self.fixing_extras = False
                self.bridge.set_busy.emit(False)

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Network actions ----------------

    def _resolve_base(self, base: str) -> str:
        """
        NAT hairpin fix: tries localhost, then LAN discovery cache before using the URL.
        Handles both same-machine and same-LAN scenarios without hitting the external IP.
        """
        try:
            parsed = urllib.parse.urlparse(base)
            host = parsed.hostname or ""
            port = parsed.port
            if not port:
                return base
            if host in ("127.0.0.1", "localhost", "::1"):
                return base
            # 1) Same-machine probe (timeout 0.5 s)
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    pass
                new = parsed._replace(netloc=f"127.0.0.1:{port}")
                return urllib.parse.urlunparse(new)
            except OSError:
                pass
            # 2) LAN discovery (disabled — reserved for future)
            pass
        except Exception:
            pass
        return base

    def on_ping(self):
        now = time.time()
        if now - self._ping_last < 1.0:
            return
        self._ping_last = now
        self.on_save()
        base = self.cfg.get("server_url", "").strip()
        if not base:
            self.on_error(self.tr("err_server_empty"))
            return

        def worker():
            self.bridge.set_busy.emit(True)
            self.bridge.status.emit(self.tr("st_ping"))
            try:
                resolved = self._resolve_base(base)
                data = modsync_v2.fetch_json(resolved.rstrip("/") + "/api/v2/info")
                self.bridge.log.emit(f"[PING] {data}")
                self.bridge.status.emit(self.tr("st_ping_ok"))
            except Exception as e:
                self.bridge.error.emit(str(e))
            finally:
                self.fixing_extras = False
                self.bridge.set_busy.emit(False)

        threading.Thread(target=worker, daemon=True).start()

    def _start_check(self, context: str):
        """Run check from GUI thread with context ('manual' or 'auto')."""
        if context not in ("manual", "auto"):
            context = "manual"

        # Guard: prevent duplicated auto pipelines (can be triggered by autocheck + game-closed resume)
        if context == "auto":
            now = time.time()
            if self._auto_pipeline_running and (now - self._auto_last_start_ts) < 30:
                self.append_log("[AUTO] pipeline already running -> skip start_check")
                return
            if (now - self._auto_last_start_ts) < 2.0:
                # spam protection (two triggers same second)
                self.append_log("[AUTO] start_check throttled")
                return

            self._auto_pipeline_running = True
            self._auto_last_start_ts = now
            self._auto_download_started = False
            # take target hash from pending, if any
            self._auto_target_hash = str(self.cfg.get("pending_update_hash", "")).strip()

            # if we start pipeline, clear pending flag so refresh_game_status won't trigger again
            if bool(self.cfg.get("pending_update", False)):
                self.cfg["pending_update"] = False
                write_config(self.cfg)

        self._next_check_context = context
        self.on_check_updates_full()

    def on_check_updates_full(self):
        """
        Полная проверка:
        - GET /mods -> server mods + hash
        - локальные хэши считаем один раз на проверку
        """
        context = getattr(self, "_next_check_context", "manual")
        self._next_check_context = "manual"

        if context == "manual":
            self._check_cooldown = time.time()
            # Reset pipeline so check button is locked and download becomes available
            self.last_diff = None

        self.on_save()

        base = self.cfg.get("server_url", "").strip()
        steamid = self.cfg.get("steamid", "").strip()
        mods_dir = self.cfg.get("mods_dir", "").strip()

        if not base:
            self.on_error(self.tr("err_server_empty"))
            return
        if not steamid:
            self.on_error(self.tr("err_steamid_empty"))
            return
        if not mods_dir:
            self.on_error(self.tr("err_mods_dir_empty"))
            return

        def worker():
            self.bridge.set_busy.emit(True)
            try:
                b = self._resolve_base(base).rstrip("/")
                sid_q = f"?steamid={steamid}" if steamid else ""

                self.bridge.status.emit(self.tr("st_get_build"))
                build = modsync_v2.fetch_json(b + "/api/v2/build")
                if not build.get("ok") or not build.get("build_id"):
                    raise RuntimeError("Сервер не имеет опубликованной сборки")
                self.v2_build = build

                self.bridge.status.emit(self.tr("st_get_manifest"))
                manifest = json.loads(
                    modsync_v2.fetch_bytes(b + "/api/v2/manifest" + sid_q).decode("utf-8"))
                self.v2_manifest = manifest

                self.bridge.status.emit(self.tr("st_scan_files"))
                mods_root = Path(mods_dir)
                qd = modsync_v2.quick_local_diff(manifest, mods_root)

                # ── адаптация под существующий UI (servermods + diff) ──
                # per-mod fingerprint из манифеста (по root-хешам файлов)
                from hashlib import sha256 as _sha
                mod_fp = {}
                for mod in manifest.get("mods", []):
                    fp = _sha("".join(
                        f["root"] for f in sorted(mod.get("files", []),
                                                  key=lambda x: x["path"].casefold())
                    ).encode()).hexdigest()
                    mod_fp[mod["name"]] = fp

                server_mods = [{"id": name, "hash": fp, "size_bytes": 0,
                                "files_count": 0} for name, fp in mod_fp.items()]
                self.bridge.servermods_ready.emit({
                    "ok": True, "mods": server_mods,
                    "server_manifest_hash": build["build_id"],
                })

                bad_mods = set(qd["by_mod_missing"]) | set(qd["by_mod_changed"])
                local_tops = set()
                if mods_root.is_dir():
                    for e in mods_root.iterdir():
                        if e.is_dir() and e.name.lower() != "disabled_mods":
                            local_tops.add(e.name)
                # local_map: совпавший fp = "как на сервере", иначе маркер
                local_map = {}
                for top in local_tops:
                    if top in mod_fp:
                        local_map[top] = mod_fp[top] if top not in bad_mods else "local-differs"
                    else:
                        local_map[top] = "extra"
                self.local_mods_map = local_map

                orphan_tops = sorted({p.split("/", 1)[0] for p in qd["orphans"]
                                      if p.split("/", 1)[0] not in mod_fp})

                diff = {
                    "ok": True,
                    "server_manifest_hash": build["build_id"],
                    "download": [{"id": m} for m in sorted(bad_mods, key=str.casefold)],
                    "delete": orphan_tops,
                    "_context": context,
                    "_v2_files": {"missing": len(qd["missing"]),
                                  "changed": len(qd["changed"])},
                }
                self.bridge.log.emit(
                    f"[V2] build {build['build_id']}: файлов не хватает {len(qd['missing'])}, "
                    f"изменено {len(qd['changed'])}, лишних {len(qd['orphans'])}")
                self.bridge.diff_ready.emit(diff)
                if diff["download"] or diff["delete"]:
                    self.bridge.status.emit(self.tr("st_update_ready"))
                else:
                    self.bridge.status.emit(self.tr("st_up_to_date"))
                    # P2P seeding: engine must be created in main thread
                    if self.share_btn.isChecked() and self.v2_engine is None:
                        try:
                            torrent_bytes = modsync_v2.fetch_bytes(b + "/api/v2/torrent")
                            def _start_seed(tb=torrent_bytes, base_url=b, md=mods_dir):
                                try:
                                    if self.v2_engine is None:
                                        self.v2_engine = modsync_v2.ClientEngine(
                                            listen_port=0,
                                            log_cb=lambda m: self.bridge.log.emit(m))
                                    self.v2_engine.update(
                                        tb, Path(md), base_url, share=True)
                                    self.bridge.log.emit("[V2] P2P раздача запущена (моды актуальны)")
                                    self._v2_poll_timer.start()
                                except Exception as _e:
                                    self.bridge.log.emit(f"[V2] не удалось запустить раздачу: {_e}")
                            QTimer.singleShot(0, _start_seed)
                        except Exception as _e:
                            self.bridge.log.emit(f"[V2] не удалось получить торрент: {_e}")
            except HTTPError as e:
                self.bridge.error.emit(f"HTTP {e.code}: {e.reason}")
            except URLError as e:
                self.bridge.error.emit(f"URL error: {e.reason}")
            except Exception as e:
                self.bridge.error.emit(str(e))
            finally:
                self.fixing_extras = False
                self.bridge.set_busy.emit(False)

        threading.Thread(target=worker, daemon=True).start()

    def on_servermods_ready(self, data: dict):
        self.server_mods = data.get("mods", []) or []
        self.server_manifest_hash = str(data.get("server_manifest_hash", "")).strip()

        # запомним hash на будущее для автопроверки
        if self.server_manifest_hash:
            self.cfg["last_seen_server_manifest_hash"] = self.server_manifest_hash
            write_config(self.cfg)

    def on_diff_ready(self, diff: dict):
        server_hash = str(diff.get("server_manifest_hash", "")).strip()
        context = str(diff.get("_context", "manual"))
        download = diff.get("download", []) or []
        delete = diff.get("delete", []) or []

        self.last_diff = DiffResult(server_manifest_hash=server_hash, download=download, delete=delete)

        self.append_log(f"[DIFF] download={len(download)} delete={len(delete)} server_manifest_hash={server_hash}")

        # build comparison table
        server_map = {m["id"]: m for m in self.server_mods if isinstance(m, dict) and m.get("id")}
        local_map = dict(self.local_mods_map)

        server_ids = set(server_map.keys())
        local_ids = set(local_map.keys())

        # actions from diff
        dl_ids = set(str(x.get("id", "")).strip() for x in download if isinstance(x, dict))
        del_ids = set(str(x).strip() for x in delete)

        all_ids = sorted(list(server_ids | local_ids), key=lambda x: x.lower())
        self.compare_table.setRowCount(len(all_ids))

        for row, mid in enumerate(all_ids):
            sh = str(server_map.get(mid, {}).get("hash", "")) if mid in server_map else ""
            lh = str(local_map.get(mid, "")) if mid in local_map else ""

            if mid in server_ids and mid in local_ids:
                status_key = "rs_ok" if sh == lh else "rs_outdated"
            elif mid in server_ids and mid not in local_ids:
                status_key = "rs_missing"
            else:
                status_key = "rs_extra"

            if mid in dl_ids:
                action_key = "ra_update"
            elif mid in del_ids:
                action_key = "ra_move"
            else:
                action_key = "ra_none"

            self.compare_table.setItem(row, 0, QTableWidgetItem(mid))
            self.compare_table.setItem(row, 1, QTableWidgetItem(sh[:12] + "..." if len(sh) > 12 else sh))
            self.compare_table.setItem(row, 2, QTableWidgetItem(lh[:12] + "..." if len(lh) > 12 else lh))
            self.compare_table.setItem(row, 3, QTableWidgetItem(self.tr(status_key)))
            self.compare_table.setItem(row, 4, QTableWidgetItem(self.tr(action_key)))

            # color-code rows by status key (language-independent)
            from PyQt6.QtGui import QColor
            if status_key == "rs_ok":
                row_color = QColor("#0A1A08")
                text_color = QColor("#5A8A40")
            elif status_key == "rs_outdated":
                row_color = QColor("#1A0E00")
                text_color = QColor("#FF8C00")
            elif status_key == "rs_missing":
                row_color = QColor("#1A0500")
                text_color = QColor("#FF3000")
            else:  # rs_extra
                row_color = QColor("#0E0E1A")
                text_color = QColor("#7070CC")

            for col in range(5):
                item = self.compare_table.item(row, col)
                if item:
                    item.setBackground(row_color)
                    item.setForeground(text_color)

        # ---- Toasts / sounds (dedup + context) ----
        auto_mode = bool(self.cfg.get("auto_mode", False))

        # Toasts:
        if context != "internal":
            if len(download) > 0 or len(delete) > 0:
                self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_updates_available", dl=len(download), rm=len(delete)))
            elif context == "manual":
                self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_up_to_date"))

        # Auto-fix extras (only once per server hash, and not during internal diff refresh)
        if context != "internal":
            if self.last_diff and len(self.last_diff.download) == 0 and len(self.last_diff.delete) > 0:
                if not is_game_running():
                    if (not getattr(self, "fixing_extras", False)) and (server_hash != getattr(self, "_last_extras_fix_hash", "")):
                        self._last_extras_fix_hash = server_hash
                        self.append_log("[AUTO] Only extras found -> moving to disabled_mods automatically")
                        self.on_fix_extras()
                else:
                    self.append_log("[AUTO] Extras found but game is running -> fix postponed")
                    self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_extras_detected"))

        # AUTO MODE pipeline (dedup):
        if auto_mode and context == "auto":
            if is_game_running():
                return

            # if pipeline not marked running (e.g. manual toggle), don't auto-act
            if not getattr(self, "_auto_pipeline_running", False):
                return

            # set target hash if empty
            if not self._auto_target_hash:
                self._auto_target_hash = server_hash

            # ignore diffs not matching current target
            if self._auto_target_hash and server_hash and server_hash != self._auto_target_hash:
                return

            # only start download once
            if self.last_diff and len(self.last_diff.download) > 0 and not self._auto_download_started:
                self._auto_download_started = True
                self.append_log("[AUTO] Updates found -> auto download")
                self.on_download_bundle()
        self.update_action_buttons()


    def on_download_bundle(self):
        """V2: обновление одним действием — recheck по хешам + докачка дельты
        прямо в папку Mods. Требует закрытую игру (файлы меняются на месте)."""
        if not self.last_diff:
            return
        if is_game_running():
            self.on_error(self.tr("err_game_running_apply"))
            return
        if self.v2_updating:
            return
        if not self.v2_manifest or not self.v2_build:
            self.on_error("Нет данных сборки — выполни проверку")
            return

        self.on_save()
        base = self._resolve_base(self.cfg.get("server_url", "").strip()).rstrip("/")
        mods_dir = self.cfg.get("mods_dir", "").strip()
        if not base or not mods_dir:
            self.on_error(self.tr("err_server_empty"))
            return

        if self.v2_engine is None:
            try:
                self.v2_engine = modsync_v2.ClientEngine(
                    listen_port=0, log_cb=lambda m: self.bridge.log.emit(m))
            except Exception as e:
                self.on_error(str(e))
                return

        self.v2_updating = True
        self.update_action_buttons()
        engine = self.v2_engine

        def worker():
            self.bridge.set_busy.emit(True)
            self.bridge.status.emit(self.tr("st_get_torrent"))
            try:
                torrent_bytes = modsync_v2.fetch_bytes(base + "/api/v2/torrent")
                engine.set_share(self.share_btn.isChecked())
                engine.update(
                    torrent_bytes, Path(mods_dir), base,
                    share=self.share_btn.isChecked())
                self.bridge.status.emit(self.tr("st_checking"))
                # дальше — _v2_poll_timer в GUI-потоке
            except Exception as e:
                self.v2_updating = False
                self.bridge.error.emit(str(e))
                self.bridge.set_busy.emit(False)
                return
            # busy остаётся True до завершения (_v2_on_done снимет)

        threading.Thread(target=worker, daemon=True).start()
        self._v2_poll_timer.start()

    def _v2_poll(self):
        if self.v2_engine is None:
            return
        for msg in self.v2_engine.pump_alerts():
            self.append_log(msg)
        p = self.v2_engine.progress()
        if not p.get("active"):
            if self.v2_updating:
                return  # торрент ещё добавляется в worker-потоке
            self._v2_poll_timer.stop()
            return

        pct = int(p["progress"] * 100)
        speed_mbs = p["download_rate"] / (1024 * 1024)
        if self.v2_updating:
            state = p["state"]
            if state == "checking":
                self.bridge.status.emit(self.tr("st_verify", pct=pct))
            else:
                self.bridge.status.emit(self.tr("st_downloading",
                    pct=pct, spd=f"{speed_mbs:.1f}", peers=p["peers"]))
            self.bridge.download_progress.emit(pct, speed_mbs)
            if p.get("done"):
                self._v2_on_done()
        else:
            # режим сида после обновления
            up_mbs = p["upload_rate"] / (1024 * 1024)
            peers = p["peers"]
            if peers > 0 or up_mbs > 0.05:
                _ru = self._lang == "ru"
                _s = ("а" if 2 <= peers % 10 <= 4 else "ов" if peers % 10 != 1 else "") if _ru else ("s" if peers != 1 else "")
                self._p2p_info_lbl.setText(self.tr("st_p2p_seeding",
                    spd=f"{up_mbs:.1f}", peers=peers, s=_s))
                self._p2p_info_lbl.setStyleSheet(
                    "color: #4ade80; font-size: 12px; background: transparent; padding-left: 6px;")
            else:
                self._p2p_info_lbl.setText(self.tr("st_p2p_idle"))
                self._p2p_info_lbl.setStyleSheet(
                    "color: #5a6070; font-size: 12px; background: transparent; padding-left: 6px;")

    def _v2_on_done(self):
        self.v2_updating = False
        mods_dir = Path(self.cfg.get("mods_dir", "").strip())
        manifest = self.v2_manifest or {}
        build = self.v2_build or {}

        def worker():
            try:
                self.bridge.status.emit(self.tr("st_applying"))
                modsync_v2.finalize_build(manifest, mods_dir,
                                          log_cb=lambda m: self.bridge.log.emit(m))

                qd = modsync_v2.quick_local_diff(manifest, mods_dir)
                if qd["need_update"]:
                    self.bridge.log.emit(
                        f"[VERIFY] NOT OK: missing={len(qd['missing'])} "
                        f"changed={len(qd['changed'])}")
                    for p in (qd["missing"][:5] + qd["changed"][:5]):
                        self.bridge.log.emit(f"[VERIFY]   → {p}")
                    self.bridge.toast.emit(self.tr("toast_title"),
                                           self.tr("toast_verify_failed"))
                    self.bridge.status.emit(self.tr("st_verify_failed"))
                else:
                    bid = build.get("build_id", "")
                    self.bridge.log.emit("[VERIFY] OK: client is up-to-date")
                    self.bridge.toast.emit(self.tr("toast_title"),
                                           self.tr("toast_update_applied"))
                    self.bridge.status.emit(self.tr("st_up_to_date"))
                    self.cfg["last_server_manifest_hash"] = bid
                    self.cfg["pending_update"] = False
                    self.cfg["pending_update_hash"] = ""
                    write_config(self.cfg)

                # завершение auto-конвейера
                if getattr(self, "_auto_pipeline_running", False):
                    self._auto_pipeline_running = False
                    self._auto_target_hash = ""
                    self._auto_download_started = False

                # политика раздачи
                if self.v2_engine is not None:
                    self.v2_engine.on_completed_apply_share_policy()

                # пересчитать карту локальных модов — иначе таблица
                # покажет старые статусы (missing) по устаревшим данным
                from hashlib import sha256 as _sha
                mod_fp = {}
                for mod in manifest.get("mods", []):
                    fp = _sha("".join(
                        f["root"] for f in sorted(mod.get("files", []),
                                                  key=lambda x: x["path"].casefold())
                    ).encode()).hexdigest()
                    mod_fp[mod["name"]] = fp
                bad_mods = set(qd["by_mod_missing"]) | set(qd["by_mod_changed"])
                local_map = {}
                if mods_dir.is_dir():
                    for e in mods_dir.iterdir():
                        if not e.is_dir() or e.name.lower() == "disabled_mods":
                            continue
                        if e.name in mod_fp:
                            local_map[e.name] = (mod_fp[e.name]
                                                 if e.name not in bad_mods
                                                 else "local-differs")
                        else:
                            local_map[e.name] = "extra"
                self.local_mods_map = local_map

                # обновить таблицу «внутренним» диффом
                diff2 = {
                    "ok": True,
                    "server_manifest_hash": build.get("build_id", ""),
                    "download": [{"id": m} for m in sorted(bad_mods, key=str.casefold)],
                    "delete": [],
                    "_context": "internal",
                }
                self.bridge.diff_ready.emit(diff2)
            except Exception as e:
                self.bridge.error.emit(str(e))
            finally:
                self.bridge.set_busy.emit(False)

        threading.Thread(target=worker, daemon=True).start()

    def on_toggle_share(self):
        share = self.share_btn.isChecked()
        self.cfg["p2p_share"] = share
        write_config(self.cfg)
        if self.v2_engine is not None:
            self.v2_engine.set_share(share)
        self.append_log(f"[V2] Раздача: {'вкл' if share else 'выкл'}")
        if share and self.v2_engine is None:
            self._try_start_seed_engine()
        self._send_heartbeat()

    def _try_start_seed_engine(self):
        """Запускает движок раздачи сразу если моды актуальны (без нажатия Обновить)."""
        b = self.cfg.get("server_url", "").strip().rstrip("/")
        mods_dir = self.cfg.get("mods_dir", "").strip()
        if not b or not mods_dir:
            return
        def worker():
            try:
                torrent_bytes = modsync_v2.fetch_bytes(b + "/api/v2/torrent")
                def _start(tb=torrent_bytes, base_url=b, md=mods_dir):
                    try:
                        if self.v2_engine is None:
                            self.v2_engine = modsync_v2.ClientEngine(
                                listen_port=0,
                                log_cb=lambda m: self.bridge.log.emit(m))
                        self.v2_engine.update(tb, Path(md), base_url, share=True)
                        self.bridge.log.emit("[V2] P2P раздача запущена")
                        self._v2_poll_timer.start()
                    except Exception as e:
                        self.bridge.log.emit(f"[V2] ошибка старта раздачи: {e}")
                QTimer.singleShot(0, _start)
            except Exception as e:
                self.bridge.log.emit(f"[V2] не удалось получить торрент: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _send_heartbeat(self):
        if getattr(self, "v2_updating", False):
            return
        base = self.cfg.get("server_url", "").strip().rstrip("/")
        steamid = self.cfg.get("steamid", "").strip()
        if not base or not steamid:
            return
        p2p = bool(self.share_btn.isChecked()) if hasattr(self, "share_btn") else False
        bt_port = self.v2_engine.port if self.v2_engine is not None else 0
        def _post():
            try:
                import json as _json
                data = _json.dumps({"steamid": steamid, "p2p": p2p,
                                    "bt_port": bt_port}).encode()
                req = urllib.request.Request(
                    base + "/api/v2/heartbeat",
                    data=data, headers={"Content-Type": "application/json"})
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                opener.open(req, timeout=5)
            except Exception:
                pass
        threading.Thread(target=_post, daemon=True).start()

    def _open_mods_folder(self):
        path = self.cfg.get("mods_dir", "").strip() or str(APPDATA_DIR)
        try:
            os.startfile(path)
        except Exception:
            subprocess.Popen(["explorer", path])

    def _update_check_bg(self):
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            with urllib.request.urlopen(url, timeout=10) as r:
                import json as _json
                data = _json.loads(r.read())
            tag = data.get("tag_name", "").lstrip("v")
            if tag and tag > APP_VERSION:
                self._gh_status_signal.emit(self.tr("gh_update", v=tag))
            else:
                self._gh_status_signal.emit(self.tr("gh_uptodate"))
        except Exception:
            pass

    def _apply_p2p_status(self, text: str):
        self._p2p_info_lbl.setText(text)

    def _apply_gh_status(self, text: str):
        self._gh_status_lbl.setText(text)
        t = text.lower()
        if "обновление" in t or ("update" in t and "to date" not in t):
            self._gh_status_lbl.setStyleSheet(
                "color: #fbbf24; font-weight: 700; font-size: 11px; background: transparent;"
            )
        elif "up to date" in t or "актуально" in t:
            self._gh_status_lbl.setStyleSheet(
                "color: #4ade80; font-size: 11px; background: transparent;"
            )


def _setup_file_logging():
    """Redirect stdout/stderr and uncaught exceptions to a log file."""
    log_path = APPDATA_DIR / "client.log"
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)

    import datetime, faulthandler
    log_file.write(f"\n{'='*60}\n[STARTUP] {datetime.datetime.now()}\n{'='*60}\n")
    log_file.flush()
    faulthandler.enable(file=log_file)  # dumps C stack trace on SIGSEGV/SIGABRT

    class Tee:
        def __init__(self, original, file):
            self._o = original  # may be None in windowed PyInstaller build
            self._f = file
        def write(self, data):
            if self._o is not None:
                try:
                    self._o.write(data)
                except Exception:
                    pass
            self._f.write(data)
            self._f.flush()
        def flush(self):
            if self._o is not None:
                try:
                    self._o.flush()
                except Exception:
                    pass
            self._f.flush()
        def fileno(self):
            return self._f.fileno()

    sys.stdout = Tee(sys.stdout, log_file)
    sys.stderr = Tee(sys.stderr, log_file)

    def _excepthook(exc_type, exc_value, exc_tb):
        import traceback
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log_file.write(f"[UNCAUGHT EXCEPTION]\n{msg}\n")
        log_file.flush()
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook


def main():
    _setup_file_logging()
    app = QApplication(sys.argv)
    icon_path = resource_path("favicon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyleSheet(GLASS_STYLE)
    w = ClientWindow()
    if icon_path.exists():
        w.setWindowIcon(QIcon(str(icon_path)))
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()