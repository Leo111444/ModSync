import json
import os
import socket
import sys
import threading
import time
import zipfile
import sqlite3
import subprocess
import webbrowser
import tempfile
from dataclasses import dataclass, field
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import urllib.request
import urllib.error

from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer, QFileSystemWatcher

import modsync_v2

APP_VERSION = "2.0.0"
GITHUB_REPO = "Leo111444/ModSync"

# ─── UPnP ────────────────────────────────────────────────────
def _get_gateway_ip():
    """Получить IP шлюза через route print."""
    import subprocess, re
    try:
        r = subprocess.run(["route", "print", "0.0.0.0"],
                           capture_output=True, text=True, timeout=3,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "0.0.0.0":
                ip = parts[2]
                if ip != "0.0.0.0":
                    return ip
    except Exception:
        pass
    return "192.168.0.1"


def _ssdp_find_igd(timeout=4.0):
    """
    Ищет UPnP IGD тремя способами:
    1. SSDP multicast (стандарт)
    2. SSDP unicast напрямую к шлюзу
    3. HTTP GET известных UPnP URL на шлюзе
    """
    import socket, select, urllib.request

    gw = _get_gateway_ip()

    crlf = "\r\n"
    ssdp_msg = crlf.join([
        "M-SEARCH * HTTP/1.1",
        "HOST: 239.255.255.250:1900",
        'MAN: "ssdp:discover"',
        "MX: 2",
        "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1",
        "", "",
    ]).encode("utf-8")

    targets = [
        ("239.255.255.250", 1900),  # multicast
        (gw, 1900),                  # unicast к шлюзу
    ]

    for dest_addr, dest_port in targets:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(min(timeout / 2, 1.5))
            sock.sendto(ssdp_msg, (dest_addr, dest_port))
            deadline = __import__("time").time() + timeout / 2
            while True:
                remain = deadline - __import__("time").time()
                if remain <= 0:
                    break
                r, _, _ = select.select([sock], [], [], remain)
                if not r:
                    break
                data, _ = sock.recvfrom(4096)
                text = data.decode("utf-8", errors="ignore")
                for line in text.splitlines():
                    if line.upper().startswith("LOCATION:"):
                        sock.close()
                        return line.split(":", 1)[1].strip()
            sock.close()
        except Exception:
            pass

    # Стратегия 3: прямой HTTP к известным UPnP путям на роутере
    upnp_paths = [
        "/rootDesc.xml", "/igddesc.xml", "/gatedesc.xml",
        "/upnp/IGD.xml", "/igd.xml", "/tr064desc.xml",
        "/xml/IGD1.xml", "/Public_UPNP_gatedesc.xml",
    ]
    for gw_port in [49000, 49152, 5000, 1900, 8080, 80]:
        for path in upnp_paths:
            try:
                url = "http://{0}:{1}{2}".format(gw, gw_port, path)
                req = urllib.request.Request(url, headers={"User-Agent": "UPnP/1.0"})
                with urllib.request.urlopen(req, timeout=0.8) as r:
                    data = r.read(512)
                    if b"WANIPConnection" in data or b"InternetGateway" in data or b"upnp" in data.lower():
                        return url
            except Exception:
                pass

    return None


def _upnp_get_control_url(location):
    """Parse UPnP device XML, return (control_url, service_type) or None."""
    import urllib.request, urllib.parse, xml.etree.ElementTree as ET
    try:
        with urllib.request.urlopen(location, timeout=5) as r:
            raw = r.read()
        root = ET.fromstring(raw)
        parsed = urllib.parse.urlparse(location)
        base = "{0}://{1}".format(parsed.scheme, parsed.netloc)
        for svc_el in root.iter():
            if not svc_el.tag.endswith("service"):
                continue
            st_el = next((c for c in svc_el if c.tag.endswith("serviceType")), None)
            cu_el = next((c for c in svc_el if c.tag.endswith("controlURL")), None)
            if st_el is None or cu_el is None:
                continue
            stype = (st_el.text or "").strip()
            ctrl  = (cu_el.text or "").strip()
            if "WANIPConnection" in stype or "WANPPPConnection" in stype:
                if not ctrl.startswith("http"):
                    ctrl = base + ctrl
                return ctrl, stype
    except Exception:
        pass
    return None


def _upnp_soap(control_url, service, action, args):
    """Send SOAP request to UPnP service."""
    import urllib.request, xml.etree.ElementTree as ET
    arg_xml = "".join("<{0}>{1}</{0}>".format(k, v) for k, v in args.items())
    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        "<u:{a} xmlns:u=\"{s}\">{args}</u:{a}>"
        "</s:Body></s:Envelope>"
    ).format(a=action, s=service, args=arg_xml).encode("utf-8")
    req = urllib.request.Request(
        control_url, data=body,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"{0}#{1}"'.format(service, action),
        }
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    return {el.tag.split("}")[-1]: el.text for el in root.iter()}


def upnp_add_port(port, log=None):
    """Open port via UPnP using raw SSDP + SOAP (no miniupnpc.discover)."""
    import socket

    def _log(msg):
        if log:
            log("[UPnP] " + msg)

    _log("Searching for UPnP IGD via SSDP...")
    location = _ssdp_find_igd(timeout=2.0)
    if not location:
        return False, "No UPnP router found — enable UPnP in router settings"
    _log("IGD location: " + location)

    info = _upnp_get_control_url(location)
    if not info:
        return False, "Could not parse UPnP device description"
    control_url, service_type = info
    _log("Control URL: " + control_url)
    _log("Service: " + service_type)

    try:
        resp = _upnp_soap(control_url, service_type, "GetExternalIPAddress", {})
        ext_ip = resp.get("NewExternalIPAddress") or "unknown"
        _log("External IP: " + ext_ip)
    except Exception as e:
        ext_ip = "unknown"
        _log("GetExternalIPAddress failed: " + str(e))

    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        lan_ip = "0.0.0.0"

    try:
        _upnp_soap(control_url, service_type, "DeletePortMapping", {
            "NewRemoteHost": "",
            "NewExternalPort": str(port),
            "NewProtocol": "TCP",
        })
        _log("Removed old mapping for port " + str(port))
    except Exception:
        pass

    try:
        _upnp_soap(control_url, service_type, "AddPortMapping", {
            "NewRemoteHost": "",
            "NewExternalPort": str(port),
            "NewProtocol": "TCP",
            "NewInternalPort": str(port),
            "NewInternalClient": lan_ip,
            "NewEnabled": "1",
            "NewPortMappingDescription": "ModSync Server",
            "NewLeaseDuration": "0",
        })
        _log("Port {0} mapped! External: {1}:{0}".format(port, ext_ip))
        return True, ext_ip
    except Exception as e:
        return False, "AddPortMapping failed: " + str(e)


def upnp_remove_port(port: int) -> None:
    """Remove UPnP port mapping on server stop."""
    try:
        location = _ssdp_find_igd(timeout=2.0)
        if not location:
            return
        info = _upnp_get_control_url(location)
        if not info:
            return
        control_url, service_type = info
        _upnp_soap(control_url, service_type, "DeletePortMapping", {
            "NewRemoteHost": "",
            "NewExternalPort": str(port),
            "NewProtocol": "TCP",
        })
    except Exception:
        pass

def upnp_add_port(port: int, log=None) -> tuple[bool, str]:
    """Пробрасывает порт через UPnP используя SSDP + raw SOAP (без miniupnpc.discover)."""
    import socket

    def _log(msg):
        if log:
            log(f"[UPnP] {msg}")

    _log("Searching for UPnP IGD via SSDP...")
    location = _ssdp_find_igd(timeout=2.0)
    if not location:
        return False, "No UPnP router found — enable UPnP in router settings"
    _log(f"IGD location: {location}")

    info = _upnp_get_control_url(location)
    if not info:
        return False, "Could not parse UPnP device description"
    control_url, service_type = info
    _log(f"Control URL: {control_url}")
    _log(f"Service: {service_type}")

    # Получить внешний IP
    try:
        resp = _upnp_soap(control_url, service_type, "GetExternalIPAddress", {})
        ext_ip = resp.get("NewExternalIPAddress", "unknown")
        _log(f"External IP: {ext_ip}")
    except Exception as e:
        ext_ip = "unknown"
        _log(f"GetExternalIPAddress failed: {e}")

    # Получить LAN IP
    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        lan_ip = "0.0.0.0"

    # Удалить старый маппинг
    try:
        _upnp_soap(control_url, service_type, "DeletePortMapping", {
            "NewRemoteHost": "",
            "NewExternalPort": port,
            "NewProtocol": "TCP",
        })
        _log(f"Removed old mapping for port {port}")
    except Exception:
        pass

    # Добавить маппинг
    try:
        _upnp_soap(control_url, service_type, "AddPortMapping", {
            "NewRemoteHost": "",
            "NewExternalPort": port,
            "NewProtocol": "TCP",
            "NewInternalPort": port,
            "NewInternalClient": lan_ip,
            "NewEnabled": 1,
            "NewPortMappingDescription": "ModSync Server",
            "NewLeaseDuration": 0,
        })
        _log(f"Port {port} mapped successfully!")
        return True, ext_ip
    except Exception as e:
        return False, f"AddPortMapping failed: {e}"


def upnp_remove_port(port: int) -> None:
    """Убирает маппинг при остановке сервера."""
    try:
        location = _ssdp_find_igd(timeout=2.0)
        if not location:
            return
        info = _upnp_get_control_url(location)
        if not info:
            return
        control_url, service_type = info
        _upnp_soap(control_url, service_type, "DeletePortMapping", {
            "NewRemoteHost": "",
            "NewExternalPort": str(port),
            "NewProtocol": "TCP",
        })
    except Exception:
        pass


def upnp_probe() -> tuple[bool, str]:
    """
    Быстрая проверка UPnP без открытия порта.
    Возвращает (ok, external_ip_or_error).
    """
    location = _ssdp_find_igd(timeout=3.0)
    if not location:
        return False, "Роутер с UPnP не найден. Включите UPnP в настройках роутера."
    info = _upnp_get_control_url(location)
    if not info:
        return False, "Роутер найден, но не удалось прочитать его описание."
    control_url, service_type = info
    try:
        resp = _upnp_soap(control_url, service_type, "GetExternalIPAddress", {})
        ext_ip = resp.get("NewExternalIPAddress", "")
        if ext_ip:
            return True, ext_ip
        return False, "Роутер найден, но не вернул внешний IP."
    except Exception as e:
        return False, f"Роутер найден, ошибка запроса IP: {e}"

from PyQt6.QtGui import QGuiApplication, QFont, QColor, QPalette, QIcon
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QTabWidget,
    QSlider, QProgressBar, QFrame, QCheckBox, QGroupBox
)

# -----------------------------
# Константы / дефолты
# -----------------------------

DEFAULT_MODS_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\7 Days to Die Dedicated Server\Mods"
DEFAULT_PORT = 8765

APPDATA_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ModSyncServer"
CONFIG_PATH = APPDATA_DIR / "config.json"
DB_PATH = APPDATA_DIR / "server.sqlite"


# -----------------------------
# AppData / Config / DB
# -----------------------------

def ensure_appdata_dirs() -> None:
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)


def read_config() -> dict:
    ensure_appdata_dirs()
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def write_config(cfg: dict) -> None:
    ensure_appdata_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def db_connect() -> sqlite3.Connection:
    ensure_appdata_dirs()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con



def resource_path(relative_path: str) -> str:
    """Путь к ресурсам — работает и в .py и в PyInstaller .exe."""
    import sys, os
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)

