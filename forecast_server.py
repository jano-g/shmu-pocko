#!/usr/bin/env python3
"""
SHMÚ ALADIN 4.5 km weather forecast — local server.

Downloads the latest ALADIN model run (GRIB2) from opendata.shmu.sk,
decodes point forecasts for major Slovak cities and serves them as JSON
to a local HTML dashboard.

Run:   python forecast_server.py
Then open http://localhost:8765 in your browser.
"""

import os
import sys
import json
import ssl
import time
import threading
import math
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Windows consoles default to cp1252 — make stdout/stderr UTF-8 so prints don't crash
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --------------------------------------------------------------------------
# eccodes bootstrap — make the bundled ecmwflibs ecCodes DLL discoverable
# (works on this Python 3.14 setup where the eccodes binary wheel is absent)
# --------------------------------------------------------------------------
def _bootstrap_eccodes():
    import ecmwflibs
    import findlibs
    os.environ["ECCODES_PYTHON_USE_FINDLIBS"] = "1"
    os.add_dll_directory(os.path.dirname(ecmwflibs.__file__))
    _dll = ecmwflibs.find("eccodes")
    _orig = findlibs.find
    findlibs.find = lambda name=None, *a, **k: (_dll if name == "eccodes" else _orig(name, *a, **k))

try:
    _bootstrap_eccodes()
    import eccodes
except Exception as e:  # pragma: no cover
    sys.stderr.write(
        "ERROR: could not initialise the ecCodes GRIB decoder.\n"
        f"Details: {e}\n\n"
        "Install the dependencies into THIS exact interpreter:\n"
        f'    "{sys.executable}" -m pip install eccodes ecmwflibs findlibs numpy\n\n'
        "(Tip: always use the same launcher for pip and for running the script,\n"
        " e.g. both `python -m pip install ...` and `python forecast_server.py`.)\n"
    )
    sys.exit(1)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
BASE = "https://opendata.shmu.sk/meteorology/weather/nwp/aladin/sk/4.5km"
MAX_HOURS = 72            # how many forecast hours to load (model goes to ~102)
PORT = 8765
REFRESH_SECONDS = 3600    # rebuild the dataset at most once per hour
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".grib_cache")

# Major Slovak cities (name, lat, lon)
CITIES = [
    ("Bratislava",      48.148, 17.107),
    ("Trnava",          48.377, 17.587),
    ("Nitra",           48.308, 18.087),
    ("Trenčín",         48.894, 18.044),
    ("Žilina",          49.223, 18.740),
    ("Banská Bystrica", 48.736, 19.146),
    ("Martin",          49.066, 18.922),
    ("Poprad",          49.059, 20.298),
    ("Prešov",          48.998, 21.239),
    ("Košice",          48.716, 21.261),
]

# GRIB shortNames we need at the surface
WANT = {"2t", "10u", "10v", "ugust", "vgust", "tcc", "lcc", "mcc", "tp", "prmsl", "cape"}

# unverified SSL context — opendata.shmu.sk serves an incomplete cert chain
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------
def _get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "shmu-forecast/1.0"})
    with urllib.request.urlopen(req, context=_SSL, timeout=60) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def _list_dirs(url):
    """Return sub-directory names from an Apache autoindex page."""
    html = _get(url)
    return re.findall(r'href="([^"/]+)/"', html)


def find_latest_run():
    """Return (date 'YYYYMMDD', run 'HHMM', sorted list of available forecast hours)."""
    dates = sorted(d for d in _list_dirs(BASE + "/") if re.fullmatch(r"\d{8}", d))
    for date in reversed(dates):
        runs = sorted(r for r in _list_dirs(f"{BASE}/{date}/") if re.fullmatch(r"\d{4}", r))
        for run in reversed(runs):
            html = _get(f"{BASE}/{date}/{run}/")
            hours = sorted(int(h) for h in re.findall(r"al-grib_sk_(\d+)-", html))
            if hours and 0 in hours:
                return date, run, hours
    raise RuntimeError("No ALADIN run found on the server.")


def _grib_url(date, run, hour):
    return f"{BASE}/{date}/{run}/al-grib_sk_{hour:03d}-{date}-{run}-nwp-.grb"


def _download(date, run, hour):
    """Download one GRIB hour file into the cache; return local path."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{date}_{run}_{hour:03d}.grb")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    data = _get(_grib_url(date, run, hour), binary=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def _cleanup_cache(keep_prefix):
    """Remove cached GRIB files that don't belong to the current run."""
    if not os.path.isdir(CACHE_DIR):
        return
    for fn in os.listdir(CACHE_DIR):
        if fn.endswith(".grb") and not fn.startswith(keep_prefix):
            try:
                os.remove(os.path.join(CACHE_DIR, fn))
            except OSError:
                pass


# --------------------------------------------------------------------------
# GRIB decoding
# --------------------------------------------------------------------------
def decode_file(path):
    """Return {city_name: {shortName: value}} for one GRIB hour file."""
    out = {name: {} for (name, _, _) in CITIES}
    with open(path, "rb") as f:
        while True:
            gid = eccodes.codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                sn = eccodes.codes_get(gid, "shortName")
                if sn in WANT:
                    tol = eccodes.codes_get(gid, "typeOfLevel")
                    # only the single-level surface fields (skip isobaric levels)
                    if tol in ("heightAboveGround", "surface", "meanSea", "entireAtmosphere"):
                        for (name, la, lo) in CITIES:
                            near = eccodes.codes_grib_find_nearest(gid, la, lo)[0]
                            out[name][sn] = near.value
            finally:
                eccodes.codes_release(gid)
    return out


