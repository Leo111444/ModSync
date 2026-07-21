# -*- coding: utf-8 -*-
"""
ModSync v2: пофайловая синхронизация поверх BitTorrent v2.

Компоненты:
  BuildManager  — состояния сборки PUBLISHED/DRAFT, генерация v2-торрента и манифеста
  TorrentEngine — сид published-ревизии через libtorrent
  Tracker       — встроенный HTTP-трекер (in-memory, compact peers)
  handle_v2_*   — обработчики HTTP-роутов для встраивания в ApiHandler

Зависимости: libtorrent >= 2.0
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import struct
import threading
import time
from pathlib import Path

import libtorrent as lt

PIECE_SIZE = 4 * 1024 * 1024  # 4 MiB
ANNOUNCE_INTERVAL = 1800       # сек, отдаём клиентам
PEER_TTL = ANNOUNCE_INTERVAL * 2
JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
LAN_DISCOVERY_UDP_PORT = 58765  # fixed port for LAN broadcast discovery


class LANBeacon:
    """Server-side: broadcasts presence on LAN every 30 s via UDP."""
    def __init__(self, lan_ip: str, http_port: int):
        self._lan_ip = lan_ip
        self._http_port = http_port
        self._stop = False

    def start(self):
        import threading as _t
        _t.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop = True

    def _run(self):
        import socket as _s, json as _j
        msg = _j.dumps({"modsync": "2.0", "lan_ip": self._lan_ip,
                        "port": self._http_port}).encode()
        sock = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
        sock.setsockopt(_s.SOL_SOCKET, _s.SO_BROADCAST, 1)
        sock.settimeout(1.0)
        while not self._stop:
            try:
                sock.sendto(msg, ("255.255.255.255", LAN_DISCOVERY_UDP_PORT))
            except Exception:
                pass
            for _ in range(30):
                if self._stop:
                    break
                import time as _time
                _time.sleep(1)
        sock.close()


class LANDiscovery:
    """Client-side: listens for LAN beacons, caches lan_ip by port."""
    def __init__(self):
        self._cache: dict[int, str] = {}  # port → lan_ip

    def start(self):
        import threading as _t
        _t.Thread(target=self._listen, daemon=True).start()

    def get_lan_ip(self, port: int) -> str:
        return self._cache.get(port, "")

    def _listen(self):
        import socket as _s, json as _j
        sock = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
        sock.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
        try:
            sock.bind(("", LAN_DISCOVERY_UDP_PORT))
        except Exception:
            return
        sock.settimeout(5.0)
        while True:
            try:
                data, _ = sock.recvfrom(512)
                msg = _j.loads(data)
                if msg.get("modsync") == "2.0":
                    port = int(msg["port"])
                    lan_ip = str(msg.get("lan_ip", ""))
                    if lan_ip:
                        self._cache[port] = lan_ip
            except Exception:
                pass

# ─────────────────────────────────────────────
# утилиты
# ─────────────────────────────────────────────

def _norm_rel(path: str) -> str:
    """Путь из торрента → относительный путь внутри Mods (без имени корня), forward slash."""
    p = path.replace("\\", "/")
    parts = p.split("/", 1)
    return parts[1] if len(parts) == 2 else p


def _is_pad(fs: "lt.file_storage", idx: int) -> bool:
    try:
        return bool(fs.file_flags(idx) & lt.file_storage.flag_pad_file)
    except Exception:
        # запасной признак
        return "/.pad/" in ("/" + fs.file_path(idx).replace("\\", "/"))


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def bencode(obj) -> bytes:
    """Минимальный bencode (int, bytes, str, list, dict)."""
    if isinstance(obj, int):
        return b"i" + str(obj).encode() + b"e"
    if isinstance(obj, str):
        obj = obj.encode("utf-8")
    if isinstance(obj, (bytes, bytearray)):
        return str(len(obj)).encode() + b":" + bytes(obj)
    if isinstance(obj, list):
        return b"l" + b"".join(bencode(x) for x in obj) + b"e"
    if isinstance(obj, dict):
        out = b"d"
        for k in sorted(obj.keys(), key=lambda x: x.encode() if isinstance(x, str) else x):
            out += bencode(k) + bencode(obj[k])
        return out + b"e"
    raise TypeError(f"bencode: unsupported {type(obj)}")


def parse_qs_raw(query: str) -> dict[bytes, bytes]:
    """
    Разбор query string БЕЗ utf-8 декодирования значений.
    info_hash/peer_id — сырые байты, обычный parse_qs их портит.
    Возвращает {key_bytes: value_bytes}, последнее вхождение ключа побеждает.
    """
    out: dict[bytes, bytes] = {}
    for pair in query.split("&"):
        if not pair:
            continue
        if "=" in pair:
            k, v = pair.split("=", 1)
        else:
            k, v = pair, ""
        out[_unquote_bytes(k)] = _unquote_bytes(v)
    return out


def _unquote_bytes(s: str) -> bytes:
    res = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == "%" and i + 2 < len(s):
            try:
                res.append(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        if c == "+":
            res.append(0x20)
        else:
            res.extend(c.encode("utf-8"))
        i += 1
    return bytes(res)


# ─────────────────────────────────────────────
# BuildManager
# ─────────────────────────────────────────────

class BuildManager:
    """
    Держит published-ревизию (манифест + торрент + infohash) и draft-флаг.
    publish() полностью пересобирает торрент из mods_root.
    Всё персистится в data_dir, при рестарте восстанавливается.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.builds_dir = self.data_dir / "builds"
        self.builds_dir.mkdir(parents=True, exist_ok=True)

        self.lock = threading.Lock()
        self.build_id: str | None = None
        self.infohash_v2: str | None = None
        self.created_at: str | None = None
        self.torrent_bytes: bytes | None = None
        self.manifest: dict | None = None          # полный dict
        self.manifest_gz: bytes | None = None      # готовый gzip для отдачи
        self.torrent_name: str | None = None       # имя корня в торренте
        self.draft_dirty: bool = False
        self.publishing: bool = False

        self._load_persisted()

    # ---------- персистентность ----------

    def _load_persisted(self) -> None:
        meta_p = self.builds_dir / "current.meta.json"
        tor_p = self.builds_dir / "current.torrent"
        man_p = self.builds_dir / "current.manifest.json.gz"
        if not (meta_p.exists() and tor_p.exists() and man_p.exists()):
            return
        try:
            meta = json.loads(meta_p.read_text("utf-8"))
            torrent_bytes = tor_p.read_bytes()
            manifest_gz = man_p.read_bytes()
            manifest = json.loads(gzip.decompress(manifest_gz).decode("utf-8"))
            ti = lt.torrent_info(lt.bdecode(torrent_bytes))
            # снапшот обязателен: без него сидить нечем (миграция со старой
            # версии → админ публикует заново)
            if not (self.builds_dir / meta["build_id"] / "Mods").is_dir():
                return
            with self.lock:
                self.build_id = meta["build_id"]
                self.infohash_v2 = meta["infohash_v2"]
                self.created_at = meta["created_at"]
                self.torrent_bytes = torrent_bytes
                self.manifest = manifest
                self.manifest_gz = manifest_gz
                self.torrent_name = ti.name()
        except Exception:
            pass  # битые файлы — просто стартуем без published

    def _persist(self) -> None:
        _atomic_write(self.builds_dir / "current.torrent", self.torrent_bytes)
        _atomic_write(self.builds_dir / "current.manifest.json.gz", self.manifest_gz)
        meta = {
            "build_id": self.build_id,
            "infohash_v2": self.infohash_v2,
            "created_at": self.created_at,
        }
        _atomic_write(self.builds_dir / "current.meta.json",
                      json.dumps(meta, ensure_ascii=False).encode("utf-8"))

    # ---------- публикация ----------

    def publish(self, mods_root: Path, server_name: str = "",
                tracker_url: str = "", webseed_url: str = "",
                tracker_url_local: str = "",
                tracked_mods: "set[str] | None" = None,
                log_cb=None, progress_cb=None) -> dict:
        """
        Синхронная публикация (звать из рабочего потока!).
        Возвращает {build_id, infohash_v2, ...} или бросает исключение.
        progress_cb(piece_idx, num_pieces) — прогресс хеширования.
        """
        def log(msg: str):
            if log_cb:
                log_cb(msg)

        mods_root = Path(mods_root)
        if not mods_root.is_dir():
            raise RuntimeError(f"mods_root не существует: {mods_root}")

        with self.lock:
            if self.publishing:
                raise RuntimeError("публикация уже идёт")
            self.publishing = True

        staging = None
        try:
            # ── СНАПШОТ: hardlink-копия сборки; сид/webseed работают с неё,
            #    живая папка может меняться в любой момент ──
            log(f"[V2] Снапшот {mods_root} ...")
            staging = self.builds_dir / f"staging_{int(time.time())}"
            snap_mods = staging / "Mods"

            def _skip(rel_parts: tuple[str, ...], name: str) -> bool:
                nl = name.lower()
                if nl in JUNK_NAMES or nl.startswith("."):
                    return True
                if any(p.lower() == "disabled_mods" for p in rel_parts + (name,)):
                    return True
                if tracked_mods is not None and len(rel_parts) == 0 and name not in tracked_mods:
                    return True
                return False

            linked = copied = 0
            for dirpath, dirnames, filenames in os.walk(mods_root):
                rel = Path(dirpath).relative_to(mods_root)
                rel_parts = rel.parts
                dirnames[:] = [d for d in dirnames if not _skip(rel_parts, d)]
                for fn in filenames:
                    if _skip(rel_parts, fn):
                        continue
                    src_f = Path(dirpath) / fn
                    dst_f = snap_mods / rel / fn
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(src_f, dst_f)
                        linked += 1
                    except OSError:
                        import shutil as _sh
                        _sh.copy2(src_f, dst_f)
                        copied += 1
            if linked + copied == 0:
                raise RuntimeError("в папке модов нет файлов")
            log(f"[V2] Снапшот готов: hardlink {linked}, копий {copied}")

            fs = lt.file_storage()
            lt.add_files(fs, str(snap_mods))
            if fs.num_files() == 0:
                raise RuntimeError("снапшот пуст")

            t = lt.create_torrent(fs, PIECE_SIZE, flags=lt.create_torrent.v2_only)
            t.set_priv(True)
            t.set_creator("ModSync")
            if tracker_url_local:
                t.add_tracker(tracker_url_local, 0)
            if tracker_url:
                t.add_tracker(tracker_url, 1 if tracker_url_local else 0)
            if webseed_url:
                t.add_url_seed(webseed_url)

            num_pieces = t.num_pieces()
            real_files = sum(1 for i in range(fs.num_files()) if not _is_pad(fs, i))
            log(f"[V2] Хеширую {real_files} файлов ({fs.num_files() - real_files} pad), {num_pieces} кусков ...")

            if progress_cb:
                lt.set_piece_hashes(t, str(staging),
                                    lambda idx: progress_cb(idx, num_pieces))
            else:
                lt.set_piece_hashes(t, str(staging))

            entry = t.generate()
            torrent_bytes = lt.bencode(entry)
            info = lt.torrent_info(entry)
            fi = info.files()

            # манифест из готового торрента — файлы не перечитываем
            files_flat: list[dict] = []
            for idx in range(fi.num_files()):
                if _is_pad(fi, idx):
                    continue
                rel = _norm_rel(fi.file_path(idx))
                root_hex = fi.root(idx).to_bytes().hex()
                files_flat.append({
                    "path": rel,
                    "size": fi.file_size(idx),
                    "root": root_hex,
                })

            # build_id — детерминированный отпечаток состава
            fingerprint = "\n".join(
                f"{f['path'].casefold()}:{f['root']}"
                for f in sorted(files_flat, key=lambda x: x["path"].casefold())
            )
            build_id = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:8]
            infohash_v2 = info.info_hashes().v2.to_bytes().hex()
            created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            # все папки сборки (включая пустые — торрент их не переносит,
            # клиент досоздаст по этому списку)
            all_dirs: set[str] = set()
            for dirpath, dirnames, _fn in os.walk(mods_root):
                rel = Path(dirpath).relative_to(mods_root)
                rel_parts = rel.parts
                dirnames[:] = [d for d in dirnames if not _skip(rel_parts, d)]
                rel_d = str(rel).replace("\\", "/")
                if rel_d != ".":
                    all_dirs.add(rel_d)
            # пустые папки в снапшот тоже (иначе их не будет в сиде — не страшно,
            # клиент создаёт их из манифеста; но каталог снапшота полон для webseed)
            for d in all_dirs:
                (snap_mods / d).mkdir(parents=True, exist_ok=True)

            # группировка по модам (первый компонент пути)
            mods_map: dict[str, list[dict]] = {}
            for f in files_flat:
                top = f["path"].split("/", 1)[0]
                mods_map.setdefault(top, []).append(f)

            manifest = {
                "schema": 1,
                "build_id": build_id,
                "created_at": created_at,
                "server_name": server_name,
                "total_size": sum(f["size"] for f in files_flat),
                "file_count": len(files_flat),
                "infohash_v2": infohash_v2,
                "dirs": sorted(all_dirs, key=str.casefold),
                "mods": [
                    {"name": name, "files": flist}
                    for name, flist in sorted(mods_map.items(), key=lambda kv: kv[0].casefold())
                ],
            }
            manifest_gz = gzip.compress(
                json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                mtime=0,
            )

            # staging → постоянное место builds/<build_id>
            final_dir = self.builds_dir / build_id
            if final_dir.exists():
                import shutil as _sh
                _sh.rmtree(final_dir, ignore_errors=True)
            os.rename(staging, final_dir)
            staging = None  # успешно переехал — в finally не трогаем

            with self.lock:
                self.build_id = build_id
                self.infohash_v2 = infohash_v2
                self.created_at = created_at
                self.torrent_bytes = torrent_bytes
                self.manifest = manifest
                self.manifest_gz = manifest_gz
                self.torrent_name = info.name()
                self.draft_dirty = False
                self._persist()

            # чистим прочие снапшоты (hardlink — места не занимали, но порядок)
            import shutil as _sh
            for entry in self.builds_dir.iterdir():
                if entry.is_dir() and entry.name != build_id:
                    _sh.rmtree(entry, ignore_errors=True)

            log(f"[V2] Опубликовано: build {build_id}, "
                f"{len(files_flat)} файлов, infohash {infohash_v2[:16]}…")
            return {"build_id": build_id, "infohash_v2": infohash_v2,
                    "file_count": len(files_flat),
                    "total_size": manifest["total_size"],
                    "files": [{"root_hash": f["root"], "size": f["size"]}
                               for f in files_flat]}
        finally:
            if staging is not None and staging.exists():
                import shutil as _sh
                _sh.rmtree(staging, ignore_errors=True)
            with self.lock:
                self.publishing = False

    # ---------- снимки состояния ----------

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "build_id": self.build_id,
                "infohash_v2": self.infohash_v2,
                "created_at": self.created_at,
                "draft_dirty": self.draft_dirty,
                "publishing": self.publishing,
                "has_published": self.torrent_bytes is not None,
                "total_size": (self.manifest or {}).get("total_size", 0),
                "file_count": (self.manifest or {}).get("file_count", 0),
            }

    def get_torrent(self) -> bytes | None:
        with self.lock:
            return self.torrent_bytes

    def get_manifest_gz(self) -> bytes | None:
        with self.lock:
            return self.manifest_gz

    def get_torrent_name(self) -> str | None:
        with self.lock:
            return self.torrent_name

    def get_snapshot_save_root(self) -> Path | None:
        """Папка, СОДЕРЖАЩАЯ Mods снапшота — это save_path для сида."""
        with self.lock:
            if not self.build_id:
                return None
            d = self.builds_dir / self.build_id
            return d if (d / "Mods").is_dir() else None

    def get_snapshot_mods(self) -> Path | None:
        """Папка Mods снапшота — корень для webseed/files."""
        root = self.get_snapshot_save_root()
        return (root / "Mods") if root else None

    def mark_dirty(self) -> None:
        with self.lock:
            self.draft_dirty = True


