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

# ─── UPnP ────────────────────────────────────────────────────
def _get_gateway_ip():
    """Получить IP шлюза через route print."""
    import subprocess, re
    try:
        r = subprocess.run(["route", "print", "0.0.0.0"],
                           capture_output=True, text=True, timeout=3)
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
CACHE_DIR = APPDATA_DIR / "cache"
DB_PATH = APPDATA_DIR / "server.sqlite"

DEFAULT_CACHE_MAX_GB = 20
DEFAULT_BUNDLE_TTL_DAYS = 14
DEFAULT_KEEP_MOD_VERSIONS = 2


# -----------------------------
# AppData / Config / DB
# -----------------------------

def ensure_appdata_dirs() -> None:
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


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
            bytes_sent_total INTEGER NOT NULL DEFAULT 0,
            last_client_manifest_hash TEXT NOT NULL DEFAULT '',
            last_server_manifest_hash TEXT NOT NULL DEFAULT ''
        );
        """)
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


def db_touch_client(
    steamid: str,
    ip: str,
    status: str,
    last_client_manifest_hash: str,
    last_server_manifest_hash: str
) -> None:
    if not steamid:
        return
    con = db_connect()
    try:
        con.execute("""
        INSERT INTO clients (steamid, last_seen, last_ip, status, bytes_sent_total, last_client_manifest_hash, last_server_manifest_hash)
        VALUES (?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(steamid) DO UPDATE SET
            last_seen=excluded.last_seen,
            last_ip=excluded.last_ip,
            status=excluded.status,
            last_client_manifest_hash=excluded.last_client_manifest_hash,
            last_server_manifest_hash=excluded.last_server_manifest_hash;
        """, (steamid, int(time.time()), ip, status, last_client_manifest_hash or "", last_server_manifest_hash or ""))
        con.commit()
    finally:
        con.close()


def db_add_bytes(steamid: str, add_bytes: int) -> None:
    if not steamid or add_bytes <= 0:
        return
    con = db_connect()
    try:
        con.execute("""
        INSERT INTO clients (steamid, last_seen, last_ip, status, bytes_sent_total, last_client_manifest_hash, last_server_manifest_hash)
        VALUES (?, 0, '', 'UNKNOWN', ?, '', '')
        ON CONFLICT(steamid) DO UPDATE SET
            bytes_sent_total = bytes_sent_total + ?;
        """, (steamid, int(add_bytes), int(add_bytes)))
        con.commit()
    finally:
        con.close()


def db_list_clients(limit: int = 500) -> list[dict]:
    con = db_connect()
    try:
        cur = con.execute("""
            SELECT steamid, last_seen, last_ip, status, bytes_sent_total, last_client_manifest_hash, last_server_manifest_hash
            FROM clients
            ORDER BY last_seen DESC
            LIMIT ?;
        """, (int(limit),))
        rows = cur.fetchall()
        out = []
        for r in rows:
            out.append({
                "steamid": r[0],
                "last_seen": r[1],
                "last_ip": r[2],
                "status": r[3],
                "bytes_sent_total": r[4],
                "last_client_manifest_hash": r[5],
                "last_server_manifest_hash": r[6],
            })
        return out
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


def safe_mod_id(mod_id: str) -> bool:
    if not mod_id or len(mod_id) > 200:
        return False
    bad = ["..", "/", "\\", ":", "%"]
    for b in bad:
        if b in mod_id:
            return False
    return True

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


def task_exists(task_name: str) -> bool:
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return r.returncode == 0


def create_autostart_task(task_name: str, command: str) -> tuple[bool, str]:
    """
    Создаёт задачу автозапуска при старте системы от имени user.
    Требуются права администратора.
    """
    # /F — перезаписать, если уже есть
    r = subprocess.run(
        ["schtasks", "/Create",
         "/TN", task_name,
         "/TR", command,
         "/SC", "ONLOGON",
         "/RU", "user",
         "/RL", "HIGHEST",
         "/IT",
         "/F"],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if r.returncode == 0:
        return True, (r.stdout or "").strip()
    return False, (r.stderr or r.stdout or "schtasks create failed").strip()


def delete_autostart_task(task_name: str) -> tuple[bool, str]:
    r = subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if r.returncode == 0:
        return True, (r.stdout or "").strip()
    return False, (r.stderr or r.stdout or "schtasks delete failed").strip()

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

def _iter_cache_files() -> list[Path]:
    ensure_appdata_dirs()
    if not CACHE_DIR.exists():
        return []
    return [p for p in CACHE_DIR.iterdir() if p.is_file()]


def cache_stats() -> tuple[int, int]:
    total = 0
    files = _iter_cache_files()
    for p in files:
        try:
            total += p.stat().st_size
        except Exception:
            pass
    return total, len(files)


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


def purge_cache(log_cb=None) -> None:
    files = _iter_cache_files()
    removed = 0
    freed = 0
    for p in files:
        try:
            sz = p.stat().st_size
            p.unlink(missing_ok=True)
            removed += 1
            freed += sz
        except Exception:
            pass
    if log_cb:
        log_cb(f"[CACHE] Purge: removed={removed}, freed={human_bytes(freed)}")


def enforce_cache_policy(cache_max_gb: int, keep_mod_versions: int, bundle_ttl_days: int, log_cb=None) -> None:
    ensure_appdata_dirs()
    now = time.time()
    max_bytes = int(cache_max_gb) * 1024 * 1024 * 1024
    ttl_sec = int(bundle_ttl_days) * 24 * 3600

    files = _iter_cache_files()

    mods_group: dict[str, list[Path]] = {}
    bundles: list[Path] = []

    for p in files:
        name = p.name
        if name.startswith("mod_") and name.endswith(".zip"):
            core = name[4:-4]
            if len(core) > 66 and core[-65] == "_":
                mod_id = core[:-65]
            else:
                mod_id = core
            mods_group.setdefault(mod_id, []).append(p)
        elif name.startswith("bundle_") and name.endswith(".zip"):
            bundles.append(p)

    removed = 0
    freed = 0

    # keep N per mod by mtime desc
    for mod_id, plist in mods_group.items():
        plist_sorted = sorted(plist, key=lambda x: x.stat().st_mtime, reverse=True)
        for p in plist_sorted[int(keep_mod_versions):]:
            try:
                sz = p.stat().st_size
                p.unlink(missing_ok=True)
                removed += 1
                freed += sz
            except Exception:
                pass

    # TTL for bundles
    for p in bundles:
        try:
            age = now - p.stat().st_mtime
            if ttl_sec > 0 and age > ttl_sec:
                sz = p.stat().st_size
                p.unlink(missing_ok=True)
                removed += 1
                freed += sz
        except Exception:
            pass

    # global cap by oldest mtime
    files2 = _iter_cache_files()
    files2_sorted = sorted(files2, key=lambda x: x.stat().st_mtime)

    cur_total = sum(pp.stat().st_size for pp in files2_sorted if pp.exists())

    if max_bytes > 0 and cur_total > max_bytes:
        for p in files2_sorted:
            if cur_total <= max_bytes:
                break
            try:
                sz = p.stat().st_size
                p.unlink(missing_ok=True)
                removed += 1
                freed += sz
                cur_total -= sz
            except Exception:
                pass

    if log_cb and (removed > 0 or freed > 0):
        log_cb(f"[CACHE] Cleanup: removed={removed}, freed={human_bytes(freed)}")

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
# ZIP / cache
# -----------------------------

def zip_mod_to_cache(mods_root: Path, mod_id: str, mod_hash: str, log_cb, cache_cfg: dict) -> Path:
    ensure_appdata_dirs()
    out_path = CACHE_DIR / f"mod_{mod_id}_{mod_hash}.zip"
    if out_path.exists():
        return out_path

    mod_dir = mods_root / mod_id
    if not mod_dir.exists() or not mod_dir.is_dir():
        raise FileNotFoundError(f"Mod folder not found: {mod_dir}")

    tmp_path = CACHE_DIR / f".tmp_mod_{mod_id}_{mod_hash}_{int(time.time())}.zip"
    if log_cb:
        log_cb(f"[ZIP] Building mod zip: {mod_id}")

    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in mod_dir.rglob("*"):
            if p.is_file():
                rel_inside = f"{mod_id}/{p.relative_to(mod_dir).as_posix()}"
                z.write(p, rel_inside)

    tmp_path.replace(out_path)
    if log_cb:
        log_cb(f"[ZIP] Cached: {out_path.name}")

    enforce_cache_policy(
        cache_max_gb=int(cache_cfg.get("cache_max_gb", DEFAULT_CACHE_MAX_GB)),
        keep_mod_versions=int(cache_cfg.get("keep_mod_versions", DEFAULT_KEEP_MOD_VERSIONS)),
        bundle_ttl_days=int(cache_cfg.get("bundle_ttl_days", DEFAULT_BUNDLE_TTL_DAYS)),
        log_cb=log_cb
    )
    return out_path


def zip_bundle_to_cache(mods_root: Path, mods: list[dict], log_cb, cache_cfg: dict) -> Path:
    ensure_appdata_dirs()
    items = [f"{m['id']}:{m['hash']}" for m in mods]
    items.sort(key=lambda s: s.lower())
    key = sha256(("|".join(items)).encode("utf-8")).hexdigest()

    out_path = CACHE_DIR / f"bundle_{key}.zip"
    if out_path.exists():
        return out_path

    tmp_path = CACHE_DIR / f".tmp_bundle_{key}_{int(time.time())}.zip"
    if log_cb:
        log_cb(f"[ZIP] Building bundle zip ({len(mods)} mods)")

    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for m in mods:
            mod_id = m["id"]
            mod_dir = mods_root / mod_id
            if not mod_dir.exists() or not mod_dir.is_dir():
                raise FileNotFoundError(f"Mod folder not found: {mod_dir}")

            for p in mod_dir.rglob("*"):
                if p.is_file():
                    rel_inside = f"{mod_id}/{p.relative_to(mod_dir).as_posix()}"
                    z.write(p, rel_inside)

    tmp_path.replace(out_path)
    if log_cb:
        log_cb(f"[ZIP] Cached bundle: {out_path.name}")

    enforce_cache_policy(
        cache_max_gb=int(cache_cfg.get("cache_max_gb", DEFAULT_CACHE_MAX_GB)),
        keep_mod_versions=int(cache_cfg.get("keep_mod_versions", DEFAULT_KEEP_MOD_VERSIONS)),
        bundle_ttl_days=int(cache_cfg.get("bundle_ttl_days", DEFAULT_BUNDLE_TTL_DAYS)),
        log_cb=log_cb
    )
    return out_path


# -----------------------------
# Состояние / Bridges
# -----------------------------

@dataclass
class ServerState:
    manifest: dict
    lock: threading.Lock = field(default_factory=threading.Lock)
    cache_cfg: dict = field(default_factory=dict)
    tracked_mods: set[str] = field(default_factory=set)

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
        self._log(f"[HTTP] {self.address_string()} - {fmt % args}")

    def _stream_file(self, file_path: Path, download_name: str, steamid: str = "") -> None:
        st = file_path.stat()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(st.st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()

        db_add_bytes(steamid, int(st.st_size))

        with file_path.open("rb") as f:
            while True:
                chunk = f.read(8 * 1024 * 1024)  # 8 MB chunks — optimal for large files
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/ping":
            self._send_json({"ok": True, "ts": int(time.time())})
            return

        if path == "/manifest":
            self._send_json(self.state.get_manifest())
            return

        if path == "/mods":
            m = self.state.get_manifest()
            mods = []
            for mod in m.get("mods", []):
                mods.append({
                    "id": mod.get("id"),
                    "hash": mod.get("hash"),
                    "size_bytes": mod.get("size_bytes", 0),
                    "files_count": mod.get("files_count", 0),
                })
            self._send_json({"ok": True, "mods": mods, "server_manifest_hash": m.get("manifest_hash")})
            return

        if path == "/clients":
            qs = parse_qs(parsed.query)
            try:
                limit = int((qs.get("limit", ["500"])[0] or "500"))
            except Exception:
                limit = 500
            items = db_list_clients(limit=limit)
            self._send_json({"ok": True, "clients": items})
            return

        if path.startswith("/mod/"):
            mod_id = path[len("/mod/"):]
            qs = parse_qs(parsed.query)
            req_hash = (qs.get("hash", [""])[0] or "").strip()
            steamid = (qs.get("steamid", [""])[0] or "").strip()
            ok, reason = self.require_auth(steamid)
            if not ok:
                self._send_json({"ok": False, "error": reason}, code=403)
                return

            if not safe_mod_id(mod_id):
                self._send_json({"ok": False, "error": "Bad mod id"}, code=400)
                return

            mod_map = self.state.get_mod_map()
            if mod_id not in mod_map:
                self._send_json({"ok": False, "error": "Mod not found"}, code=404)
                return

            server_mod = mod_map[mod_id]
            server_hash = str(server_mod.get("hash", "")).strip()

            if req_hash and req_hash != server_hash:
                self._send_json({"ok": False, "error": "Hash mismatch", "server_hash": server_hash}, code=409)
                return

            if server_hash in ("", "ERROR"):
                self._send_json({"ok": False, "error": "Server mod hash error"}, code=500)
                return

            mods_root = self.state.get_mods_root()
            try:
                zip_path = zip_mod_to_cache(mods_root, mod_id, server_hash, self._log, self.state.cache_cfg)
            except Exception as e:
                self._log(f"[MOD] ERROR building zip: {e}")
                self._send_json({"ok": False, "error": "Failed to build zip"}, code=500)
                return

            self._log(f"[MOD] Send '{mod_id}' -> {zip_path.name} ({human_bytes(zip_path.stat().st_size)})")
            self._stream_file(zip_path, f"{mod_id}.zip", steamid=steamid)
            return

        self._send_json({"ok": False, "error": "Not found"}, code=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/heartbeat":
            data = self._read_json()
            if not data:
                self._send_json({"ok": False, "error": "Bad JSON"}, code=400)
                return

            steamid = str(data.get("steamid", "")).strip()
            ok, reason = self.require_auth(steamid)
            if not ok:
                self._send_json({"ok": False, "error": reason}, code=403)
                return

            client_manifest_hash = str(data.get("client_manifest_hash", "")).strip()

            server_manifest_hash = str(self.state.get_manifest().get("manifest_hash", "")).strip()
            status = "OK" if client_manifest_hash and client_manifest_hash == server_manifest_hash else "OUTDATED"

            db_touch_client(
                steamid=steamid,
                ip=self._get_client_ip(),
                status=status,
                last_client_manifest_hash=client_manifest_hash,
                last_server_manifest_hash=server_manifest_hash
            )

            self._log(f"[HEARTBEAT] steamid={steamid or 'N/A'} status={status}")
            self._send_json({"ok": True, "status": status, "server_manifest_hash": server_manifest_hash})
            return

        if path == "/diff":
            data = self._read_json()
            if not data:
                self._send_json({"ok": False, "error": "Bad JSON"}, code=400)
                return

            steamid = str(data.get("steamid", "")).strip()
            ok, reason = self.require_auth(steamid)
            if not ok:
                self._send_json({"ok": False, "error": reason}, code=403)
                return

            client_mods = data.get("mods", [])
            client_manifest_hash = str(data.get("client_manifest_hash", "")).strip()

            if not isinstance(client_mods, list):
                self._send_json({"ok": False, "error": "mods must be a list"}, code=400)
                return

            client_map = {}
            for item in client_mods:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("id", "")).strip()
                mh = str(item.get("hash", "")).strip()
                if mid:
                    client_map[mid] = mh

            server_manifest = self.state.get_manifest()
            server_hash = server_manifest.get("manifest_hash")
            server_mods = server_manifest.get("mods", [])
            server_map = {m.get("id"): m for m in server_mods if isinstance(m, dict) and m.get("id")}

            download = []
            delete = []

            for mid, smod in server_map.items():
                sh = str(smod.get("hash", "")).strip()
                if mid not in client_map or client_map[mid] != sh:
                    download.append({"id": mid, "hash": sh, "size_bytes": smod.get("size_bytes", 0)})

            for mid in client_map.keys():
                if mid not in server_map:
                    delete.append(mid)

            # OK если у клиента нечего скачивать и нечего удалять
            status = "OK" if (len(download) == 0 and len(delete) == 0) else "OUTDATED"
            db_touch_client(
                steamid=steamid,
                ip=self._get_client_ip(),
                status=status,
                last_client_manifest_hash=str(server_hash or ""),  # сохраняем серверный хеш — он теперь "эталон"
                last_server_manifest_hash=str(server_hash or "")
            )

            self._log(f"[DIFF] steamid={steamid or 'N/A'} download={len(download)} delete={len(delete)} status={status}")
            self._send_json({
                "ok": True,
                "server_manifest_hash": server_hash,
                "download": download,
                "delete": delete,
            })
            return

        if path == "/bundle":
            data = self._read_json()
            if not data:
                self._send_json({"ok": False, "error": "Bad JSON"}, code=400)
                return

            steamid = str(data.get("steamid", "")).strip()
            ok, reason = self.require_auth(steamid)
            if not ok:
                self._send_json({"ok": False, "error": reason}, code=403)
                return

            server_map = self.state.get_mod_map()

            normalized = []

            if isinstance(data.get("ids"), list):
                for mid in data["ids"]:
                    mid = str(mid).strip()
                    if not mid:
                        continue
                    if not safe_mod_id(mid):
                        self._send_json({"ok": False, "error": f"Bad mod id: {mid}"}, code=400)
                        return
                    if mid not in server_map:
                        self._send_json({"ok": False, "error": f"Mod not found: {mid}"}, code=404)
                        return
                    sh = str(server_map[mid].get("hash", "")).strip()
                    if sh in ("", "ERROR"):
                        self._send_json({"ok": False, "error": f"Server hash error for {mid}"}, code=500)
                        return
                    normalized.append({"id": mid, "hash": sh})

            elif isinstance(data.get("mods"), list):
                for item in data["mods"]:
                    if not isinstance(item, dict):
                        continue
                    mid = str(item.get("id", "")).strip()
                    mh = str(item.get("hash", "")).strip()
                    if not mid:
                        continue
                    if not safe_mod_id(mid):
                        self._send_json({"ok": False, "error": f"Bad mod id: {mid}"}, code=400)
                        return
                    if mid not in server_map:
                        self._send_json({"ok": False, "error": f"Mod not found: {mid}"}, code=404)
                        return
                    sh = str(server_map[mid].get("hash", "")).strip()
                    if sh in ("", "ERROR"):
                        self._send_json({"ok": False, "error": f"Server hash error for {mid}"}, code=500)
                        return
                    if mh and mh != sh:
                        self._send_json({"ok": False, "error": f"Hash mismatch for {mid}", "server_hash": sh}, code=409)
                        return
                    normalized.append({"id": mid, "hash": sh})
            else:
                self._send_json({"ok": False, "error": "Provide ids:[..] or mods:[{id,hash?}]"}, code=400)
                return

            if not normalized:
                self._send_json({"ok": False, "error": "No valid mods in request"}, code=400)
                return

            mods_root = self.state.get_mods_root()
            try:
                zip_path = zip_bundle_to_cache(mods_root, normalized, self._log, self.state.cache_cfg)
            except Exception as e:
                self._log(f"[BUNDLE] ERROR building bundle: {e}")
                self._send_json({"ok": False, "error": "Failed to build bundle"}, code=500)
                return

            self._log(f"[BUNDLE] Send bundle -> {zip_path.name} ({human_bytes(zip_path.stat().st_size)})")
            self._stream_file(zip_path, "mods.zip", steamid=steamid)
            return

        self._send_json({"ok": False, "error": "Not found"}, code=404)


class HttpServerThread(threading.Thread):
    def __init__(self, host: str, port: int, state: ServerState, log: LogBridge):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.state = state
        self.log = log
        self.httpd: ThreadingHTTPServer | None = None

    def run(self):
        try:
            self.httpd = ThreadingHTTPServer((self.host, self.port), ApiHandler)
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
# Bridges
# -----------------------------

class ManifestBridge(QObject):
    manifest_ready = pyqtSignal(dict)


# -----------------------------
# GUI
# -----------------------------

DARK_STYLE = """
QWidget {
    background-color: #1a1d2e;
    color: #c9d1d9;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
}
QGroupBox {
    background-color: #212436;
    border: 1px solid #30364a;
    border-radius: 8px;
    margin-top: 18px;
    padding: 10px 10px 6px 10px;
    font-weight: bold;
    color: #8b9ebe;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 0px;
    padding: 2px 6px;
    color: #7aa2f7;
    font-size: 11px;
    letter-spacing: 1px;
    text-transform: uppercase;
    background-color: #212436;
}
QLineEdit {
    background-color: #0d1117;
    border: 1px solid #30364a;
    border-radius: 5px;
    padding: 5px 8px;
    color: #c9d1d9;
    selection-background-color: #3d59a1;
}
QLineEdit:focus {
    border: 1px solid #7aa2f7;
}
QPushButton {
    background-color: #2a2f45;
    border: 1px solid #3d4663;
    border-radius: 6px;
    padding: 5px 16px;
    color: #c9d1d9;
    font-weight: 500;
    min-height: 28px;
    min-width: 40px;
}
QPushButton:hover {
    background-color: #313857;
    border-color: #7aa2f7;
    color: #ffffff;
}
QPushButton:pressed {
    background-color: #1a1f36;
}
QPushButton:focus {
    outline: none;
    border-color: #5a7abf;
}
QPushButton:disabled {
    background-color: #1e2235;
    border-color: #252a40;
    color: #4a5270;
}
QPushButton#start_btn {
    background-color: #1a3a2a;
    border-color: #2ea04b;
    color: #3fb950;
    font-weight: bold;
}
QPushButton#start_btn:hover {
    background-color: #1f4a32;
    border-color: #3fb950;
    color: #4dca60;
}
QPushButton#stop_btn {
    background-color: #3a1a1a;
    border-color: #a02e2e;
    color: #f85149;
    font-weight: bold;
}
QPushButton#stop_btn:hover {
    background-color: #4a2020;
    border-color: #f85149;
}
QPushButton#scan_btn {
    background-color: #1a2a3a;
    border-color: #2e6aa0;
    color: #58a6ff;
}
QPushButton#scan_btn:hover {
    background-color: #1f3550;
    border-color: #58a6ff;
}
QPushButton#purge_btn {
    background-color: #2a1a0a;
    border-color: #a05e2e;
    color: #e3814c;
}
QPushButton#purge_btn:hover {
    border-color: #e3814c;
}
QPushButton#mode_btn {
    background-color: #1e2235;
    border: 1px solid #3d4663;
    border-radius: 6px;
    padding: 5px 14px;
    color: #6a7494;
    font-weight: 500;
    min-height: 26px;
}
QPushButton#mode_btn:hover {
    border-color: #7aa2f7;
    color: #c9d1d9;
}
QPushButton#mode_btn:checked {
    background-color: #1a2d4a;
    border: 2px solid #7aa2f7;
    color: #89b4fa;
    font-weight: bold;
}
QPushButton#spin_btn {
    background-color: #1e2235;
    border: 1px solid #3d4663;
    border-radius: 4px;
    padding: 0px 4px;
    color: #7aa2f7;
    font-size: 11px;
    min-width: 20px;
    min-height: 20px;
    max-width: 20px;
    max-height: 20px;
    font-weight: normal;
}
QPushButton#spin_btn:hover {
    background-color: #2a3050;
    border-color: #7aa2f7;
    color: #ffffff;
}
QPushButton#spin_btn:pressed {
    background-color: #1a1f36;
}
QPushButton#spin_btn:focus {
    outline: none;
    border-color: #3d4663;
}
QProgressBar {
    background-color: #0d1117;
    border: 1px solid #30364a;
    border-radius: 5px;
    text-align: center;
    color: #c9d1d9;
    font-size: 11px;
    max-height: 16px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #3d5999, stop:1 #7aa2f7);
    border-radius: 4px;
}
QProgressBar#cache_bar[warningLevel="warn"]::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #a07020, stop:1 #f0a020);
}
QProgressBar#cache_bar[warningLevel="crit"]::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #a02020, stop:1 #f05050);
}
QTextEdit {
    background-color: #0d1117;
    border: 1px solid #21262d;
    border-radius: 6px;
    color: #8b9ebe;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    padding: 4px;
}
QTableWidget {
    background-color: #0d1117;
    border: 1px solid #21262d;
    border-radius: 6px;
    gridline-color: #21262d;
    color: #c9d1d9;
    selection-background-color: #1f2d4a;
    alternate-background-color: #111520;
}
QTableWidget::item:selected {
    background-color: #1f2d4a;
    color: #ffffff;
}
QTableWidget::item:focus {
    outline: none;
    border: none;
}
QTableCornerButton::section {
    background-color: #0d1117;
    border: 1px solid #21262d;
}

