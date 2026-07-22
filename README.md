# ModSync

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![PyQt6](https://img.shields.io/badge/GUI-PyQt6-green?logo=qt&logoColor=white)
![libtorrent](https://img.shields.io/badge/P2P-libtorrent-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Game](https://img.shields.io/badge/Game-7%20Days%20to%20Die-red)

**A mod synchronization tool for 7 Days to Die servers.**  
Host your modpack — your players always get the right files automatically.

[🇷🇺 Русская версия](#-русская-версия)

</div>

---

## Overview

ModSync is a client-server application that keeps mod files in sync between a server host and players. The host runs `server_app.py`, points it at the Mods folder, and shares the address. Players run `client_app.py`, enter the address, and their mods are automatically updated.

**v2** distributes files over BitTorrent — players seed to each other while downloading, so the host's bandwidth is not the bottleneck. An embedded tracker and a fallback HTTP protocol run on the same port simultaneously.

---

## Features

- **P2P distribution** — BitTorrent sync via embedded libtorrent tracker; players seed to each other
- **Automatic sync** — client downloads only what changed, verified by per-file hash manifest
- **UPnP / NAT-PMP** — automatic port forwarding for inbound P2P connections
- **Steam ID whitelist** — control who can connect; supports external auth URL
- **Live file watching** — server detects new/changed mods automatically, no restart needed
- **Auto-publish on server restart** — detects 7DaysToDieServer.exe restart and re-publishes
- **Dark glassmorphism UI** — PyQt6 with EN/RU localisation
- **Dual protocol** — v2 BitTorrent + v1 HTTP fallback on the same port

---

## Downloads

Pre-built Windows executables (no Python required) on the [Releases](https://github.com/Leo111444/ModSync/releases) page.

Download and extract the zip — keep the `_internal` folder next to the `.exe`.

---

## Requirements (running from source)

- Python 3.10+
- PyQt6
- libtorrent (Python bindings)

```bash
pip install -r requirements.txt
```

---

## Running from source

```bash
python server_app.py   # server GUI
python client_app.py   # client GUI
```

---

## Building executables

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name ModSync-Server server_app.py
pyinstaller --onefile --windowed --name ModSync-Client client_app.py
```

---

## Usage

### Server (host)

1. Point to your `Mods` folder
2. Set port (default 8766; open it in your router or let UPnP handle it)
3. Add player Steam IDs to the whitelist (or switch to Open mode)
4. Click **Publish** — server builds a torrent snapshot and starts seeding
5. Share your external IP and port with players

### Client (players)

1. Enter the server address (`ip:port`)
2. Enter your Steam ID
3. Click **Check** — missing or outdated mods download automatically via P2P
4. Enable **P2P sharing** to seed to other players while they download

---

## How it works (v2)

```
Server                              Clients
  │                                    │
  ├─ Scans Mods folder                 │
  ├─ Builds torrent snapshot           │
  ├─ Starts seeding (libtorrent)       │
  ├─ Embedded tracker /announce        │
  │                                    │
  │   ◄── GET /api/v2/build ───────────┤  check current build hash
  │   ◄── GET /api/v2/manifest ────────┤  download file list + hashes
  │   ◄── GET /api/v2/torrent ─────────┤  download .torrent file
  │                                    │
  │   ◄── /announce ───────────────────┤  peer announces to tracker
  │   ──── compact peer list ──────►   │  tracker returns swarm
  │                                    │
  │   ←────── BitTorrent P2P ─────────►│  peers exchange pieces
```

The v1 HTTP protocol (`GET /manifest`, `GET /mod/<id>`) remains available as fallback.

---

## Project structure

```
ModSync/
├── server_app.py    # Server GUI + embedded HTTP server + SQLite
├── client_app.py    # Client GUI + sync logic
└── modsync_v2.py    # Shared: BitTorrent engine, tracker, build pipeline
```

---

## License

MIT

---
---

# 🇷🇺 Русская версия

<div align="center">

**Инструмент синхронизации модов для серверов 7 Days to Die.**  
Ведёшь сервер — игроки всегда получают актуальные моды автоматически.

</div>

---

## О проекте

ModSync — клиент-серверное приложение для синхронизации модов. Владелец сервера запускает `server_app.py`, указывает папку с модами и сообщает адрес. Игроки запускают `client_app.py`, вводят адрес — моды обновляются автоматически.

**v2** раздаёт файлы через BitTorrent: игроки сидируют друг другу в процессе загрузки, поэтому канал хоста не является узким местом. Встроенный трекер и резервный HTTP-протокол работают на одном порту одновременно.

---

## Возможности

- **P2P-раздача** — синхронизация через BitTorrent (libtorrent) со встроенным трекером
- **Автоматическая синхронизация** — клиент скачивает только изменившиеся файлы
- **UPnP / NAT-PMP** — автоматический проброс портов для входящих P2P-соединений
- **Whitelist Steam ID** — контроль доступа; поддержка внешнего URL авторизации
- **Слежение за файлами** — сервер обнаруживает изменения без перезапуска
- **Авто-публикация при рестарте** — определяет перезапуск 7DaysToDieServer.exe и переиздаёт сборку
- **Тёмный glassmorphism UI** — PyQt6, EN/RU
- **Двойной протокол** — v2 BitTorrent + v1 HTTP-резерв на одном порту

---

## Скачать

Готовые `.exe` для Windows (Python не нужен) — на странице [Releases](https://github.com/Leo111444/ModSync/releases).

Скачай и распакуй zip — папку `_internal` оставь рядом с `.exe`.

---

## Требования (запуск из исходников)

- Python 3.10+
- PyQt6
- libtorrent (Python-биндинги)

```bash
pip install -r requirements.txt
```

---

## Запуск из исходников

```bash
python server_app.py   # GUI сервера
python client_app.py   # GUI клиента
```

---

## Сборка в EXE

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name ModSync-Server server_app.py
pyinstaller --onefile --windowed --name ModSync-Client client_app.py
```

---

## Использование

### Сервер (хост)

1. Укажи путь к папке `Mods`
2. Задай порт (по умолчанию 8766; открой в роутере или доверь UPnP)
3. Добавь Steam ID игроков в whitelist (или переключись в режим Open)
4. Нажми **Publish** — сервер создаёт снапшот-торрент и начинает сидировать
5. Сообщи игрокам внешний IP и порт

### Клиент (игроки)

1. Введи адрес сервера (`ip:port`)
2. Введи свой Steam ID
3. Нажми **Check** — недостающие моды загружаются через P2P
4. Включи **P2P sharing** чтобы сидировать другим игрокам

---

## Как это работает (v2)

```
Сервер                              Клиенты
  │                                    │
  ├─ Сканирует папку модов             │
  ├─ Создаёт торрент-снапшот           │
  ├─ Начинает сидировать               │
  ├─ Встроенный трекер /announce       │
  │                                    │
  │   ◄── GET /api/v2/build ───────────┤  проверка хэша сборки
  │   ◄── GET /api/v2/manifest ────────┤  список файлов с хэшами
  │   ◄── GET /api/v2/torrent ─────────┤  скачать .torrent
  │                                    │
  │   ◄── /announce ───────────────────┤  пир анонсирует себя
  │   ──── список пиров ─────────────► │  трекер возвращает рой
  │                                    │
  │   ←────── BitTorrent P2P ─────────►│  пиры обмениваются кусками
```

Протокол v1 (`GET /manifest`, `GET /mod/<id>`) доступен как резервный.

---

## Структура проекта

```
ModSync/
├── server_app.py    # GUI сервера + встроенный HTTP-сервер + SQLite
├── client_app.py    # GUI клиента + логика синхронизации
└── modsync_v2.py    # Общий модуль: BitTorrent-движок, трекер, пайплайн сборки
```

---

## Лицензия

MIT