# ─────────────────────────────────────────────
# TorrentEngine — сид published-ревизии
# ─────────────────────────────────────────────

class TorrentEngine:
    def __init__(self, listen_port: int, log_cb=None):
        self.log_cb = log_cb
        self.listen_port = listen_port
        self.session = lt.session({
            "listen_interfaces": f"0.0.0.0:{listen_port}",
            "enable_dht": False,
            "enable_lsd": False,
            "enable_upnp": False,
            "enable_natpmp": False,
            "alert_mask": lt.alert.category_t.error_notification
                        | lt.alert.category_t.status_notification,
        })
        self.handle: lt.torrent_handle | None = None
        self._lock = threading.Lock()

    def _log(self, msg: str):
        if self.log_cb:
            self.log_cb(msg)

    def seed_from(self, torrent_bytes: bytes, save_root: Path) -> None:
        """Сидит торрент из save_root (папка, содержащая корень торрента).
        Для v2-сервера save_root = builds/<build_id> со снапшотом Mods."""
        ti = lt.torrent_info(lt.bdecode(torrent_bytes))
        with self._lock:
            if self.handle is not None:
                try:
                    self.session.remove_torrent(self.handle)
                except Exception:
                    pass
                self.handle = None
            params = lt.add_torrent_params()
            params.ti = ti
            params.save_path = str(save_root)
            # seed_mode: файлы гарантированно совпадают (торрент только что собран из них)
            params.flags |= lt.torrent_flags.seed_mode
            params.flags &= ~lt.torrent_flags.paused
            params.flags &= ~lt.torrent_flags.auto_managed
            self.handle = self.session.add_torrent(params)
        self._log(f"[V2] Сид запущен: {ti.name()} ({ti.num_files()} файлов)")

    def stats(self) -> dict:
        with self._lock:
            h = self.handle
        if h is None or not h.is_valid():
            return {"active": False}
        st = h.status()
        return {
            "active": True,
            "peers": st.num_peers,
            "seeds": st.num_seeds,
            "upload_rate": st.upload_rate,
            "total_upload": st.total_upload,
        }

    def pump_alerts(self) -> list[str]:
        """Звать периодически (QTimer). Возвращает строки для лога."""
        out = []
        for a in self.session.pop_alerts():
            if a.category() & lt.alert.category_t.error_notification:
                msg = a.message()
                # loopback can't reach LAN tracker — expected noise, not an error
                if "skipping tracker announce" in msg:
                    continue
                out.append(f"[V2][lt] {msg}")
        return out

    def stop(self) -> None:
        with self._lock:
            if self.handle is not None:
                try:
                    self.session.remove_torrent(self.handle)
                except Exception:
                    pass
                self.handle = None
        # lt.session умирает вместе с процессом; явный shutdown не обязателен


