# ModSync

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![PyQt6](https://img.shields.io/badge/GUI-PyQt6-green?logo=qt&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Game](https://img.shields.io/badge/Game-7%20Days%20to%20Die-red)

**A mod synchronization tool for 7 Days to Die servers.**  
Host your modpack — your players always get the right files automatically.

[🇷🇺 Русская версия](#-русская-версия)

</div>

---

## Overview

ModSync is a lightweight client-server application that keeps mod files in sync between a server host and players. The server host runs `server_app.py`, points it at the mods folder, and shares the address. Players run `client_app.py`, enter the address, and their mods are automatically updated to match the server.

No more "wrong mod version" errors. No more manually sending zip files to friends.

---

## Features

- **Automatic sync** — clients download only what changed (file diffing via manifest)
- **Steam ID whitelist** — control who can connect and download mods
- **Cache management** — configurable cache size, version retention, and TTL
- **Live file watching** — server detects new/changed mods automatically, no restart needed
- **Dark GUI** — built with PyQt6, status indicators, client table, sliders for cache settings
- **Auth integration** — optional external auth URL for Steam ID validation

---

## Downloads

**Don't want to install Python?** Grab the pre-built executables from the [Releases](https://github.com/Leo111444/ModSync/releases) page — no installation required, just run and go.

---

## Requirements

- Python 3.10 or newer
- PyQt6

Install dependencies:

```bash
pip install PyQt6
```

---

## Building an Executable

If you want to build a standalone `.exe` yourself:

```bash
pip install pyinstaller
```

**Server:**
```bash
pyinstaller --onefile --windowed --name ModSync-Server server_app.py
```

**Client:**
```bash
pyinstaller --onefile --windowed --name ModSync-Client client_app.py
```

The output will be in the `dist/` folder. The `--windowed` flag hides the console window since both apps have a GUI.

---

## Usage

### Server (host)

```bash
python server_app.py
```

1. Set the path to your `Mods` folder
2. Set the port (default is fine for LAN, open it in your router for internet play)
3. Add allowed Steam IDs to the whitelist
4. Click **Start**
5. Share your IP and port with players

### Client (players)

```bash
python client_app.py
```

1. Enter the server address (`ip:port`)
2. Enter your Steam ID
3. Click **Sync** — missing or outdated mods are downloaded automatically

---

## Configuration

Settings are saved automatically via the GUI. Key options:

| Setting | Description |
|---|---|
| `mods_path` | Path to the folder containing your mods |
| `port` | HTTP port the server listens on |
| `cache_max_gb` | Maximum disk space used for cached bundles |
| `keep_mod_versions` | How many old versions of each mod to keep |
| `bundle_ttl_days` | How long cached bundles are kept before cleanup |
| `auth_url` | Optional external URL for Steam ID validation |

---

## How It Works

```
Server                          Client
  │                               │
  ├─ Scans mods folder            │
  ├─ Builds manifest (hashes)     │
  ├─ Starts HTTP server           │
  │                               │
  │    ◄── GET /manifest ─────────┤
  │    ──── manifest.json ────►   │
  │                               ├─ Compares with local files
  │    ◄── GET /mod/<file> ───────┤  (downloads only what changed)
  │    ──── file bytes ────────►  │
  │                               ├─ Places files in Mods folder
```

---

## Project Structure

```
ModSync/
├── server_app.py   # Server: HTTP file server + PyQt6 GUI
└── client_app.py   # Client: sync logic + PyQt6 GUI
```

---

## License

MIT — do whatever you want with it.

---

---

# 🇷🇺 Русская версия

<div align="center">

**Инструмент синхронизации модов для серверов 7 Days to Die.**  
Ведёшь сервер — игроки всегда получают актуальные моды автоматически.

</div>

---

## О проекте

ModSync — лёгкое клиент-серверное приложение для синхронизации модов. Владелец сервера запускает `server_app.py`, указывает папку с модами и сообщает адрес игрокам. Игроки запускают `client_app.py`, вводят адрес — и их моды автоматически обновляются до актуальной версии.

Никаких «не та версия мода». Никаких zip-архивов в Discord.

---

## Возможности

- **Автоматическая синхронизация** — клиент скачивает только изменившиеся файлы (сравнение по манифесту)
- **Whitelist Steam ID** — контроль над тем, кто может подключаться и скачивать моды
- **Управление кэшем** — настраиваемый размер кэша, количество хранимых версий, TTL
- **Слежение за файлами** — сервер автоматически обнаруживает новые и изменённые моды, перезапуск не нужен
- **Тёмный интерфейс** — PyQt6 с индикаторами статуса, таблицей клиентов и ползунками для настроек кэша
- **Авторизация** — опциональная проверка Steam ID через внешний URL

---

## Скачать

**Не хочешь возиться с Python?** Скачай готовые `.exe` со страницы [Releases](https://github.com/Leo111444/ModSync/releases) — просто запусти и всё.

---

## Требования

- Python 3.10 и выше
- PyQt6

Установка зависимостей:

```bash
pip install PyQt6
```

---

## Сборка в EXE

Если хочешь собрать `.exe` самостоятельно:

```bash
pip install pyinstaller
```

**Сервер:**
```bash
pyinstaller --onefile --windowed --name ModSync-Server server_app.py
```

**Клиент:**
```bash
pyinstaller --onefile --windowed --name ModSync-Client client_app.py
```

Готовые файлы появятся в папке `dist/`. Флаг `--windowed` скрывает консоль — у обоих приложений есть GUI.

---

## Запуск

### Сервер (хост)

```bash
python server_app.py
```

1. Укажи путь к папке `Mods`
2. Задай порт (по умолчанию подойдёт для локальной сети; для интернета — открой порт в роутере)
3. Добавь Steam ID игроков в whitelist
4. Нажми **Start**
5. Сообщи игрокам свой IP и порт

### Клиент (игроки)

```bash
python client_app.py
```

1. Введи адрес сервера (`ip:port`)
2. Введи свой Steam ID
3. Нажми **Sync** — недостающие и устаревшие моды скачаются автоматически

---

## Настройки

Настройки сохраняются автоматически через интерфейс. Основные параметры:

| Параметр | Описание |
|---|---|
| `mods_path` | Путь к папке с модами |
| `port` | Порт HTTP-сервера |
| `cache_max_gb` | Максимальный объём диска для кэша бандлов |
| `keep_mod_versions` | Сколько старых версий каждого мода хранить |
| `bundle_ttl_days` | Через сколько дней удалять устаревшие бандлы |
| `auth_url` | Внешний URL для проверки Steam ID (опционально) |

---

## Как это работает

```
Сервер                          Клиент
  │                               │
  ├─ Сканирует папку модов        │
  ├─ Строит манифест (хэши)       │
  ├─ Запускает HTTP-сервер        │
  │                               │
  │    ◄── GET /manifest ─────────┤
  │    ──── manifest.json ────►   │
  │                               ├─ Сравнивает с локальными файлами
  │    ◄── GET /mod/<file> ───────┤  (скачивает только изменения)
  │    ──── байты файла ───────►  │
  │                               ├─ Кладёт файлы в папку Mods
```

---

## Структура проекта

```
ModSync/
├── server_app.py   # Сервер: HTTP-раздача файлов + GUI (PyQt6)
└── client_app.py   # Клиент: логика синхронизации + GUI (PyQt6)
```

---

## Лицензия

MIT — делай что хочешь.
