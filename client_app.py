import json
import os
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
    QComboBox, QSystemTrayIcon, QMenu, QFrame, QProgressBar
)

# =====================================================================
#  APOCALYPSE THEME  –  7 Days to Die ModSync Client
# =====================================================================
MODSYNC_QSS = """
/* ─── Global ─────────────────────────────────────────────── */
QWidget {
    background-color: #1E1E2E;
    color: #CDD6F4;
    font-family: "Segoe UI", "Consolas", sans-serif;
    font-size: 12px;
}

QWidget#MainWindow {
    background-color: #1E1E2E;
    border: 1px solid #45475A;
}

/* ─── Labels ──────────────────────────────────────────────── */
QLabel {
    color: #A6ADC8;
    font-size: 11px;
    background: transparent;
}

QLabel#StatusLabel {
    color: #A6E3A1;
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 1px;
    background: transparent;
}

QLabel#GameLabel {
    color: #FAB387;
    font-weight: bold;
}

QLabel#SectionLabel {
    color: #89B4FA;
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 2px;
    padding: 2px 0px;
    border-bottom: 1px solid #45475A;
}

QLabel#SpeedLabel {
    color: #89DCEB;
    font-size: 11px;
    font-weight: bold;
    background: transparent;
}

/* ─── Line Edits ──────────────────────────────────────────── */
QLineEdit {
    background-color: #181825;
    color: #CDD6F4;
    border: 1px solid #45475A;
    border-left: 3px solid #89B4FA;
    padding: 5px 8px;
    selection-background-color: #89B4FA;
    selection-color: #1E1E2E;
    font-size: 12px;
}

QLineEdit:focus {
    border: 1px solid #89B4FA;
    border-left: 3px solid #CBA6F7;
}

QLineEdit:read-only {
    color: #585B70;
    border-left: 3px solid #313244;
    background-color: #181825;
}

QLineEdit::placeholder {
    color: #45475A;
}

/* ─── Buttons ─────────────────────────────────────────────── */
QPushButton {
    background-color: #313244;
    color: #CDD6F4;
    border: 1px solid #45475A;
    border-left: 3px solid #89B4FA;
    padding: 6px 14px;
    font-size: 11px;
    min-width: 60px;
}

QPushButton:hover {
    background-color: #45475A;
    color: #CDD6F4;
    border: 1px solid #89B4FA;
    border-left: 3px solid #CBA6F7;
}

QPushButton:pressed {
    background-color: #89B4FA;
    color: #1E1E2E;
}

QPushButton:disabled {
    background-color: #1E1E2E;
    color: #45475A;
    border: 1px solid #313244;
    border-left: 3px solid #313244;
}

QPushButton:checked {
    background-color: #1E3A5F;
    color: #89B4FA;
    border: 1px solid #89B4FA;
    border-left: 3px solid #CBA6F7;
}

/* ─── ComboBox ────────────────────────────────────────────── */
QComboBox {
    background-color: #181825;
    color: #CDD6F4;
    border: 1px solid #45475A;
    border-left: 3px solid #89B4FA;
    padding: 5px 8px;
    min-width: 80px;
}

QComboBox:hover {
    border: 1px solid #89B4FA;
    background-color: #313244;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox::down-arrow {
    width: 8px;
    height: 8px;
    border-left: 2px solid #89B4FA;
    border-bottom: 2px solid #89B4FA;
}

QComboBox QAbstractItemView {
    background-color: #181825;
    color: #CDD6F4;
    border: 1px solid #89B4FA;
    selection-background-color: #313244;
    selection-color: #89B4FA;
    outline: none;
}

/* ─── Table ───────────────────────────────────────────────── */
QTableWidget {
    background-color: #181825;
    color: #CDD6F4;
    border: 1px solid #313244;
    gridline-color: #313244;
    selection-background-color: #313244;
    selection-color: #89B4FA;
    font-size: 12px;
}

QTableWidget::item {
    padding: 5px 6px;
    border-bottom: 1px solid #313244;
}

QTableWidget::item:selected {
    background-color: #313244;
    color: #89B4FA;
}

QHeaderView::section {
    background-color: #181825;
    color: #89B4FA;
    border: none;
    border-right: 1px solid #313244;
    border-bottom: 2px solid #89B4FA;
    padding: 6px 8px;
    font-size: 11px;
    letter-spacing: 1px;
    font-weight: bold;
}

/* ─── Log / TextEdit ──────────────────────────────────────── */
QTextEdit {
    background-color: #11111B;
    color: #A6E3A1;
    border: 1px solid #313244;
    border-left: 3px solid #45475A;
    font-family: "Consolas", monospace;
    font-size: 13px;
    line-height: 1.5;
    padding: 4px;
}

/* ─── Splitter ────────────────────────────────────────────── */
QSplitter::handle {
    background-color: #313244;
    width: 3px;
}

QSplitter::handle:hover {
    background-color: #89B4FA;
}

/* ─── Scrollbars ──────────────────────────────────────────── */
QScrollBar:vertical {
    background: #1E1E2E;
    width: 8px;
    border: none;
}

QScrollBar::handle:vertical {
    background: #45475A;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background: #89B4FA;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical { height: 0; }

QScrollBar:horizontal {
    background: #1E1E2E;
    height: 8px;
    border: none;
}

QScrollBar::handle:horizontal {
    background: #45475A;
    min-width: 20px;
}

QScrollBar::handle:horizontal:hover {
    background: #89B4FA;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal { width: 0; }

/* ─── MessageBox ──────────────────────────────────────────── */
QMessageBox {
    background-color: #1E1E2E;
    color: #CDD6F4;
}

QMessageBox QPushButton {
    min-width: 80px;
    padding: 6px 16px;
}

/* ─── Menu ────────────────────────────────────────────────── */
QMenu {
    background-color: #181825;
    color: #CDD6F4;
    border: 1px solid #45475A;
}

QMenu::item:selected {
    background-color: #313244;
    color: #89B4FA;
}

/* ─── Progress bar ────────────────────────────────────────── */
QProgressBar {
    background-color: #181825;
    border: 1px solid #313244;
    color: transparent;
    max-height: 5px;
    min-height: 5px;
    text-align: center;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #89B4FA, stop:1 #CBA6F7);
}
"""

# -----------------------------
# Paths / Config
# -----------------------------

APPDATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "ModSyncClient"
CONFIG_PATH = APPDATA_DIR / "config.json"
TEMP_DIR = APPDATA_DIR / "temp"

DEFAULT_GAME_EXE_STEAM = r"C:\Program Files (x86)\Steam\steamapps\common\7 Days to Die\7DaysToDie.exe"