# ─────────────────────────────────────────────
# Tracker — HTTP announce, in-memory
# ─────────────────────────────────────────────

class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        # {info_hash(20b): {peer_id(20b): (ip_str, port, last_seen, left)}}
        self.swarms: dict[bytes, dict[bytes, tuple]] = {}

    def announce(self, info_hash: bytes, peer_id: bytes, ip: str, port: int,
                 left: int, event: str) -> bytes:
        now = time.time()
        with self.lock:
            swarm = self.swarms.setdefault(info_hash, {})
            if event == "stopped":
                swarm.pop(peer_id, None)
            else:
                swarm[peer_id] = (ip, port, now, left)
            # чистка протухших
            dead = [pid for pid, (_, _, ts, _) in swarm.items() if now - ts > PEER_TTL]
            for pid in dead:
                swarm.pop(pid, None)
            peers = [(v[0], v[1]) for pid, v in swarm.items() if pid != peer_id]
            complete = sum(1 for v in swarm.values() if v[3] == 0)
            incomplete = len(swarm) - complete

        compact = b""
        for ip_s, p in peers[:50]:
            try:
                packed_ip = bytes(int(x) for x in ip_s.split("."))
                if len(packed_ip) != 4:
                    continue
                compact += packed_ip + struct.pack(">H", p)
            except Exception:
                continue

        return bencode({
            "interval": ANNOUNCE_INTERVAL,
            "min interval": 60,
            "complete": complete,
            "incomplete": incomplete,
            "peers": compact,
        })

    @staticmethod
    def failure(reason: str) -> bytes:
        return bencode({"failure reason": reason})


