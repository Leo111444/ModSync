# -*- coding: utf-8 -*-
"""
Тест-клиент ModSync v2: скачивает опубликованную сборку с сервера через торрент.
Проверяет всю цепочку: /api/v2/torrent → трекер → пир-сид → загрузка.

Использование:
    python test_client_download.py http://192.168.0.32:8765 C:\Temp\msv2-test
"""
import sys
import time
import urllib.request
from pathlib import Path

import libtorrent as lt


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    server = sys.argv[1].rstrip("/")
    target = Path(sys.argv[2])
    target.mkdir(parents=True, exist_ok=True)

    print(f"[1] Качаю торрент: {server}/api/v2/torrent")
    with urllib.request.urlopen(f"{server}/api/v2/torrent", timeout=10) as r:
        torrent_bytes = r.read()
    print(f"    получено {len(torrent_bytes)} байт")

    ti = lt.torrent_info(lt.bdecode(torrent_bytes))
    print(f"    имя: {ti.name()}, файлов (с падами): {ti.num_files()}")

    print("[2] Стартую сессию")
    ses = lt.session({
        "listen_interfaces": "0.0.0.0:6885",
        "enable_dht": False,
        "enable_lsd": False,
        "enable_upnp": False,
        "enable_natpmp": False,
    })

    params = lt.add_torrent_params()
    params.ti = ti
    params.save_path = str(target)
    # трекер подставляем сами от адреса сервера — как будет делать боевой клиент
    params.trackers = [f"{server}/announce"]
    h = ses.add_torrent(params)

    print(f"[3] Качаю в {target}")
    last = -1
    while True:
        st = h.status()
        pct = int(st.progress * 100)
        if pct != last:
            print(f"    {pct:3d}%  ↓{st.download_rate/1024/1024:6.1f} МБ/с"
                  f"  пиров: {st.num_peers}  сидов: {st.num_seeds}")
            last = pct
        if st.is_seeding:
            break
        time.sleep(1)

    print("[4] Готово — сборка скачана и проверена по хешам.")


if __name__ == "__main__":
    main()