CHECK_INTERVAL_OPTIONS_MIN = [30, 60, 180, 300]
DEFAULT_CHECK_INTERVAL_MIN = 30

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
        "lbl_status_idle":          "STATUS: IDLE",
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
        "btn_download":             "Download bundle",
        "btn_apply":                "Apply update",
        "btn_fix":                  "Fix extras (move to disabled)",
        "btn_lang":                 "🌐 RU",
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
            "Welcome to ModSync — mod manager for 7 Days to Die servers.\n"
            "Please read before using:\n\n"

            "── WHAT THIS PROGRAM DOES ──────────────────────\n"
            "ModSync keeps your Mods folder in sync with the server.\n"
            "It compares your local mod files with the server list,\n"
            "downloads missing or outdated mods, and moves extras\n"
            "out of the way — so you always join with the right mods.\n\n"

            "── CONNECTING TO THE SERVER ────────────────────\n"
            "• Enter the server address in the 'Server URL' field.\n"
            "  Usually it's the same IP as the game server, port 8765.\n"
            "  Example: http://192.168.1.10:8765\n"
            "• Ask your server admin if you're unsure of the address.\n"
            "• Use the Ping button to check the connection.\n"
            "  If ping fails — the server may be offline or the address\n"
            "  is wrong. Contact your admin.\n\n"

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

            "── WHILE THE GAME IS RUNNING ───────────────────\n"
            "• You can check for updates and download at any time.\n"
            "• Applying updates requires the game to be closed.\n"
            "  ModSync will remind you and wait.\n\n"

            "── AUTO MODE ───────────────────────────────────\n"
            "• When enabled, ModSync checks and applies updates\n"
            "  automatically in the background.\n"
            "• If the game is running, the update waits until you close it.\n\n"

            "By clicking YES you confirm that you have read this\n"
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
        "lbl_status_idle":          "СТАТУС: ОЖИДАНИЕ",
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
        "btn_download":             "Скачать пакет",
        "btn_apply":                "Применить обновление",
        "btn_fix":                  "Убрать лишние моды",
        "btn_lang":                 "🌐 EN",
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
            "Добро пожаловать в ModSync — менеджер модов для серверов 7 Days to Die.\n"
            "Пожалуйста, прочитайте перед использованием:\n\n"

            "── ЧТО ДЕЛАЕТ ЭТА ПРОГРАММА ────────────────────\n"
            "ModSync синхронизирует вашу папку Mods с сервером.\n"
            "Программа сравнивает ваши моды со списком сервера,\n"
            "скачивает отсутствующие или устаревшие, а лишние\n"
            "убирает в сторону — чтобы вы всегда заходили с нужными модами.\n\n"

            "── ПОДКЛЮЧЕНИЕ К СЕРВЕРУ ────────────────────────\n"
            "• Введите адрес сервера в поле «Адрес сервера».\n"
            "  Обычно это тот же IP, что и у игрового сервера, порт 8765.\n"
            "  Пример: http://192.168.1.10:8765\n"
            "• Если не знаете адрес — спросите у администратора сервера.\n"
            "• Нажмите Пинг, чтобы проверить соединение.\n"
            "  Если пинг не проходит — сервер недоступен или адрес неверный.\n"
            "  Обратитесь к администратору.\n\n"

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

            "── ПОКА ИГРА ЗАПУЩЕНА ───────────────────────────\n"
            "• Проверять обновления и скачивать пакет можно в любое время.\n"
            "• Применить обновление можно только после закрытия игры.\n"
            "  Программа напомнит об этом и подождёт.\n\n"

            "── АВТОРЕЖИМ ────────────────────────────────────\n"
            "• При включённом авторежиме ModSync самостоятельно проверяет\n"
            "  и применяет обновления в фоне.\n"
            "• Если игра запущена — обновление отложится до её закрытия.\n\n"

            "Нажимая ДА, вы подтверждаете, что прочитали это\n"
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
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

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


def compute_mod_hash(mod_dir: Path) -> tuple[str, int, int]:
    files = []
    for p in mod_dir.rglob("*"):
        if p.is_file():
            rel = p.relative_to(mod_dir).as_posix().lower()
            files.append((rel, p))
    files.sort(key=lambda x: x[0])

    h = sha256()
    total_size = 0
    files_count = 0

    for rel, p in files:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        total_size += p.stat().st_size
        files_count += 1

    return h.hexdigest(), total_size, files_count


def discover_local_mods(mods_root: Path) -> dict[str, str]:
    """
    local_map: id -> hash
    Мод = папка Mods/<ModName>/ModInfo.xml
    """
    out: dict[str, str] = {}
    if not mods_root.exists() or not mods_root.is_dir():
        return out

    for entry in mods_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.lower() == "disabled_mods":
            continue
        if not (entry / "ModInfo.xml").exists():
            continue
        mod_id = entry.name
        mod_hash, _, _ = compute_mod_hash(entry)
        out[mod_id] = mod_hash

    return out


def ensure_disabled_dir(mods_root: Path) -> Path:
    d = mods_root / "disabled_mods"
    d.mkdir(parents=True, exist_ok=True)
    return d


def move_to_disabled(mods_root: Path, src: Path, reason: str, log_cb):
    """
    Перемещает папку/файл в Mods/disabled_mods с причинами и timestamp.
    Ничего не удаляем жёстко.
    """
    if not src.exists():
        return
    disabled = ensure_disabled_dir(mods_root)
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_name = src.name
    dst = disabled / f"{safe_name}__{reason}__{ts}"
    log_cb(f"[MOVE] {src.name} -> disabled_mods ({reason})")
    try:
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
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


def http_get_json(url: str, headers: Optional[dict] = None, timeout: int = 8) -> dict:
    req = urlrequest.Request(url, method="GET")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8", errors="replace"))


def http_post_json(url: str, payload: dict, timeout: int = 12) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8", errors="replace"))


def _http_get_range(host: str, port: int, path: str, body: bytes,
                    start: int, end: int, out_file: Path, offset: int,
                    timeout: int = 300) -> int:
    """Download one byte range via GET with Range header. Returns bytes received."""
    import http.client as _http
    conn = _http.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.connect()
        sock = conn.sock
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        conn.putrequest("GET", path + f"?range={start}-{end}")
        conn.putheader("Range", f"bytes={start}-{end}")
        conn.endheaders()

        resp = conn.getresponse()
        if resp.status not in (200, 206):
            raise RuntimeError(f"HTTP {resp.status} for range {start}-{end}")

        received = 0
        CHUNK = 2 * 1024 * 1024
        with out_file.open("r+b") as f:
            f.seek(offset)
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
        return received
    finally:
        conn.close()


