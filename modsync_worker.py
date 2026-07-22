import sys, os

import modsync_v2 as _mv
import json as _j, base64 as _b64, time as _t, traceback as _tb
from pathlib import Path as _P


def _w(sf_path, d):
    try:
        with open(sf_path, 'w') as f:
            _j.dump(d, f)
    except Exception:
        pass


def main():
    if len(sys.argv) < 2:
        print("Usage: modsync_worker <cfg_path>")
        sys.exit(1)

    cfg_path = sys.argv[1]
    with open(cfg_path) as f:
        cfg = _j.load(f)
    sf_path = cfg['status_file']

    _w(sf_path, {'state': 'loading', 'active': True})

    tb = _b64.b64decode(cfg['torrent_b64'])
    md = _P(cfg['mods_dir'])
    su = cfg.get('server_url', '')
    sh = cfg.get('share', False)

    _buf: list[str] = []
    def _log(m): _buf.append(m)

    _w(sf_path, {'state': 'init', 'active': True})
    eng = _mv.ClientEngine(listen_port=0, log_cb=_log)
    eng.set_share(sh)
    eng.update(tb, md, su, share=sh)
    _w(sf_path, {'state': 'checking', 'active': True, 'progress': 0.0, 'alerts': []})

    while True:
        p = eng.progress()
        al = eng.pump_alerts()
        _buf.extend(al)
        try:
            mp = {k: list(v) for k, v in eng.mod_progress().items()}
        except Exception:
            mp = {}
        _w(sf_path, {
            'state':         p.get('state', 'checking'),
            'active':        True,
            'progress':      p.get('progress', 0.0),
            'download_rate': p.get('download_rate', 0),
            'upload_rate':   p.get('upload_rate', 0),
            'peers':         p.get('peers', 0),
            'seeds':         p.get('seeds', 0),
            'done':          p.get('done', False),
            'alerts':        _buf[-20:],
            'mod_progress':  mp,
        })
        _buf.clear()
        if p.get('done'):
            _w(sf_path, {'state': 'done', 'active': False, 'done': True, 'alerts': []})
            break
        _t.sleep(0.5)

    eng.stop()


try:
    main()
except Exception as e:
    try:
        with open(sys.argv[1]) as f:
            c = _j.load(f)
        with open(c['status_file'], 'w') as f:
            _j.dump({'state': 'error', 'active': False,
                     'msg': str(e), 'tb': _tb.format_exc()}, f)
    except Exception:
        pass
    sys.exit(1)