# ─────────────────────────────────────────────
# V2State — контейнер для встраивания в server_app
# ─────────────────────────────────────────────

class V2State:
    def __init__(self, data_dir: Path):
        self.build = BuildManager(data_dir)
        self.tracker = Tracker()
        self.engine: TorrentEngine | None = None  # создаётся на on_start

    def ensure_engine(self, listen_port: int, log_cb=None) -> TorrentEngine:
        if self.engine is None:
            self.engine = TorrentEngine(listen_port, log_cb)
        return self.engine


# ─────────────────────────────────────────────
# HTTP-обработчики (встраиваются в ApiHandler)
# ─────────────────────────────────────────────

_SAFE_PATH_RE = re.compile(r"^[^\0]+$")


def _reject_path(rel: str) -> bool:
    if not rel or not _SAFE_PATH_RE.match(rel):
        return True
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return True
    return parts[0].lower() == "disabled_mods"


def handle_v2_get(handler, parsed, v2: V2State, mods_root: Path,
                  require_auth, touch_client_cb=None) -> bool:
    """
    Обработка GET для /api/v2/* и /announce.
    handler — ApiHandler (BaseHTTPRequestHandler).
    require_auth(steamid) -> (ok, reason) — существующая проверка server_app.
    touch_client_cb(steamid, ip, kind) — вызывается при успешном запросе manifest/torrent.
    Возвращает True если запрос обработан.
    """
    path = parsed.path

    # ---- /announce (трекер) ----
    if path == "/announce":
        q = parse_qs_raw(parsed.query)
        info_hash = q.get(b"info_hash", b"")
        peer_id = q.get(b"peer_id", b"")
        try:
            port = int(q.get(b"port", b"0").decode("ascii", "ignore") or "0")
        except ValueError:
            port = 0
        try:
            left = int(q.get(b"left", b"0").decode("ascii", "ignore") or "0")
        except ValueError:
            left = 0
        event = q.get(b"event", b"").decode("ascii", "ignore")

        if len(info_hash) != 20 or len(peer_id) != 20 or not (0 < port < 65536):
            body = Tracker.failure("bad announce")
        else:
            ip = handler._get_client_ip()
            body = v2.tracker.announce(info_hash, peer_id, ip, port, left, event)

        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return True

    if not path.startswith("/api/v2/"):
        return False

    sub = path[len("/api/v2/"):]

    # ---- лёгкий статус ----
    if sub == "build":
        snap = v2.build.snapshot()
        handler._send_json({
            "ok": True,
            "build_id": snap["build_id"],
            "infohash_v2": snap["infohash_v2"],
            "created_at": snap["created_at"],
            "total_size": snap["total_size"],
            "file_count": snap["file_count"],
        })
        return True

    if sub == "info":
        import socket as _socket
        cfg = handler._get_cfg()
        snap = v2.build.snapshot()
        try:
            lan_ip = _socket.gethostbyname(_socket.gethostname())
        except Exception:
            lan_ip = ""
        handler._send_json({
            "ok": True,
            "server_name": cfg.get("server_name", ""),
            "auth_mode": cfg.get("auth_mode", "open"),
            "modsync": "2.0",
            "has_published": snap["has_published"],
            "lan_ip": lan_ip,
        })
        return True

    # ---- дальше нужна авторизация в whitelist-режиме ----
    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query)
    steamid = (qs.get("steamid", [""])[0] or "").strip()
    cfg = handler._get_cfg()
    auth_mode = str(cfg.get("auth_mode", "whitelist")).strip().lower()
    if auth_mode == "open" and not steamid:
        pass  # open-режим: steamid опционален (нужен только для статистики)
    else:
        ok, reason = require_auth(steamid)
        if not ok:
            handler._send_json({"ok": False, "error": reason}, code=403)
            return True

    if sub == "manifest":
        gz = v2.build.get_manifest_gz()
        if gz is None:
            handler._send_json({"ok": False, "error": "no published build"}, code=404)
            return True
        if touch_client_cb and steamid:
            touch_client_cb(steamid, handler._get_client_ip(), "manifest")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Encoding", "gzip")
        handler.send_header("Content-Length", str(len(gz)))
        handler.end_headers()
        handler.wfile.write(gz)
        return True

    if sub == "torrent":
        data = v2.build.get_torrent()
        if data is None:
            handler._send_json({"ok": False, "error": "no published build"}, code=404)
            return True
        if touch_client_cb and steamid:
            touch_client_cb(steamid, handler._get_client_ip(), "torrent")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/x-bittorrent")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return True

    if sub.startswith("ws/") or sub.startswith("files/"):
        # оба эндпоинта отдают файлы ОПУБЛИКОВАННОЙ ревизии — из снапшота,
        # живая папка Mods может отличаться (draft)
        snap = v2.build.get_snapshot_mods()
        if snap is None:
            handler._send_json({"ok": False, "error": "no published build"}, code=404)
            return True
        from urllib.parse import unquote
        if sub.startswith("ws/"):
            rel = unquote(sub[len("ws/"):]).replace("\\", "/")
            # BEP19: первый компонент — имя корня торрента, срезаем безусловно
            if "/" in rel:
                rel = rel.split("/", 1)[1]
        else:
            rel = unquote(sub[len("files/"):]).replace("\\", "/")
            tname = v2.build.get_torrent_name()
            if tname and (rel == tname or rel.startswith(tname + "/")):
                rel = rel[len(tname):].lstrip("/")
        if _reject_path(rel):
            handler._send_json({"ok": False, "error": "bad path"}, code=400)
            return True
        fpath = (snap / rel).resolve()
        try:
            fpath.relative_to(snap.resolve())
        except ValueError:
            handler._send_json({"ok": False, "error": "bad path"}, code=400)
            return True
        if not fpath.is_file():
            handler._send_json({"ok": False, "error": "not found"}, code=404)
            return True
        _serve_file_range(handler, fpath)
        return True

    return False