# --------------------------------------------------------------------------
# Forecast assembly
# --------------------------------------------------------------------------
def _wind_dir(u, v):
    """Meteorological wind direction (deg, where wind comes FROM)."""
    return (270.0 - math.degrees(math.atan2(v, u))) % 360.0


def _condition(tcc, precip, cape, is_day):
    """Derive a coarse condition code + emoji from cloud/precip/CAPE."""
    if precip >= 0.2 and cape >= 800 and precip >= 1.0:
        return "thunderstorm", "⛈️"
    if precip >= 2.0:
        return "rain", "🌧️"
    if precip >= 0.2:
        return "showers", "🌦️"
    if tcc >= 85:
        return "overcast", "☁️"
    if tcc >= 50:
        return "cloudy", "🌥️"
    if tcc >= 20:
        return "partly", ("⛅" if is_day else "☁️")
    return "clear", ("☀️" if is_day else "🌙")


def build_forecast():
    """Download the latest run and return the full JSON-serialisable dataset."""
    date, run, avail = find_latest_run()
    hours = [h for h in range(0, MAX_HOURS + 1) if h in avail]
    _cleanup_cache(f"{date}_{run}_")

    # download in parallel
    with ThreadPoolExecutor(max_workers=8) as ex:
        paths = list(ex.map(lambda h: (h, _download(date, run, h)), hours))

    # decode each hour
    per_hour = {}
    for h, path in paths:
        try:
            per_hour[h] = decode_file(path)
        except Exception as e:
            sys.stderr.write(f"decode error hour {h}: {e}\n")

    run_epoch = _run_epoch(date, run)
    cities_out = []
    for (name, la, lo) in CITIES:
        series = []
        prev_tp = None
        for h in sorted(per_hour):
            d = per_hour[h].get(name, {})
            if "2t" not in d:
                continue
            valid = run_epoch + h * 3600
            hour_of_day = int(((valid // 3600) % 24 + 24) % 24)  # UTC hour
            local_hour = (hour_of_day + 2) % 24                   # CEST (UTC+2)
            is_day = 5 <= local_hour <= 20

            tp_total = d.get("tp", 0.0)
            if prev_tp is None:
                precip = 0.0
            else:
                precip = max(0.0, tp_total - prev_tp)
            prev_tp = tp_total

            u, v = d.get("10u", 0.0), d.get("10v", 0.0)
            gu, gv = d.get("ugust", u), d.get("vgust", v)
            tcc = max(0.0, min(100.0, d.get("tcc", 0.0)))
            cape = d.get("cape", 0.0)
            cond, icon = _condition(tcc, precip, cape, is_day)

            series.append({
                "t": valid,                                   # unix seconds (UTC)
                "temp": round(d["2t"] - 273.15, 1),
                "wind": round(math.hypot(u, v) * 3.6, 1),     # km/h
                "gust": round(math.hypot(gu, gv) * 3.6, 1),
                "dir": round(_wind_dir(u, v)),
                "cloud": round(tcc),
                "cloud_low": round(max(0.0, min(100.0, d.get("lcc", 0.0)))),
                "precip": round(precip, 2),
                "pressure": round(d.get("prmsl", 101325.0) / 100.0),  # hPa
                "cape": round(cape),
                "cond": cond,
                "icon": icon,
                "is_day": is_day,
            })

        if series:
            cities_out.append({"name": name, "lat": la, "lon": lo, "hours": series})

    return {
        "run": {"date": date, "time": run, "epoch": run_epoch},
        "generated": int(time.time()),
        "source": f"{BASE}/{date}/{run}/",
        "cities": cities_out,
    }


def _run_epoch(date, run):
    """UTC unix seconds for the model run initialisation time."""
    import calendar
    y, m, d = int(date[:4]), int(date[4:6]), int(date[6:8])
    hh, mm = int(run[:2]), int(run[2:4])
    return calendar.timegm((y, m, d, hh, mm, 0, 0, 0, 0))


# --------------------------------------------------------------------------
# Cache + server
# --------------------------------------------------------------------------
_state = {"data": None, "built_at": 0, "building": False, "error": None}
_lock = threading.Lock()


def get_data(force=False):
    with _lock:
        fresh = _state["data"] and (time.time() - _state["built_at"] < REFRESH_SECONDS)
        if fresh and not force:
            return _state["data"]
        if _state["building"]:
            return _state["data"]  # may be None on first ever call
        _state["building"] = True
    try:
        data = build_forecast()
        with _lock:
            _state["data"] = data
            _state["built_at"] = time.time()
            _state["error"] = None
        return data
    except Exception as e:
        with _lock:
            _state["error"] = str(e)
        sys.stderr.write(f"build_forecast failed: {e}\n")
        return _state["data"]
    finally:
        with _lock:
            _state["building"] = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "index.html not found", "text/plain")
        elif path == "/api/forecast":
            data = get_data(force="force" in self.path)
            if data is None:
                msg = _state.get("error") or "Building forecast, please retry in a moment…"
                self._send(503, json.dumps({"error": msg}), "application/json")
            else:
                self._send(200, json.dumps(data), "application/json; charset=utf-8")
        else:
            self._send(404, "Not found", "text/plain")


def main():
    print(f"SHMÚ ALADIN forecast server")
    print(f"  loading latest model run (this can take ~20-40 s on first start)…")
    get_data(force=True)  # warm the cache before serving
    if _state["data"]:
        r = _state["data"]["run"]
        print(f"  loaded run {r['date']} {r['time']} UTC, {len(_state['data']['cities'])} cities")
    else:
        print(f"  WARNING: initial load failed: {_state.get('error')}")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"\n  ->  open  http://localhost:{PORT}\n  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