def db_init() -> None:
    con = db_connect()
    try:
        con.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            steamid TEXT PRIMARY KEY,
            last_seen INTEGER NOT NULL DEFAULT 0,
            last_ip TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'UNKNOWN',
            build_id TEXT NOT NULL DEFAULT '',
            torrent_build_id TEXT NOT NULL DEFAULT ''
        );
        """)
        # Миграция: старая схема → новая
        for col in ("build_id TEXT NOT NULL DEFAULT ''",
                    "torrent_build_id TEXT NOT NULL DEFAULT ''",
                    "p2p_enabled INTEGER NOT NULL DEFAULT -1"):
            try:
                con.execute(f"ALTER TABLE clients ADD COLUMN {col}")
            except Exception:
                pass
        for old_col in ("bytes_sent_total", "last_client_manifest_hash", "last_server_manifest_hash"):
            try:
                con.execute(f"ALTER TABLE clients DROP COLUMN {old_col}")
            except Exception:
                pass
        con.execute("""
        CREATE TABLE IF NOT EXISTS allowed_steamids (
            steamid TEXT PRIMARY KEY,
            updated_at INTEGER NOT NULL DEFAULT 0
        );
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            steamid TEXT PRIMARY KEY,
            added_at INTEGER NOT NULL DEFAULT 0,
            reason  TEXT NOT NULL DEFAULT ''
        );
        """)
        con.commit()
    finally:
        con.close()


def db_touch_client(steamid: str, ip: str, build_id: str = "", kind: str = "manifest") -> None:
    if not steamid:
        return
    now = int(time.time())
    con = db_connect()
    try:
        if kind == "torrent":
            con.execute("""
            INSERT INTO clients (steamid, last_seen, last_ip, status, build_id, torrent_build_id)
            VALUES (?, ?, ?, '', '', ?)
            ON CONFLICT(steamid) DO UPDATE SET
                last_seen=excluded.last_seen,
                last_ip=excluded.last_ip,
                torrent_build_id=excluded.torrent_build_id;
            """, (steamid, now, ip, build_id))
        else:
            con.execute("""
            INSERT INTO clients (steamid, last_seen, last_ip, status, build_id, torrent_build_id)
            VALUES (?, ?, ?, '', ?, '')
            ON CONFLICT(steamid) DO UPDATE SET
                last_seen=excluded.last_seen,
                last_ip=excluded.last_ip,
                build_id=excluded.build_id;
            """, (steamid, now, ip, build_id))
        con.commit()
    finally:
        con.close()



def db_set_client_p2p(steamid: str, ip: str, p2p_enabled: int) -> None:
    if not steamid:
        return
    now = int(time.time())
    con = db_connect()
    try:
        con.execute("""
        INSERT INTO clients (steamid, last_seen, last_ip, status, build_id, torrent_build_id, p2p_enabled)
        VALUES (?, ?, ?, '', '', '', ?)
        ON CONFLICT(steamid) DO UPDATE SET
            last_seen=excluded.last_seen,
            last_ip=excluded.last_ip,
            p2p_enabled=excluded.p2p_enabled;
        """, (steamid, now, ip, p2p_enabled))
        con.commit()
    finally:
        con.close()


def db_list_clients(limit: int = 500) -> list[dict]:
    con = db_connect()
    try:
        cur = con.execute("""
            SELECT steamid, last_seen, last_ip, status, build_id, torrent_build_id,
                   COALESCE(p2p_enabled, -1)
            FROM clients
            ORDER BY last_seen DESC
            LIMIT ?;
        """, (int(limit),))
        rows = cur.fetchall()
        return [{"steamid": r[0], "last_seen": r[1], "last_ip": r[2],
                 "status": r[3], "build_id": r[4], "torrent_build_id": r[5],
                 "p2p_enabled": r[6]} for r in rows]
    finally:
        con.close()

def db_list_allowed_steamids(limit: int = 5000) -> list[str]:
    con = db_connect()
    try:
        cur = con.execute("""
            SELECT steamid
              FROM allowed_steamids
             ORDER BY steamid ASC
             LIMIT ?;
        """, (int(limit),))
        return [r[0] for r in cur.fetchall()]
    finally:
        con.close()

def db_replace_allowed_steamids(steamids: list[str], updated_at: int) -> None:
    con = db_connect()
    try:
        con.execute("DELETE FROM allowed_steamids;")
        con.executemany(
            "INSERT INTO allowed_steamids (steamid, updated_at) VALUES (?, ?);",
            [(s, int(updated_at)) for s in steamids]
        )
        con.commit()
    finally:
        con.close()


def db_is_allowed_steamid(steamid: str) -> bool:
    if not steamid:
        return False
    con = db_connect()
    try:
        cur = con.execute("SELECT 1 FROM allowed_steamids WHERE steamid = ? LIMIT 1;", (steamid,))
        return cur.fetchone() is not None
    finally:
        con.close()


def db_allowed_stats() -> dict:
    con = db_connect()
    try:
        cur = con.execute("SELECT COUNT(*) FROM allowed_steamids;")
        count = int(cur.fetchone()[0])
        cur2 = con.execute("SELECT MAX(updated_at) FROM allowed_steamids;")
        last = cur2.fetchone()[0]
        last = int(last) if last is not None else 0
        return {"count": count, "updated_at": last}
    finally:
        con.close()

# -----------------------------
# Порт / сеть
# -----------------------------

def is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, preferred: int) -> int:
    candidates = [preferred, preferred + 1, preferred + 2, preferred + 3, preferred + 4]
    for p in candidates:
        if is_port_free(host, p):
            return p
    for p in range(20000, 21000):
        if is_port_free(host, p):
            return p
    return preferred


# -----------------------------
# Хэши / манифест
# -----------------------------

def sha256_file(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def build_manifest(mods_root: Path, tracked_mods: set[str] | None = None) -> dict:
    now_ts = int(time.time())
    mods_list = []

    if not mods_root.exists() or not mods_root.is_dir():
        manifest = {
            "manifest_version": 1,
            "generated_at": now_ts,
            "mods_root": str(mods_root),
            "mods": [],
            "note": "Mods path not found or not a directory"
        }
        manifest["manifest_hash"] = sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()
        return manifest

    for entry in mods_root.iterdir():
        if not entry.is_dir():
            continue
        modinfo = entry / "ModInfo.xml"
        if not modinfo.exists():
            continue

        mod_id = entry.name

        if tracked_mods is not None and mod_id not in tracked_mods:
            continue

        try:
            mod_hash, size_bytes, files_count = compute_mod_hash(entry)
        except Exception:
            mod_hash, size_bytes, files_count = "ERROR", 0, 0

        try:
            modinfo_hash = sha256_file(modinfo)
        except Exception:
            modinfo_hash = "ERROR"

        mods_list.append({
            "id": mod_id,
            "hash": mod_hash,
            "size_bytes": size_bytes,
            "files_count": files_count,
            "modinfo_hash": modinfo_hash,
        })

    mods_list.sort(key=lambda x: x["id"].lower())

    manifest = {
        "manifest_version": 1,
        "generated_at": now_ts,
        "mods_root": str(mods_root),
        "mods": mods_list,
    }
    manifest["manifest_hash"] = sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()
    return manifest


def discover_mod_ids(mods_root: Path) -> list[str]:
    """
    Возвращает список всех ModName, где есть Mods/<ModName>/ModInfo.xml
    """
    out = []
    if not mods_root.exists() or not mods_root.is_dir():
        return out

    for entry in mods_root.iterdir():
        if entry.is_dir() and (entry / "ModInfo.xml").exists():
            out.append(entry.name)

    out.sort(key=lambda x: x.lower())
    return out


# -----------------------------
# Autoruns defs
# -----------------------------

def _task_name_for_port(port: int) -> str:
    return f"ModSync_{port}"


def _build_task_command() -> str:
    """
    Что запускать в планировщике:
      - если упаковано в EXE (PyInstaller): запускаем sys.executable
      - иначе: запускаем python.exe + путь до server_app.py
    """
    exe = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        # уже EXE
        return f'"{exe}"'
    else:
        script = Path(__file__).resolve()
        return f'"{exe}" "{script}"'


_AUTOSTART_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def task_exists(task_name: str) -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY) as k:
            winreg.QueryValueEx(k, task_name)
            return True
    except FileNotFoundError:
        return False


def create_autostart_task(task_name: str, command: str) -> tuple[bool, str]:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY,
                            access=winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, task_name, 0, winreg.REG_SZ, command)
        return True, "ok"
    except Exception as e:
        return False, str(e)


def delete_autostart_task(task_name: str) -> tuple[bool, str]:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY,
                            access=winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, task_name)
        return True, "ok"
    except FileNotFoundError:
        return True, "ok"
    except Exception as e:
        return False, str(e)

# -----------------------------
def db_blacklist_add(steamid: str, reason: str = "") -> None:
    con = db_connect()
    try:
        con.execute(
            "INSERT OR REPLACE INTO blacklist (steamid, added_at, reason) VALUES (?, ?, ?)",
            (steamid, int(time.time()), reason)
        )
        con.commit()
    finally:
        con.close()


def db_blacklist_remove(steamid: str) -> None:
    con = db_connect()
    try:
        con.execute("DELETE FROM blacklist WHERE steamid=?", (steamid,))
        con.commit()
    finally:
        con.close()


def db_blacklist_contains(steamid: str) -> bool:
    con = db_connect()
    try:
        row = con.execute("SELECT 1 FROM blacklist WHERE steamid=?", (steamid,)).fetchone()
        return row is not None
    finally:
        con.close()


def db_blacklist_list() -> list[dict]:
    con = db_connect()
    try:
        rows = con.execute(
            "SELECT steamid, added_at, reason FROM blacklist ORDER BY added_at DESC"
        ).fetchall()
        return [{"steamid": r[0], "added_at": r[1], "reason": r[2]} for r in rows]
    finally:
        con.close()


# Cache utils
# -----------------------------

def read_server_name_from_config(mods_path: str) -> str | None:
    """Ищет serverconfig.xml рядом с папкой Mods и возвращает значение ServerName."""
    import xml.etree.ElementTree as ET
    candidates = [
        Path(mods_path).parent / "serverconfig.xml",
        Path(mods_path).parent.parent / "serverconfig.xml",
    ]
    for cfg_path in candidates:
        if not cfg_path.exists():
            continue
        try:
            root = ET.parse(cfg_path).getroot()
            for prop in root.iter("property"):
                if prop.get("name") == "ServerName":
                    val = (prop.get("value") or "").strip()
                    if val:
                        return val
        except Exception:
            pass
    return None


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


def fetch_allowed_steamids_from_site(auth_url: str, auth_key: str, timeout_sec: int = 6) -> dict:
    """
    Дёргает твой сайт: GET /api/modsync/allowed_steamids
    Header: X-ModSync-Key: <auth_key>
    Возвращает dict: {"ok": True/False, "steamids": [...], "updated_at": int, "error": "..."}
    """
    if not auth_url:
        return {"ok": False, "error": "auth_url is empty"}

    req = urllib.request.Request(auth_url, method="GET")
    if auth_key:
        req.add_header("X-ModSync-Key", auth_key)

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
            data = json.loads(raw.decode("utf-8", errors="replace"))
            if not isinstance(data, dict) or not data.get("ok"):
                return {"ok": False, "error": f"bad response: {data}"}

            steamids = data.get("steamids", [])
            updated_at = int(data.get("updated_at", int(time.time())))

            # нормализуем
            if not isinstance(steamids, list):
                return {"ok": False, "error": "steamids is not a list"}
            steamids = [str(x).strip() for x in steamids if str(x).strip()]

            return {"ok": True, "steamids": steamids, "updated_at": updated_at}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"URL error: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# -----------------------------
# Состояние / Bridges
# -----------------------------

@dataclass
class ServerState:
    manifest: dict
    lock: threading.Lock = field(default_factory=threading.Lock)
    cache_cfg: dict = field(default_factory=dict)
    tracked_mods: set[str] = field(default_factory=set)
    v2: object = None                # modsync_v2.V2State
    publish_trigger: object = None   # колбэк ServerWindow.trigger_publish

    def get_manifest(self) -> dict:
        with self.lock:
            return self.manifest

    def set_manifest(self, m: dict) -> None:
        with self.lock:
            self.manifest = m

    def get_mods_root(self) -> Path:
        m = self.get_manifest()
        return Path(m.get("mods_root", DEFAULT_MODS_PATH))

    def get_mod_map(self) -> dict:
        m = self.get_manifest()
        out = {}
        for mod in m.get("mods", []):
            if isinstance(mod, dict) and mod.get("id"):
                out[mod["id"]] = mod
        return out


class LogBridge(QObject):
    log_signal = pyqtSignal(str)


class ManifestBridge(QObject):
    manifest_ready = pyqtSignal(dict)


class PublishProgressBridge(QObject):
    progress = pyqtSignal(int, int)   # (piece_idx, num_pieces)
    done = pyqtSignal()


# -----------------------------
# HTTP handler
# -----------------------------

class ApiHandler(BaseHTTPRequestHandler):
    state: ServerState = None
    log: LogBridge = None

    def _log(self, msg: str) -> None:
        if self.log:
            self.log.log_signal.emit(msg)

    def _send_json(self, data: dict, code: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except Exception:
            length = 0
        if length <= 0:
            return None
        body = self.rfile.read(length)
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return None

    def _get_client_ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _get_cfg(self) -> dict:
        # state.cache_cfg уже есть; добавим сюда auth_cfg
        # хранится в state.cache_cfg["auth"] (мы добавим в GUI/State)
        return self.state.cache_cfg.get("auth", {})

    def require_auth(self, steamid: str) -> tuple[bool, str]:
        """
        Режим Open:      принимаем любой непустой steamid, если не в blacklist
        Режим Whitelist: текущее поведение с внешним эндпоинтом
        Blacklist проверяется в обоих режимах всегда.
        """
        cfg = self._get_cfg()

        if not steamid:
            return False, "steamid is required"

        # Blacklist — проверяем всегда, в любом режиме
        if db_blacklist_contains(steamid):
            return False, "steamid is blacklisted"

        auth_mode = str(cfg.get("auth_mode", "whitelist")).strip().lower()

        # ── OPEN MODE ──────────────────────────────────────
        if auth_mode == "open":
            return True, "open mode"

        # ── WHITELIST MODE ─────────────────────────────────
        fail_open = bool(cfg.get("auth_fail_open", False))

        if db_is_allowed_steamid(steamid):
            return True, "ok"

        auth_url = str(cfg.get("auth_url", "")).strip()
        auth_key = str(cfg.get("auth_key", "")).strip()

        r = fetch_allowed_steamids_from_site(auth_url, auth_key)
        if r.get("ok"):
            db_replace_allowed_steamids(r["steamids"], int(r["updated_at"]))
            if db_is_allowed_steamid(steamid):
                return True, "ok_after_refresh"
            return False, "steamid not allowed"
        else:
            if fail_open:
                return True, f"fail_open ({r.get('error')})"
            return False, f"auth refresh failed: {r.get('error')}"

    def log_message(self, fmt, *args):
        msg = fmt % args
        # /announce шумит каждые 30 мин от каждого пира — отправляем в TRACKER (приглушённый цвет)
        if "/announce" in self.path:
            self._log(f"[TRACKER] {self.address_string()} - {msg}")
        else:
            self._log(f"[HTTP] {self.address_string()} - {msg}")

    def do_GET(self):
        parsed = urlparse(self.path)

        if self.state.v2 is not None:
            if modsync_v2.handle_v2_get(
                self, parsed, self.state.v2,
                self.state.get_mods_root(), self.require_auth,
                touch_client_cb=lambda sid, ip, kind="manifest": db_touch_client(
                    sid, ip,
                    (self.state.v2.build.snapshot().get("build_id", "") if self.state.v2 else ""),
                    kind,
                ),
            ):
                return

        self._send_json({"ok": False, "error": "Not found"}, code=404)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/v2/heartbeat":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                steamid = str(body.get("steamid", "")).strip()
                p2p = int(bool(body.get("p2p", False)))
                ip = self._get_client_ip()
                if steamid:
                    db_set_client_p2p(steamid, ip, p2p)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, code=400)
            return

        if self.state.v2 is not None and self.state.publish_trigger is not None:
            if modsync_v2.handle_v2_post(self, parsed, self.state.v2,
                                          self.state.publish_trigger):
                return

        self._send_json({"ok": False, "error": "Not found"}, code=404)


class QuietHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class HttpServerThread(threading.Thread):
    def __init__(self, host: str, port: int, state: ServerState, log: LogBridge):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.state = state
        self.log = log
        self.httpd: QuietHTTPServer | None = None

    def run(self):
        try:
            self.httpd = QuietHTTPServer((self.host, self.port), ApiHandler)
            # Disable Nagle's algorithm — send data immediately without buffering
            self.httpd.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # Increase send buffer to 8 MB for fast file transfers
            self.httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 * 1024 * 1024)
            self.httpd.RequestHandlerClass.state = self.state
            self.httpd.RequestHandlerClass.log = self.log
            self.log.log_signal.emit(f"[SERVER] HTTP server started on http://{self.host}:{self.port}")
            self.httpd.serve_forever(poll_interval=0.1)  # was 0.5 — faster connection acceptance
        except Exception as e:
            self.log.log_signal.emit(f"[SERVER] ERROR: {e}")

    def stop(self):
        if self.httpd:
            self.log.log_signal.emit("[SERVER] Stopping HTTP server...")
            self.httpd.shutdown()
            self.httpd.server_close()
            self.log.log_signal.emit("[SERVER] HTTP server stopped.")


# -----------------------------
# GUI
# -----------------------------

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
QLineEdit:disabled {
    color: rgba(255,255,255,0.25);
    border-color: rgba(255,255,255,0.04);
}
/* ── Buttons base ── */
QPushButton {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px;
    padding: 5px 14px;
    color: #dde3f0;
    min-height: 28px;
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
/* ── Start / Stop ── */
QPushButton#start_btn {
    background: rgba(34,197,94,0.12);
    border-color: rgba(34,197,94,0.40);
    color: #4ade80;
    font-weight: 700;
    min-width: 90px;
}
QPushButton#start_btn:hover {
    background: rgba(34,197,94,0.20);
    border-color: #4ade80;
}
QPushButton#start_btn:disabled {
    background: rgba(34,197,94,0.03);
    border-color: rgba(34,197,94,0.08);
    color: rgba(74,222,128,0.18);
}
QPushButton#stop_btn {
    background: rgba(239,68,68,0.12);
    border-color: rgba(239,68,68,0.40);
    color: #f87171;
    font-weight: 700;
    min-width: 90px;
}
QPushButton#stop_btn:hover {
    background: rgba(239,68,68,0.20);
    border-color: #f87171;
}
QPushButton#stop_btn:disabled {
    background: rgba(239,68,68,0.03);
    border-color: rgba(239,68,68,0.08);
    color: rgba(248,113,113,0.18);
}
/* ── Accent (publish) ── */
QPushButton#accent_btn {
    background: rgba(99,102,241,0.15);
    border-color: rgba(99,102,241,0.45);
    color: #a5b4fc;
    font-weight: 600;
}
QPushButton#accent_btn:hover {
    background: rgba(99,102,241,0.25);
    border-color: #a5b4fc;
}
QPushButton#accent_btn:disabled {
    background: rgba(99,102,241,0.04);
    border-color: rgba(99,102,241,0.10);
    color: rgba(165,180,252,0.20);
}
/* ── Scan / utility blue ── */
QPushButton#scan_btn {
    background: rgba(56,189,248,0.10);
    border-color: rgba(56,189,248,0.30);
    color: #7dd3fc;
}
QPushButton#scan_btn:hover {
    background: rgba(56,189,248,0.18);
    border-color: #7dd3fc;
}
/* ── Mode buttons (Open / Whitelist) ── */
QPushButton#mode_btn {
    background: rgba(255,255,255,0.04);
    border-color: rgba(255,255,255,0.08);
    color: #5a6070;
    font-weight: 500;
    min-width: 100px;
}
QPushButton#mode_btn:hover {
    border-color: rgba(99,102,241,0.40);
    color: #dde3f0;
}
QPushButton#mode_btn:checked {
    background: rgba(99,102,241,0.18);
    border: 1.5px solid rgba(99,102,241,0.65);
    color: #a5b4fc;
    font-weight: 600;
}
/* ── Helper buttons (?) ── */
QPushButton#help_btn {
    background: rgba(56,189,248,0.10);
    border: 1px solid rgba(56,189,248,0.30);
    border-radius: 11px;
    color: #7dd3fc;
    font-weight: 700;
    font-size: 12px;
    min-width: 22px;
    max-width: 22px;
    min-height: 22px;
    max-height: 22px;
    padding: 0;
}
QPushButton#help_btn:hover {
    background: rgba(56,189,248,0.22);
    border-color: #7dd3fc;
}
/* ── Update notification ── */
QPushButton#update_btn {
    background: rgba(251,191,36,0.12);
    border-color: rgba(251,191,36,0.35);
    color: #fbbf24;
    font-weight: 600;
    font-size: 11px;
    padding: 3px 10px;
    min-height: 22px;
}
QPushButton#update_btn:hover {
    background: rgba(251,191,36,0.22);
    border-color: #fbbf24;
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
        stop:0 #6366f1, stop:1 #a5b4fc);
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
/* ── Tabs ── */
QTabWidget::pane {
    border: none;
    background: transparent;
}
QTabWidget > QStackedWidget {
    background: transparent;
}
QTabWidget > QStackedWidget > QWidget {
    background: transparent;
}
QTabBar {
    background: transparent;
}
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
/* ── Status bar ── */
QFrame#statusbar {
    background: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    min-height: 32px;
    max-height: 32px;
    margin: 0 0 8px 0;
}
/* ── Status bar small buttons ── */
QPushButton#sb_btn {
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 6px;
    padding: 2px 10px;
    color: #6c7086;
    min-height: 22px;
    max-height: 22px;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#sb_btn:hover {
    background: rgba(255,255,255,0.10);
    border-color: rgba(99,102,241,0.40);
    color: #dde3f0;
}
"""
DARK_STYLE = GLASS_STYLE  # backward compat alias