def handle_v2_post(handler, parsed, v2: V2State, publish_trigger) -> bool:
    """
    POST /api/v2/publish — только с localhost.
    publish_trigger() — колбэк server_app, запускающий публикацию в рабочем потоке.
    """
    if parsed.path != "/api/v2/publish":
        return False
    client_ip = handler._get_client_ip()
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        handler._send_json({"ok": False, "error": "localhost only"}, code=403)
        return True
    snap = v2.build.snapshot()
    if snap["publishing"]:
        handler._send_json({"ok": False, "error": "publish in progress"}, code=409)
        return True
    publish_trigger()
    handler._send_json({"ok": True, "started": True})
    return True


def _serve_file_range(handler, fpath: Path) -> None:
    """Отдача файла с поддержкой Range (одиночный диапазон) — для web seed."""
    fsize = fpath.stat().st_size
    range_hdr = handler.headers.get("Range", "")
    start, end = 0, fsize - 1
    is_partial = False

    m = re.match(r"bytes=(\d*)-(\d*)$", range_hdr.strip()) if range_hdr else None
    if m:
        s, e = m.group(1), m.group(2)
        if s == "" and e == "":
            pass
        elif s == "":                      # bytes=-N (последние N байт)
            n = int(e)
            start = max(0, fsize - n)
            is_partial = True
        else:
            start = int(s)
            end = int(e) if e else fsize - 1
            is_partial = True
        if start >= fsize or end >= fsize or start > end:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{fsize}")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return

    length = end - start + 1
    handler.send_response(206 if is_partial else 200)
    handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Accept-Ranges", "bytes")
    if is_partial:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{fsize}")
    handler.send_header("Content-Length", str(length))
    handler.end_headers()

    try:
        with open(fpath, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 512, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass


# ─────────────────────────────────────────────
# ClientEngine — загрузка/докачка сборки клиентом
# ─────────────────────────────────────────────

class ClientEngine:
    """
    Управляет одной активной сборкой на клиенте:
      update()   — добавить торрент (recheck по месту + докачка в папку Mods)
      progress() — снимок состояния для UI
      set_share()/set_upload_limit() — управление раздачей
      stop()     — убрать торрент из сессии (файлы не трогаются)
    """

    def __init__(self, listen_port: int = 0, log_cb=None):
        self.log_cb = log_cb
        actual_port = listen_port if listen_port else 6893
        self.session = lt.session({
            "listen_interfaces": f"0.0.0.0:{actual_port}",
            "enable_dht": False,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
            "alert_mask": lt.alert.category_t.error_notification
                        | lt.alert.category_t.status_notification
                        | lt.alert.category_t.port_mapping_notification,
        })
        self.handle: "lt.torrent_handle | None" = None
        self._lock = threading.Lock()
        self._share = True
        self._num_files = 0
        self._file_tops: list[str] = []   # idx → имя мода (top dir), '' для падов
        self._file_sizes: list[int] = []

    def _log(self, msg: str):
        if self.log_cb:
            self.log_cb(msg)

    # ---------- запуск обновления ----------

    def update(self, torrent_bytes: bytes, mods_dir: Path,
               server_url: str, share: bool = True) -> None:
        """
        Добавляет торрент: libtorrent сам сверит существующие файлы по хешам
        (checking_files) и докачает только отличающееся.
        mods_dir — папка Mods клиента; файлы пишутся прямо в неё.
        """
        mods_dir = Path(mods_dir)
        server_url = server_url.rstrip("/")
        ti = lt.torrent_info(lt.bdecode(torrent_bytes))

        # имя корня торрента может отличаться от имени папки клиента —
        # переименовываем пути файлов под локальное имя
        tname = ti.name()
        local_name = mods_dir.name
        if tname != local_name:
            self._log(f"[V2] Корень торрента '{tname}' → '{local_name}'")
            fi = ti.files()
            for idx in range(fi.num_files()):
                old = fi.file_path(idx).replace("\\", "/")
                rest = old.split("/", 1)[1] if "/" in old else old
                ti.rename_file(idx, os.path.join(local_name, *rest.split("/")))

        # карта файлов для per-mod прогресса
        fi = ti.files()
        self._num_files = fi.num_files()
        self._file_tops = []
        self._file_sizes = []
        for idx in range(self._num_files):
            p = fi.file_path(idx).replace("\\", "/")
            rest = p.split("/", 1)[1] if "/" in p else p
            is_pad = False
            try:
                is_pad = bool(fi.file_flags(idx) & lt.file_storage.flag_pad_file)
            except Exception:
                pass
            top = "" if (is_pad or rest.startswith(".pad/")) else rest.split("/", 1)[0]
            self._file_tops.append(top)
            self._file_sizes.append(fi.file_size(idx))

        params = lt.add_torrent_params()
        params.ti = ti
        params.save_path = str(mods_dir.parent)
        params.trackers = [f"{server_url}/announce"]
        params.url_seeds = [f"{server_url}/api/v2/ws/"]
        params.flags &= ~lt.torrent_flags.paused
        params.flags &= ~lt.torrent_flags.auto_managed

        with self._lock:
            self._share = share
            if self.handle is not None:
                try:
                    self.session.remove_torrent(self.handle)
                except Exception:
                    pass
            self.handle = self.session.add_torrent(params)
        self._log("[V2] Торрент добавлен: сверка локальных файлов…")

    # ---------- состояние ----------

    _STATE_NAMES = {
        lt.torrent_status.states.checking_files: "checking",
        lt.torrent_status.states.downloading_metadata: "metadata",
        lt.torrent_status.states.downloading: "downloading",
        lt.torrent_status.states.finished: "finished",
        lt.torrent_status.states.seeding: "seeding",
        lt.torrent_status.states.checking_resume_data: "checking",
    }

    def progress(self) -> dict:
        with self._lock:
            h = self.handle
        if h is None or not h.is_valid():
            return {"active": False}
        st = h.status()
        state = self._STATE_NAMES.get(st.state, str(st.state))
        return {
            "active": True,
            "state": state,
            "progress": st.progress,               # 0..1 (на этапе checking — прогресс сверки)
            "download_rate": st.download_rate,     # bytes/s
            "upload_rate": st.upload_rate,
            "total_download": st.total_download,
            "total_upload": st.total_upload,
            "peers": st.num_peers,
            "seeds": st.num_seeds,
            "done": st.is_seeding or state == "finished",
        }

    def mod_progress(self) -> dict[str, tuple[int, int]]:
        """{имя_мода: (скачано_байт, всего_байт)} — для таблицы модов."""
        with self._lock:
            h = self.handle
        if h is None or not h.is_valid():
            return {}
        try:
            fp = h.file_progress()
        except Exception:
            return {}
        out: dict[str, list[int]] = {}
        for idx, done in enumerate(fp):
            top = self._file_tops[idx] if idx < len(self._file_tops) else ""
            if not top:
                continue
            acc = out.setdefault(top, [0, 0])
            acc[0] += int(done)
            acc[1] += self._file_sizes[idx]
        return {k: (v[0], v[1]) for k, v in out.items()}

    # ---------- раздача ----------

    def on_completed_apply_share_policy(self) -> None:
        """Вызвать когда done: если раздача выключена — снять торрент."""
        with self._lock:
            share = self._share
            h = self.handle
        if not share and h is not None:
            self._log("[V2] Раздача выключена — сид остановлен")
            self.stop()

    def set_share(self, share: bool) -> None:
        with self._lock:
            self._share = share
            h = self.handle
        if h is None or not h.is_valid():
            return
        if not share and h.status().is_seeding:
            self.stop()

    def set_upload_limit(self, bytes_per_sec: int) -> None:
        """0 = без лимита."""
        try:
            self.session.apply_settings({"upload_rate_limit": int(bytes_per_sec)})
        except Exception:
            pass

    def pump_alerts(self) -> list[str]:
        out = []
        for a in self.session.pop_alerts():
            cat = a.category()
            msg = a.message()
            if cat & lt.alert.category_t.port_mapping_notification:
                out.append(f"[V2][UPnP] {msg}")
            elif cat & lt.alert.category_t.error_notification:
                if "skipping tracker announce" in msg:
                    continue
                out.append(f"[V2][lt] {msg}")
        return out

    def stop(self) -> None:
        with self._lock:
            if self.handle is not None:
                try:
                    self.session.remove_torrent(self.handle)
                except Exception:
                    pass
                self.handle = None


# ─────────────────────────────────────────────
# Master Server Client
# ─────────────────────────────────────────────

MS_URL = "https://bar7dtd.ru"
_MS_HEARTBEAT_INTERVAL = 300  # сек


class MasterClient:
    """
    Клиент мастер-сервера ModSync (bar7dtd.ru).
    Регистрирует сервер, шлёт heartbeat каждые 5 мин,
    загружает индекс файлов после публикации.
    """

    def __init__(self, log_cb=None):
        self._log = log_cb or (lambda m: None)
        self._token: str | None = None
        self._listing: bool = False
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._get_build_info = None
        self._save_token_cb = None
        self._get_server_name = lambda: ""
        self._port = 8765
        self._whitelist_mode: bool = False

    # ── публичный API ──────────────────────────────────────────

    def start(self, token: str | None, get_server_name, port: int, listing: bool,
              get_build_info, save_token_cb, whitelist_mode: bool = False):
        self._listing = listing
        self._whitelist_mode = whitelist_mode
        self._get_build_info = get_build_info
        self._save_token_cb = save_token_cb
        self._get_server_name = get_server_name if callable(get_server_name) else lambda: get_server_name
        self._port = port

        if not listing:
            return

        if not token:
            token = self._register()
            if token:
                save_token_cb(token)
        self._token = token

        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True, name="ms-heartbeat"
        )
        self._thread.start()

    def stop(self):
        self._stop_evt.set()

    def unregister(self):
        """Удалить сервер с мастера (при выключении листинга)."""
        if not self._token:
            return
        try:
            self._post("/ms/unregister", {"server_token": self._token})
            self._log("[MS] Сервер удалён из реестра мастера")
        except Exception as e:
            self._log(f"[MS] Ошибка при удалении с мастера: {e}")
        self._token = None

    def set_listing(self, listing: bool):
        self._listing = listing

    def upload_manifest(self, files: list[dict]):
        """files = [{'root_hash': '...', 'size': N}, ...]"""
        if not self._token or not self._listing or self._whitelist_mode:
            return
        try:
            self._post("/ms/manifest", {
                "server_token": self._token,
                "files": files,
            })
            self._log(f"[MS] Загружен индекс файлов: {len(files)} шт.")
        except Exception as e:
            self._log(f"[MS] Ошибка загрузки манифеста: {e}")

    # ── внутренние ────────────────────────────────────────────

    def _register(self) -> str | None:
        try:
            resp = self._post("/ms/register", {
                "server_name": self._get_server_name(),
                "port": self._port,
                "listing": self._listing,
                "whitelist": self._whitelist_mode,
            })
            token = resp.get("server_token", "")
            if token:
                self._log(f"[MS] Зарегистрирован: token={token[:12]}…")
                return token
        except Exception as e:
            self._log(f"[MS] Ошибка регистрации: {e}")
        return None

    def send_offline(self):
        """Синхронно пометить сервер как offline на мастере (вызывать при закрытии)."""
        if not self._token:
            return
        try:
            build_id, infohash_v2, _ = ("", "", True)
            if self._get_build_info:
                build_id, infohash_v2, _ = self._get_build_info()
            if self._whitelist_mode:
                infohash_v2 = ""
            self._post("/ms/heartbeat", {
                "server_token": self._token,
                "build_id": build_id,
                "infohash_v2": infohash_v2,
                "online": False,
                "whitelist": self._whitelist_mode,
            })
        except Exception:
            pass

    def set_whitelist_mode(self, whitelist: bool):
        """Обновить режим без перезапуска и сразу отправить heartbeat."""
        self._whitelist_mode = whitelist
        threading.Thread(target=self._send_heartbeat, daemon=True).start()

    def force_heartbeat(self):
        """Немедленный heartbeat в фоновом потоке."""
        threading.Thread(target=self._send_heartbeat, daemon=True).start()

    def _send_heartbeat(self):
        import urllib.error
        if not self._token or not self._listing:
            return
        try:
            build_id, infohash_v2, online = ("", "", True)
            if self._get_build_info:
                build_id, infohash_v2, online = self._get_build_info()
            if self._whitelist_mode:
                infohash_v2 = ""
            self._post("/ms/heartbeat", {
                "server_token": self._token,
                "build_id": build_id,
                "infohash_v2": infohash_v2,
                "online": online,
                "whitelist": self._whitelist_mode,
            })
            self._log(f"[MS] Heartbeat отправлен (build={build_id[:8] or '—'})")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self._log("[MS] Токен не найден на мастере — перерегистрация…")
                self._token = None
                if self._save_token_cb:
                    self._save_token_cb(None)
            else:
                self._log(f"[MS] Heartbeat ошибка: {e}")
        except Exception as e:
            self._log(f"[MS] Heartbeat ошибка: {e}")

    def _heartbeat_loop(self):
        first = True
        while not self._stop_evt.is_set():
            if not first:
                self._stop_evt.wait(_MS_HEARTBEAT_INTERVAL)
            first = False
            if self._stop_evt.is_set():
                break
            if not self._listing:
                continue
            if not self._token:
                token = self._register()
                if token:
                    self._token = token
                    if self._save_token_cb:
                        self._save_token_cb(token)
                continue
            self._send_heartbeat()

    def _post(self, path: str, payload: dict) -> dict:
        import urllib.request
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            MS_URL + path,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "ModSync/2.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))