def http_post_download(url: str, payload: dict, out_file: Path,
                       timeout: int = 60 * 10,
                       progress_cb=None) -> None:
    """
    Parallel multi-stream download:
    - Stream 0: POST /bundle (no Range) -> gets full file + total size + Accept-Ranges
    - If server supports ranges: streams 1-5 immediately connect with Range headers
      for the remaining portions while stream 0 reads its share
    - Progress reported every 0.25s via sliding window
    """
    import http.client as _http
    from urllib.parse import urlparse as _up
    import threading as _threading

    _p = _up(url)
    host = _p.hostname
    port = _p.port or (443 if _p.scheme == "https" else 80)
    path = (_p.path or "/") + (("?" + _p.query) if _p.query else "")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def make_conn(range_bytes=None):
        c = _http.HTTPConnection(host, port, timeout=timeout)
        c.connect()
        c.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
        c.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        c.putrequest("POST", path)
        c.putheader("Content-Type", "application/json; charset=utf-8")
        c.putheader("Content-Length", str(len(body)))
        if range_bytes:
            c.putheader("Range", f"bytes={range_bytes[0]}-{range_bytes[1]}")
        c.endheaders(body)
        return c

    NUM_STREAMS   = 6
    MIN_PARALLEL  = 8 * 1024 * 1024
    CHUNK         = 2 * 1024 * 1024

    out_file.parent.mkdir(parents=True, exist_ok=True)

    # ── Open stream 0 (full request — no Range header)
    conn0 = make_conn()
    resp0 = conn0.getresponse()
    if resp0.status != 200:
        conn0.close()
        raise RuntimeError(f"HTTP {resp0.status} {resp0.reason}")

    total         = int(resp0.getheader("Content-Length") or 0)
    accepts_range = resp0.getheader("Accept-Ranges", "") == "bytes"

    # ── Decide: parallel or single
    if not accepts_range or total < MIN_PARALLEL:
        # Single stream — simple read loop with progress
        downloaded = 0
        _win: list = []
        t_last_cb  = 0.0
        with out_file.open("wb", buffering=4 * 1024 * 1024) as f:
            while True:
                chunk = resp0.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total > 0:
                    now = time.monotonic()
                    _win.append((now, len(chunk)))
                    cutoff = now - 1.5
                    while _win and _win[0][0] < cutoff:
                        _win.pop(0)
                    if now - t_last_cb >= 0.25:
                        t_last_cb = now
                        pct  = int(downloaded * 100 / total)
                        ws   = (now - _win[0][0]) if len(_win) > 1 else 0.25
                        wb   = sum(b for _, b in _win)
                        speed = (wb / ws / 1024 / 1024) if ws > 0 else 0.0
                        progress_cb(pct, speed)
        conn0.close()
        if progress_cb:
            progress_cb(100, 0.0)
        return

    # ── Parallel: pre-allocate file on disk
    with out_file.open("wb") as f:
        f.seek(total - 1)
        f.write(b"\x00")

    # Divide into NUM_STREAMS equal parts
    part = total // NUM_STREAMS
    ranges = []
    for i in range(NUM_STREAMS):
        s = i * part
        e = (s + part - 1) if i < NUM_STREAMS - 1 else total - 1
        ranges.append((s, e))

    # downloaded_parts[i] = bytes received by stream i
    downloaded_parts = [0] * NUM_STREAMS
    errors           = []
    lock             = _threading.Lock()

    def stream0_worker():
        """Stream 0 reads from the already-open full response."""
        s, e = ranges[0]
        length   = e - s + 1
        received = 0
        try:
            with out_file.open("r+b") as f:
                f.seek(s)
                while received < length:
                    chunk = resp0.read(min(CHUNK, length - received))
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    with lock:
                        downloaded_parts[0] = received
        except Exception as ex:
            errors.append(f"stream0: {ex}")
        finally:
            conn0.close()

    def range_worker(idx: int):
        s, e     = ranges[idx]
        length   = e - s + 1
        received = 0
        try:
            c    = make_conn(range_bytes=(s, e))
            resp = c.getresponse()
            if resp.status not in (200, 206):
                raise RuntimeError(f"HTTP {resp.status} for bytes={s}-{e}")
            with out_file.open("r+b") as f:
                f.seek(s)
                while received < length:
                    chunk = resp.read(min(CHUNK, length - received))
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    with lock:
                        downloaded_parts[idx] = received
            c.close()
        except Exception as ex:
            errors.append(f"stream{idx}: {ex}")

    threads = [_threading.Thread(target=stream0_worker, daemon=True)]
    for i in range(1, NUM_STREAMS):
        threads.append(_threading.Thread(target=range_worker, args=(i,), daemon=True))

    for t in threads:
        t.start()

    # ── Progress reporting loop
    _win: list  = []
    t_last_cb   = 0.0
    prev_total  = 0
    while any(t.is_alive() for t in threads):
        time.sleep(0.15)
        if progress_cb and total > 0:
            now = time.monotonic()
            with lock:
                cur = sum(downloaded_parts)
            delta = cur - prev_total
            prev_total = cur
            if delta > 0:
                _win.append((now, delta))
            cutoff = now - 1.5
            while _win and _win[0][0] < cutoff:
                _win.pop(0)
            if now - t_last_cb >= 0.25:
                t_last_cb = now
                pct   = int(cur * 100 / total)
                ws    = (now - _win[0][0]) if len(_win) > 1 else 0.15
                wb    = sum(b for _, b in _win)
                speed = (wb / ws / 1024 / 1024) if ws > 0 else 0.0
                progress_cb(pct, speed)

    for t in threads:
        t.join()

    if errors:
        raise RuntimeError(f"Parallel download errors: {'; '.join(errors)}")

    if progress_cb:
        progress_cb(100, 0.0)


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
    download_ready = pyqtSignal(Path)
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
    """Компактный тост в правом нижнем углу."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._msg = QLabel("")
        self._msg.setMinimumWidth(200)
        self._msg.setMaximumWidth(300)
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet(
            "font-size: 13px; color: #CDD6F4;"
            "font-family: 'Segoe UI', sans-serif; background: transparent;"
        )

        box = QHBoxLayout()
        box.setContentsMargins(14, 10, 14, 10)
        box.addWidget(self._msg)

        card = QWidget()
        card.setLayout(box)
        card.setStyleSheet(
            "QWidget { background: rgba(24,24,37,235);"
            "border-left: 4px solid #89B4FA;"
            "border-top: 1px solid #45475A;"
            "border-right: 1px solid #45475A;"
            "border-bottom: 1px solid #45475A; }"
        )

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(card)
        self.setLayout(root)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_toast(self, title: str, message: str, duration_ms: int = 4000):
        text = f"<b>{title}</b>  {message}" if title else message
        self._msg.setText(text)
        self.adjustSize()
        self._move_to_bottom_right()
        self.show()
        self.raise_()
        self._hide_timer.start(duration_ms)

    def _move_to_bottom_right(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()  # не лезем под панель задач
        w = self.width()
        h = self.height()

        margin = 18
        x = geo.x() + geo.width() - w - margin
        y = geo.y() + geo.height() - h - margin
        self.move(QPoint(x, y))

# -----------------------------
# Client app
# -----------------------------

class ClientWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MODSYNC  //  7 DAYS TO DIE")
        self.resize(1280, 760)
        self.setObjectName("MainWindow")

        ensure_dirs()
        self.cfg = read_config()
        self._lang = self.cfg.get("lang", "ru")  # current language
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
        self.last_bundle_zip: Optional[Path] = None
        self.server_mods: list[dict] = []
        self.server_manifest_hash: str = ""
        self.local_mods_map: dict[str, str] = {}

        # auto-pipeline guards (prevent double auto download/apply)
        self._auto_pipeline_running = False
        self._auto_target_hash = ""
        self._auto_download_started = False
        self._auto_apply_started = False
        self._auto_last_start_ts = 0.0

        # bridge
        self.bridge = UiBridge()
        self.bridge.log.connect(self.append_log)
        self.bridge.status.connect(self.set_status)
        self.bridge.diff_ready.connect(self.on_diff_ready)
        self.bridge.servermods_ready.connect(self.on_servermods_ready)
        self.bridge.download_ready.connect(self.on_download_ready)
        self.bridge.error.connect(self.on_error)
        self.bridge.set_busy.connect(self.set_busy)
        self.bridge.toast.connect(self.show_toast)
        self.bridge.steamid_ready.connect(self.on_steamid_ready)
        self.bridge.download_progress.connect(self.on_download_progress)

        # ── Cooldown timers for check/ping buttons ─────────────
        self._check_cooldown = 0.0   # timestamp of last manual check
        self._ping_cooldown  = 0.0   # timestamp of last ping
        self._CHECK_CD_SEC   = 60
        self._PING_CD_SEC    = 60

        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.setInterval(1000)
        self._cooldown_timer.timeout.connect(self._tick_cooldowns)
        self._cooldown_timer.start()
        self.bridge.start_check.connect(self._start_check)

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
        # ══════════════════════════════════════════════════════
        #  MAIN LAYOUT:
        #  [labels | fields+controls+table ] [ log (full height) ]
        # ══════════════════════════════════════════════════════

        # ── Outer: horizontal split — left panel | log ────────
        outer = QHBoxLayout()
        outer.setSpacing(6)
        outer.setContentsMargins(6, 6, 6, 6)

        # ── LEFT PANEL (labels + fields + controls + table) ───
        left_panel = QWidget()
        left_vbox = QVBoxLayout(left_panel)
        left_vbox.setContentsMargins(0, 0, 0, 0)
        left_vbox.setSpacing(4)

        # ── TOP SECTION: labels col + fields/buttons col ──────
        top_section = QHBoxLayout()
        top_section.setSpacing(6)

        # Labels column (orange box — narrow, fixed)
        labels_widget = QWidget()
        labels_widget.setFixedWidth(130)
        labels_vbox = QVBoxLayout(labels_widget)
        labels_vbox.setContentsMargins(4, 4, 4, 4)
        labels_vbox.setSpacing(8)

        self.lbl_server_url = QLabel("Server URL:")
        self.lbl_steamid    = QLabel("SteamID:")
        self.lbl_game_exe   = QLabel("Game EXE:")
        self.lbl_mods_dir   = QLabel("Mods dir:")
        self.lbl_autocheck  = QLabel("Auto-check:")

        for lbl in [self.lbl_server_url, self.lbl_steamid,
                    self.lbl_game_exe, self.lbl_mods_dir, self.lbl_autocheck]:
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.setFixedHeight(26)
            labels_vbox.addWidget(lbl)

        labels_vbox.addStretch(1)
        top_section.addWidget(labels_widget)

        # Fields + buttons column (yellow box)
        fields_widget = QWidget()
        fields_grid = QGridLayout(fields_widget)
        fields_grid.setContentsMargins(0, 0, 0, 0)
        fields_grid.setHorizontalSpacing(6)
        fields_grid.setVerticalSpacing(6)
        fields_grid.setColumnStretch(0, 1)  # field stretches

        # Row 0: server_edit | ping | save | lang
        self.server_edit = QLineEdit(self.cfg.get("server_url", ""))
        self.server_edit.setPlaceholderText("e.g. http://192.168.0.11:8765")
        fields_grid.addWidget(self.server_edit, 0, 0)

        self.ping_btn = QPushButton("Ping")
        self.ping_btn.setFixedWidth(80)
        self.ping_btn.clicked.connect(self.on_ping)
        fields_grid.addWidget(self.ping_btn, 0, 1)

        self.save_btn = QPushButton("Save")
        self.save_btn.setFixedWidth(100)
        self.save_btn.clicked.connect(self.on_save)
        fields_grid.addWidget(self.save_btn, 0, 2)

        # Lang + folder in one cell as a mini HBox
        lang_folder_widget = QWidget()
        lang_folder_box = QHBoxLayout(lang_folder_widget)
        lang_folder_box.setContentsMargins(0, 0, 0, 0)
        lang_folder_box.setSpacing(3)

        self.lang_btn = QPushButton("🌐 RU")
        self.lang_btn.setFixedWidth(66)
        self.lang_btn.clicked.connect(self.on_toggle_lang)
        lang_folder_box.addWidget(self.lang_btn)

        self.open_temp_btn = QPushButton("📁")
        self.open_temp_btn.setFixedWidth(30)
        self.open_temp_btn.setToolTip(str(APPDATA_DIR))
        self.open_temp_btn.clicked.connect(lambda: subprocess.Popen(f'explorer "{APPDATA_DIR}"'))
        lang_folder_box.addWidget(self.open_temp_btn)

        fields_grid.addWidget(lang_folder_widget, 0, 3)

        # Row 1: steam_edit | login btn | change btn
        self.steam_edit = QLineEdit(self.cfg.get("steamid", ""))
        self.steam_edit.setPlaceholderText("Click \'Login via Steam\' or enter manually")
        if self.cfg.get("steamid", "").strip():
            self.steam_edit.setReadOnly(True)
        fields_grid.addWidget(self.steam_edit, 1, 0)

        self.steam_login_btn = QPushButton("⚙ Login via Steam")
        self.steam_login_btn.clicked.connect(self.on_steam_login)
        # Disable if already authenticated (created before check, so do it after)
        if self.cfg.get("steamid", "").strip():
            self.steam_login_btn.setEnabled(False)
        fields_grid.addWidget(self.steam_login_btn, 1, 1, 1, 2)

        self.steam_change_btn = QPushButton("Change Account")
        self.steam_change_btn.clicked.connect(self.on_change_account)
        fields_grid.addWidget(self.steam_change_btn, 1, 3)

        # Row 2: game_edit | find_game btn
        self.game_edit = QLineEdit(self.cfg.get("game_exe", ""))
        self.game_edit.setReadOnly(True)
        fields_grid.addWidget(self.game_edit, 2, 0, 1, 3)

        self.find_game_btn = QPushButton("Find game")
        self.find_game_btn.clicked.connect(self.on_find_game)
        fields_grid.addWidget(self.find_game_btn, 2, 3)

        # Row 3: mods_edit (full width)
        self.mods_edit = QLineEdit(self.cfg.get("mods_dir", ""))
        self.mods_edit.setReadOnly(True)
        fields_grid.addWidget(self.mods_edit, 3, 0, 1, 4)

        # Row 4: autocheck combo | autocheck btn | automode | rules
        self.autocheck_combo = QComboBox()
        for m in CHECK_INTERVAL_OPTIONS_MIN:
            self.autocheck_combo.addItem(f"{m} min", m)
        saved_interval = int(self.cfg.get("check_interval_min", DEFAULT_CHECK_INTERVAL_MIN))
        if saved_interval not in CHECK_INTERVAL_OPTIONS_MIN:
            saved_interval = DEFAULT_CHECK_INTERVAL_MIN
        self.autocheck_combo.setCurrentIndex(CHECK_INTERVAL_OPTIONS_MIN.index(saved_interval))
        fields_grid.addWidget(self.autocheck_combo, 4, 0)

        self.autocheck_btn = QPushButton("Enabled")
        self.autocheck_btn.setCheckable(True)
        self.autocheck_btn.setChecked(bool(self.cfg.get("auto_check_enabled", True)))  # default True
        self.autocheck_btn.setText("Enabled" if self.autocheck_btn.isChecked() else "Disabled")
        self.autocheck_btn.clicked.connect(self.on_toggle_autocheck)
        fields_grid.addWidget(self.autocheck_btn, 4, 1)

        self.automode_btn = QPushButton("AUTO MODE: OFF")
        self.automode_btn.setCheckable(True)
        self.automode_btn.setChecked(bool(self.cfg.get("auto_mode", False)))
        self.automode_btn.setText("AUTO MODE: ON" if self.automode_btn.isChecked() else "AUTO MODE: OFF")
        self.automode_btn.clicked.connect(self.on_toggle_automode)
        fields_grid.addWidget(self.automode_btn, 4, 2)

        self.rules_btn = QPushButton("Show rules")
        self.rules_btn.clicked.connect(lambda: self.show_rules_dialog(force=True))
        fields_grid.addWidget(self.rules_btn, 4, 3)

        top_section.addWidget(fields_widget, 1)
        left_vbox.addLayout(top_section)

        self.test_toast_btn = QPushButton("Test toast")
        self.test_toast_btn.clicked.connect(lambda: self.show_toast(self.tr("toast_title"), self.tr("toast_test")))
        self.test_toast_btn.setVisible(False)

        self.reset_rules_btn = QPushButton("Reset rules")
        self.reset_rules_btn.clicked.connect(self.on_reset_rules)
        self.reset_rules_btn.setVisible(False)

        # ── GAME STATUS ────────────────────────────────────────
        self.game_status_lbl = QLabel("GAME: ...")
        self.game_status_lbl.setObjectName("GameLabel")
        left_vbox.addWidget(self.game_status_lbl)

        # ── ACTION BUTTONS ROW + STATUS ───────────────────────
        action_row = QHBoxLayout()
        action_row.setSpacing(6)

        self.check_btn = QPushButton("Check updates")
        self.check_btn.clicked.connect(self.on_check_updates_full)
        action_row.addWidget(self.check_btn)

        self.download_btn = QPushButton("Download bundle")
        self.download_btn.clicked.connect(self.on_download_bundle)
        self.download_btn.setEnabled(False)
        action_row.addWidget(self.download_btn)

        self.apply_btn = QPushButton("Apply update")
        self.apply_btn.clicked.connect(self.on_apply_update)
        self.apply_btn.setEnabled(False)
        action_row.addWidget(self.apply_btn)

        self.fix_btn = QPushButton("Fix extras (move to disabled)")
        self.fix_btn.clicked.connect(self.on_fix_extras)
        self.fix_btn.setEnabled(False)
        action_row.addWidget(self.fix_btn)

        action_row.addStretch(1)

        self.status_lbl = QLabel("STATUS: IDLE")
        self.status_lbl.setObjectName("StatusLabel")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        action_row.addWidget(self.status_lbl)

        left_vbox.addLayout(action_row)

        # ── PROGRESS BAR + SPEED ──────────────────────────────
        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        progress_row.addWidget(self.progress_bar, 1)

        self.progress_pct_lbl = QLabel("")
        self.progress_pct_lbl.setObjectName("SpeedLabel")
        self.progress_pct_lbl.setFixedWidth(42)
        self.progress_pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.progress_pct_lbl.setVisible(False)
        progress_row.addWidget(self.progress_pct_lbl)

        self.speed_lbl = QLabel("")
        self.speed_lbl.setObjectName("SpeedLabel")
        self.speed_lbl.setFixedWidth(90)
        self.speed_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.speed_lbl.setVisible(False)
        progress_row.addWidget(self.speed_lbl)

        left_vbox.addLayout(progress_row)

        # ── TABLE ─────────────────────────────────────────────
        self.compare_table = QTableWidget(0, 5)
        self.compare_table.setHorizontalHeaderLabels(["Mod", "Server hash", "Local hash", "Status", "Action"])
        # Stretch all columns except Status (col 3) and Action (col 4)
        hdr = self.compare_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)          # Мод — растягивается
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents) # Хэш сервера
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents) # Локальный хэш
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents) # Статус — по содержимому
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents) # Действие — по содержимому
        self.compare_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.compare_table.cellDoubleClicked.connect(self.on_table_double_click)

        table_wrapper = QWidget()
        table_vbox = QVBoxLayout(table_wrapper)
        table_vbox.setContentsMargins(0, 0, 0, 0)
        table_vbox.setSpacing(2)
        self.lbl_compare = QLabel("▸ MOD COMPARISON")
        self.lbl_compare.setObjectName("SectionLabel")
        table_vbox.addWidget(self.lbl_compare)
        table_vbox.addWidget(self.compare_table, 1)

        left_vbox.addWidget(table_wrapper, 1)

        # ── RIGHT PANEL: LOG (blue box, full height) ───────────
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumWidth(320)

        # ── Assemble outer splitter ────────────────────────────
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(self.log_view)
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 2)
        main_splitter.setHandleWidth(10)

        outer.addWidget(main_splitter)

        # ── FOOTER ────────────────────────────────────────────
        footer = QWidget()
        footer.setFixedHeight(24)
        footer.setStyleSheet("background: #181B20; border-top: 1px solid #313244;")
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(10, 0, 10, 0)
        footer_row.setSpacing(0)

        def _footer_lbl(text, color="#6C7086", url=None):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color: {color}; font-size: 10px; background: transparent;"
                + (" text-decoration: underline;" if url else "")
            )
            if url:
                lbl.setOpenExternalLinks(True)
                lbl.setText(f'<a href="{url}" style="color:{color};text-decoration:none;">{text}</a>')
            return lbl

        footer_row.addWidget(_footer_lbl("ModSync v1.0", "#89B4FA"))
        footer_row.addWidget(_footer_lbl("  •  ", "#45475A"))
        footer_row.addWidget(_footer_lbl("by SkyLett & AI Assistant", "#A6ADC8"))
        footer_row.addStretch(1)
        footer_row.addWidget(_footer_lbl("🌐 bar7dtd.ru", "#89DCEB", "http://bar7dtd.ru"))
        footer_row.addWidget(_footer_lbl("   ", "#45475A"))
        footer_row.addWidget(_footer_lbl("💬 Discord", "#CBA6F7", "https://discord.gg/B3zN2h7Ukf"))

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(outer)
        root.addWidget(footer)
        self.setLayout(root)

        # timers
        self.game_timer = QTimer(self)
        self.game_timer.setInterval(10000)
        self.game_timer.timeout.connect(self.refresh_game_status)
        self.game_timer.start()

        self.autocheck_timer = QTimer(self)
        self.autocheck_timer.timeout.connect(self.on_autocheck_tick)

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
        self.append_log(f"[APP] Temp:   {TEMP_DIR}")

        # Apply translations (handles initial lang from config)
        self.retranslate_ui()

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
        # Labels
        self.lbl_server_url.setText(t("lbl_server_url"))
        self.lbl_steamid.setText(t("lbl_steamid"))
        self.lbl_game_exe.setText(t("lbl_game_exe"))
        self.lbl_mods_dir.setText(t("lbl_mods_dir"))
        self.lbl_autocheck.setText(t("lbl_autocheck"))
        self.lbl_compare.setText(t("lbl_mod_comparison"))
        # Placeholders
        self.server_edit.setPlaceholderText(t("ph_server_url"))
        self.steam_edit.setPlaceholderText(t("ph_steamid"))
        # Buttons
        self.ping_btn.setText(t("btn_ping"))
        self.test_toast_btn.setText(t("btn_test_toast"))
        self.steam_login_btn.setText(t("btn_steam_login"))
        self.steam_change_btn.setText(t("btn_steam_change"))
        self.save_btn.setText(t("btn_save"))
        self.find_game_btn.setText(t("btn_find_game"))
        self.autocheck_btn.setText(t("btn_autocheck_on") if self.autocheck_btn.isChecked() else t("btn_autocheck_off"))
        self.automode_btn.setText(t("btn_automode_on") if self.automode_btn.isChecked() else t("btn_automode_off"))
        self.rules_btn.setText(t("btn_show_rules"))
        self.reset_rules_btn.setText(t("btn_reset_rules"))
        self.check_btn.setText(t("btn_check"))
        self.download_btn.setText(t("btn_download"))
        self.apply_btn.setText(t("btn_apply"))
        self.fix_btn.setText(t("btn_fix"))
        self.lang_btn.setText(t("btn_lang"))
        # Status / game labels
        running = is_game_running()
        self.game_status_lbl.setText(t("lbl_game_running") if running else t("lbl_game_offline"))
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

    def append_log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_view.append(f"[{ts}] {msg}")

    def set_status(self, msg: str):
        self.status_lbl.setText(f"STATUS: {msg.upper()}")

    def set_busy(self, busy: bool):
        self._is_busy = busy
        # During operation — lock everything
        lock_all = [self.ping_btn, self.save_btn, self.find_game_btn,
                    self.check_btn, self.download_btn, self.apply_btn, self.fix_btn]
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
        has_bundle    = self.last_bundle_zip is not None and self.last_bundle_zip.exists()
        has_delete    = has_diff and len(self.last_diff.delete) > 0
        game_running  = is_game_running()

        # Check: always available — cooldown is handled by _tick_cooldowns only
        self.check_btn.setEnabled(True)

        # Download: only if updates exist and bundle not yet downloaded
        self.download_btn.setEnabled(has_download and not has_bundle)

        # Apply: only if bundle exists and game not running
        self.apply_btn.setEnabled(has_bundle and not game_running)

        # Fix extras: only if extras exist and game not running
        self.fix_btn.setEnabled(has_delete and not game_running)

    def _reset_pipeline(self):
        """Call after apply or to restart the cycle."""
        self.last_diff = None
        self.last_bundle_zip = None
        self.update_action_buttons()

    def _tick_cooldowns(self):
        """Update check/ping button labels with countdown."""
        now = time.time()
        busy = getattr(self, "_is_busy", False)

        # Check button cooldown — only when not busy
        if not busy:
            check_elapsed = now - self._check_cooldown
            if check_elapsed < self._CHECK_CD_SEC:
                remaining = int(self._CHECK_CD_SEC - check_elapsed) + 1
                self.check_btn.setText(f"{self.tr('btn_check')} ({remaining}s)")
                self.check_btn.setEnabled(False)
            else:
                self.check_btn.setText(self.tr("btn_check"))
                self.check_btn.setEnabled(True)

        # Ping button cooldown
        ping_elapsed = now - self._ping_cooldown
        if ping_elapsed < self._PING_CD_SEC:
            remaining = int(self._PING_CD_SEC - ping_elapsed) + 1
            self.ping_btn.setText(f"{self.tr('btn_ping')} ({remaining}s)")
            self.ping_btn.setEnabled(False)
        else:
            self.ping_btn.setText(self.tr("btn_ping"))
            self.ping_btn.setEnabled(not busy)
        
    def on_toggle_automode(self):
        self.cfg["auto_mode"] = bool(self.automode_btn.isChecked())
        self.automode_btn.setText(self.tr("btn_automode_on") if self.automode_btn.isChecked() else self.tr("btn_automode_off"))
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
        fn, _ = QFileDialog.getOpenFileName(self, "Select 7DaysToDie.exe", "", "7DTD exe (7DaysToDie.exe)")
        if not fn:
            return
        p = Path(fn)
        if p.name.lower() != "7daystodie.exe":
            QMessageBox.warning(self, self.tr("dlg_wrong_file_title"), self.tr("dlg_wrong_file_text"))
            return
        self.set_game_path(p)

    def set_game_path(self, exe_path: Path):
        self.game_edit.setText(str(exe_path))
        mods_dir = exe_path.parent / "Mods"
        self.mods_edit.setText(str(mods_dir))

        self.cfg["game_exe"] = str(exe_path)
        self.cfg["mods_dir"] = str(mods_dir)
        write_config(self.cfg)

        self.append_log(f"[GAME] exe={exe_path}")
        self.append_log(f"[GAME] mods={mods_dir}")

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
        self.cfg["server_url"] = self.server_edit.text().strip()
        self.cfg["steamid"] = self.steam_edit.text().strip()

        self.cfg["check_interval_min"] = int(self.autocheck_combo.currentData())
        self.cfg["auto_check_enabled"] = bool(self.autocheck_btn.isChecked())

        write_config(self.cfg)
        self.append_log("[CFG] saved")

    # ---------------- Auto-check ----------------

    def on_toggle_autocheck(self):
        self.autocheck_btn.setText(self.tr("btn_autocheck_on") if self.autocheck_btn.isChecked() else self.tr("btn_autocheck_off"))
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
        # лёгкая проверка: только server_manifest_hash через /mods
        self.on_save()
        if not self.cfg.get("server_url") or not self.cfg.get("steamid"):
            return

        def worker():
            try:
                base = self.cfg["server_url"].rstrip("/")
                data = http_get_json(base + "/mods")
                if not data.get("ok"):
                    return
                new_hash = str(data.get("server_manifest_hash", "")).strip()
                old_hash = str(self.cfg.get("last_server_manifest_hash", "")).strip()

                if new_hash and new_hash != old_hash:
                    auto_mode = bool(self.cfg.get("auto_mode", False))

                    # AUTO MODE + игра запущена -> ставим "pending", но last_server_manifest_hash НЕ трогаем
                    if auto_mode and is_game_running():
                        self.cfg["pending_update"] = True
                        self.cfg["pending_update_hash"] = new_hash
                        write_config(self.cfg)

                        self.bridge.log.emit(f"[AUTO] pending update (game running): {new_hash[:8]}")
                        self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_auto_pending"))
                        # звук по желанию
                        return

                    # если AUTO MODE выключен -> просто уведомим (и можем запомнить, что сервер изменился)
                    if not auto_mode:
                        self.cfg["last_seen_server_manifest_hash"] = new_hash
                        write_config(self.cfg)
                        self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_auto_available"))
                        return

                    # AUTO MODE включен и игра НЕ запущена -> запускаем пайплайн и помечаем pending
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
        # Prevent duplicate parallel runs
        if getattr(self, "fixing_extras", False):
            self.append_log("[AUTO] Extras fix already running -> skip")
            return
        self.fixing_extras = True
        if is_game_running():
            self.on_error(self.tr("err_game_running_fix"))
            return
        if not self.last_diff or not self.last_diff.delete:
            return

        base = self.cfg.get("server_url", "").strip()
        steamid = self.cfg.get("steamid", "").strip()
        mods_dir = self.cfg.get("mods_dir", "").strip()
        if not base or not steamid or not mods_dir:
            self.on_error(self.tr("err_missing_server_url"))
            return

        def worker():
            self.bridge.set_busy.emit(True)
            try:
                mods_root = Path(mods_dir)
                server_ids = set(m.get("id") for m in self.server_mods if isinstance(m, dict) and m.get("id"))

                # move delete list to disabled_mods
                for mod_id in self.last_diff.delete:
                    p = mods_root / str(mod_id)
                    if p.exists():
                        move_to_disabled(mods_root, p, "server_removed", self.bridge.log.emit)

                # strict cleanup (nonmods/extras) -> disabled_mods
                strict_cleanup_to_disabled(mods_root, server_ids, self.bridge.log.emit)

                # verify again
                local_map = discover_local_mods(mods_root)
                payload = {"steamid": steamid, "client_manifest_hash": "", "mods": [{"id": k, "hash": v} for k, v in local_map.items()]}
                diff2 = http_post_json(base.rstrip("/") + "/diff", payload)
                self.local_mods_map = local_map
                diff2["_context"] = "internal"
                diff2["_context"] = "internal"
                self.bridge.diff_ready.emit(diff2)

                if (diff2.get("download") or []) == [] and (diff2.get("delete") or []) == []:
                    self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_extras_moved"))
            except Exception as e:
                self.bridge.error.emit(str(e))
            finally:
                self.fixing_extras = False
                self.bridge.set_busy.emit(False)

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Network actions ----------------

    def on_ping(self):
        now = time.time()
        if now - self._ping_cooldown < self._PING_CD_SEC:
            return  # still on cooldown, button should be disabled anyway
        self._ping_cooldown = now
        self.on_save()
        base = self.cfg.get("server_url", "").strip()
        if not base:
            self.on_error(self.tr("err_server_empty"))
            return

        def worker():
            self.bridge.set_busy.emit(True)
            self.bridge.status.emit("ping...")
            try:
                data = http_get_json(base.rstrip("/") + "/ping")
                self.bridge.log.emit(f"[PING] {data}")
                self.bridge.status.emit("ping ok")
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
            self._auto_apply_started = False
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
            self.last_bundle_zip = None

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
                self.bridge.status.emit("get server mods...")
                server_data = http_get_json(base.rstrip("/") + "/mods")
                if not server_data.get("ok"):
                    raise RuntimeError(f"Bad /mods response: {server_data}")

                self.bridge.servermods_ready.emit(server_data)

                self.bridge.status.emit("build local hashes...")
                mods_root = Path(mods_dir)
                local_map = discover_local_mods(mods_root)
                # сохраняем для сравнения
                self.local_mods_map = local_map

                payload = {
                    "steamid": steamid,
                    "client_manifest_hash": "",
                    "mods": [{"id": k, "hash": v} for k, v in local_map.items()]
                }

                self.bridge.status.emit("request /diff ...")
                diff = http_post_json(base.rstrip("/") + "/diff", payload)

                if not diff.get("ok"):
                    raise RuntimeError(f"Bad /diff response: {diff}")

                diff["_context"] = context
                self.bridge.diff_ready.emit(diff)
                self.bridge.status.emit("diff ready")
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
        self.last_bundle_zip = None

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
        if not self.last_diff:
            return

        self.on_save()
        base = self.cfg.get("server_url", "").strip()
        steamid = self.cfg.get("steamid", "").strip()
        if not base or not steamid:
            self.on_error(self.tr("err_server_empty"))
            return

        ids = [str(x.get("id", "")).strip() for x in self.last_diff.download if isinstance(x, dict)]
        ids = [x for x in ids if x]
        if not ids:
            self.on_error(self.tr("err_nothing_to_dl"))
            return

        out_zip = TEMP_DIR / "mods.zip"
        try:
            if out_zip.exists():
                out_zip.unlink()
        except Exception:
            pass


        def worker():
            self.bridge.set_busy.emit(True)
            self.bridge.status.emit("download bundle...")
            try:
                payload = {"steamid": steamid, "ids": ids}

                def on_progress(pct: int, speed: float):
                    self.bridge.download_progress.emit(pct, speed)

                http_post_download(base.rstrip("/") + "/bundle", payload, out_zip,
                                   progress_cb=on_progress)
                self.bridge.log.emit(f"[DL] saved: {out_zip} ({human_bytes(out_zip.stat().st_size)})")
                self.bridge.download_ready.emit(out_zip)
                self.bridge.status.emit("downloaded")
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

    def on_download_ready(self, zip_path: Path):
        self.last_bundle_zip = zip_path
        self.append_log("[DL] bundle ready")
        self.update_action_buttons()

        if is_game_running():
            self.append_log("[INFO] Game is running -> apply disabled, but bundle is downloaded.")
            self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_downloaded"))

        if bool(self.cfg.get("auto_mode", False)) and not is_game_running():
            # dedup auto-apply
            if getattr(self, "_auto_pipeline_running", False) and self._auto_download_started and (not self._auto_apply_started):
                self._auto_apply_started = True
                self.append_log("[AUTO] Bundle downloaded -> auto apply")
                self.on_apply_update()

    def on_apply_update(self):
        if is_game_running():
            self.on_error(self.tr("err_game_running_apply"))
            return
        if not self.last_bundle_zip or not self.last_bundle_zip.exists():
            self.on_error(self.tr("err_no_bundle"))
            return
        if not self.last_diff:
            self.on_error(self.tr("err_no_diff"))
            return

        self.on_save()
        base = self.cfg.get("server_url", "").strip()
        steamid = self.cfg.get("steamid", "").strip()
        mods_dir = self.cfg.get("mods_dir", "").strip()

        def worker():
            self.bridge.set_busy.emit(True)
            self.bridge.status.emit("applying...")

            try:
                mods_root = Path(mods_dir)
                mods_root.mkdir(parents=True, exist_ok=True)

                # extract
                extract_dir = TEMP_DIR / "extract"
                if extract_dir.exists():
                    shutil.rmtree(extract_dir, ignore_errors=True)
                extract_dir.mkdir(parents=True, exist_ok=True)

                self.bridge.log.emit(f"[ZIP] Extract -> {extract_dir}")
                with zipfile.ZipFile(self.last_bundle_zip, "r") as z:
                    z.extractall(extract_dir)

                server_ids = set(m.get("id") for m in self.server_mods if isinstance(m, dict) and m.get("id"))

                # apply downloaded mods: <ModName>/...
                download_ids = [str(x.get("id", "")).strip() for x in self.last_diff.download if isinstance(x, dict)]
                download_ids = [x for x in download_ids if x]

                for mod_id in download_ids:
                    src = extract_dir / mod_id
                    if not src.exists() or not src.is_dir():
                        raise RuntimeError(f"Bundle missing folder: {mod_id}")

                    dst = mods_root / mod_id

                    # если что-то уже есть — не бэкапим, а переносим в disabled_mods
                    if dst.exists():
                        move_to_disabled(mods_root, dst, "replaced", self.bridge.log.emit)

                    self.bridge.log.emit(f"[APPLY] Install '{mod_id}'")
                    shutil.move(str(src), str(dst))

                # "delete" список не удаляем — переносим в disabled_mods
                for mod_id in self.last_diff.delete:
                    p = mods_root / str(mod_id)
                    if p.exists():
                        move_to_disabled(mods_root, p, "server_removed", self.bridge.log.emit)

                # строгая чистка, но в disabled_mods
                strict_cleanup_to_disabled(mods_root, server_ids, self.bridge.log.emit)

                # verify
                self.bridge.status.emit("verifying...")
                local_map = discover_local_mods(mods_root)
                payload = {"steamid": steamid, "client_manifest_hash": "", "mods": [{"id": k, "hash": v} for k, v in local_map.items()]}
                diff2 = http_post_json(base.rstrip("/") + "/diff", payload)
                if not diff2.get("ok"):
                    raise RuntimeError(f"Verify diff failed: {diff2}")

                dl2 = diff2.get("download", []) or []
                del2 = diff2.get("delete", []) or []

                if len(dl2) == 0 and len(del2) == 0:
                    self.bridge.log.emit("[VERIFY] OK: client is up-to-date")
                    self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_update_applied"))
                    self.bridge.status.emit("idle (up-to-date)")
                    # фиксируем: этот серверный хэш реально применён
                    if self.last_diff and self.last_diff.server_manifest_hash:
                        self.cfg["last_server_manifest_hash"] = self.last_diff.server_manifest_hash

                    # сброс pending
                    self.cfg["pending_update"] = False
                    self.cfg["pending_update_hash"] = ""
                    write_config(self.cfg)

                    # auto pipeline завершён
                    if getattr(self, "_auto_pipeline_running", False):
                        self._auto_pipeline_running = False
                        self._auto_target_hash = ""
                        self._auto_download_started = False
                        self._auto_apply_started = False
                
                else:
                    self.bridge.log.emit(f"[VERIFY] NOT OK: download={len(dl2)} delete={len(del2)}")
                    self.bridge.toast.emit(self.tr("toast_title"), self.tr("toast_verify_failed"))
                    self.bridge.status.emit("needs update (verify failed)")

                # update UI
                self.local_mods_map = local_map
                diff2["_context"] = "internal"
                self.bridge.diff_ready.emit(diff2)

            except Exception as e:
                self.bridge.error.emit(str(e))
            finally:
                self.fixing_extras = False
                self.bridge.set_busy.emit(False)

        threading.Thread(target=worker, daemon=True).start()


def main():
    app = QApplication(sys.argv)
    icon_path = resource_path("favicon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyleSheet(MODSYNC_QSS)
    w = ClientWindow()
    if icon_path.exists():
        w.setWindowIcon(QIcon(str(icon_path)))
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()