class StatusLight(QLabel):
    """Цветной индикатор — кружок зелёный/красный/жёлтый."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.set_state("stopped")

    def set_state(self, state: str):
        colors = {
            "running": "#3fb950",
            "stopped": "#f85149",
            "scanning": "#e3af4c",
        }
        c = colors.get(state, "#4a5270")
        self.setStyleSheet(
            f"background-color: {c}; border-radius: 7px;"
            f"border: 1px solid rgba(255,255,255,0.15);"
        )
        self.setToolTip(state.capitalize())


class ToggleSwitch(QWidget):
    """iOS-style toggle switch emitting toggled(bool)."""
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

    def blockSignals(self, block: bool) -> bool:
        return super().blockSignals(block)

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
        # track: off=#2c3044 → on=#22c55e
        r = int(0x2c + (0x22 - 0x2c) * t)
        g = int(0x30 + (0xc5 - 0x30) * t)
        b = int(0x44 + (0x5e - 0x44) * t)
        p.setBrush(QColor(r, g, b))
        p.setPen(Qt.PenStyle.NoPen)
        track_h = h - 4
        p.drawRoundedRect(0, 2, w, track_h, track_h // 2, track_h // 2)
        # knob: diameter fits track with 2px margin top/bottom
        knob_d = track_h - 4
        knob_y = (h - knob_d) // 2
        knob_x = int(2 + t * (w - 4 - knob_d))
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(knob_x, knob_y, knob_d, knob_d)
        p.end()


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


# ─── LOCALISATION (EN / RU) ──────────────────────────────────
TRANSLATIONS = {
    "en": {
        "window_title":         "ModSync Server",
        # Groups
        "grp_server":           "Server Settings",
        "grp_build":            "Build & Publish",
        "grp_cache":            "Cache Policy",
        "grp_access":           "Access Control",
        # Labels
        "lbl_mods_path":        "Mods path:",
        "lbl_port":             "Port:",
        "lbl_status_stopped":   "Stopped",
        "lbl_status_running":   "Running  ·  {ip}  📋",
        "lbl_status_tip":       "Click to copy IP to clipboard",
        "lbl_cache_max":        "Max (GB):",
        "lbl_keep_ver":         "Keep versions:",
        "lbl_ttl":              "Bundle TTL (days):",
        "lbl_mode":             "Mode:",
        "lbl_auth_url":         "Auth URL:",
        "lbl_auth_key":         "Auth Key:",
        "lbl_auth_hint_open":   "Any non-empty SteamID is allowed",
        "lbl_auth_hint_wl":     "Only SteamIDs from whitelist are allowed",
        "lbl_auth_status":      "Auth: …",
        "lbl_clients":          "Connected Clients",
        "lbl_whitelist":        "Whitelist — Allowed SteamIDs",
        "lbl_mods_summary":     "Detected: {total}  ·  Selected: {sel}  ·  {size}",
        "lbl_blacklist":        "Blacklist — Blocked SteamIDs",
        # Buttons
        "btn_select":           "Select",
        "btn_select_tip":       "Select mods folder",
        "btn_auto_port":        "Auto",
        "btn_start":            "Start",
        "btn_stop":             "Stop",
        "btn_autostart_on":     "Autostart: ON",
        "btn_autostart_off":    "Autostart: OFF",
        "btn_upnp_off":         "UPnP: OFF",
        "btn_upnp_on":          "UPnP: ON",
        "btn_upnp_failed":      "UPnP: ERR",
        "btn_upnp_tip":         "Auto port-forward via UPnP.\nRequires UPnP-enabled router.",
        "btn_upnp_fail_tip":    "Could not open port {port} automatically.\nForward it manually in router settings:\n  Protocol: TCP\n  External port: {port}\n  Internal IP: {ip}\n  Internal port: {port}",
        "btn_upnp_ok_tip":      "UPnP active — external {ip}:{port}",
        "btn_apply_cache":      "Apply",
        "btn_scan":             "Refresh",
        "btn_scan_tip":         "Rescan the Mods folder and update the file list.\nHappens automatically on file change.",
        "btn_publish":          "Publish",
        "btn_publish_tip":      "Publish the current Mods snapshot as a new build for clients to download.",
        "btn_auto_publish":     "Auto-publish",
        "tip_auto_publish":     "ON: changes in Mods folder are published automatically (5 sec delay).\nOFF: manual publish only.",
        "lbl_build":            "Build:",
        "lbl_listing":          "Public listing",
        "tip_listing":          "Register this server on the master server (bar7dtd.ru) so clients can find it automatically.\nIn Whitelist mode the server is visible but mod exchange works only with your authorised players.",
        "lbl_auto_publish":     "Auto-publish",
        "lbl_server_name":      "Listing name:",
        "tip_server_name":      "Name shown in the public server list. Leave empty to use serverconfig.xml.",
        "lbl_port":             "Port:",
        "tip_port":             "HTTP port the server listens on. Players connect to this port.",
        "lbl_auto_port":        "Auto",
        "lbl_auto_port_colon":  "Auto:",
        "tip_auto_port":        "ON: pick a random free port automatically.\nOFF: use the port entered above.",
        "lbl_seed_stats":       "peers: {peers} · sent: {mb:.1f} MB · {kb:.0f} KB/s",
        "lbl_log_title":        "Server Log",
        "btn_clear_log":        "Clear",
        "log_auto_pub_on":      "[APP] Auto-publish enabled",
        "log_auto_pub_off":     "[APP] Auto-publish disabled",
        "btn_mode_open":        "Open",
        "tip_mode_open":        "Anyone with a Steam account can download mods.",
        "btn_mode_wl":          "Whitelist",
        "tip_mode_wl":          "Only SteamIDs on your whitelist can download mods.",
        "btn_instr":            "Whitelist setup guide",
        "btn_sync_auth":        "Sync SteamIDs",
        "tip_sync_auth":        "Fetch the whitelist from Auth URL right now.",
        "btn_copy_steamid":     "Copy SteamID",
        "btn_ban":              "Ban",
        "btn_ban_tip":          "Add selected SteamID to blacklist",
        "btn_unban":            "Unban",
        "btn_copy_allowed":     "Copy SteamID",
        "btn_mods_all":         "All",
        "btn_mods_none":        "None",
        "btn_mods_apply":       "Apply",
        "btn_lang":             "RU",
        "btn_tooltips_on":      "Hints: ON",
        "btn_tooltips_off":     "Hints: OFF",
        "tip_tooltips":         "Show/hide button tooltips.",
        "lbl_update":           "Update {v} available",
        "btn_download_update":  "↗ Download",
        # Tabs
        "tab_clients":          "Clients",
        "tab_whitelist":        "Whitelist",
        "tab_mods":             "Mods",
        "tab_blacklist":        "Blacklist",
        "tab_logs":             "Logs",
        # Table headers
        "th_steamid":           "SteamID",
        "th_last_seen":         "Last Seen",
        "th_ip":                "IP",
        "th_status":            "Status",
        "th_build":             "Build",
        "th_p2p":               "P2P",
        "th_mod_name":          "Mod Name",
        "th_files":             "Files",
        "th_size":              "Size",
        "th_banned_at":         "Banned At",
        # Placeholders
        "ph_auth_url":          "https://yoursite.com/api/modsync/allowed_steamids",
        "ph_auth_key":          "secret key…",
        "ph_ban_steamid":       "SteamID…",
        # Misc
        "lbl_cache_info":       "Used: {used} / {total} ({cnt} files)",
        "upnp_opening":         "[UPnP] Opening port {port} via UPnP…",
        "upnp_ok":              "[UPnP] ✅ Port {port} opened! External IP: {ip}",
        "upnp_players":         "[UPnP] Players can connect: {ip}:{port}",
        "upnp_fail":            "[UPnP] ⚠️ Failed: {reason}",
        "upnp_manual":          "[UPnP] Forward port {port} TCP manually in router settings.",
        "upnp_removed":         "[UPnP] Port mapping removed.",
        "upnp_enabled_log":     "[UPnP] Enabled — port will be forwarded on next server start.",
        "upnp_disabled_log":    "[UPnP] Disabled.",
        "upnp_restore_tip":     "Auto port-forward via UPnP.\nRequires UPnP-enabled router.",
        # Tooltips (not in retranslate yet)
        "tip_mods_path":        "Path to the game server Mods folder.",
        "tip_serverconfig":     "Select serverconfig.xml to read the server name automatically.",
        "tip_start":            "Start the HTTP server and begin serving mods.",
        "tip_stop":             "Stop the server.",
        "tip_autostart":        "Automatically start the server when Windows boots.",
        "tip_ms_help":          "What is the master server?",
        "tip_copy_steamid":     "Copy the selected player's SteamID to clipboard.",
        "tip_copy_allowed":     "Copy the selected SteamID from whitelist.",
        "tip_bl_add":           "Add SteamID to blacklist.",
        "tip_bl_remove":        "Remove selected SteamID from blacklist.",
        "tip_instr":            "Open the whitelist setup guide.",
        # Master server dialog
        "dlg_ms_help_title":    "ModSync Master Server",
        "dlg_ms_help_text": (
            "<b>What is the master server?</b><br><br>"
            "The master server is a central registry of ModSync servers at bar7dtd.ru.<br><br>"
            "<b>What public listing gives you:</b><br>"
            "• ModSync clients can find your server automatically — no manual address entry needed.<br>"
            "• Your server participates in cross-seeding: clients from other servers "
            "can download shared mod files from your players, reducing your bandwidth load.<br><br>"
            "<b>What is sent to the master server:</b><br>"
            "• Server name, IP address and ModSync port.<br>"
            "• build_id and a list of file root-hashes (no file contents).<br>"
            "• Heartbeat every 5 minutes (online/offline status).<br><br>"
            "<b>Privacy:</b> if listing is disabled — no data is transmitted "
            "and your server is not visible in the list."
        ),
        # Build status
        "v2_publishing":        "publishing…",
        "v2_not_published":     "not published",
        "v2_has_changes":       "{build_id} · pending changes",
        "v2_published":         "{build_id} · {fc} files · published",
        # Restart watch
        "lbl_wl_hint_listing":  "ℹ Whitelist mode: visible in the list, but mods shared only with authorised players",
        "lbl_restart_watch":    "Track server restart",
        "tip_restart_watch":    (
            "Watch 7DaysToDieServer.exe PID every 30 sec.\n"
            "When PID changes (server restarted) — republish the build after 10 sec\n"
            "so players always get up-to-date mods before joining."
        ),
        "log_rw_on":            "[APP] Server restart tracking enabled",
        "log_rw_off":           "[APP] Server restart tracking disabled",
        "log_rw_restarted":     "[APP] Server restarted (new PID: {pid}) — republishing in 10 sec…",
        "log_rw_republish":     "[APP] Auto-republish triggered after server restart",
        "dlg_close_title":      "Stop server?",
        "dlg_close_text":       "The HTTP server is currently running. Clients will not be able to sync.\n\nStop the server and close the application?",
        "lbl_status_tip_ports": "\nHTTP: {http}  |  P2P (torrent): {p2p}",
        "gh_checking":          "GitHub: …",
        "gh_uptodate":          "GitHub: ✓ up to date",
        "gh_update":            "GitHub: update {v}",
        "log_ext_ip":           "[APP] External IP: {ip}",
    },
    "ru": {
        "window_title":         "ModSync Server",
        "grp_server":           "Настройки сервера",
        "grp_build":            "Сборка и публикация",
        "grp_cache":            "Кэш",
        "grp_access":           "Контроль доступа",
        "lbl_mods_path":        "Папка модов:",
        "tip_mods_path":        "Путь к папке Mods игрового сервера.",
        "btn_select":           "Выбрать",
        "btn_select_tip":       "Выбрать папку с модами",
        "lbl_port":             "Порт:",
        "tip_port":             "HTTP-порт, на котором слушает сервер. Игроки подключаются к этому порту.",
        "lbl_auto_port":        "Авто",
        "tip_auto_port":        "ВКЛ: случайный свободный порт при каждом старте.\nВЫКЛ: использовать порт из поля выше.",
        "lbl_status_stopped":   "Остановлен",
        "lbl_status_running":   "Работает  ·  {ip}  📋",
        "lbl_status_tip":       "Нажмите чтобы скопировать IP",
        "lbl_cache_max":        "Максимум (ГБ):",
        "lbl_keep_ver":         "Хранить версии:",
        "lbl_ttl":              "Хранение сборок (дней):",
        "lbl_mode":             "Режим:",
        "lbl_auth_url":         "Auth URL:",
        "lbl_auth_key":         "Auth Key:",
        "lbl_auth_hint_open":   "Разрешён любой непустой SteamID",
        "lbl_auth_hint_wl":     "Разрешены только SteamID из вайтлиста",
        "lbl_auth_status":      "Авторизация: …",
        "lbl_clients":          "Подключённые клиенты",
        "lbl_whitelist":        "Вайтлист — разрешённые SteamID",
        "lbl_mods_summary":     "Обнаружено: {total}  ·  Выбрано: {sel}  ·  {size}",
        "lbl_blacklist":        "Чёрный список — заблокированные SteamID",
        "btn_auto_port":        "Авто",
        "btn_start":            "Старт",
        "btn_stop":             "Стоп",
        "btn_autostart_on":     "Автозапуск: ВКЛ",
        "btn_autostart_off":    "Автозапуск: ВЫКЛ",
        "btn_upnp_off":         "UPnP: ВЫКЛ",
        "btn_upnp_on":          "UPnP: ВКЛ",
        "btn_upnp_failed":      "UPnP: ERR",
        "btn_upnp_tip":         "Автоматически пробросить порт через UPnP.\nТребует роутер с включённым UPnP.",
        "btn_upnp_fail_tip":    "Не удалось автоматически пробросить порт {port}.\nОткройте порт вручную в настройках роутера:\n  Протокол: TCP\n  Внешний порт: {port}\n  Внутренний IP: {ip}\n  Внутренний порт: {port}",
        "btn_upnp_ok_tip":      "UPnP активен — внешний адрес {ip}:{port}",
        "btn_apply_cache":      "Применить",
        "btn_scan":             "Обновить",
        "btn_scan_tip":         "Перечитать папку модов и обновить список файлов.\nПроисходит автоматически при изменении файлов.",
        "btn_publish":          "Публикация",
        "btn_publish_tip":      "Опубликовать текущий снапшот модов как новую сборку для скачивания клиентами.",
        "btn_auto_publish":     "Автопубликация",
        "tip_auto_publish":     "ВКЛ: изменения в папке модов публикуются автоматически через 5 сек.\nВЫКЛ: публикация только вручную.",
        "lbl_build":            "Сборка:",
        "lbl_listing":          "Публичный листинг",
        "tip_listing":          "Зарегистрировать сервер на мастер-сервере (bar7dtd.ru), чтобы клиенты могли найти его автоматически.\nВ режиме Вайтлист сервер виден в списке, но обмен модами работает только с авторизованными игроками.",
        "lbl_auto_publish":     "Автопубликация",
        "lbl_server_name":      "Имя в листинге:",
        "tip_server_name":      "Имя, отображаемое в публичном списке серверов. Оставьте пустым для чтения из serverconfig.xml.",
        "lbl_auto_port_colon":  "Авто:",
        "lbl_seed_stats":       "пиров: {peers} · отдано: {mb:.1f} МБ · {kb:.0f} КБ/с",
        "lbl_log_title":        "Лог сервера",
        "btn_clear_log":        "Очистить",
        "log_auto_pub_on":      "[APP] Автопубликация включена",
        "log_auto_pub_off":     "[APP] Автопубликация выключена",
        "btn_mode_open":        "Общий",
        "tip_mode_open":        "Все игроки со Steam-аккаунтом могут скачивать моды.",
        "btn_mode_wl":          "Вайтлист",
        "tip_mode_wl":          "Только SteamID из вашего вайтлиста могут скачивать моды.",
        "btn_instr":            "Настройка вайтлиста",
        "btn_sync_auth":        "Синхронизировать",
        "tip_sync_auth":        "Загрузить вайтлист с Auth URL прямо сейчас.",
        "btn_copy_steamid":     "Копировать SteamID",
        "btn_ban":              "Бан",
        "btn_ban_tip":          "Добавить SteamID в чёрный список",
        "btn_unban":            "Разбан",
        "btn_copy_allowed":     "Копировать SteamID",
        "btn_mods_all":         "Все",
        "btn_mods_none":        "Ничего",
        "btn_mods_apply":       "Применить",
        "btn_lang":             "EN",
        "btn_tooltips_on":      "Подсказки: ВКЛ",
        "btn_tooltips_off":     "Подсказки: ВЫКЛ",
        "tip_tooltips":         "Показывать/скрывать подсказки при наведении.",
        "lbl_update":           "Доступна версия {v}",
        "btn_download_update":  "↗ Скачать",
        # Tabs
        "tab_clients":          "Клиенты",
        "tab_whitelist":        "Вайтлист",
        "tab_mods":             "Моды",
        "tab_blacklist":        "Чёрный список",
        "tab_logs":             "Логи",
        # Table headers
        "th_steamid":           "SteamID",
        "th_last_seen":         "Последний визит",
        "th_ip":                "IP",
        "th_status":            "Статус",
        "th_build":             "Сборка",
        "th_p2p":               "P2P",
        "th_mod_name":          "Мод",
        "th_files":             "Файлы",
        "th_size":              "Размер",
        "th_banned_at":         "Дата бана",
        "ph_auth_url":          "https://yoursite.com/api/modsync/allowed_steamids",
        "ph_auth_key":          "секретный ключ…",
        "ph_ban_steamid":       "SteamID…",
        "lbl_cache_info":       "Использовано: {used} / {total} ({cnt} файлов)",
        "upnp_opening":         "[UPnP] Открываем порт {port} через UPnP…",
        "upnp_ok":              "[UPnP] ✅ Порт {port} открыт! Внешний IP: {ip}",
        "upnp_players":         "[UPnP] Игроки могут подключаться: {ip}:{port}",
        "upnp_fail":            "[UPnP] ⚠️ Ошибка: {reason}",
        "upnp_manual":          "[UPnP] Пробросьте порт {port} TCP вручную в настройках роутера.",
        "upnp_removed":         "[UPnP] Проброс порта удалён.",
        "upnp_enabled_log":     "[UPnP] Включён — порт будет пробит при следующем старте сервера.",
        "upnp_disabled_log":    "[UPnP] Отключён.",
        "upnp_restore_tip":     "Автоматически пробросить порт через UPnP.\nТребует роутер с включённым UPnP.",
        # Tooltips
        "tip_mods_path":        "Путь к папке Mods игрового сервера.",
        "tip_serverconfig":     "Выбрать serverconfig.xml для автоматического чтения имени сервера.",
        "tip_start":            "Запустить HTTP-сервер и начать раздачу модов.",
        "tip_stop":             "Остановить сервер.",
        "tip_autostart":        "Автоматически запускать сервер при входе в Windows.",
        "tip_ms_help":          "Что такое мастер-сервер?",
        "tip_copy_steamid":     "Скопировать SteamID выбранного игрока в буфер обмена.",
        "tip_copy_allowed":     "Скопировать выбранный SteamID из вайтлиста.",
        "tip_bl_add":           "Добавить SteamID в чёрный список.",
        "tip_bl_remove":        "Убрать выбранный SteamID из чёрного списка.",
        "tip_instr":            "Открыть руководство по настройке вайтлиста.",
        # Master server dialog
        "dlg_ms_help_title":    "Мастер-сервер ModSync",
        "dlg_ms_help_text": (
            "<b>Что такое мастер-сервер?</b><br><br>"
            "Мастер-сервер — это центральный реестр ModSync-серверов на bar7dtd.ru.<br><br>"
            "<b>Что даёт публичный листинг:</b><br>"
            "• Клиенты ModSync смогут найти ваш сервер автоматически — "
            "без ручного ввода адреса.<br>"
            "• Ваш сервер участвует в кросс-раздаче: клиенты других серверов "
            "могут скачивать общие файлы модов у ваших игроков, снижая нагрузку на вас.<br><br>"
            "<b>Что передаётся на мастер-сервер:</b><br>"
            "• Название сервера, IP-адрес и порт ModSync.<br>"
            "• build_id и список root-хешей файлов сборки (без содержимого файлов).<br>"
            "• Heartbeat каждые 5 минут (онлайн/офлайн).<br><br>"
            "<b>Приватность:</b> если листинг выключен — никакие данные "
            "не передаются и сервер не виден в списке."
        ),
        # Build status
        "v2_publishing":        "публикация…",
        "v2_not_published":     "не опубликована",
        "v2_has_changes":       "{build_id} · есть неопубликованные изменения",
        "v2_published":         "{build_id} · {fc} файлов · опубликована",
        # Restart watch
        "lbl_wl_hint_listing":  "ℹ Whitelist: виден в списке, но раздача только авторизованным игрокам",
        "lbl_restart_watch":    "Отслеживать рестарт",
        "tip_restart_watch":    (
            "Следить за PID процесса 7DaysToDieServer.exe каждые 30 сек.\n"
            "При смене PID (рестарт сервера) — публикуем новый билд через 10 сек,\n"
            "чтобы игроки получили актуальные моды перед входом."
        ),
        "log_rw_on":            "[APP] Отслеживание рестарта сервера включено",
        "log_rw_off":           "[APP] Отслеживание рестарта сервера выключено",
        "log_rw_restarted":     "[APP] Сервер перезапущен (новый PID: {pid}) — публикация через 10 сек…",
        "log_rw_republish":     "[APP] Автоматическая публикация после рестарта сервера",
        "dlg_close_title":      "Остановить сервер?",
        "dlg_close_text":       "HTTP-сервер сейчас работает. Клиенты не смогут синхронизироваться.\n\nОстановить сервер и закрыть приложение?",
        "lbl_status_tip_ports": "\nHTTP: {http}  |  P2P (торрент): {p2p}",
        "gh_checking":          "GitHub: …",
        "gh_uptodate":          "GitHub: ✓ актуально",
        "gh_update":            "GitHub: обновление {v}",
        "log_ext_ip":           "[APP] Внешний IP: {ip}",
    },
}

class ServerWindow(QWidget):
    _gh_status_signal  = pyqtSignal(str, str)  # (text, css_color)
    _status_lbl_signal = pyqtSignal(str)       # (ip:port text)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ModSync Server")
        _ico = resource_path("favicon.ico")
        if os.path.exists(_ico):
            self.setWindowIcon(QIcon(_ico))
        self.resize(1100, 800)
        self.setMinimumWidth(900)
        self.setStyleSheet(DARK_STYLE)

        self.log_bridge = LogBridge()
        self.log_bridge.log_signal.connect(self.append_log)

        self.manifest_bridge = ManifestBridge()
        self.manifest_bridge.manifest_ready.connect(self.on_manifest_ready)

        self.pub_progress_bridge = PublishProgressBridge()
        self.pub_progress_bridge.progress.connect(self._on_publish_progress)
        self.pub_progress_bridge.done.connect(self._on_publish_done)

        cfg = read_config()
        auth_url = cfg.get("auth_url", "")
        auth_key = cfg.get("auth_key", "")
        auth_refresh_hours = int(cfg.get("auth_refresh_hours", 24))
        auth_fail_open = bool(cfg.get("auth_fail_open", False))
        auth_mode = cfg.get("auth_mode", "whitelist")

        mods_path = cfg.get("mods_path", DEFAULT_MODS_PATH)
        port = int(cfg.get("port", DEFAULT_PORT))

        tracked_mods = cfg.get("tracked_mods", None)  # None = ещё не задано

        mods_root = Path(mods_path)
        discovered = discover_mod_ids(mods_root)

        # если tracked_mods в конфиге не задан — берём все найденные (мягкий дефолт)
        if tracked_mods is None:
            tracked_set = set(discovered)
        else:
            tracked_set = set(str(x).strip() for x in tracked_mods if str(x).strip())

        self.state = ServerState(
            manifest=build_manifest(mods_root, tracked_set),
            cache_cfg={
                "auth": {
                    "auth_url": auth_url,
                    "auth_key": auth_key,
                    "auth_refresh_hours": auth_refresh_hours,
                    "auth_fail_open": auth_fail_open,
                    "auth_mode": auth_mode,
                }
            },
            tracked_mods=tracked_set
        )

        self.http_thread: HttpServerThread | None = None
        self.host = "0.0.0.0"
        self._running_ip = ""

        # ── V2: сборки + торрент ──
        self.state.v2 = modsync_v2.V2State(APPDATA_DIR)
        self.state.publish_trigger = self.trigger_publish
        self._publish_thread: threading.Thread | None = None

        # watcher + debounce
        self.fs_watcher = QFileSystemWatcher()
        self.fs_watcher.directoryChanged.connect(self.on_fs_changed)
        self.fs_watcher.fileChanged.connect(self.on_fs_changed)

        self.rescan_debounce = QTimer(self)
        self.rescan_debounce.setSingleShot(True)
        self.rescan_debounce.timeout.connect(self.on_scan_async)

        # clients table timer
        self.clients_timer = QTimer(self)
        self.clients_timer.setInterval(3000)
        self.clients_timer.timeout.connect(self.refresh_clients_table)

        # auth timer
        self.auth_timer = QTimer(self)
        self.auth_timer.setInterval(60 * 60 * 1000)
        self.auth_timer.timeout.connect(self.on_auth_tick)
        self.auth_timer.start()

        # v2: статус сборки + статистика сида + автопубликация (дебаунс)
        self.v2_timer = QTimer(self)
        self.v2_timer.setInterval(2000)
        self.v2_timer.timeout.connect(self.on_v2_tick)
        self.v2_timer.start()

        self._auto_pub_timer = QTimer(self)
        self._auto_pub_timer.setSingleShot(True)
        self._auto_pub_timer.timeout.connect(self.trigger_publish)

        # ─── BLOCK 1: Mods folder (full width) ───
        card1, c1, _ = _make_card()
        r_mods = QHBoxLayout()
        r_mods.setSpacing(8)
        self.lbl_mods_path = QLabel("Папка модов:")
        self.lbl_mods_path.setStyleSheet("color: #5a6070; font-size: 11px;")
        r_mods.addWidget(self.lbl_mods_path)
        self.mods_edit = QLineEdit(mods_path)
        self.mods_edit.setMinimumWidth(200)
        self.mods_edit.setToolTip("Путь к папке Mods игрового сервера.")
        r_mods.addWidget(self.mods_edit, 1)
        self.select_mods_btn = QPushButton("Выбрать")
        self.select_mods_btn.setObjectName("scan_btn")
        self.select_mods_btn.setToolTip("Выбрать папку с модами")
        self.select_mods_btn.clicked.connect(self.on_select_mods_folder)
        r_mods.addWidget(self.select_mods_btn)
        c1.addLayout(r_mods)

        # ─── Middle row: 3 cards ───
        mid_row = QHBoxLayout()
        mid_row.setSpacing(10)

        # ── BLOCK 2: Server settings ──
        card2, c2, self._card2_title_lbl = _make_card(self.tr("grp_server"))

        _auto_name = read_server_name_from_config(mods_path)
        _saved_name = cfg.get("server_name", "")
        _initial_name = _saved_name or _auto_name or ""
        r_sname = QHBoxLayout()
        self.lbl_sname_w = QLabel(self.tr("lbl_server_name"))
        self.lbl_sname_w.setStyleSheet("color: #5a6070; font-size: 11px;")
        r_sname.addWidget(self.lbl_sname_w)
        self.server_name_edit = QLineEdit(_initial_name)
        self.server_name_edit.setPlaceholderText("Из serverconfig.xml…")
        self.server_name_edit.setToolTip("Имя, отображаемое в публичном списке серверов.")
        self.server_name_edit.textChanged.connect(self._on_server_name_changed)
        r_sname.addWidget(self.server_name_edit, 1)
        self.select_serverconfig_btn = QPushButton("…")
        self.select_serverconfig_btn.setObjectName("scan_btn")
        self.select_serverconfig_btn.setFixedWidth(34)
        self.select_serverconfig_btn.setToolTip("Выбрать serverconfig.xml вручную")
        self.select_serverconfig_btn.clicked.connect(self.on_select_serverconfig)
        r_sname.addWidget(self.select_serverconfig_btn)
        c2.addLayout(r_sname)

        r_port = QHBoxLayout()
        r_port.setSpacing(8)
        self.lbl_port = QLabel("Порт:")
        self.lbl_port.setStyleSheet("color: #5a6070; font-size: 11px;")
        r_port.addWidget(self.lbl_port)
        self.port_edit = QLineEdit(str(port))
        self.port_edit.setFixedWidth(70)
        self.port_edit.setToolTip("HTTP-порт сервера.")
        r_port.addWidget(self.port_edit)
        self.lbl_auto_port_label = QLabel(self.tr("lbl_auto_port_colon"))
        self.lbl_auto_port_label.setStyleSheet("color: #5a6070; font-size: 11px; margin-left:6px;")
        r_port.addWidget(self.lbl_auto_port_label)
        _auto_port_on = bool(cfg.get("auto_port", False))
        self.auto_port_btn = ToggleSwitch(checked=_auto_port_on)
        self.auto_port_btn.setToolTip(
            "ВКЛ: случайный свободный порт при каждом старте.\n"
            "ВЫКЛ: использовать порт из поля выше."
        )
        self.auto_port_btn.toggled.connect(self.on_auto_port)
        r_port.addWidget(self.auto_port_btn)
        if _auto_port_on:
            self.port_edit.setEnabled(False)
        r_port.addStretch(1)
        c2.addLayout(r_port)

        r_ss = QHBoxLayout()
        r_ss.setSpacing(8)
        self.start_btn = QPushButton("Старт")
        self.start_btn.setObjectName("start_btn")
        self.start_btn.setToolTip("Запустить HTTP-сервер и начать раздачу модов.")
        self.start_btn.clicked.connect(self.on_start)
        r_ss.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Стоп")
        self.stop_btn.setObjectName("stop_btn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setToolTip("Остановить сервер.")
        self.stop_btn.clicked.connect(self.on_stop)
        r_ss.addWidget(self.stop_btn)
        c2.addLayout(r_ss)

        r_status = QHBoxLayout()
        r_status.setSpacing(6)
        self.status_light = StatusLight()
        r_status.addWidget(self.status_light)
        self.status_lbl = QLabel("Остановлен")
        self.status_lbl.setObjectName("status_stop")
        self.status_lbl.setToolTip("Нажмите чтобы скопировать IP")
        self.status_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_lbl.mousePressEvent = self._on_status_lbl_click
        r_status.addWidget(self.status_lbl)
        r_status.addStretch(1)
        c2.addLayout(r_status)

        r_aux = QHBoxLayout()
        r_aux.setSpacing(6)
        self.autostart_btn = QPushButton("Автозапуск: ВЫКЛ")
        self.autostart_btn.setCheckable(True)
        self.autostart_btn.setToolTip("Автоматически запускать сервер при входе в Windows.")
        self.autostart_btn.clicked.connect(self.on_toggle_autostart)
        r_aux.addWidget(self.autostart_btn)
        self.upnp_btn = QPushButton("UPnP: ВЫКЛ")
        self.upnp_btn.setCheckable(True)
        self.upnp_btn.setToolTip("Автоматически пробросить порт через UPnP.\nТребует роутер с включённым UPnP.")
        self.upnp_btn.clicked.connect(self.on_toggle_upnp)
        r_aux.addWidget(self.upnp_btn)
        c2.addLayout(r_aux)

        mid_row.addWidget(card2, 2)

        # ── BLOCK 3: Build & Publish ──
        card3, c3, self._card3_title_lbl = _make_card(self.tr("grp_build"))

        r_build = QHBoxLayout()
        self.lbl_v2 = QLabel("Сборка:")
        self.lbl_v2.setStyleSheet("color: #5a6070; font-size: 11px;")
        r_build.addWidget(self.lbl_v2)
        self.v2_status_lbl = QLabel("—")
        self.v2_status_lbl.setStyleSheet("color: #8898b8; font-size: 12px;")
        r_build.addWidget(self.v2_status_lbl, 1)
        c3.addLayout(r_build)

        self.v2_seed_lbl = QLabel("")
        self.v2_seed_lbl.setStyleSheet("color: #3d4458; font-size: 11px;")
        c3.addWidget(self.v2_seed_lbl)

        r_listing = QHBoxLayout()
        r_listing.setSpacing(8)
        self.lbl_listing_w = QLabel(self.tr("lbl_listing") + ":")
        self.lbl_listing_w.setStyleSheet("color: #5a6070; font-size: 12px;")
        r_listing.addWidget(self.lbl_listing_w)
        self.ms_listing_chk = ToggleSwitch(checked=bool(cfg.get("ms_listing", False)))
        self.ms_listing_chk.setToolTip(
            "Зарегистрировать сервер на мастер-сервере (bar7dtd.ru).\n"
            "В режиме Вайтлист виден в списке, но обмен модами только с авторизованными игроками."
        )
        self.ms_listing_chk.toggled.connect(self.on_ms_listing_toggled)
        r_listing.addWidget(self.ms_listing_chk)
        self.ms_help_btn = QPushButton("?")
        self.ms_help_btn.setObjectName("help_btn")
        self.ms_help_btn.setFixedSize(22, 22)
        self.ms_help_btn.setToolTip("Что такое мастер-сервер?")
        self.ms_help_btn.clicked.connect(self.on_ms_help)
        r_listing.addWidget(self.ms_help_btn)
        r_listing.addStretch(1)
        self.ms_status_lbl = QLabel("")
        self.ms_status_lbl.setStyleSheet("color: #3d4458; font-size: 11px;")
        r_listing.addWidget(self.ms_status_lbl)
        c3.addLayout(r_listing)

        self.ms_listing_wl_hint = QLabel(self.tr("lbl_wl_hint_listing"))
        self.ms_listing_wl_hint.setStyleSheet("color: #5a7494; font-size: 11px;")
        self.ms_listing_wl_hint.setVisible(False)
        c3.addWidget(self.ms_listing_wl_hint)

        r_ap = QHBoxLayout()
        r_ap.setSpacing(8)
        self.lbl_auto_pub_label = QLabel(self.tr("lbl_auto_publish") + ":")
        self.lbl_auto_pub_label.setStyleSheet("color: #5a6070; font-size: 12px;")
        r_ap.addWidget(self.lbl_auto_pub_label)
        self.auto_publish_chk = ToggleSwitch(checked=bool(cfg.get("auto_publish", False)))
        self.auto_publish_chk.setToolTip(self.tr("tip_auto_publish"))
        self.auto_publish_chk.toggled.connect(
            lambda on: self.append_log(self.tr("log_auto_pub_on" if on else "log_auto_pub_off"))
        )
        r_ap.addWidget(self.auto_publish_chk)
        r_ap.addStretch(1)
        c3.addLayout(r_ap)

        self.publish_progress = QProgressBar()
        self.publish_progress.setRange(0, 100)
        self.publish_progress.setFixedHeight(8)
        self.publish_progress.setTextVisible(False)
        self.publish_progress.setVisible(False)
        c3.addWidget(self.publish_progress)

        r_pub_btns = QHBoxLayout()
        r_pub_btns.setSpacing(8)
        self.scan_btn = QPushButton("Обновить")
        self.scan_btn.setObjectName("scan_btn")
        self.scan_btn.setToolTip(
            "Перечитать папку модов и обновить список файлов.\n"
            "Происходит автоматически при изменении файлов."
        )
        self.scan_btn.clicked.connect(self.on_scan_async)
        r_pub_btns.addWidget(self.scan_btn)
        self.publish_btn = QPushButton("Публикация")
        self.publish_btn.setObjectName("accent_btn")
        self.publish_btn.setToolTip("Опубликовать текущий снапшот модов как новую сборку для скачивания.")
        self.publish_btn.clicked.connect(self.on_publish_clicked)
        r_pub_btns.addWidget(self.publish_btn)
        c3.addLayout(r_pub_btns)

        r_rw = QHBoxLayout()
        r_rw.setSpacing(8)
        self.lbl_restart_watch_w = QLabel(self.tr("lbl_restart_watch") + ":")
        self.lbl_restart_watch_w.setStyleSheet("color: #5a6070; font-size: 12px;")
        r_rw.addWidget(self.lbl_restart_watch_w)
        self.restart_watch_chk = ToggleSwitch(checked=bool(cfg.get("restart_watch", False)))
        self.restart_watch_chk.setToolTip(self.tr("tip_restart_watch"))
        self.restart_watch_chk.toggled.connect(self._on_restart_watch_toggled)
        r_rw.addWidget(self.restart_watch_chk)
        r_rw.addStretch(1)
        c3.addLayout(r_rw)

        mid_row.addWidget(card3, 2)

        # ── BLOCK 4: Access Control ──
        card4, c4, self._card4_title_lbl = _make_card(self.tr("grp_access"))

        r_mode = QHBoxLayout()
        r_mode.setSpacing(6)
        auth_mode_val = cfg.get("auth_mode", "whitelist")
        self.auth_mode_open_btn = QPushButton("Общий")
        self.auth_mode_open_btn.setObjectName("mode_btn")
        self.auth_mode_open_btn.setCheckable(True)
        self.auth_mode_open_btn.setChecked(auth_mode_val == "open")
        self.auth_mode_open_btn.setToolTip("Все игроки со Steam-аккаунтом могут скачивать моды.")
        self.auth_mode_open_btn.clicked.connect(lambda: self._set_auth_mode("open"))
        r_mode.addWidget(self.auth_mode_open_btn)
        self.auth_mode_wl_btn = QPushButton("Вайтлист")
        self.auth_mode_wl_btn.setObjectName("mode_btn")
        self.auth_mode_wl_btn.setCheckable(True)
        self.auth_mode_wl_btn.setChecked(auth_mode_val == "whitelist")
        self.auth_mode_wl_btn.setToolTip("Только SteamID из вашего вайтлиста могут скачивать моды.")
        self.auth_mode_wl_btn.clicked.connect(lambda: self._set_auth_mode("whitelist"))
        r_mode.addWidget(self.auth_mode_wl_btn)
        r_mode.addStretch(1)
        c4.addLayout(r_mode)

        self.auth_mode_hint = QLabel("")
        self.auth_mode_hint.setStyleSheet("color: #3d8a5e; font-size: 11px;")
        c4.addWidget(self.auth_mode_hint)

        self._wl_config_frame = QFrame()
        self._wl_config_frame.setStyleSheet("background: transparent;")
        wl_cfg_l = QVBoxLayout(self._wl_config_frame)
        wl_cfg_l.setContentsMargins(0, 4, 0, 0)
        wl_cfg_l.setSpacing(6)
        r_url = QHBoxLayout()
        self.lbl_auth_url_w = QLabel("Auth URL:")
        self.lbl_auth_url_w.setStyleSheet("color: #5a6070; font-size: 11px;")
        r_url.addWidget(self.lbl_auth_url_w)
        self.auth_url_edit = QLineEdit(auth_url)
        self.auth_url_edit.setPlaceholderText("https://yoursite.com/api/modsync/allowed_steamids")
        r_url.addWidget(self.auth_url_edit, 1)
        wl_cfg_l.addLayout(r_url)
        r_key = QHBoxLayout()
        self.lbl_auth_key_w = QLabel("Auth Key:")
        self.lbl_auth_key_w.setStyleSheet("color: #5a6070; font-size: 11px;")
        r_key.addWidget(self.lbl_auth_key_w)
        self.auth_key_edit = QLineEdit(auth_key)
        self.auth_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.auth_key_edit.setPlaceholderText("секретный ключ…")
        r_key.addWidget(self.auth_key_edit, 1)
        self.sync_auth_btn = QPushButton("Sync")
        self.sync_auth_btn.setObjectName("scan_btn")
        self.sync_auth_btn.setToolTip("Загрузить вайтлист с Auth URL прямо сейчас.")
        self.sync_auth_btn.clicked.connect(self.on_sync_auth_now)
        r_key.addWidget(self.sync_auth_btn)
        wl_cfg_l.addLayout(r_key)
        self.auth_status_lbl = QLabel("Авторизация: …")
        self.auth_status_lbl.setStyleSheet("color: #5a6070; font-size: 11px;")
        wl_cfg_l.addWidget(self.auth_status_lbl)
        c4.addWidget(self._wl_config_frame)

        self.instr_btn = QPushButton("Настройка вайтлиста")
        self.instr_btn.setToolTip("Открыть руководство по настройке вайтлиста.")
        self.instr_btn.clicked.connect(self.on_show_whitelist_instructions)
        c4.addWidget(self.instr_btn)

        self._auth_url_row_widgets = [self.lbl_auth_url_w, self.auth_url_edit]
        self._auth_key_row_widgets = [self.lbl_auth_key_w, self.auth_key_edit,
                                       self.sync_auth_btn, self.auth_status_lbl]
        # stub for compat
        self.lbl_mode = QLabel()
        self.lbl_mode.setVisible(False)

        self._apply_auth_mode_ui(auth_mode_val)
        mid_row.addWidget(card4, 2)

        # ─── BLOCK 5: Tabs ───
        clients_box = QWidget()
        clients_l = QVBoxLayout(clients_box)
        clients_l.setContentsMargins(8, 8, 8, 4)
        clients_header = QHBoxLayout()
        self.lbl_clients = QLabel("Подключённые клиенты")
        self.lbl_clients.setStyleSheet("font-weight: 600; color: #7aa2f7; font-size: 12px;")
        clients_header.addWidget(self.lbl_clients)
        clients_header.addStretch(1)
        self.copy_btn = QPushButton("📋  Copy SteamID")
        self.copy_btn.setToolTip("Скопировать SteamID выбранного игрока.")
        self.copy_btn.clicked.connect(self.on_copy_steamid)
        clients_header.addWidget(self.copy_btn)
        self.ban_from_clients_btn = QPushButton("🚫  Ban")
        self.ban_from_clients_btn.setToolTip("Добавить выбранный SteamID в чёрный список.")
        self.ban_from_clients_btn.clicked.connect(self.on_ban_from_clients)
        clients_header.addWidget(self.ban_from_clients_btn)
        clients_l.addLayout(clients_header)
        self.clients_table = QTableWidget(0, 6)
        self.clients_table.setHorizontalHeaderLabels(["SteamID", "Last Seen", "IP", "Status", "Build", "P2P"])
        hdr_c = self.clients_table.horizontalHeader()
        hdr_c.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr_c.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr_c.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr_c.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr_c.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr_c.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.clients_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.clients_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.clients_table.setAlternatingRowColors(True)
        self.clients_table.cellDoubleClicked.connect(self._on_client_double_click)
        clients_l.addWidget(self.clients_table, 1)

        allowed_box = QWidget()
        allowed_l = QVBoxLayout(allowed_box)
        allowed_l.setContentsMargins(8, 8, 8, 4)
        allowed_header = QHBoxLayout()
        self.lbl_whitelist = QLabel("Вайтлист — разрешённые SteamID")
        self.lbl_whitelist.setStyleSheet("font-weight: 600; color: #7aa2f7; font-size: 12px;")
        allowed_header.addWidget(self.lbl_whitelist)
        allowed_header.addStretch(1)
        self.copy_allowed_btn = QPushButton("📋  Copy SteamID")
        self.copy_allowed_btn.setToolTip("Скопировать SteamID из вайтлиста.")
        self.copy_allowed_btn.clicked.connect(self.on_copy_allowed_steamid)
        allowed_header.addWidget(self.copy_allowed_btn)
        allowed_l.addLayout(allowed_header)
        self.allowed_table = QTableWidget(0, 1)
        self.allowed_table.setHorizontalHeaderLabels(["SteamID"])
        self.allowed_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.allowed_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.allowed_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.allowed_table.setAlternatingRowColors(True)
        allowed_l.addWidget(self.allowed_table, 1)

        blacklist_box = QWidget()
        blacklist_l = QVBoxLayout(blacklist_box)
        blacklist_l.setContentsMargins(8, 8, 8, 4)
        bl_header = QHBoxLayout()
        self.lbl_blacklist = QLabel("Чёрный список — заблокированные SteamID")
        self.lbl_blacklist.setStyleSheet("font-weight: 600; color: #f38ba8; font-size: 12px;")
        bl_header.addWidget(self.lbl_blacklist)
        bl_header.addStretch(1)
        self.bl_add_edit = QLineEdit()
        self.bl_add_edit.setPlaceholderText("SteamID…")
        self.bl_add_edit.setFixedWidth(160)
        bl_header.addWidget(self.bl_add_edit)
        self.bl_add_btn = QPushButton("🚫  Ban")
        self.bl_add_btn.setToolTip("Добавить SteamID в чёрный список.")
        self.bl_add_btn.clicked.connect(self.on_blacklist_add)
        bl_header.addWidget(self.bl_add_btn)
        self.bl_remove_btn = QPushButton("✅  Unban")
        self.bl_remove_btn.setToolTip("Убрать выбранный SteamID из чёрного списка.")
        self.bl_remove_btn.clicked.connect(self.on_blacklist_remove)
        bl_header.addWidget(self.bl_remove_btn)
        blacklist_l.addLayout(bl_header)
        self.blacklist_table = QTableWidget(0, 2)
        self.blacklist_table.setHorizontalHeaderLabels(["SteamID", "Banned At"])
        hdr_bl = self.blacklist_table.horizontalHeader()
        hdr_bl.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr_bl.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.blacklist_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.blacklist_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.blacklist_table.setAlternatingRowColors(True)
        blacklist_l.addWidget(self.blacklist_table, 1)

        mods_box = QWidget()
        mods_l = QVBoxLayout(mods_box)
        mods_l.setContentsMargins(8, 8, 8, 4)
        mods_header = QHBoxLayout()
        self.mods_summary_lbl = QLabel("Mods: …")
        self.mods_summary_lbl.setStyleSheet("color: #5a6070; font-size: 11px;")
        mods_header.addWidget(self.mods_summary_lbl)
        mods_header.addStretch(1)
        self.mods_select_all_btn = QPushButton("All")
        self.mods_select_all_btn.clicked.connect(self.on_mods_select_all)
        mods_header.addWidget(self.mods_select_all_btn)
        self.mods_select_none_btn = QPushButton("None")
        self.mods_select_none_btn.clicked.connect(self.on_mods_select_none)
        mods_header.addWidget(self.mods_select_none_btn)
        self.mods_apply_btn = QPushButton("✔  Apply")
        self.mods_apply_btn.clicked.connect(self.on_mods_apply)
        mods_header.addWidget(self.mods_apply_btn)
        mods_l.addLayout(mods_header)
        self.mods_table = QTableWidget(0, 3)
        self.mods_table.setHorizontalHeaderLabels(["Mod Name", "Files", "Size"])
        hdr_m = self.mods_table.horizontalHeader()
        hdr_m.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr_m.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr_m.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.mods_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.mods_table.setAlternatingRowColors(True)
        self.mods_table.verticalHeader().setDefaultSectionSize(26)
        self.mods_table.itemClicked.connect(self._on_mods_item_clicked)
        mods_l.addWidget(self.mods_table, 1)

        log_box = QWidget()
        log_l = QVBoxLayout(log_box)
        log_l.setContentsMargins(8, 8, 8, 4)
        log_header = QHBoxLayout()
        self.lbl_log_title = QLabel(self.tr("lbl_log_title"))
        self.lbl_log_title.setStyleSheet("font-weight: 600; color: #7aa2f7; font-size: 12px;")
        log_header.addWidget(self.lbl_log_title)
        log_header.addStretch(1)
        self.clear_log_btn = QPushButton(self.tr("btn_clear_log"))
        self.clear_log_btn.setMinimumWidth(80)
        self.clear_log_btn.setToolTip(self.tr("btn_clear_log"))
        self.clear_log_btn.clicked.connect(lambda: self.log_view.clear())
        log_header.addWidget(self.clear_log_btn)
        log_l.addLayout(log_header)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        log_l.addWidget(self.log_view, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(clients_box,   "Clients")
        self.tabs.addTab(allowed_box,   "Whitelist")
        self.tabs.addTab(blacklist_box, "Blacklist")
        self.tabs.addTab(mods_box,      "Mods")
        self.tabs.addTab(log_box,       "Logs")

        # ─── Status bar ───
        sb_frame = QFrame()
        sb_frame.setObjectName("statusbar")
        sb_l = QHBoxLayout(sb_frame)
        sb_l.setContentsMargins(12, 0, 12, 0)
        sb_l.setSpacing(10)
        def _sb_lbl(text, color="#3d4458", url=None):
            lbl = QLabel()
            lbl.setStyleSheet(f"color: {color}; font-size: 11px; background: transparent;")
            if url:
                lbl.setOpenExternalLinks(True)
                lbl.setText(f'<a href="{url}" style="color:{color};text-decoration:none;">{text}</a>')
            else:
                lbl.setText(text)
            return lbl

        sb_l.addWidget(_sb_lbl(f"ModSync Server  v{APP_VERSION}"))
        sb_l.addWidget(_sb_lbl("  ·  ", "#2a2d3a"))
        sb_l.addWidget(_sb_lbl("by SkyLett & AI", "#4a5060"))
        sb_l.addWidget(_sb_lbl("  ·  ", "#2a2d3a"))
        sb_l.addWidget(_sb_lbl("bar7dtd.ru", "#4a7060", "http://bar7dtd.ru"))
        sb_l.addWidget(_sb_lbl("  ·  ", "#2a2d3a"))
        sb_l.addWidget(_sb_lbl("Discord", "#504870", "https://discord.gg/B3zN2h7Ukf"))
        sb_l.addWidget(_sb_lbl("  ·  ", "#2a2d3a"))
        self._gh_status_lbl = _sb_lbl("GitHub: …", "#3d5060")
        self._gh_check_result = None  # None | "uptodate" | ("update", tag) | "error"
        sb_l.addWidget(self._gh_status_lbl)
        self._update_banner = QPushButton()
        self._update_banner.setObjectName("update_btn")
        self._update_banner.setVisible(False)
        self._update_banner.clicked.connect(self._on_update_click)
        sb_l.addWidget(self._update_banner)
        sb_l.addStretch(1)
        _show_tips = bool(cfg.get("show_tooltips", True))
        self._show_tooltips = _show_tips
        self.tooltips_btn = QPushButton(
            "Подсказки: ВКЛ" if _show_tips else "Подсказки: ВЫКЛ"
        )
        self.tooltips_btn.setObjectName("sb_btn")
        self.tooltips_btn.setToolTip("Показывать/скрывать подсказки при наведении.")
        self.tooltips_btn.clicked.connect(self._on_toggle_tooltips)
        sb_l.addWidget(self.tooltips_btn)
        self.lang_btn = QPushButton("EN")
        self.lang_btn.setObjectName("sb_btn")
        self.lang_btn.setFixedWidth(42)
        self.lang_btn.setToolTip("Switch language / Сменить язык")
        self.lang_btn.clicked.connect(self.on_toggle_lang)
        sb_l.addWidget(self.lang_btn)

        # ─── Tab card wrapper ───
        tab_card = QFrame()
        tab_card.setObjectName("glass_card")
        tab_card_l = QVBoxLayout(tab_card)
        tab_card_l.setContentsMargins(8, 8, 8, 8)
        tab_card_l.setSpacing(0)
        tab_card_l.addWidget(self.tabs)

        # ─── Root layout ───
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 0)
        root.setSpacing(8)
        root.addWidget(card1)
        root.addLayout(mid_row)
        root.addWidget(tab_card, 1)
        root.addWidget(sb_frame)

        # ─── MS / UPnP / LAN beacon state ───
        self._ms_client: modsync_v2.MasterClient | None = None
        self._lan_beacon: modsync_v2.LANBeacon | None = None
        self._lan_ip: str = ""
        self._upnp_enabled = bool(cfg.get("upnp_enabled", False))
        self._upnp_failed = False
        self._upnp_port: int | None = None
        if self._upnp_enabled:
            self.upnp_btn.setChecked(True)
            self.upnp_btn.setText("UPnP: …")
            QTimer.singleShot(500, lambda: threading.Thread(
                target=self._upnp_probe_bg, daemon=True).start()
            )

        # ─── Language ───
        self._lang = cfg.get("lang", "ru")
        self.retranslate_ui()

        # ─── Init data ───
        self.update_watcher_paths()
        self.clients_timer.start()
        self.refresh_clients_table()
        self.refresh_auth_label()
        self.refresh_allowed_table()
        self.refresh_blacklist_table()
        self.refresh_mods_table()
        self.refresh_autostart_button()

        self.append_log(f"[APP] Config: {CONFIG_PATH}")
        self.append_log(f"[APP] DB:     {DB_PATH}")

        self._gh_status_signal.connect(self._apply_gh_status)
        self._status_lbl_signal.connect(self._apply_ext_ip)
        threading.Thread(target=self._update_check_bg, daemon=True).start()

        # ─── Restart watch ───
        self._restart_watch_pid: int | None = None
        self._restart_watch_timer: QTimer | None = None
        if bool(cfg.get("restart_watch", False)):
            QTimer.singleShot(1000, self._start_restart_watch)

        if bool(cfg.get("auto_start_server", True)):
            QTimer.singleShot(200, self.on_start)

    # ---------------- logs ----------------

    # Цвета по префиксу тега [TAG]
    _LOG_COLORS = {
        "APP":      "#8b949e",  # серый — системные
        "AUTH":     "#cba6f7",  # фиолетовый
        "UPnP":     "#89b4fa",  # синий
        "AUTOSTART":"#89b4fa",
        "PORT":     "#89b4fa",
        "V2":       "#a6e3a1",  # зелёный — торрент/публикация
        "TRACKER":  "#6a7494",  # тёмно-серый — шумный трекер
        "MS":       "#89dceb",  # голубой — мастер-сервер
        "SCAN":     "#f9e2af",  # жёлтый — сканирование
        "WATCHER":  "#f9e2af",
        "ERROR":    "#f38ba8",  # красный
        "WARN":     "#fab387",  # оранжевый
    }
    _LOG_DEFAULT_COLOR = "#cdd6f4"

    def append_log(self, msg: str):
        import html as _html
        ts = time.strftime("%H:%M:%S")

        # Определяем цвет по тегу [TAG] в начале сообщения
        color = self._LOG_DEFAULT_COLOR
        import re as _re
        m = _re.match(r"\[([A-Za-z0-9_\-]+)\]", msg)
        if m:
            tag = m.group(1).upper()
            color = self._LOG_COLORS.get(tag, self._LOG_DEFAULT_COLOR)
            # Ошибки и предупреждения в любом теге
        if "error" in msg.lower() or "ошибка" in msg.lower() or "❌" in msg:
            color = self._LOG_COLORS["ERROR"]
        elif "warn" in msg.lower() or "⚠" in msg:
            color = self._LOG_COLORS["WARN"]

        ts_html = f'<span style="color:#4a5568">[{ts}]</span>'
        msg_html = f'<span style="color:{color}">{_html.escape(msg)}</span>'
        self.log_view.append(f'{ts_html} {msg_html}')

    # ---------------- watcher ----------------

    def update_watcher_paths(self):
        try:
            old_dirs = self.fs_watcher.directories()
            old_files = self.fs_watcher.files()
            if old_dirs:
                self.fs_watcher.removePaths(old_dirs)
            if old_files:
                self.fs_watcher.removePaths(old_files)
        except Exception:
            pass

        mods_root = Path(self.mods_edit.text().strip() or DEFAULT_MODS_PATH)
        paths_to_watch = []

        if mods_root.exists() and mods_root.is_dir():
            paths_to_watch.append(str(mods_root))
            try:
                tracked = set(self.state.tracked_mods)
                for entry in mods_root.iterdir():
                    if entry.is_dir() and entry.name in tracked:
                        paths_to_watch.append(str(entry))
            except Exception:
                pass

        if paths_to_watch:
            self.fs_watcher.addPaths(paths_to_watch)
            self.append_log(f"[WATCH] Watching {len(paths_to_watch)} folders")

    def on_fs_changed(self, _path: str):
        self.rescan_debounce.start(1500)
        if self.state.v2 is not None:
            self.state.v2.build.mark_dirty()
            if self.auto_publish_chk.isChecked():
                self._auto_pub_timer.start(5000)
            else:
                self.refresh_v2_status()

    # ---------------- scan async ----------------

    # ---------------- V2: публикация ----------------

    def trigger_publish(self):
        """Публикация в рабочем потоке (кнопка / POST /api/v2/publish / автомат)."""
        if self._publish_thread is not None and self._publish_thread.is_alive():
            self.append_log("[V2] Публикация уже идёт")
            return
        mods_root = Path(self.mods_edit.text().strip() or DEFAULT_MODS_PATH)
        try:
            port = int(self.port_edit.text().strip())
        except Exception:
            port = None

        self.publish_progress.setValue(0)
        self.publish_progress.setVisible(True)
        self.publish_btn.setEnabled(False)

        def worker():
            try:
                cfg = read_config()
                tracker_url = ""
                tracker_url_local = ""
                webseed_url = ""
                ip = self._running_ip.split(":")[0] if self._running_ip else ""
                lan_ip = self._lan_ip or ip
                if port and ip:
                    tracker_url = f"http://{ip}:{port}/announce"
                    tracker_url_local = f"http://{lan_ip}:{port}/announce"
                    webseed_url = f"http://{ip}:{port}/api/v2/ws/"
                res = self.state.v2.build.publish(
                    mods_root,
                    server_name=cfg.get("server_name", ""),
                    tracker_url=tracker_url,
                    tracker_url_local=tracker_url_local,
                    webseed_url=webseed_url,
                    tracked_mods=set(self.state.tracked_mods) if self.state.tracked_mods else None,
                    log_cb=lambda m: self.log_bridge.log_signal.emit(m),
                    progress_cb=lambda idx, total: self.pub_progress_bridge.progress.emit(idx, total),
                )
                if self.state.v2.engine is not None:
                    snap_root = self.state.v2.build.get_snapshot_save_root()
                    if snap_root is not None:
                        self.state.v2.engine.seed_from(
                            self.state.v2.build.get_torrent(), snap_root)
                self.log_bridge.log_signal.emit(f"[V2] Готово: {res['build_id']}")
                if self._ms_client is not None:
                    files = res.get("files") or []
                    self._ms_client.upload_manifest(files)
            except Exception as e:
                self.log_bridge.log_signal.emit(f"[V2] Ошибка публикации: {e}")
            finally:
                self.pub_progress_bridge.done.emit()

        self._publish_thread = threading.Thread(target=worker, daemon=True)
        self._publish_thread.start()

    def on_publish_clicked(self):
        self.trigger_publish()

    def _on_publish_progress(self, idx: int, total: int):
        if total > 0:
            self.publish_progress.setValue(int(idx * 100 / total))

    def _on_publish_done(self):
        self.publish_progress.setValue(100)
        self.publish_progress.setVisible(False)
        self.publish_btn.setEnabled(True)

    # ── Master Server ─────────────────────────────────────────

    def _ms_get_build_info(self):
        s = self.state.v2.build.snapshot()
        return (
            s.get("build_id", ""),
            s.get("infohash_v2", ""),
            self.http_thread is not None,
        )

    def _ms_start(self):
        if not self.ms_listing_chk.isChecked():
            return
        try:
            port = int(self.port_edit.text().strip())
        except Exception:
            port = DEFAULT_PORT

        self._ms_client = modsync_v2.MasterClient(
            log_cb=lambda m: self.log_bridge.log_signal.emit(m)
        )
        self.ms_status_lbl.setText("Подключение…")
        self.ms_status_lbl.setStyleSheet("color: #e8971f; font-size: 11px;")

        def _do_start():
            self._ms_client.start(
                token=None,  # всегда свежая регистрация — сервер сам удалит дубль по host+port
                get_server_name=lambda: self.server_name_edit.text().strip() or "ModSync Server",
                port=port,
                listing=True,
                get_build_info=self._ms_get_build_info,
                save_token_cb=self._ms_save_token,
                whitelist_mode=self._is_whitelist_mode(),
            )
            registered = self._ms_client._token is not None
            self.log_bridge.log_signal.emit(
                "[MS] Зарегистрирован на мастер-сервере" if registered
                else "[MS] Не удалось зарегистрироваться"
            )
            self.ms_status_lbl.setText(
                f"● bar7dtd.ru (token …{self._ms_client._token[-6:]})" if registered else "● Ошибка регистрации"
            )
            self.ms_status_lbl.setStyleSheet(
                "color: #3fb950; font-size: 11px;" if registered else "color: #f85149; font-size: 11px;"
            )

        threading.Thread(target=_do_start, daemon=True).start()

    def _ms_save_token(self, token: str | None):
        cfg = read_config()
        if token:
            cfg["ms_token"] = token
        else:
            cfg.pop("ms_token", None)
        write_config(cfg)

    def on_ms_listing_toggled(self, checked: bool):
        if not checked:
            reply = QMessageBox.question(
                self,
                "Отключить публичный листинг?",
                "Ваш сервер будет исключён из публичного списка ModSync.\n"
                "Клиентам придётся вводить адрес сервера вручную.\n\n"
                "Выключить листинг?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                # Отмена — вернуть галочку обратно без рекурсии
                self.ms_listing_chk.blockSignals(True)
                self.ms_listing_chk.setChecked(True)
                self.ms_listing_chk.blockSignals(False)
                return

        if not checked:
            cfg = read_config()
            cfg.pop("ms_token", None)
            write_config(cfg)
        self.save_config()
        if checked and self.http_thread is not None:
            self._ms_start()
        elif not checked:
            if self._ms_client is not None:
                threading.Thread(
                    target=self._ms_client.unregister, daemon=True
                ).start()
                self._ms_client.stop()
                self._ms_client = None
            self.ms_status_lbl.setText("")

    def on_ms_help(self):
        QMessageBox.information(
            self, self.tr("dlg_ms_help_title"),
            self.tr("dlg_ms_help_text"),
        )

    def refresh_v2_status(self):
        if self.state.v2 is None:
            return
        s = self.state.v2.build.snapshot()
        if s["publishing"]:
            txt, color = self.tr("v2_publishing"), "#e8971f"
        elif not s["has_published"]:
            txt, color = self.tr("v2_not_published"), "#f85149"
        elif s["draft_dirty"]:
            txt, color = self.tr("v2_has_changes", build_id=s["build_id"]), "#e8971f"
        else:
            fc = s.get("file_count", 0)
            txt, color = self.tr("v2_published", build_id=s["build_id"], fc=fc), "#3fb950"
        self.v2_status_lbl.setText(txt)
        self.v2_status_lbl.setStyleSheet(
            f"color: {color}; font-weight: bold; font-size: 13px;")

    def on_v2_tick(self):
        if self.state.v2 is None:
            return
        self.refresh_v2_status()
        eng = self.state.v2.engine
        if eng is not None:
            for msg in eng.pump_alerts():
                self.append_log(msg)
            st = eng.stats()
            if st.get("active"):
                up_mb = st["total_upload"] / (1024 * 1024)
                rate_kb = st["upload_rate"] / 1024
                self.v2_seed_lbl.setText(
                    self.tr("lbl_seed_stats", peers=st["peers"], mb=up_mb, kb=rate_kb))
            else:
                self.v2_seed_lbl.setText("")

    def on_scan_async(self):
        mods_path = self.mods_edit.text().strip()
        if not mods_path:
            QMessageBox.warning(self, "Error", "Mods path is empty")
            return
        if not self.scan_btn.isEnabled():
            return

        self.scan_btn.setEnabled(False)
        self.append_log(f"[SCAN] (async) Building manifest from: {mods_path}")

        def worker():
            try:
                tracked = set(self.state.tracked_mods) if self.state.tracked_mods else set()
                m = build_manifest(Path(mods_path), tracked_mods=tracked)
            except Exception as e:
                m = {"manifest_version": 1, "generated_at": int(time.time()), "mods_root": mods_path, "mods": [], "error": str(e)}
                m["manifest_hash"] = sha256(json.dumps(m, sort_keys=True).encode("utf-8")).hexdigest()
            self.manifest_bridge.manifest_ready.emit(m)

        threading.Thread(target=worker, daemon=True).start()

    def on_manifest_ready(self, m: dict):
        self.state.set_manifest(m)
        mh = m.get("manifest_hash")
        count = len(m.get("mods", []))
        self.append_log(f"[SCAN] Done. mods={count}, manifest_hash={mh}")
        self.scan_btn.setEnabled(True)
        self.refresh_mods_table()
        self.update_watcher_paths()
        self.save_config()

    # ----------------select mods-------------------
    def _on_mods_item_clicked(self, item: QTableWidgetItem):
        """Клик по строке — переключаем tracked состояние мода."""
        row = item.row()
        name_item = self.mods_table.item(row, 0)
        if name_item is None:
            return
        mod_id = name_item.data(Qt.ItemDataRole.UserRole)
        if not mod_id:
            return
        if mod_id in self.state.tracked_mods:
            self.state.tracked_mods.discard(mod_id)
        else:
            self.state.tracked_mods.add(mod_id)
        self._refresh_mods_row(row, mod_id)
        # Обновить итоговую строку
        total = self.mods_table.rowCount()
        sel = sum(
            1 for r in range(total)
            if (it := self.mods_table.item(r, 0)) and it.data(Qt.ItemDataRole.UserRole) in self.state.tracked_mods
        )
        manifest_mods = {m["id"]: m for m in self.state.get_manifest().get("mods", [])}
        sel_bytes = sum(
            manifest_mods.get(
                self.mods_table.item(r, 0).data(Qt.ItemDataRole.UserRole), {}
            ).get("size_bytes", 0)
            for r in range(total)
            if (it2 := self.mods_table.item(r, 0)) and it2.data(Qt.ItemDataRole.UserRole) in self.state.tracked_mods
        )
        self.mods_summary_lbl.setText(
            self.tr("lbl_mods_summary", total=total, sel=sel, size=human_bytes(sel_bytes))
        )

    def _refresh_mods_row(self, row: int, mod_id: str):
        """Перерисовывает одну строку по текущему состоянию tracked_mods."""
        is_tracked = mod_id in self.state.tracked_mods
        name_item = self.mods_table.item(row, 0)
        if name_item:
            name_item.setText(("✔  " if is_tracked else "      ") + mod_id)
            name_item.setForeground(QColor("#cdd6f4") if is_tracked else QColor("#5a6480"))
            bg = QColor("#0f1e35") if is_tracked else QColor("#111520" if row % 2 else "#0d1117")
            name_item.setBackground(bg)

    def refresh_mods_table(self):
        mods_root = Path(self.mods_edit.text().strip() or DEFAULT_MODS_PATH)
        # Берём размеры из манифеста — уже посчитаны при скане
        manifest_mods = {m["id"]: m for m in self.state.get_manifest().get("mods", [])}
        # Дополняем discover'ом: в манифесте только tracked; нам нужны все для отображения
        all_ids = sorted(
            set(discover_mod_ids(mods_root)) | set(manifest_mods.keys()),
            key=str.lower
        )

        self.mods_table.setRowCount(len(all_ids))

        total_count = len(all_ids)
        selected_count = 0
        selected_bytes = 0

        for row, mod_id in enumerate(all_ids):
            is_tracked = mod_id in self.state.tracked_mods
            if is_tracked:
                selected_count += 1

            info = manifest_mods.get(mod_id, {})
            files_count = info.get("files_count", 0)
            size_bytes  = info.get("size_bytes", 0)
            if is_tracked:
                selected_bytes += size_bytes

            # Col 0: имя с галочкой, mod_id хранится в UserRole
            name_item = QTableWidgetItem(("✔  " if is_tracked else "      ") + mod_id)
            name_item.setData(Qt.ItemDataRole.UserRole, mod_id)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            name_item.setForeground(QColor("#cdd6f4") if is_tracked else QColor("#5a6480"))
            bg = QColor("#0f1e35") if is_tracked else QColor("#111520" if row % 2 else "#0d1117")
            name_item.setBackground(bg)
            self.mods_table.setItem(row, 0, name_item)

            fc_item = QTableWidgetItem(str(files_count) if files_count else "–")
            fc_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            fc_item.setForeground(QColor("#8b9ebe"))
            fc_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.mods_table.setItem(row, 1, fc_item)

            sz_item = QTableWidgetItem(human_bytes(size_bytes) if size_bytes else "–")
            sz_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            sz_item.setForeground(QColor("#89dceb"))
            sz_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.mods_table.setItem(row, 2, sz_item)

        self.mods_summary_lbl.setText(
            self.tr("lbl_mods_summary", total=total_count, sel=selected_count, size=human_bytes(selected_bytes))
        )

    def on_mods_select_all(self):
        for row in range(self.mods_table.rowCount()):
            it = self.mods_table.item(row, 0)
            if it:
                mod_id = it.data(Qt.ItemDataRole.UserRole)
                if mod_id:
                    self.state.tracked_mods.add(mod_id)
        self.refresh_mods_table()

    def on_mods_select_none(self):
        for row in range(self.mods_table.rowCount()):
            it = self.mods_table.item(row, 0)
            if it:
                mod_id = it.data(Qt.ItemDataRole.UserRole)
                if mod_id:
                    self.state.tracked_mods.discard(mod_id)
        self.refresh_mods_table()

    def on_mods_apply(self):
        new_set = set()
        for row in range(self.mods_table.rowCount()):
            it = self.mods_table.item(row, 0)
            if not it:
                continue
            mod_id = it.data(Qt.ItemDataRole.UserRole)
            if mod_id and mod_id in self.state.tracked_mods:
                new_set.add(mod_id)

        self.state.tracked_mods = new_set
        self.append_log(f"[MODS] Apply tracking: {len(new_set)} mods selected")
        self.refresh_mods_table()

        # Пересканить манифест уже с фильтром
        self.on_scan_async()

        # Обновить watcher, чтобы следить только за выбранными
        self.update_watcher_paths()

        # Сохранить конфиг
        self.save_config()
    # ---------------- server start/stop ----------------

    def on_auto_port(self, checked: bool):
        self.port_edit.setEnabled(not checked)
        if checked:
            try:
                preferred = int(self.port_edit.text().strip())
            except Exception:
                preferred = DEFAULT_PORT
            chosen = pick_port("0.0.0.0", preferred)
            self.port_edit.setText(str(chosen))
            self.append_log(f"[PORT] Auto: selected free port {chosen}")
        self.refresh_autostart_button()
        self.save_config()


    def _on_status_lbl_click(self, event):
        if self._running_ip:
            QApplication.clipboard().setText(self._running_ip)
            self.append_log(f"[APP] Copied to clipboard: {self._running_ip}")

    def on_select_mods_folder(self):
        from PyQt6.QtWidgets import QFileDialog
        current = self.mods_edit.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select Mods Folder", current)
        if folder:
            self.mods_edit.setText(folder)
            # попробовать автоподхватить имя из serverconfig рядом
            auto = read_server_name_from_config(folder)
            if auto and not self.server_name_edit.text().strip():
                self.server_name_edit.setText(auto)
                self.server_name_edit.setToolTip(f"Прочитано из serverconfig.xml: {auto}")
            self.on_scan_async()

    def on_select_serverconfig(self):
        from PyQt6.QtWidgets import QFileDialog
        current = str(Path(self.mods_edit.text().strip()).parent) if self.mods_edit.text().strip() else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать serverconfig.xml", current, "XML files (*.xml)"
        )
        if not path:
            return
        import xml.etree.ElementTree as ET
        try:
            root = ET.parse(path).getroot()
            for prop in root.iter("property"):
                if prop.get("name") == "ServerName":
                    val = (prop.get("value") or "").strip()
                    if val:
                        self.server_name_edit.setText(val)
                        self.server_name_edit.setToolTip(f"Прочитано из: {path}")
                        return
            QMessageBox.warning(self, "serverconfig.xml", "Свойство ServerName не найдено в файле.")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось прочитать файл:\n{e}")

    def _on_server_name_changed(self, text: str):
        cfg = read_config()
        cfg["server_name"] = text.strip()
        write_config(cfg)



    def tr(self, key: str, **kwargs) -> str:
        """Возвращает перевод строки для текущего языка."""
        lang = getattr(self, "_lang", "ru")
        text = TRANSLATIONS.get(lang, TRANSLATIONS["ru"]).get(key, key)
        return text.format(**kwargs) if kwargs else text

    def on_toggle_lang(self):
        self._lang = "ru" if self._lang == "en" else "en"
        self.save_config()
        self.retranslate_ui()

    def retranslate_ui(self):
        """Обновляет все тексты UI без пересборки виджетов."""
        t = self.tr
        self.setWindowTitle(t("window_title"))
        self.lang_btn.setText(t("btn_lang"))
        # Card titles
        if self._card2_title_lbl:
            self._card2_title_lbl.setText(t("grp_server").upper())
        if self._card3_title_lbl:
            self._card3_title_lbl.setText(t("grp_build").upper())
        if self._card4_title_lbl:
            self._card4_title_lbl.setText(t("grp_access").upper())
        # Inline labels
        self.lbl_sname_w.setText(t("lbl_server_name"))
        self.lbl_auto_port_label.setText(t("lbl_auto_port_colon"))
        self.lbl_listing_w.setText(t("lbl_listing") + ":")
        self.ms_listing_wl_hint.setText(t("lbl_wl_hint_listing"))
        self.lbl_auto_pub_label.setText(t("lbl_auto_publish") + ":")
        self.lbl_restart_watch_w.setText(t("lbl_restart_watch") + ":")
        self.restart_watch_chk.setToolTip(t("tip_restart_watch"))
        self.lbl_log_title.setText(t("lbl_log_title"))
        self.clear_log_btn.setText(t("btn_clear_log"))
        # Labels
        self.lbl_mods_path.setText(t("lbl_mods_path"))
        self.lbl_port.setText(t("lbl_port"))
        self.lbl_auth_url_w.setText(t("lbl_auth_url"))
        self.lbl_auth_key_w.setText(t("lbl_auth_key"))
        self.auth_status_lbl.setText(t("lbl_auth_status"))
        self.lbl_clients.setText(t("lbl_clients"))
        self.lbl_whitelist.setText(t("lbl_whitelist"))
        self.lbl_blacklist.setText(t("lbl_blacklist"))
        # Buttons + tooltips
        self.select_mods_btn.setText(t("btn_select"))
        self.select_mods_btn.setToolTip(t("btn_select_tip"))
        self.mods_edit.setToolTip(t("tip_mods_path"))
        self.select_serverconfig_btn.setToolTip(t("tip_serverconfig"))
        self.server_name_edit.setToolTip(t("tip_server_name"))
        self.start_btn.setText(t("btn_start"))
        self.start_btn.setToolTip(t("tip_start"))
        self.stop_btn.setText(t("btn_stop"))
        self.stop_btn.setToolTip(t("tip_stop"))
        self.port_edit.setToolTip(t("tip_port"))
        self.auto_port_btn.setToolTip(t("tip_auto_port"))
        self.autostart_btn.setText(t("btn_autostart_on") if self.autostart_btn.isChecked() else t("btn_autostart_off"))
        self.autostart_btn.setToolTip(t("tip_autostart"))
        self.upnp_btn.setToolTip(t("btn_upnp_tip"))
        self.scan_btn.setText(t("btn_scan"))
        self.scan_btn.setToolTip(t("btn_scan_tip"))
        self.publish_btn.setText(t("btn_publish"))
        self.publish_btn.setToolTip(t("btn_publish_tip"))
        self.auto_publish_chk.setToolTip(t("tip_auto_publish"))
        self.ms_listing_chk.setToolTip(t("tip_listing"))
        self.ms_help_btn.setToolTip(t("tip_ms_help"))
        self.lbl_v2.setText(t("lbl_build"))
        self.auth_mode_open_btn.setText(t("btn_mode_open"))
        self.auth_mode_open_btn.setToolTip(t("tip_mode_open"))
        self.auth_mode_wl_btn.setText(t("btn_mode_wl"))
        self.auth_mode_wl_btn.setToolTip(t("tip_mode_wl"))
        self.instr_btn.setText(t("btn_instr"))
        self.instr_btn.setToolTip(t("tip_instr"))
        self.sync_auth_btn.setText(t("btn_sync_auth"))
        self.sync_auth_btn.setToolTip(t("tip_sync_auth"))
        self.copy_btn.setText(t("btn_copy_steamid"))
        self.copy_btn.setToolTip(t("tip_copy_steamid"))
        self.ban_from_clients_btn.setText(t("btn_ban"))
        self.ban_from_clients_btn.setToolTip(t("btn_ban_tip"))
        self.copy_allowed_btn.setText(t("btn_copy_allowed"))
        self.copy_allowed_btn.setToolTip(t("tip_copy_allowed"))
        self.mods_select_all_btn.setText(t("btn_mods_all"))
        self.mods_select_none_btn.setText(t("btn_mods_none"))
        self.mods_apply_btn.setText(t("btn_mods_apply"))
        self.bl_add_btn.setText(t("btn_ban"))
        self.bl_add_btn.setToolTip(t("tip_bl_add"))
        self.bl_remove_btn.setText(t("btn_unban"))
        self.bl_remove_btn.setToolTip(t("tip_bl_remove"))
        self.tooltips_btn.setText(t("btn_tooltips_on") if self._show_tooltips else t("btn_tooltips_off"))
        self.tooltips_btn.setToolTip(t("tip_tooltips"))
        # UPnP button (keep current state)
        if not self.upnp_btn.isEnabled():
            pass  # failed state — tooltip already set
        elif self.upnp_btn.isChecked():
            self.upnp_btn.setText(t("btn_upnp_on"))
            self.upnp_btn.setToolTip(t("btn_upnp_tip"))
        else:
            self.upnp_btn.setText(t("btn_upnp_off"))
            self.upnp_btn.setToolTip(t("btn_upnp_tip"))
        # Tabs
        self.tabs.setTabText(0, t("tab_clients"))
        self.tabs.setTabText(1, t("tab_whitelist"))
        self.tabs.setTabText(2, t("tab_blacklist"))
        self.tabs.setTabText(3, t("tab_mods"))
        self.tabs.setTabText(4, t("tab_logs"))
        # Table headers
        self.clients_table.setHorizontalHeaderLabels([
            t("th_steamid"), t("th_last_seen"), t("th_ip"), t("th_status"), t("th_build"), t("th_p2p")
        ])
        self.mods_table.setHorizontalHeaderLabels([
            t("th_mod_name"), t("th_files"), t("th_size")
        ])
        self.blacklist_table.setHorizontalHeaderLabels([
            t("th_steamid"), t("th_banned_at")
        ])
        # Placeholders
        self.auth_url_edit.setPlaceholderText(t("ph_auth_url"))
        self.auth_key_edit.setPlaceholderText(t("ph_auth_key"))
        self.bl_add_edit.setPlaceholderText(t("ph_ban_steamid"))
        # Status label
        if self._running_ip:
            self.status_lbl.setText(t("lbl_status_running", ip=self._running_ip))
            self.status_lbl.setToolTip(t("lbl_status_tip"))
        else:
            self.status_lbl.setText(t("lbl_status_stopped"))
        # GitHub status label
        result = getattr(self, "_gh_check_result", None)
        if result is None:
            self._gh_status_lbl.setText(t("gh_checking"))
        elif result == "uptodate":
            self._gh_status_lbl.setText(t("gh_uptodate"))
        elif result == "error":
            self._gh_status_lbl.setText("GitHub: ✗")
        elif isinstance(result, tuple) and result[0] == "update":
            self._gh_status_lbl.setText(t("gh_update", v=result[1]))
        # Auth mode hint
        if self.auth_mode_open_btn.isChecked():
            self.auth_mode_hint.setText(t("lbl_auth_hint_open"))
        else:
            self.auth_mode_hint.setText(t("lbl_auth_hint_wl"))
        # Mods summary refresh
        self.refresh_mods_table()

    # ─── Tooltips toggle ─────────────────────────────────────────

    def _on_toggle_tooltips(self):
        self._show_tooltips = not self._show_tooltips
        t = self.tr
        self.tooltips_btn.setText(t("btn_tooltips_on") if self._show_tooltips else t("btn_tooltips_off"))
        if self._show_tooltips:
            self.setStyleSheet(GLASS_STYLE)
        else:
            self.setStyleSheet(GLASS_STYLE + "\nQToolTip { opacity: 0; max-height: 0; padding: 0; border: none; }")
        self.save_config()

    # ─── Auto-update check ───────────────────────────────────────

    def _apply_gh_status(self, text: str, color: str):
        self._gh_status_lbl.setText(text)
        weight = "font-weight: 600; " if color == "#f59e0b" else ""
        self._gh_status_lbl.setStyleSheet(
            f"color: {color}; font-size: 11px; background: transparent; {weight}")

    def _update_check_bg(self):
        def _ver(s):
            try:
                return tuple(int(x) for x in s.split("."))
            except Exception:
                return (0,)
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": f"ModSync/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            tag = data.get("tag_name", "").lstrip("vV")
            if not tag:
                self._gh_check_result = "uptodate"
                self._gh_status_signal.emit(self.tr("gh_uptodate"), "#4ade80")
                return
            if _ver(tag) > _ver(APP_VERSION):
                html_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases/latest")
                self._pending_update = (tag, html_url)
                self._gh_check_result = ("update", tag)
                self._gh_status_signal.emit(self.tr("gh_update", v=tag), "#f59e0b")
                QTimer.singleShot(0, self, lambda: self._show_update_banner(tag, html_url))
            else:
                self._gh_check_result = "uptodate"
                self._gh_status_signal.emit(self.tr("gh_uptodate"), "#4ade80")
        except Exception as e:
            self._gh_check_result = "error"
            self.append_log(f"[APP] GitHub check failed: {e}")
            self._gh_status_signal.emit("GitHub: ✗", "#6c7086")

    def _apply_ext_ip(self, ext_running: str):
        self._running_ip = ext_running
        self.status_lbl.setText(self.tr("lbl_status_running", ip=ext_running))

    def _fetch_external_ip_bg(self, port: int):
        try:
            req = urllib.request.Request(
                "https://api.ipify.org", headers={"User-Agent": f"ModSync/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                ext_ip = resp.read().decode().strip()
            if not ext_ip:
                return
            ext_running = f"{ext_ip}:{port}"
            t_port = port + 1
            self.append_log(self.tr("log_ext_ip", ip=ext_ip))
            self._status_lbl_signal.emit(ext_running)
        except Exception as e:
            self.append_log(f"[APP] External IP fetch failed: {e}")

    def _show_update_banner(self, version: str, url: str):
        self._update_banner.setText(self.tr("lbl_update", v=version) + "  " + self.tr("btn_download_update"))
        self._update_banner.setVisible(True)
        self._update_url = url

    def _on_update_click(self):
        url = getattr(self, "_update_url", f"https://github.com/{GITHUB_REPO}/releases/latest")
        webbrowser.open(url)

    def on_toggle_upnp(self):
        self._upnp_enabled = self.upnp_btn.isChecked()
        self._upnp_failed = False
        self.upnp_btn.setEnabled(True)
        if self._upnp_enabled:
            self.upnp_btn.setText("UPnP: …")
            self.upnp_btn.setToolTip(self.tr("btn_upnp_tip"))
            self.append_log(self.tr("upnp_enabled_log"))
            self.save_config()
            threading.Thread(target=self._upnp_probe_bg, daemon=True).start()
        else:
            self.upnp_btn.setText(self.tr("btn_upnp_off"))
            self.upnp_btn.setToolTip(self.tr("btn_upnp_tip"))
            self.append_log(self.tr("upnp_disabled_log"))
            self.save_config()

    def _upnp_probe_bg(self):
        """Фоновая проверка UPnP-доступности без открытия порта."""
        ok, result = upnp_probe()
        if ok:
            self.log_bridge.log_signal.emit(f"[UPnP] ✅ Роутер найден, внешний IP: {result} — порт будет пробит при старте сервера")
            self.upnp_btn.setText(f"UPnP: {result}")
            self.upnp_btn.setToolTip(f"UPnP работает. Внешний IP: {result}\nПорт будет открыт при следующем старте сервера.")
        else:
            self.log_bridge.log_signal.emit(f"[UPnP] ⚠ {result}")
            self.upnp_btn.setText("UPnP: ERR")
            self.upnp_btn.setToolTip(
                f"{result}\n\nОткройте порт вручную в настройках роутера:\n"
                f"  Протокол: TCP\n"
                f"  Внешний порт: {self.port_edit.text().strip() or '8765'}\n"
                f"  Внутренний IP: (этот компьютер)\n"
                f"  Внутренний порт: {self.port_edit.text().strip() or '8765'}"
            )
            self._upnp_failed = True
            self.save_config()

    def _do_upnp_open(self, port: int):
        """Запускает UPnP в фоне, результат логирует в GUI."""
        self.append_log(self.tr("upnp_opening", port=port))

        def _run():
            ok, result = upnp_add_port(port, log=self.log_bridge.log_signal.emit)
            if ok:
                self._upnp_port = port
                self.log_bridge.log_signal.emit(self.tr("upnp_ok", port=port, ip=result))
                self.log_bridge.log_signal.emit(self.tr("upnp_players", ip=result, port=port))
                self.upnp_btn.setEnabled(True)
                self.upnp_btn.setText(f"UPnP: {result}")
                self.upnp_btn.setToolTip(self.tr("btn_upnp_ok_tip", ip=result, port=port))
                # Обновляем статус-метку с реальным внешним IP
                ext_running = f"{result}:{port}"
                self._running_ip = ext_running
                self.status_lbl.setText(self.tr("lbl_status_running", ip=ext_running))
                self.status_lbl.setToolTip(
                    self.tr("lbl_status_tip") + f"\nHTTP: {port}  |  P2P: {port + 1}"
                )
            else:
                self._upnp_port = None
                self._upnp_failed = True
                self.save_config()
                self.log_bridge.log_signal.emit(self.tr("upnp_fail", reason=result))
                self.log_bridge.log_signal.emit(self.tr("upnp_manual", port=port))
                self.upnp_btn.setText(self.tr("btn_upnp_failed"))
                self.upnp_btn.setEnabled(False)
                self.upnp_btn.setToolTip(
                    self.tr("btn_upnp_fail_tip", port=port, ip=self._running_ip.split(":")[0])
                )

        threading.Thread(target=_run, daemon=True).start()

    def on_start(self):
        if self.http_thread is not None:
            return
        try:
            port = int(self.port_edit.text().strip())
        except Exception:
            QMessageBox.warning(self, "Error", "Port must be a number")
            return

        if not is_port_free("0.0.0.0", port):
            chosen = pick_port("0.0.0.0", port)
            if chosen != port:
                self.append_log(f"[PORT] Port {port} is busy. Auto-chosen {chosen}.")
                self.port_edit.setText(str(chosen))
                port = chosen
            else:
                QMessageBox.warning(self, "Error", f"Port {port} is busy and no free port found")
                return

        self.on_scan_async()

        self.http_thread = HttpServerThread("0.0.0.0", port, self.state, self.log_bridge)
        self.http_thread.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.port_edit.setEnabled(False)
        self.auto_port_btn.setEnabled(False)
        self.autostart_btn.setEnabled(False)
        self.status_light.set_state("running")
        try:
            real_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            real_ip = "127.0.0.1"
        self._lan_ip = real_ip
        t_port = port + 1
        self._running_ip = f"{real_ip}:{port}"
        self.status_lbl.setText(self.tr("lbl_status_running", ip=self._running_ip))
        self.status_lbl.setObjectName("status_ok")
        self.status_lbl.setStyleSheet("color: #4ade80; font-weight: 600; font-size: 13px;")
        self.status_lbl.setToolTip(
            self.tr("lbl_status_tip") + self.tr("lbl_status_tip_ports", http=port, p2p=t_port)
        )
        self.append_log(f"[APP] Server started on {self._running_ip} (torrent port: {t_port})")

        # LAN beacon — рассылаем LAN IP на широковещательный UDP
        lan_ip_local = self._running_ip.split(":")[0] if self._running_ip else ""
        if lan_ip_local:
            if getattr(self, "_lan_beacon", None):
                self._lan_beacon.stop()
            self._lan_beacon = modsync_v2.LANBeacon(lan_ip_local, port)
            self._lan_beacon.start()

        # Показать внешний IP в статусе (в фоне)
        threading.Thread(target=self._fetch_external_ip_bg, args=(port,), daemon=True).start()

        # UPnP проброс порта
        if self._upnp_enabled and not getattr(self, "_upnp_failed", False):
            self._do_upnp_open(port)

        # V2: торрент-движок на порту HTTP+1 (t_port уже задан выше)
        try:
            eng = self.state.v2.ensure_engine(
                t_port, log_cb=lambda m: self.log_bridge.log_signal.emit(m))
            tor = self.state.v2.build.get_torrent()
            snap_root = self.state.v2.build.get_snapshot_save_root()
            if tor is not None and snap_root is not None:
                eng.seed_from(tor, snap_root)
            else:
                self.append_log("[V2] Нет опубликованной сборки — нажми «Опубликовать»")
            if self._upnp_enabled and not getattr(self, "_upnp_failed", False):
                self._do_upnp_open(t_port)
        except Exception as e:
            self.append_log(f"[V2] Движок не запустился: {e}")

        # Master Server
        self._ms_start()

        # Auto-publish при старте если включена галочка
        if self.auto_publish_chk.isChecked():
            QTimer.singleShot(3000, self.trigger_publish)

        self.save_config()

    def on_stop(self):
        if self.http_thread is None:
            return
        self.http_thread.stop()
        self.http_thread = None
        if self._lan_beacon is not None:
            self._lan_beacon.stop()
            self._lan_beacon = None
        if self.state.v2 is not None and self.state.v2.engine is not None:
            self.state.v2.engine.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.port_edit.setEnabled(not self.auto_port_btn.isChecked())
        self.auto_port_btn.setEnabled(True)
        self.autostart_btn.setEnabled(True)
        self.status_light.set_state("stopped")
        self._running_ip = ""
        self.status_lbl.setText(self.tr("lbl_status_stopped"))
        self.status_lbl.setObjectName("status_stop")
        self.status_lbl.setStyleSheet("color: #f87171; font-weight: 600; font-size: 13px;")
        self.status_lbl.setToolTip("")
        self.append_log("[APP] Server stopped.")

        # Master Server
        if self._ms_client is not None:
            self._ms_client.stop()
            self._ms_client = None
        self.ms_status_lbl.setText("")

        # Убрать UPnP маппинг
        if self._upnp_port is not None:
            threading.Thread(
                target=upnp_remove_port, args=(self._upnp_port,), daemon=True
            ).start()
            self._upnp_port = None
            self.append_log(self.tr("upnp_removed"))
            self.upnp_btn.setText(self.tr("btn_upnp_on"))
            self._upnp_failed = False
            self.save_config()
            self.upnp_btn.setEnabled(True)
            self.upnp_btn.setToolTip(
                "Автоматически пробросить порт через UPnP.\n"
                "Требует поддержки UPnP на роутере."
            )

    def refresh_autostart_button(self):
        # используем текущий порт из поля
        try:
            port = int(self.port_edit.text().strip())
        except Exception:
            port = DEFAULT_PORT

        tn = _task_name_for_port(port)
        exists = task_exists(tn)

        self.autostart_btn.blockSignals(True)
        self.autostart_btn.setChecked(exists)
        self.autostart_btn.setText(self.tr("btn_autostart_on") if exists else self.tr("btn_autostart_off"))
        self.autostart_btn.blockSignals(False)

    def on_toggle_autostart(self):
        # ВАЖНО: привязка к текущему порту
        try:
            port = int(self.port_edit.text().strip())
        except Exception:
            QMessageBox.warning(self, "Error", "Port must be a number before enabling autostart.")
            self.refresh_autostart_button()
            return

        tn = _task_name_for_port(port)

        if self.autostart_btn.isChecked():
            # включаем автозапуск
            cmd = _build_task_command()
            ok, msg = create_autostart_task(tn, cmd)
            if ok:
                self.append_log(f"[AUTOSTART] Installed task: {tn} -> {cmd}")
            else:
                self.append_log(f"[AUTOSTART] ERROR install: {msg}")
                QMessageBox.warning(self, "Autostart error", msg)
        else:
            # выключаем автозапуск
            ok, msg = delete_autostart_task(tn)
            if ok:
                self.append_log(f"[AUTOSTART] Removed task: {tn}")
            else:
                self.append_log(f"[AUTOSTART] ERROR remove: {msg}")
                QMessageBox.warning(self, "Autostart error", msg)

        # обновим визуально (на случай ошибки/отката)
        self.refresh_autostart_button()

    # ---------------- clients GUI ----------------

    def _on_client_double_click(self, row: int, col: int):
        item = self.clients_table.item(row, 0)
        if not item:
            return
        steamid = item.text().strip()
        if steamid:
            webbrowser.open(f"https://steamcommunity.com/profiles/{steamid}")

    def refresh_clients_table(self):
        items = db_list_clients(limit=500)
        cur_build = ""
        if self.state.v2 is not None:
            s = self.state.v2.build.snapshot()
            cur_build = s.get("build_id", "")

        self.clients_table.setRowCount(len(items))
        for row, it in enumerate(items):
            steamid = it.get("steamid", "")
            last_seen = int(it.get("last_seen", 0) or 0)
            ip = it.get("last_ip", "")
            client_build = it.get("build_id", "")
            torrent_build = it.get("torrent_build_id", "")

            # Статус: клиент забрал manifest текущего build → OK (ничего не качает)
            #         клиент забрал torrent текущего build → Syncing (скачивает)
            #         иначе → OUTDATED
            if not client_build and not torrent_build:
                status, status_color = "—", "#8b9ebe"
            elif cur_build and torrent_build == cur_build:
                status, status_color = "Syncing", "#e8a020"
            elif cur_build and client_build == cur_build:
                status, status_color = "OK", "#3fb950"
            else:
                status, status_color = "OUTDATED", "#f0a020"

            last_seen_str = "—" if last_seen <= 0 else time.strftime("%Y-%m-%d %H:%M", time.localtime(last_seen))

            sid_item = QTableWidgetItem(steamid)
            sid_item.setForeground(QColor("#89b4fa"))
            sid_item.setToolTip("Двойной клик — открыть профиль Steam")
            self.clients_table.setItem(row, 0, sid_item)
            self.clients_table.setItem(row, 1, QTableWidgetItem(last_seen_str))
            self.clients_table.setItem(row, 2, QTableWidgetItem(ip))
            status_item = QTableWidgetItem(status)
            status_item.setForeground(QColor(status_color))
            self.clients_table.setItem(row, 3, status_item)
            build_item = QTableWidgetItem(client_build[:8] if client_build else "—")
            build_item.setForeground(QColor("#89b4fa" if client_build == cur_build else "#6a7494"))
            self.clients_table.setItem(row, 4, build_item)

            p2p_val = it.get("p2p_enabled", -1)
            if p2p_val == 1:
                p2p_text, p2p_color = "ВКЛ", "#4ade80"
            elif p2p_val == 0:
                p2p_text, p2p_color = "ВЫКЛ", "#f87171"
            else:
                p2p_text, p2p_color = "—", "#4a5060"
            p2p_item = QTableWidgetItem(p2p_text)
            p2p_item.setForeground(QColor(p2p_color))
            p2p_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.clients_table.setItem(row, 5, p2p_item)

    def refresh_allowed_table(self):
        steamids = db_list_allowed_steamids(limit=5000)

        self.allowed_table.setRowCount(len(steamids))
        for row, sid in enumerate(steamids):
            self.allowed_table.setItem(row, 0, QTableWidgetItem(sid))

    def on_copy_allowed_steamid(self):
        rows = self.allowed_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        item = self.allowed_table.item(row, 0)
        if not item:
            return
        QGuiApplication.clipboard().setText(item.text().strip())
        self.append_log(f"[GUI] Copied allowed SteamID: {item.text().strip()}")

    def on_copy_steamid(self):
        rows = self.clients_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        item = self.clients_table.item(row, 0)
        if not item:
            return
        QGuiApplication.clipboard().setText(item.text().strip())
        self.append_log(f"[GUI] Copied SteamID: {item.text().strip()}")

    def refresh_auth_label(self):
        st = db_allowed_stats()
        count = st["count"]
        upd = st["updated_at"]
        upd_str = "-" if upd <= 0 else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(upd))
        self.auth_status_lbl.setText(f"Auth: {count} ids, last={upd_str}")

    def on_sync_auth_now(self):
        # берём данные из полей + сохраняем в state.cfg
        url = self.auth_url_edit.text().strip()
        key = self.auth_key_edit.text().strip()

        self.state.cache_cfg["auth"]["auth_url"] = url
        self.state.cache_cfg["auth"]["auth_key"] = key

        self.append_log(f"[AUTH] Sync from: {url}")

        r = fetch_allowed_steamids_from_site(url, key)
        if r.get("ok"):
            db_replace_allowed_steamids(r["steamids"], int(r["updated_at"]))
            self.append_log(f"[AUTH] OK: {len(r['steamids'])} steamids")
            self.refresh_allowed_table()
        else:
            self.append_log(f"[AUTH] ERROR: {r.get('error')}")

        self.refresh_auth_label()
        self.save_config()

    def on_auth_tick(self):
        cfg = self.state.cache_cfg.get("auth", {})
        refresh_hours = int(cfg.get("auth_refresh_hours", 24))
        if refresh_hours <= 0:
            return

        st = db_allowed_stats()
        last = int(st["updated_at"] or 0)
        now = int(time.time())
        if last <= 0 or (now - last) >= refresh_hours * 3600:
            # тихая авто-синхра
            url = str(cfg.get("auth_url", "")).strip()
            key = str(cfg.get("auth_key", "")).strip()
            if not url:
                return
            r = fetch_allowed_steamids_from_site(url, key)
            if r.get("ok"):
                db_replace_allowed_steamids(r["steamids"], int(r["updated_at"]))
                self.append_log(f"[AUTH] Auto-sync OK: {len(r['steamids'])} steamids")
                self.refresh_auth_label()
                self.refresh_allowed_table()
            else:
                self.append_log(f"[AUTH] Auto-sync ERROR: {r.get('error')}")

    # ── Auth mode helpers ────────────────────────────────────

    def _set_auth_mode(self, mode: str):
        self.auth_mode_open_btn.setChecked(mode == "open")
        self.auth_mode_wl_btn.setChecked(mode == "whitelist")
        self.state.cache_cfg.setdefault("auth", {})["auth_mode"] = mode
        self._apply_auth_mode_ui(mode)
        self.save_config()
        self.append_log(f"[AUTH] Mode set to: {mode.upper()}")
        # Сообщить мастер-серверу об изменении режима немедленно
        if self._ms_client is not None:
            self._ms_client.set_whitelist_mode(mode == "whitelist")

    def _apply_auth_mode_ui(self, mode: str):
        is_wl = (mode == "whitelist")
        # Показываем/прячем весь блок конфига вайтлиста
        if hasattr(self, "_wl_config_frame"):
            self._wl_config_frame.setVisible(is_wl)
        # Compat: индивидуальные виджеты
        for w in (self._auth_url_row_widgets + self._auth_key_row_widgets):
            w.setVisible(is_wl)
        if is_wl:
            self.auth_mode_hint.setText("Разрешены только SteamID из вашего вайтлиста")
            self.auth_mode_hint.setStyleSheet("color: #f9a870; font-size: 11px;")
        else:
            self.auth_mode_hint.setText("Разрешён любой игрок со Steam-аккаунтом")
            self.auth_mode_hint.setStyleSheet("color: #4ade80; font-size: 11px;")
        self._update_ms_listing_state()

    def _update_ms_listing_state(self):
        """Show info hint in whitelist mode; listing itself remains available."""
        hint = getattr(self, "ms_listing_wl_hint", None)
        if hint is None:
            return
        is_wl = getattr(self, "auth_mode_wl_btn", None) and self.auth_mode_wl_btn.isChecked()
        hint.setVisible(bool(is_wl))

    def _is_whitelist_mode(self) -> bool:
        return getattr(self, "auth_mode_wl_btn", None) and self.auth_mode_wl_btn.isChecked()

    def on_show_whitelist_instructions(self):
        html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>ModSync — Whitelist Setup Guide</title>
<style>
  body { background: #1e1e2e; color: #cdd6f4; font-family: 'Segoe UI', sans-serif; margin: 0; padding: 32px 48px; }
  h1 { color: #89b4fa; border-bottom: 2px solid #313244; padding-bottom: 12px; }
  h2 { color: #cba6f7; margin-top: 36px; }
  pre { background: #181825; border: 1px solid #313244; border-radius: 6px; padding: 16px;
        color: #a6e3a1; font-family: Consolas, monospace; font-size: 13px;
        overflow-x: auto; white-space: pre; }
  code { background: #313244; border-radius: 3px; padding: 2px 6px; color: #f38ba8; font-family: Consolas, monospace; }
  .label { color: #89dceb; font-weight: bold; }
  .note { background: #2a273f; border-left: 4px solid #cba6f7; padding: 12px 16px;
          border-radius: 0 6px 6px 0; margin: 16px 0; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; }
  th { background: #313244; color: #89b4fa; padding: 8px 12px; text-align: left; }
  td { padding: 8px 12px; border-bottom: 1px solid #313244; }
  a { color: #89b4fa; }
</style>
</head>
<body>
<h1>🛡 ModSync — Whitelist Setup Guide</h1>
<p>Your server needs an HTTP endpoint that returns the list of allowed SteamIDs.
ModSync calls this URL periodically to refresh the whitelist automatically.</p>

<h2>1. Endpoint Specification</h2>
<table>
  <tr><th>Method</th><td>GET</td></tr>
  <tr><th>URL</th><td><code>https://yoursite.com/api/modsync/allowed_steamids</code></td></tr>
  <tr><th>Header</th><td><code>X-ModSync-Key: your_secret_key</code></td></tr>
</table>

<p><span class="label">Success response:</span></p>
<pre>{
    "ok": true,
    "steamids": ["76561198000000001", "76561198000000002"],
    "updated_at": 1700000000
}</pre>

<p><span class="label">Error response:</span></p>
<pre>{
    "ok": false,
    "error": "Forbidden"
}</pre>

<h2>2. Example — Python (Flask)</h2>
<pre>from flask import Flask, request, jsonify
import time

app = Flask(__name__)

AUTH_KEY = "your_secret_key_here"  # same key as in ModSync server config

ALLOWED_STEAMIDS = [
    "76561198000000001",
    "76561198000000002",
]

@app.route("/api/modsync/allowed_steamids", methods=["GET"])
def allowed_steamids():
    key = request.headers.get("X-ModSync-Key", "")
    if key != AUTH_KEY:
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    return jsonify({
        "ok": True,
        "steamids": ALLOWED_STEAMIDS,
        "updated_at": int(time.time())
    })</pre>

<h2>3. Example — PHP</h2>
<pre>&lt;?php
define('AUTH_KEY', 'your_secret_key_here');

$key = $_SERVER['HTTP_X_MODSYNC_KEY'] ?? '';
if ($key !== AUTH_KEY) {
    http_response_code(403);
    echo json_encode(['ok' => false, 'error' => 'Forbidden']);
    exit;
}

$steamids = ['76561198000000001', '76561198000000002'];

header('Content-Type: application/json');
echo json_encode([
    'ok'         => true,
    'steamids'   => $steamids,
    'updated_at' => time()
]);</pre>

<h2>4. Generating a Secret Key</h2>
<div class="note">
  The key can be any string — keep it private, never share it publicly.
</div>
<p>Generate a strong random key:</p>
<pre># Python
python -c "import secrets; print(secrets.token_hex(32))"</pre>
<p>Or use an online generator: <a href="https://generate-secret.vercel.app/32" target="_blank">generate-secret.vercel.app</a></p>

<h2>5. ModSync Server Config</h2>
<table>
  <tr><th>Auth URL</th><td><code>https://yoursite.com/api/modsync/allowed_steamids</code></td></tr>
  <tr><th>Auth Key</th><td>your_secret_key_here</td></tr>
  <tr><th>Mode</th><td>🛡 Whitelist</td></tr>
</table>
<p>Click <strong>Sync SteamIDs</strong> to test the connection immediately.<br>
The list refreshes automatically every N hours (configurable).</p>

</body>
</html>"""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False,
            encoding="utf-8", prefix="modsync_whitelist_guide_"
        )
        tmp.write(html)
        tmp.close()
        webbrowser.open(f"file:///{tmp.name.replace(chr(92), '/')}")

    # ── Blacklist ────────────────────────────────────────────

    def refresh_blacklist_table(self):
        rows = db_blacklist_list()
        self.blacklist_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            sid_item = QTableWidgetItem(r["steamid"])
            sid_item.setForeground(QColor("#f38ba8"))
            self.blacklist_table.setItem(i, 0, sid_item)
            ts = r["added_at"]
            ts_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "-"
            ts_item = QTableWidgetItem(ts_str)
            ts_item.setForeground(QColor("#8b9ebe"))
            self.blacklist_table.setItem(i, 1, ts_item)

    def on_blacklist_add(self):
        sid = self.bl_add_edit.text().strip()
        if not sid:
            QMessageBox.warning(self, "Blacklist", "Enter a SteamID first.")
            return
        db_blacklist_add(sid, reason="manual")
        self.bl_add_edit.clear()
        self.refresh_blacklist_table()
        self.append_log(f"[BAN] Added to blacklist: {sid}")

    def on_blacklist_remove(self):
        row = self.blacklist_table.currentRow()
        if row < 0:
            return
        sid = self.blacklist_table.item(row, 0).text()
        db_blacklist_remove(sid)
        self.refresh_blacklist_table()
        self.append_log(f"[BAN] Removed from blacklist: {sid}")

    def on_ban_from_clients(self):
        row = self.clients_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Ban", "Select a client row first.")
            return
        item = self.clients_table.item(row, 0)
        if not item:
            return
        sid = item.text().strip()
        reply = QMessageBox.question(
            self, "Ban SteamID",
            f"Add {sid} to blacklist?\nThey will be blocked from all future requests.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            db_blacklist_add(sid, reason="banned from clients list")
            self.refresh_blacklist_table()
            self.append_log(f"[BAN] Banned from clients table: {sid}")


    # ---------------- config ----------------

    def save_config(self):
        cfg = {
            "auto_publish": self.auto_publish_chk.isChecked(),
            "mods_path": self.mods_edit.text().strip(),
            "port": int(self.port_edit.text().strip()) if self.port_edit.text().strip().isdigit() else DEFAULT_PORT,
            "tracked_mods": sorted(list(self.state.tracked_mods), key=lambda x: x.lower()),
            "auto_start_server": True,
            "auth_url": self.state.cache_cfg.get("auth", {}).get("auth_url", ""),
            "auth_key": self.state.cache_cfg.get("auth", {}).get("auth_key", ""),
            "auth_refresh_hours": int(self.state.cache_cfg.get("auth", {}).get("auth_refresh_hours", 24)),
            "auth_fail_open": bool(self.state.cache_cfg.get("auth", {}).get("auth_fail_open", False)),
            "auth_mode": str(self.state.cache_cfg.get("auth", {}).get("auth_mode", "whitelist")),
            "upnp_enabled": self._upnp_enabled,
            "upnp_failed": getattr(self, "_upnp_failed", False),
            "lang": self._lang,
            "ms_listing": self.ms_listing_chk.isChecked(),
            "auto_port": self.auto_port_btn.isChecked(),
            "show_tooltips": getattr(self, "_show_tooltips", True),
            "server_name": self.server_name_edit.text().strip(),
            "restart_watch": self.restart_watch_chk.isChecked(),
        }
        write_config(cfg)

    # ── Restart watch ──────────────────────────────────────────

    @staticmethod
    def _get_7dtd_pid() -> int | None:
        """Возвращает PID 7DaysToDieServer.exe или None если не запущен."""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq 7DaysToDieServer.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.strip('"').split('","')
                if len(parts) >= 2:
                    try:
                        return int(parts[1])
                    except ValueError:
                        pass
        except Exception:
            pass
        return None

    def _start_restart_watch(self):
        if self._restart_watch_timer is not None:
            return
        self._restart_watch_pid = self._get_7dtd_pid()
        self._restart_watch_timer = QTimer(self)
        self._restart_watch_timer.setInterval(30_000)
        self._restart_watch_timer.timeout.connect(self._check_server_restart)
        self._restart_watch_timer.start()

    def _stop_restart_watch(self):
        if self._restart_watch_timer is not None:
            self._restart_watch_timer.stop()
            self._restart_watch_timer = None
        self._restart_watch_pid = None

    def _check_server_restart(self):
        new_pid = self._get_7dtd_pid()
        old_pid = self._restart_watch_pid
        if new_pid is None:
            self._restart_watch_pid = None
            return
        if old_pid is None:
            # Сервер только что обнаружен — запоминаем PID, не публикуем
            self._restart_watch_pid = new_pid
            return
        if new_pid != old_pid:
            self._restart_watch_pid = new_pid
            self.log_bridge.log_signal.emit(self.tr("log_rw_restarted", pid=new_pid))
            QTimer.singleShot(10_000, self._on_restart_republish)

    def _on_restart_republish(self):
        self.append_log(self.tr("log_rw_republish"))
        self.trigger_publish()

    def _on_restart_watch_toggled(self, on: bool):
        self.append_log(self.tr("log_rw_on" if on else "log_rw_off"))
        self.save_config()
        if on:
            self._start_restart_watch()
        else:
            self._stop_restart_watch()

    def closeEvent(self, event):
        if self.http_thread is not None:
            reply = QMessageBox.question(
                self,
                self.tr("dlg_close_title"),
                self.tr("dlg_close_text"),
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Ok:
                event.ignore()
                return
        self._stop_restart_watch()
        # Уведомить мастер-сервер об уходе в оффлайн синхронно перед закрытием
        if self._ms_client is not None:
            self._ms_client.send_offline()
        try:
            self.on_stop()
        except Exception:
            pass
        event.accept()


def main():
    ensure_appdata_dirs()
    db_init()

    app = QApplication(sys.argv)
    w = ServerWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()