QHeaderView::section {
    background-color: #161b27;
    color: #8b9ebe;
    border: none;
    border-bottom: 1px solid #30364a;
    padding: 6px 8px;
    font-weight: bold;
    font-size: 11px;
    letter-spacing: 0.5px;
}
QHeaderView::section:first {
    border-radius: 6px 0 0 0;
}
QTabWidget::pane {
    border: 1px solid #30364a;
    border-radius: 6px;
    background-color: #1a1d2e;
}
QTabBar::tab {
    background-color: #161b27;
    border: 1px solid #21262d;
    border-bottom: none;
    border-radius: 5px 5px 0 0;
    padding: 6px 18px;
    color: #8b9ebe;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #1a1d2e;
    color: #7aa2f7;
    border-color: #30364a;
}
QTabBar::tab:hover:!selected {
    background-color: #1e2335;
    color: #c9d1d9;
}
QSplitter::handle {
    background-color: #30364a;
    width: 2px;
}
QScrollBar:vertical {
    background: #0d1117;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #30364a;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background: #4a5270;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QLabel {
    background: transparent;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #3d4663;
    background: #0d1117;
}
QCheckBox::indicator:checked {
    background: #1a3a6a;
    border: 2px solid #7aa2f7;
    image: none;
}
"""





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



# ─── LOCALISATION (EN / RU) ──────────────────────────────────
TRANSLATIONS = {
    "en": {
        "window_title":         "ModSync Server",
        # Groups
        "grp_server":           "Server",
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
        "btn_select":           "📁  Select",
        "btn_select_tip":       "Select mods folder",
        "btn_auto_port":        "Auto",
        "btn_start":            "▶  Start",
        "btn_stop":             "■  Stop",
        "btn_autostart_on":     "Autostart: ON",
        "btn_autostart_off":    "Autostart: OFF",
        "btn_upnp_off":         "🌐  UPnP: OFF",
        "btn_upnp_on":          "🌐  UPnP: ON",
        "btn_upnp_failed":      "🌐  UPnP: FAILED",
        "btn_upnp_tip":         "Auto port-forward via UPnP.\nRequires UPnP-enabled router.",
        "btn_upnp_fail_tip":    "Could not open port {port} automatically.\nForward it manually in router settings:\n  Protocol: TCP\n  External port: {port}\n  Internal IP: {ip}\n  Internal port: {port}",
        "btn_upnp_ok_tip":      "UPnP active — external {ip}:{port}",
        "btn_apply_cache":      "✔  Apply",
        "btn_scan":             "⟳  Scan",
        "btn_purge":            "🗑  Purge",
        "btn_open_cache":       "📂  Open Cache",
        "btn_open_cache_tip":   "Open cache folder in Explorer",
        "btn_mode_open":        "🌐  Open",
        "btn_mode_wl":          "🛡  Whitelist",
        "btn_instr":            "📖  How to setup Whitelist",
        "btn_sync_auth":        "⟳  Sync SteamIDs",
        "btn_copy_steamid":     "📋  Copy SteamID",
        "btn_ban":              "🚫  Ban",
        "btn_ban_tip":          "Add selected SteamID to blacklist",
        "btn_unban":            "✅  Unban",
        "btn_copy_allowed":     "📋  Copy SteamID",
        "btn_mods_all":         "All",
        "btn_mods_none":        "None",
        "btn_mods_apply":       "✔  Apply",
        "btn_lang":             "🌐 RU",
        # Tabs
        "tab_clients":          "Clients",
        "tab_whitelist":        "Whitelist",
        "tab_mods":             "Mods",
        "tab_blacklist":        "Blacklist",
        # Table headers
        "th_steamid":           "SteamID",
        "th_last_seen":         "Last Seen",
        "th_ip":                "IP",
        "th_status":            "Status",
        "th_sent":              "Sent",
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
        "upnp_restore_tip":     "Автоматически пробросить порт через UPnP.\nТребует роутер с включённым UPnP.",
    },
    "ru": {
        "window_title":         "ModSync Server",
        "grp_server":           "Сервер",
        "grp_cache":            "Кэш",
        "grp_access":           "Контроль доступа",
        "lbl_mods_path":        "Папка модов:",
        "lbl_port":             "Порт:",
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
        "btn_select":           "📁  Выбрать",
        "btn_select_tip":       "Выбрать папку с модами",
        "btn_auto_port":        "Авто",
        "btn_start":            "▶  Старт",
        "btn_stop":             "■  Стоп",
        "btn_autostart_on":     "Автозапуск: ВКЛ",
        "btn_autostart_off":    "Автозапуск: ВЫКЛ",
        "btn_upnp_off":         "🌐  UPnP: ВЫКЛ",
        "btn_upnp_on":          "🌐  UPnP: ВКЛ",
        "btn_upnp_failed":      "🌐  UPnP: ОШИБКА",
        "btn_upnp_tip":         "Автоматически пробросить порт через UPnP.\nТребует роутер с включённым UPnP.",
        "btn_upnp_fail_tip":    "Не удалось автоматически пробросить порт {port}.\nОткройте порт вручную в настройках роутера:\n  Протокол: TCP\n  Внешний порт: {port}\n  Внутренний IP: {ip}\n  Внутренний порт: {port}",
        "btn_upnp_ok_tip":      "UPnP активен — внешний адрес {ip}:{port}",
        "btn_apply_cache":      "✔  Применить",
        "btn_scan":             "⟳  Сканировать",
        "btn_purge":            "🗑  Очистить",
        "btn_open_cache":       "📂  Открыть кэш",
        "btn_open_cache_tip":   "Открыть папку кэша в Проводнике",
        "btn_mode_open":        "🌐  Общий",
        "btn_mode_wl":          "🛡  Вайтлист",
        "btn_instr":            "📖  Настройка вайтлиста",
        "btn_sync_auth":        "⟳  Синхронизировать",
        "btn_copy_steamid":     "📋  Копировать SteamID",
        "btn_ban":              "🚫  Бан",
        "btn_ban_tip":          "Добавить SteamID в чёрный список",
        "btn_unban":            "✅  Разбан",
        "btn_copy_allowed":     "📋  Копировать SteamID",
        "btn_mods_all":         "Все",
        "btn_mods_none":        "Ничего",
        "btn_mods_apply":       "✔  Применить",
        "btn_lang":             "🌐 EN",
        "tab_clients":          "Клиенты",
        "tab_whitelist":        "Вайтлист",
        "tab_mods":             "Моды",
        "tab_blacklist":        "Чёрный список",
        "th_steamid":           "SteamID",
        "th_last_seen":         "Последний визит",
        "th_ip":                "IP",
        "th_status":            "Статус",
        "th_sent":              "Отправлено",
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
    },
}

class ServerWindow(QWidget):
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

        cfg = read_config()
        auth_url = cfg.get("auth_url", "")
        auth_key = cfg.get("auth_key", "")
        auth_refresh_hours = int(cfg.get("auth_refresh_hours", 24))
        auth_fail_open = bool(cfg.get("auth_fail_open", False))
        auth_mode = cfg.get("auth_mode", "whitelist")

        mods_path = cfg.get("mods_path", DEFAULT_MODS_PATH)
        port = int(cfg.get("port", DEFAULT_PORT))

        cache_max_gb = int(cfg.get("cache_max_gb", DEFAULT_CACHE_MAX_GB))
        keep_mod_versions = int(cfg.get("keep_mod_versions", DEFAULT_KEEP_MOD_VERSIONS))
        bundle_ttl_days = int(cfg.get("bundle_ttl_days", DEFAULT_BUNDLE_TTL_DAYS))
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
                "cache_max_gb": cache_max_gb,
                "keep_mod_versions": keep_mod_versions,
                "bundle_ttl_days": bundle_ttl_days,
                "auth": {
                    "auth_url": auth_url,
                    "auth_key": auth_key,
                    "auth_refresh_hours": auth_refresh_hours,
                    "auth_fail_open": auth_fail_open,
                "auth_mode": auth_mode
                }
            },
            tracked_mods=tracked_set
        )

        self.http_thread: HttpServerThread | None = None
        self.host = "0.0.0.0"
        self._running_ip = ""

        # watcher + debounce
        self.fs_watcher = QFileSystemWatcher()
        self.fs_watcher.directoryChanged.connect(self.on_fs_changed)
        self.fs_watcher.fileChanged.connect(self.on_fs_changed)

        self.rescan_debounce = QTimer(self)
        self.rescan_debounce.setSingleShot(True)
        self.rescan_debounce.timeout.connect(self.on_scan_async)

        # cache stats timer
        self.cache_timer = QTimer(self)
        self.cache_timer.setInterval(2000)
        self.cache_timer.timeout.connect(self.refresh_cache_label)

        # clients table timer
        self.clients_timer = QTimer(self)
        self.clients_timer.setInterval(3000)
        self.clients_timer.timeout.connect(self.refresh_clients_table)

        # auth timer
        self.auth_timer = QTimer(self)
        self.auth_timer.setInterval(60 * 60 * 1000)
        self.auth_timer.timeout.connect(self.on_auth_tick)
        self.auth_timer.start()

        # ───────────────────────────────────────────────
        # ВЕРХНЯЯ ПАНЕЛЬ: путь к модам + управление сервером
        # ───────────────────────────────────────────────
        self.server_group = QGroupBox("Server")
        server_l = QVBoxLayout(self.server_group)
        server_l.setSpacing(8)

        # Строка 1: путь + кнопка выбора папки
        row1 = QHBoxLayout()
        self.lbl_mods_path = QLabel("Mods path:")
        self.lbl_mods_path.setStyleSheet("color: #7aa2f7; font-size: 11px; font-weight: bold;")
        row1.addWidget(self.lbl_mods_path)
        self.mods_edit = QLineEdit(mods_path)
        self.mods_edit.setMinimumWidth(200)
        row1.addWidget(self.mods_edit, 1)

        self.select_mods_btn = QPushButton("📁  Select")
        self.select_mods_btn.setObjectName("scan_btn")
        self.select_mods_btn.setToolTip("Select mods folder")
        self.select_mods_btn.clicked.connect(self.on_select_mods_folder)
        row1.addWidget(self.select_mods_btn)
        server_l.addLayout(row1)

        # Строка 2: порт + кнопки + статус
        row2 = QHBoxLayout()
        self.lbl_port = QLabel("Port:")
        self.lbl_port.setStyleSheet("color: #7aa2f7; font-size: 11px; font-weight: bold;")
        row2.addWidget(self.lbl_port)
        self.port_edit = QLineEdit(str(port))
        self.port_edit.setFixedWidth(80)
        row2.addWidget(self.port_edit)

        self.auto_port_btn = QPushButton("Auto")
        self.auto_port_btn.setFixedWidth(60)
        self.auto_port_btn.clicked.connect(self.on_auto_port)
        row2.addWidget(self.auto_port_btn)

        row2.addSpacing(16)

        self.start_btn = QPushButton("▶  Start")
        self.start_btn.setObjectName("start_btn")
        self.start_btn.setFixedWidth(100)
        self.start_btn.clicked.connect(self.on_start)
        row2.addWidget(self.start_btn)

        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setObjectName("stop_btn")
        self.stop_btn.setFixedWidth(100)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_stop)
        row2.addWidget(self.stop_btn)

        self.autostart_btn = QPushButton("Autostart: OFF")
        self.autostart_btn.setCheckable(True)
        self.autostart_btn.clicked.connect(self.on_toggle_autostart)
        row2.addWidget(self.autostart_btn)

        self.upnp_btn = QPushButton("🌐  UPnP: OFF")
        self.upnp_btn.setCheckable(True)
        self.upnp_btn.setToolTip(
            "Автоматически пробросить порт через UPnP.\n"
            "Требует роутер с включённым UPnP."
        )
        self.upnp_btn.clicked.connect(self.on_toggle_upnp)
        row2.addWidget(self.upnp_btn)

        self.lang_btn = QPushButton("🌐 EN")
        self.lang_btn.setFixedWidth(66)
        self.lang_btn.clicked.connect(self.on_toggle_lang)
        row2.addWidget(self.lang_btn)
        self._lang = cfg.get("lang", "ru")
        self._upnp_enabled = bool(cfg.get("upnp_enabled", False))
        self._upnp_failed = bool(cfg.get("upnp_failed", False))
        self._upnp_port: int | None = None
        if self._upnp_enabled:
            self.upnp_btn.setChecked(True)
            self.upnp_btn.setText("🌐  UPnP: ON")
        if self._upnp_failed:
            self.upnp_btn.setEnabled(False)
            self.upnp_btn.setText("🌐  UPnP: FAILED")
            port_val = cfg.get("port", 8765)
            lan_ip = cfg.get("mods_path", "")  # placeholder, real IP at runtime
            self.upnp_btn.setToolTip(
                f"Не удалось автоматически пробросить порт {port_val}.\n"
                "Откройте порт вручную в настройках роутера:\n"
                f"  Протокол: TCP\n"
                f"  Внешний порт: {port_val}\n"
                f"  Внутренний порт: {port_val}"
            )

        row2.addSpacing(16)

        self.status_light = StatusLight()
        row2.addWidget(self.status_light)

        # Статус — кликабельный лейбл (копирует IP в буфер)
        self.status_lbl = QLabel("Stopped")
        self.status_lbl.setStyleSheet("color: #f85149; font-weight: bold; font-size: 13px;")
        self.status_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_lbl.mousePressEvent = self._on_status_lbl_click
        row2.addWidget(self.status_lbl)

        row2.addStretch(1)
        server_l.addLayout(row2)

        # ───────────────────────────────────────────────
        # НАСТРОЙКИ КЭША
        # ───────────────────────────────────────────────
        def _spin(layout, val, lo, hi, attr_name):
            """Добавляет ◀ поле ▶ прямо в layout, без обёрток."""
            minus = QPushButton("◀"); minus.setObjectName("spin_btn"); minus.setFixedSize(20, 24)
            edit = QLineEdit(str(val)); edit.setFixedSize(50, 24); edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            plus = QPushButton("▶"); plus.setObjectName("spin_btn"); plus.setFixedSize(20, 24)
            layout.addWidget(minus); layout.addWidget(edit); layout.addWidget(plus)
            setattr(self, attr_name, edit)
            def _safe_inc():
                try: edit.setText(str(min(int(edit.text()) + 1, hi)))
                except Exception: pass
            def _safe_dec():
                try: edit.setText(str(max(int(edit.text()) - 1, lo)))
                except Exception: pass
            plus.clicked.connect(_safe_inc)
            minus.clicked.connect(_safe_dec)

        def _clbl(text):
            l = QLabel(text); l.setStyleSheet("color: #8b9ebe; font-size: 12px;")
            return l

        self.cache_group = QGroupBox("Cache Policy")
        cache_vl = QVBoxLayout(self.cache_group)
        cache_vl.setSpacing(5)
        cache_vl.setContentsMargins(12, 6, 12, 8)

        # Строка 1: Max size: [+] 20 [−]  ▓░░ 8% used  Cache: 1.73 GB…
        r1 = QHBoxLayout(); r1.setSpacing(8); r1.setContentsMargins(0,0,0,0)
        self.lbl_cache_max = _clbl("Max size:")
        r1.addWidget(self.lbl_cache_max)
        _spin(r1, cache_max_gb, 1, 500, "cache_max_edit")
        self.cache_bar = QProgressBar()
        self.cache_bar.setObjectName("cache_bar")
        self.cache_bar.setRange(0, 100); self.cache_bar.setValue(0)
        self.cache_bar.setFormat("%p%  used"); self.cache_bar.setFixedWidth(140)
        self.cache_bar.setFixedHeight(24)
        r1.addWidget(self.cache_bar)
        self.cache_lbl = QLabel("Cache: …"); self.cache_lbl.setStyleSheet("color: #8b9ebe; font-size: 11px;")
        r1.addWidget(self.cache_lbl)
        r1.addStretch(1)
        cache_vl.addLayout(r1)

        # Строка 2: Keep versions: [+] 5 [−]
        r2 = QHBoxLayout(); r2.setSpacing(8); r2.setContentsMargins(0,0,0,0)
        self.lbl_keep_ver = _clbl("Keep versions:")
        r2.addWidget(self.lbl_keep_ver)
        _spin(r2, keep_mod_versions, 1, 10, "keep_versions_edit")
        r2.addStretch(1)
        cache_vl.addLayout(r2)

        # Строка 3: Bundle TTL (days): [+] 14 [−]
        r3 = QHBoxLayout(); r3.setSpacing(8); r3.setContentsMargins(0,0,0,0)
        self.lbl_ttl = _clbl("Bundle TTL (days):")
        r3.addWidget(self.lbl_ttl)
        _spin(r3, bundle_ttl_days, 0, 365, "bundle_ttl_edit")
        r3.addStretch(1)
        cache_vl.addLayout(r3)

        # Строка 4: кнопки (левый край)
        r4 = QHBoxLayout(); r4.setSpacing(8); r4.setContentsMargins(0,0,0,0)

        self.apply_cache_btn = QPushButton("✔  Apply")
        self.apply_cache_btn.setObjectName("scan_btn")
        self.apply_cache_btn.clicked.connect(self.on_apply_cache_settings)
        r4.addWidget(self.apply_cache_btn)

        self.scan_btn = QPushButton("⟳  Scan")
        self.scan_btn.setObjectName("scan_btn")
        self.scan_btn.clicked.connect(self.on_scan_async)
        r4.addWidget(self.scan_btn)

        self.purge_btn = QPushButton("🗑  Purge")
        self.purge_btn.setObjectName("purge_btn")
        self.purge_btn.clicked.connect(self.on_purge_cache)
        r4.addWidget(self.purge_btn)

        self.open_cache_btn = QPushButton("📂  Open Cache")
        self.open_cache_btn.setObjectName("scan_btn")
        self.open_cache_btn.setToolTip("Open cache folder in Explorer")
        self.open_cache_btn.clicked.connect(lambda: subprocess.Popen(f'explorer "{CACHE_DIR}"'))
        r4.addWidget(self.open_cache_btn)
        r4.addStretch(1)
        cache_vl.addLayout(r4)

        # ───────────────────────────────────────────────
        # AUTH BOX
        # ───────────────────────────────────────────────
        self.auth_group = QGroupBox("Access Control")
        auth_l = QGridLayout(self.auth_group)
        auth_l.setSpacing(8)

        # Row 0 — mode selector
        self.lbl_mode = QLabel("Mode:")
        self.lbl_mode.setStyleSheet("color: #8b9ebe;")
        auth_l.addWidget(self.lbl_mode, 0, 0)

        auth_mode_val = cfg.get("auth_mode", "whitelist")
        self.auth_mode_open_btn = QPushButton("🌐  Open")
        self.auth_mode_open_btn.setObjectName("mode_btn")
        self.auth_mode_open_btn.setCheckable(True)
        self.auth_mode_open_btn.setChecked(auth_mode_val == "open")
        self.auth_mode_open_btn.setMinimumWidth(80)

        self.auth_mode_wl_btn = QPushButton("🛡  Whitelist")
        self.auth_mode_wl_btn.setObjectName("mode_btn")
        self.auth_mode_wl_btn.setCheckable(True)
        self.auth_mode_wl_btn.setChecked(auth_mode_val == "whitelist")
        self.auth_mode_wl_btn.setMinimumWidth(80)

        self.auth_mode_open_btn.clicked.connect(lambda: self._set_auth_mode("open"))
        self.auth_mode_wl_btn.clicked.connect(lambda: self._set_auth_mode("whitelist"))

        mode_box = QHBoxLayout()
        mode_box.setSpacing(4)
        mode_box.addWidget(self.auth_mode_open_btn)
        mode_box.addWidget(self.auth_mode_wl_btn)
        mode_box.addStretch(1)

        self.auth_mode_hint = QLabel("")
        self.auth_mode_hint.setStyleSheet("color: #a6e3a1; font-size: 11px;")
        mode_box.addWidget(self.auth_mode_hint)

        self.instr_btn = QPushButton("📖  How to setup Whitelist")
        self.instr_btn.clicked.connect(self.on_show_whitelist_instructions)
        mode_box.addWidget(self.instr_btn)

        auth_l.addLayout(mode_box, 0, 1, 1, 5)

        # Row 1 — URL + key (whitelist only)
        self.lbl_auth_url_w = QLabel("Auth URL:")
        self.lbl_auth_url_w.setStyleSheet("color: #8b9ebe;")
        auth_l.addWidget(self.lbl_auth_url_w, 1, 0)
        self.auth_url_edit = QLineEdit(auth_url)
        self.auth_url_edit.setPlaceholderText("https://yoursite.com/api/modsync/allowed_steamids")
        auth_l.addWidget(self.auth_url_edit, 1, 1, 1, 5)

        self.lbl_auth_key_w = QLabel("Auth Key:")
        self.lbl_auth_key_w.setStyleSheet("color: #8b9ebe;")
        auth_l.addWidget(self.lbl_auth_key_w, 2, 0)
        self.auth_key_edit = QLineEdit(auth_key)
        self.auth_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.auth_key_edit.setPlaceholderText("secret key…")
        auth_l.addWidget(self.auth_key_edit, 2, 1, 1, 3)

        self.sync_auth_btn = QPushButton("⟳  Sync SteamIDs")
        self.sync_auth_btn.setObjectName("scan_btn")
        self.sync_auth_btn.clicked.connect(self.on_sync_auth_now)
        auth_l.addWidget(self.sync_auth_btn, 2, 4)

        self.auth_status_lbl = QLabel("Auth: …")
        self.auth_status_lbl.setStyleSheet("color: #8b9ebe; font-size: 11px;")
        auth_l.addWidget(self.auth_status_lbl, 2, 5)

        # Группируем виджеты строк для show/hide
        self._auth_url_row_widgets = [self.lbl_auth_url_w, self.auth_url_edit]
        self._auth_key_row_widgets = [self.lbl_auth_key_w, self.auth_key_edit,
                                       self.sync_auth_btn, self.auth_status_lbl]

        self._apply_auth_mode_ui(auth_mode_val)

        # ───────────────────────────────────────────────
        # ТАБЛИЦА КЛИЕНТОВ
        # ───────────────────────────────────────────────
        clients_box = QWidget()
        clients_l = QVBoxLayout(clients_box)
        clients_l.setContentsMargins(0, 0, 0, 0)

        clients_header = QHBoxLayout()
        self.lbl_clients = QLabel("Connected Clients")
        self.lbl_clients.setStyleSheet("font-weight: bold; color: #7aa2f7; font-size: 12px;")
        clients_header.addWidget(self.lbl_clients)
        clients_header.addStretch(1)
        self.copy_btn = QPushButton("📋  Copy SteamID")
        self.copy_btn.clicked.connect(self.on_copy_steamid)
        clients_header.addWidget(self.copy_btn)
        self.ban_from_clients_btn = QPushButton("🚫  Ban")
        self.ban_from_clients_btn.setToolTip("Add selected SteamID to blacklist")
        self.ban_from_clients_btn.clicked.connect(self.on_ban_from_clients)
        clients_header.addWidget(self.ban_from_clients_btn)
        clients_l.addLayout(clients_header)

        self.clients_table = QTableWidget(0, 5)
        self.clients_table.setHorizontalHeaderLabels(["SteamID", "Last Seen", "IP", "Status", "Sent"])
        hdr_c = self.clients_table.horizontalHeader()
        hdr_c.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr_c.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr_c.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr_c.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr_c.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.clients_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.clients_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.clients_table.setAlternatingRowColors(True)
        self.clients_table.cellDoubleClicked.connect(self._on_client_double_click)
        clients_l.addWidget(self.clients_table, 1)

        # ───────────────────────────────────────────────
        # WHITELIST
        # ───────────────────────────────────────────────
        allowed_box = QWidget()
        allowed_l = QVBoxLayout(allowed_box)
        allowed_l.setContentsMargins(0, 0, 0, 0)

        allowed_header = QHBoxLayout()
        self.lbl_whitelist = QLabel("Whitelist — Allowed SteamIDs")
        self.lbl_whitelist.setStyleSheet("font-weight: bold; color: #7aa2f7; font-size: 12px;")
        allowed_header.addWidget(self.lbl_whitelist)
        allowed_header.addStretch(1)
        self.copy_allowed_btn = QPushButton("📋  Copy SteamID")
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

        # --- Mods tab
        mods_box = QWidget()
        mods_l = QVBoxLayout()
        mods_box.setLayout(mods_l)

        mods_header = QHBoxLayout()
        self.mods_summary_lbl = QLabel("Detected mods: …")
        self.mods_summary_lbl.setStyleSheet("color: #8b9ebe; font-size: 11px;")
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
        # ───────────────────────────────────────────────
        # ЛОГ
        # ───────────────────────────────────────────────
        log_box = QWidget()
        log_l = QVBoxLayout(log_box)
        log_l.setContentsMargins(0, 0, 0, 0)
        log_header = QHBoxLayout()
        lbl_log = QLabel("Server Log")
        lbl_log.setStyleSheet("font-weight: bold; color: #7aa2f7; font-size: 12px;")
        log_header.addWidget(lbl_log)
        log_header.addStretch(1)
        clear_log_btn = QPushButton("Clear")
        clear_log_btn.setFixedWidth(60)
        clear_log_btn.clicked.connect(lambda: self.log_view.clear())
        log_header.addWidget(clear_log_btn)
        log_l.addLayout(log_header)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        log_l.addWidget(self.log_view, 1)

        # ───────────────────────────────────────────────
        # SPLITTER + TABS
        # ───────────────────────────────────────────────
        # ───────────────────────────────────────────────
        # BLACKLIST TAB
        # ───────────────────────────────────────────────
        blacklist_box = QWidget()
        blacklist_l = QVBoxLayout(blacklist_box)
        blacklist_l.setContentsMargins(0, 0, 0, 0)

        bl_header = QHBoxLayout()
        self.lbl_blacklist = QLabel("Blacklist — Blocked SteamIDs")
        self.lbl_blacklist.setStyleSheet("font-weight: bold; color: #f38ba8; font-size: 12px;")
        bl_header.addWidget(self.lbl_blacklist)
        bl_header.addStretch(1)

        self.bl_add_edit = QLineEdit()
        self.bl_add_edit.setPlaceholderText("SteamID…")
        self.bl_add_edit.setFixedWidth(160)
        bl_header.addWidget(self.bl_add_edit)

        self.bl_add_btn = QPushButton("🚫  Ban")
        self.bl_add_btn.clicked.connect(self.on_blacklist_add)
        bl_header.addWidget(self.bl_add_btn)

        self.bl_remove_btn = QPushButton("✅  Unban")
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

        self.tabs = QTabWidget()
        self.tabs.addTab(clients_box, "👥  Clients")
        self.tabs.addTab(allowed_box, "🛡  Whitelist")
        self.tabs.addTab(blacklist_box, "🚫  Blacklist")
        self.tabs.addTab(mods_box, "🔢  Mods")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tabs)
        splitter.addWidget(log_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # ───────────────────────────────────────────────
        # ROOT LAYOUT
        # ───────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        root.addWidget(self.server_group)
        root.addWidget(self.cache_group)
        root.addWidget(self.auth_group)
        root.addWidget(splitter, 1)

        # логи старт
        self.append_log(f"[APP] Config: {CONFIG_PATH}")
        self.append_log(f"[APP] Cache:  {CACHE_DIR}")
        self.append_log(f"[APP] DB:     {DB_PATH}")
        self.append_log(f"[APP] Cache policy: max={cache_max_gb}GB keep_versions={keep_mod_versions} ttl_days={bundle_ttl_days}")
        self.append_log(f"[APP] manifest_hash: {self.state.get_manifest().get('manifest_hash')}")

        self.update_watcher_paths()
        self.refresh_cache_label()
        self.cache_timer.start()
        self.clients_timer.start()
        self.refresh_clients_table()
        self.refresh_auth_label()
        self.refresh_allowed_table()
        self.refresh_blacklist_table()
        self.refresh_mods_table()
        self.refresh_autostart_button()

        if bool(cfg.get("auto_start_server", True)):
            QTimer.singleShot(200, self.on_start)

        enforce_cache_policy(cache_max_gb, keep_mod_versions, bundle_ttl_days, log_cb=self.append_log)
        self.refresh_cache_label()

    # ---- helpers для синхронизации ползунков ----
    def _sync_slider_cache_max(self, text): pass
    def _sync_slider_keep_versions(self, text): pass
    def _sync_slider_bundle_ttl(self, text): pass

    # ---------------- logs ----------------

    def append_log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_view.append(f"[{ts}] {msg}")

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

    # ---------------- cache ----------------

    def refresh_cache_label(self):
        total, cnt = cache_stats()
        max_gb = int(self.state.cache_cfg.get("cache_max_gb", DEFAULT_CACHE_MAX_GB))
        max_bytes = max_gb * 1024 * 1024 * 1024
        self.cache_lbl.setText(self.tr("lbl_cache_info", used=human_bytes(total), total=human_bytes(max_bytes), cnt=cnt))
        pct = int(total * 100 / max_bytes) if max_bytes > 0 else 0
        pct = min(pct, 100)
        self.cache_bar.setValue(pct)
        if pct >= 90:
            self.cache_bar.setProperty("warningLevel", "crit")
        elif pct >= 70:
            self.cache_bar.setProperty("warningLevel", "warn")
        else:
            self.cache_bar.setProperty("warningLevel", "")
        self.cache_bar.style().unpolish(self.cache_bar)
        self.cache_bar.style().polish(self.cache_bar)

    def on_purge_cache(self):
        purge_cache(log_cb=self.append_log)
        self.refresh_cache_label()

    def on_apply_cache_settings(self):
        try:
            max_gb = int(self.cache_max_edit.text().strip())
            keep_v = int(self.keep_versions_edit.text().strip())
            ttl_d = int(self.bundle_ttl_edit.text().strip())
        except Exception:
            QMessageBox.warning(self, "Error", "Cache settings must be integers")
            return

        if max_gb < 1 or max_gb > 500:
            QMessageBox.warning(self, "Error", "Cache max GB must be 1..500")
            return
        if keep_v < 1 or keep_v > 10:
            QMessageBox.warning(self, "Error", "Keep mod versions must be 1..10")
            return
        if ttl_d < 0 or ttl_d > 365:
            QMessageBox.warning(self, "Error", "Bundle TTL days must be 0..365 (0 = disable TTL)")
            return

        self.state.cache_cfg["cache_max_gb"] = max_gb
        self.state.cache_cfg["keep_mod_versions"] = keep_v
        self.state.cache_cfg["bundle_ttl_days"] = ttl_d

        self.append_log(f"[CACHE] Apply policy: max={max_gb}GB keep_mod_versions={keep_v} bundle_ttl_days={ttl_d}")
        enforce_cache_policy(max_gb, keep_v, ttl_d, log_cb=self.append_log)
        self.refresh_cache_label()
        self.save_config()

    # ---------------- scan async ----------------

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

    def on_auto_port(self):
        try:
            preferred = int(self.port_edit.text().strip())
        except Exception:
            preferred = DEFAULT_PORT
        chosen = pick_port("0.0.0.0", preferred)
        self.port_edit.setText(str(chosen))
        self.append_log(f"[PORT] Selected free port: {chosen}")
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
            self.on_scan_async()



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
        # Groups
        self.server_group.setTitle(t("grp_server"))
        self.cache_group.setTitle(t("grp_cache"))
        self.auth_group.setTitle(t("grp_access"))
        # Labels
        self.lbl_mods_path.setText(t("lbl_mods_path"))
        self.lbl_port.setText(t("lbl_port"))
        self.lbl_mode.setText(t("lbl_mode"))
        self.lbl_auth_url_w.setText(t("lbl_auth_url"))
        self.lbl_auth_key_w.setText(t("lbl_auth_key"))
        self.auth_status_lbl.setText(t("lbl_auth_status"))
        # Cache labels
        self.lbl_cache_max.setText(t("lbl_cache_max"))
        self.lbl_keep_ver.setText(t("lbl_keep_ver"))
        self.lbl_ttl.setText(t("lbl_ttl"))
        self.lbl_clients.setText(t("lbl_clients"))
        self.lbl_whitelist.setText(t("lbl_whitelist"))
        self.lbl_blacklist.setText(t("lbl_blacklist"))
        # Buttons
        self.select_mods_btn.setText(t("btn_select"))
        self.select_mods_btn.setToolTip(t("btn_select_tip"))
        self.auto_port_btn.setText(t("btn_auto_port"))
        self.start_btn.setText(t("btn_start"))
        self.stop_btn.setText(t("btn_stop"))
        self.autostart_btn.setText(t("btn_autostart_on") if self.autostart_btn.isChecked() else t("btn_autostart_off"))
        self.apply_cache_btn.setText(t("btn_apply_cache"))
        self.scan_btn.setText(t("btn_scan"))
        self.purge_btn.setText(t("btn_purge"))
        self.open_cache_btn.setText(t("btn_open_cache"))
        self.open_cache_btn.setToolTip(t("btn_open_cache_tip"))
        self.auth_mode_open_btn.setText(t("btn_mode_open"))
        self.auth_mode_wl_btn.setText(t("btn_mode_wl"))
        self.instr_btn.setText(t("btn_instr"))
        self.sync_auth_btn.setText(t("btn_sync_auth"))
        self.copy_btn.setText(t("btn_copy_steamid"))
        self.ban_from_clients_btn.setText(t("btn_ban"))
        self.ban_from_clients_btn.setToolTip(t("btn_ban_tip"))
        self.copy_allowed_btn.setText(t("btn_copy_allowed"))
        self.mods_select_all_btn.setText(t("btn_mods_all"))
        self.mods_select_none_btn.setText(t("btn_mods_none"))
        self.mods_apply_btn.setText(t("btn_mods_apply"))
        self.bl_add_btn.setText(t("btn_ban"))
        self.bl_remove_btn.setText(t("btn_unban"))
        # UPnP button (keep current state)
        if not self.upnp_btn.isEnabled():
            pass  # failed state — tooltip already set with port info
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
        # Table headers
        self.clients_table.setHorizontalHeaderLabels([
            t("th_steamid"), t("th_last_seen"), t("th_ip"), t("th_status"), t("th_sent")
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
        # Auth mode hint
        if self.auth_mode_open_btn.isChecked():
            self.auth_mode_hint.setText(t("lbl_auth_hint_open"))
        else:
            self.auth_mode_hint.setText(t("lbl_auth_hint_wl"))
        # Mods summary refresh
        self.refresh_mods_table()

    def on_toggle_upnp(self):
        self._upnp_enabled = self.upnp_btn.isChecked()
        if self._upnp_enabled:
            self.upnp_btn.setText(self.tr("btn_upnp_on"))
            self.append_log(self.tr("upnp_enabled_log"))
        else:
            self.upnp_btn.setText(self.tr("btn_upnp_off"))
            self.append_log(self.tr("upnp_disabled_log"))

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
                self.upnp_btn.setText(f"🌐  {result}")
                self.upnp_btn.setToolTip(self.tr("btn_upnp_ok_tip", ip=result, port=port))
            else:
                self._upnp_port = None
                self._upnp_failed = True
                self.save_config()
                self.log_bridge.log_signal.emit(self.tr("upnp_fail", reason=result))
                self.log_bridge.log_signal.emit(self.tr("upnp_manual", port=port))
                self.upnp_btn.setText("🌐  UPnP: FAILED")
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
        self._running_ip = f"{real_ip}:{port}"
        self.status_lbl.setText(self.tr("lbl_status_running", ip=self._running_ip))
        self.status_lbl.setStyleSheet(
            "color: #3fb950; font-weight: bold; font-size: 13px; "
            "text-decoration: underline;"
        )
        self.status_lbl.setToolTip(self.tr("lbl_status_tip"))
        self.append_log(f"[APP] Server started on {self._running_ip}")

        # UPnP проброс порта
        if self._upnp_enabled and not getattr(self, "_upnp_failed", False):
            self._do_upnp_open(port)

        self.save_config()

    def on_stop(self):
        if self.http_thread is None:
            return
        self.http_thread.stop()
        self.http_thread = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.port_edit.setEnabled(True)
        self.auto_port_btn.setEnabled(True)
        self.autostart_btn.setEnabled(True)
        self.status_light.set_state("stopped")
        self._running_ip = ""
        self.status_lbl.setText(self.tr("lbl_status_stopped"))
        self.status_lbl.setStyleSheet("color: #f85149; font-weight: bold; font-size: 13px;")
        self.status_lbl.setToolTip("")
        self.append_log("[APP] Server stopped.")

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

        self.clients_table.setRowCount(len(items))
        for row, it in enumerate(items):
            steamid = it.get("steamid", "")
            last_seen = int(it.get("last_seen", 0) or 0)
            ip = it.get("last_ip", "")
            db_status = it.get("status", "UNKNOWN")
            client_mh = it.get("last_client_manifest_hash", "")
            sent = int(it.get("bytes_sent_total", 0) or 0)

            # Пересчитываем статус на лету: сравниваем хэш клиента с текущим манифестом сервера
            cur_server_hash = self.state.get_manifest().get("manifest_hash", "")
            if client_mh and cur_server_hash and client_mh == cur_server_hash:
                status = "OK"
            elif not client_mh:
                status = db_status  # ещё не подключался — оставляем то что в БД
            else:
                status = "OUTDATED"

            last_seen_str = "-" if last_seen <= 0 else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_seen))
            sent_str = human_bytes(sent)

            sid_item = QTableWidgetItem(steamid)
            sid_item.setForeground(QColor("#89b4fa"))
            sid_item.setToolTip(f"Click to open Steam profile: {steamid}")
            self.clients_table.setItem(row, 0, sid_item)
            self.clients_table.setItem(row, 1, QTableWidgetItem(last_seen_str))
            self.clients_table.setItem(row, 2, QTableWidgetItem(ip))
            status_item = QTableWidgetItem(status)
            if status == "OK":
                status_item.setForeground(QColor("#3fb950"))
            elif status == "OUTDATED":
                status_item.setForeground(QColor("#f0a020"))
            else:
                status_item.setForeground(QColor("#8b9ebe"))
            self.clients_table.setItem(row, 3, status_item)
            self.clients_table.setItem(row, 4, QTableWidgetItem(sent_str))

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

    def _apply_auth_mode_ui(self, mode: str):
        is_wl = (mode == "whitelist")
        # Показываем/прячем строки URL и Key целиком
        for w in (self._auth_url_row_widgets + self._auth_key_row_widgets):
            w.setVisible(is_wl)
        if is_wl:
            self.auth_mode_hint.setText("Only SteamIDs from your site are allowed")
            self.auth_mode_hint.setStyleSheet("color: #fab387; font-size: 11px;")
        else:
            self.auth_mode_hint.setText("Everyone with a SteamID can connect")
            self.auth_mode_hint.setStyleSheet("color: #a6e3a1; font-size: 11px;")

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
            "mods_path": self.mods_edit.text().strip(),
            "port": int(self.port_edit.text().strip()) if self.port_edit.text().strip().isdigit() else DEFAULT_PORT,
            "cache_max_gb": int(self.state.cache_cfg.get("cache_max_gb", DEFAULT_CACHE_MAX_GB)),
            "keep_mod_versions": int(self.state.cache_cfg.get("keep_mod_versions", DEFAULT_KEEP_MOD_VERSIONS)),
            "bundle_ttl_days": int(self.state.cache_cfg.get("bundle_ttl_days", DEFAULT_BUNDLE_TTL_DAYS)),
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
        }
        write_config(cfg)

    def closeEvent(self, event):
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