# ─────────────────────────────────────────────
# Клиентские утилиты: манифест, диффы, финализация
# ─────────────────────────────────────────────

def fetch_bytes(url: str, timeout: int = 15) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "ModSync/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data


def fetch_json(url: str, timeout: int = 10) -> dict:
    return json.loads(fetch_bytes(url, timeout).decode("utf-8"))


def manifest_paths(manifest: dict) -> dict[str, dict]:
    """{casefold_path: file_entry} по всем модам манифеста."""
    out = {}
    for mod in manifest.get("mods", []):
        for f in mod.get("files", []):
            out[f["path"].replace("\\", "/").casefold()] = f
    return out


def quick_local_diff(manifest: dict, mods_dir: Path) -> dict:
    """
    Быстрый дифф без хеширования (пути + размеры):
      missing  — в манифесте есть, на диске нет
      changed  — размер отличается (точно другой файл)
      maybe    — размер совпал (проверится хешами при обновлении)
      orphans  — на диске есть, в манифесте нет (кроме disabled_mods)
    Возвращает агрегаты по модам и плоские списки.
    """
    mods_dir = Path(mods_dir)
    want = manifest_paths(manifest)

    have: dict[str, int] = {}
    if mods_dir.is_dir():
        for dirpath, dirnames, filenames in os.walk(mods_dir):
            dirnames[:] = [d for d in dirnames if d.lower() != "disabled_mods"]
            for fn in filenames:
                p = Path(dirpath) / fn
                rel = p.relative_to(mods_dir).as_posix()
                try:
                    have[rel.casefold()] = p.stat().st_size
                except OSError:
                    continue

    missing, changed, maybe, orphans = [], [], [], []
    for key, entry in want.items():
        if key not in have:
            missing.append(entry["path"])
        elif have[key] != entry["size"]:
            changed.append(entry["path"])
        else:
            maybe.append(entry["path"])
    want_keys = set(want.keys())
    for key in have:
        if key not in want_keys:
            orphans.append(key)

    def _by_mod(paths):
        agg: dict[str, int] = {}
        for p in paths:
            agg[p.split("/", 1)[0]] = agg.get(p.split("/", 1)[0], 0) + 1
        return agg

    return {
        "missing": missing, "changed": changed,
        "maybe_ok": maybe, "orphans": orphans,
        "by_mod_missing": _by_mod(missing),
        "by_mod_changed": _by_mod(changed),
        "need_update": bool(missing or changed),
    }


