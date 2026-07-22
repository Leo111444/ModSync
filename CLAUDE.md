# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ModSync is a client-server mod synchronization tool for 7 Days to Die dedicated servers. A server host runs `server_app.py` pointed at their `Mods` folder; players run `client_app.py`, which downloads whatever mods are missing or outdated. Both are single-file PyQt6 desktop apps (dark GUI + embedded HTTP server/client logic) — there is no separate backend service or database server, everything is local (SQLite + filesystem).

## Commands

```bash
pip install -r requirements.txt   # PyQt6, libtorrent
python server_app.py              # run the server GUI
python client_app.py              # run the client GUI
```

There is no test suite, linter, or build config in this repo. `test_client_download.py` is a standalone manual script (not pytest) for exercising the v2 torrent download path end-to-end:

```bash
python test_client_download.py http://<server-ip>:<port> C:\Temp\msv2-test
```

Building standalone executables (PyInstaller, output goes to `dist/`):

```bash
pyinstaller --onefile --windowed --name ModSync-Server server_app.py
pyinstaller --onefile --windowed --name ModSync-Client client_app.py
```

## Architecture

Three top-level modules, no packages/subfolders:

- **`server_app.py`** — server GUI (`ServerWindow`) + embedded HTTP server (`ApiHandler` / `HttpServerThread`) + SQLite persistence (client stats, Steam ID whitelist/blacklist) + cache/zip management + UPnP port forwarding.
- **`client_app.py`** — client GUI (`ClientWindow`) that talks to a server's HTTP API, diffs local mods against the server manifest, and downloads/deletes files to match.
- **`modsync_v2.py`** — shared module imported by *both* of the above (`import modsync_v2`). Implements the v2 sync protocol: BitTorrent-based distribution via `libtorrent`, including an embedded BitTorrent tracker (`Tracker`), build/publish pipeline (`BuildManager`), torrent engine wrappers (`TorrentEngine`, `ClientEngine`), and the `/api/v2/*` + `/announce` HTTP route handlers (`handle_v2_get`, `handle_v2_post`) that `ApiHandler` in `server_app.py` delegates into.

### Two sync protocols, one server

The server's `ApiHandler.do_GET`/`do_POST` first delegate to `modsync_v2.handle_v2_get`/`handle_v2_post` for anything under `/api/v2/*` or `/announce`; if unhandled, it falls through to the legacy v1 routes:

- **v1 (legacy, HTTP zip download)**: `GET /manifest`, `GET /mods`, `GET /mod/<id>`, `GET /clients`, `POST /heartbeat`, `POST /diff`, `POST /bundle`. Manifest = per-mod SHA-256 hash + size, computed by `build_manifest`/`compute_mod_hash`. Downloads are zipped on demand into a disk cache (`zip_mod_to_cache`, `zip_bundle_to_cache`) and streamed out.
- **v2 (BitTorrent-based)**: `GET /api/v2/build|info|manifest|torrent|ws/<path>|files/<path>`, `POST /api/v2/publish`, `GET /announce` (tracker). The server publishes a "build" (snapshot of the Mods folder) that clients fetch via an embedded torrent client/tracker instead of plain HTTP zip streaming — meant to scale better with many concurrent players. Both protocols run on the same port simultaneously; the client (`client_app.py`) tries v2 first and can fall back to v1.

When touching sync/download logic, check whether the change needs to apply to both `zip_mod_to_cache`/`zip_bundle_to_cache` (v1) and `BuildManager`/`ClientEngine` (v2) — they are independent code paths that both need to stay consistent with whatever is on disk in the Mods folder.

### Auth model

Steam ID based. The server whitelists Steam IDs (SQLite table, `db_is_allowed_steamid`), optionally synced from an external `auth_url` (`fetch_allowed_steamids_from_site`). `require_auth(steamid)` gates v1 endpoints and (in non-"open" `auth_mode`) v2 endpoints too. There's also a blacklist (`db_blacklist_*`) checked independently. v2's `/api/v2/build` and `/api/v2/info` are always unauthenticated (status-only); `/api/v2/publish` is restricted to localhost callers.

### State/config locations

- Server: `%ProgramData%\ModSyncServer\` — `config.json`, `cache\`, `server.sqlite` (constants at top of `server_app.py`: `APPDATA_DIR`, `CONFIG_PATH`, `CACHE_DIR`, `DB_PATH`).
- Client: `%APPDATA%\ModSyncClient\` — `config.json`, `temp\` (constants at top of `client_app.py`).

Config (`read_config`/`write_config`) is a flat JSON dict persisted automatically from the GUI; there's no schema/migration layer, so new keys should be read with `.get(...)` defaults.

### GUI code shape

Both `ServerWindow` and `ClientWindow` are large single classes mixing: Qt widget construction/layout, Qt signal wiring (`pyqtSignal`/`QObject` bridges like `LogBridge`, `ManifestBridge`, `UiBridge` used to marshal background-thread events onto the Qt main thread), and business logic (sync/publish/download triggered from button handlers). Background work (HTTP server loop, torrent engine, file scanning) runs in `threading.Thread`s and reports back through these Qt bridges — don't call Qt widget methods directly from a non-GUI thread.