def finalize_build(manifest: dict, mods_dir: Path, log_cb=None) -> None:
    """
    После завершения загрузки:
      1) досоздать пустые папки из manifest['dirs']
      2) осиротевшие файлы (нет в манифесте) → Mods/disabled_mods/orphans__TS/…
    """
    def log(m):
        if log_cb:
            log_cb(m)

    mods_dir = Path(mods_dir)

    for d in manifest.get("dirs", []):
        if _reject_path(d):
            continue
        (mods_dir / d).mkdir(parents=True, exist_ok=True)

    want_keys = set(manifest_paths(manifest).keys())
    orphan_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(mods_dir):
        dirnames[:] = [x for x in dirnames if x.lower() != "disabled_mods"]
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(mods_dir).as_posix()
            if rel.casefold() not in want_keys:
                orphan_files.append(p)

    if orphan_files:
        import shutil
        ts = time.strftime("%Y%m%d_%H%M%S")
        base = mods_dir / "disabled_mods" / f"orphans__{ts}"
        log(f"[V2] Осиротевших файлов: {len(orphan_files)} → {base.name}")
        for p in orphan_files:
            rel = p.relative_to(mods_dir)
            dst = base / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(p), str(dst))
            except Exception as e:
                log(f"[V2] Не перенести {rel}: {e}")
        # подчистить опустевшие каталоги (кроме тех что в manifest.dirs)
        keep = {d.casefold() for d in manifest.get("dirs", [])}
        for dirpath, dirnames, filenames in os.walk(mods_dir, topdown=False):
            d = Path(dirpath)
            if d == mods_dir:
                continue
            rel = d.relative_to(mods_dir).as_posix()
            if rel.casefold().startswith("disabled_mods"):
                continue
            if rel.casefold() in keep:
                continue
            try:
                if not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass


def read_steam_accounts() -> list[dict]:
    """
    Читает аккаунты из Steam\\config\\loginusers.vdf.
    Возвращает [{steamid, name, most_recent}], отсортировано most_recent первым.
    """
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam",
    ]
    # путь из реестра надёжнее
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            sp, _ = winreg.QueryValueEx(k, "SteamPath")
            candidates.insert(0, Path(sp))
    except Exception:
        pass

    vdf = None
    for c in candidates:
        p = c / "config" / "loginusers.vdf"
        if p.is_file():
            vdf = p
            break
    if vdf is None:
        return []

    try:
        text = vdf.read_text("utf-8", errors="replace")
    except OSError:
        return []

    accounts = []
    # блоки вида "7656119..." { ... "PersonaName" "X" ... "MostRecent" "1" ... }
    for m in re.finditer(r'"(7656119\d{10})"\s*\{(.*?)\}', text, re.S):
        sid, body = m.group(1), m.group(2)
        name_m = re.search(r'"PersonaName"\s*"((?:[^"\\]|\\.)*)"', body)
        recent_m = re.search(r'"(?:MostRecent|mostrecent)"\s*"1"', body)
        accounts.append({
            "steamid": sid,
            "name": (name_m.group(1) if name_m else sid),
            "most_recent": bool(recent_m),
        })
    accounts.sort(key=lambda a: (not a["most_recent"],))
    return accounts