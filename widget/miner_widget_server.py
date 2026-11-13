#!/usr/bin/env python3
import json, os, subprocess, sys, tempfile, threading, time, traceback, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HOT_C = 70
TIMEOUT = 4
CACHE_S = 10
HIST_GAP = 30
HIST_MAX = 120
LOW_POLLS = 2
REBOOT_GRACE_S = 120
LOG_MAX = 1000000
GAP_RESET = 120
BACKGROUND_S = 15
DEFAULTS = {"notify": True, "no_shares_minutes": 10, "offline_minutes": 2, "not_mining_ths": 0.1, "reminder_minutes": 60,
            "hot_clear_margin_c": 2}
ORDER = ("OFFLINE", "NOT_MINING", "NO_SHARES", "HOT", "BACKUP_POOL")


def now():
    return time.time()


def clock(ts):
    d = datetime.fromtimestamp(ts)
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'am' if d.hour < 12 else 'pm'}"


def human(n):
    if not isinstance(n, (int, float)):
        return "-"
    for unit, size in (("T", 1e12), ("G", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= size:
            v = n / size
            return f"{v:.0f}{unit}" if v >= 100 else f"{v:.1f}".rstrip("0").rstrip(".") + unit
    return str(int(n))


def duration(sec):
    if not isinstance(sec, (int, float)):
        return "-"
    sec = int(sec)
    d, h, m = sec // 86400, sec % 86400 // 3600, sec % 3600 // 60
    if d:
        return f"{d}d {h}h"
    return f"{h}h {m}m" if h else f"{m}m"


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_settings(cfg):
    s = dict(DEFAULTS)
    if not isinstance(cfg, dict):
        return s
    if isinstance(cfg.get("notify"), bool):
        s["notify"] = cfg["notify"]
    a = cfg.get("alerts") if isinstance(cfg.get("alerts"), dict) else {}
    for k in ("no_shares_minutes", "offline_minutes", "not_mining_ths", "reminder_minutes", "hot_clear_margin_c"):
        v = num(a.get(k))
        if v is not None and v > 0:
            s[k] = v
    return s


def clean(text):
    return " ".join("".join(c if c.isprintable() else " " for c in str(text)).replace("|", "/").split())


def applescript(body, title="Miner alert"):
    def q(t):
        return '"' + clean(t)[:200].replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"display notification {q(body)} with title {q(title)}"


def where(info, my_nodes=()):
    s = info.get("stratum") if isinstance(info.get("stratum"), dict) else {}
    fb = info.get("isUsingFallbackStratum", s.get("usingFallback"))
    if fb:
        return "Backup pool"
    host = info.get("stratumURL")
    if not host:
        return "-"
    return "Your node" if str(host) in my_nodes else str(host)


def read_row(m, info, now, my_nodes=()):
    s = info.get("stratum") if isinstance(info.get("stratum"), dict) else {}
    hr, w, t = num(info.get("hashRate")), num(info.get("power")), num(info.get("temp"))
    best = num(info.get("bestDiff"))
    if best is None:
        best = num(s.get("totalBestDiff"))
    row = {
        "name": m["name"],
        "state": "hot" if t is not None and t >= HOT_C else "ok",
        "ths": None if hr is None else round(hr / 1000, 1),
        "watts": None if w is None else round(w),
        "temp": None if t is None else round(t),
        "mining": where(info, my_nodes),
        "best": human(best),
        "running": duration(num(info.get("uptimeSeconds"))),
        "accepted": num(info.get("sharesAccepted")),
        "rejected": num(info.get("sharesRejected")),
        "last_answered": clock(now),
        "fan": num(info.get("fanrpm")),
        "ip": m["host"],
    }
    return row


class Poller:
    def __init__(self, config_path):
        self.config_path = config_path
        self.lock = threading.Lock()
        self.cached = None
        self.cached_at = 0.0
        self.seen = {}  # host -> {"last_ok": ts, "first_fail": ts}
        self.hist = {}  # host -> [[ts, ths], ...] ring buffer
        self.settings = dict(DEFAULTS)
        self.alerts = {}  # host -> alert tracking state
        self.alock = threading.Lock()
        self.log_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), "alerts.log")
        self.log_max = LOG_MAX

    def load(self):
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
            settings = parse_settings(cfg)
            nodes = []
            if isinstance(cfg, dict):
                nodes = [str(h).strip() for h in cfg.get("my_node_hosts") or []]
                cfg = cfg.get("miners")
            if not isinstance(cfg, list):
                raise ValueError
            out = []
            for i, e in enumerate(cfg):
                if not isinstance(e, dict):
                    raise ValueError
                out.append({"name": str(e.get("name") or f"Miner {i + 1}"),
                            "host": str(e.get("host") or "").strip()})
            self.settings = settings
            return out, nodes
        except Exception:
            return None

    def fetch(self, host):
        url = host if host.startswith("http") else f"http://{host}"
        with urllib.request.urlopen(url.rstrip("/") + "/api/system/info", timeout=TIMEOUT) as r:
            info = json.load(r)
        if not isinstance(info, dict):
            raise ValueError("not an object")
        return info

    def record(self, host, now, ths):
        if ths is None:
            return
        h = self.hist.setdefault(host, [])
        if h and now - h[-1][0] < HIST_GAP:
            return
        h.append([round(now, 1), ths])
        del h[:-HIST_MAX]

    def settle(self, m, t, codes, reading_ok=True):
        st = self.alerts[m["host"]]
        name, active = m["name"], st["active"]
        for code, (level, text, note) in codes.items():
            if code not in active:
                active[code] = {"noted": t}
                self.log(t, name, code, "raised", text)
                self.say(f"{name}: {note}")
            elif t - active[code]["noted"] >= self.settings["reminder_minutes"] * 60:
                active[code]["noted"] = t
                self.log(t, name, code, "reminder", text)
                self.say(f"{name}: still {note}")
            active[code].update(level=level, text=text)
        gone = [c for c in active if c not in codes]
        for c in gone:
            self.log(t, name, c, "cleared", active.pop(c)["text"])
        if gone:
            st["await_normal"] = True
        if st.get("await_normal") and not active and reading_ok:
            st["await_normal"] = False
            self.say(f"{name}: back to normal")
        return self.shown(active)

    @staticmethod
    def shown(active):
        return [{"code": c, "level": active[c]["level"], "text": active[c]["text"]} for c in ORDER if c in active]

    def track(self, m, t, info):
        with self.alock:
            st = self.alerts.setdefault(m["host"], {"low": 0, "shares": None, "since": None, "last": None, "active": {}})
            cfg, codes = self.settings, {}
            if info is None:
                down = t - self.seen[m["host"]]["first_fail"]
                if down < cfg["offline_minutes"] * 60:
                    return self.shown(st["active"])
                codes["OFFLINE"] = ("red", f"OFFLINE · no answer for {int(down // 60)} min", "not answering")
            else:
                hr, acc = num(info.get("hashRate")), num(info.get("sharesAccepted"))
                gap = st["last"] is not None and t - st["last"] > GAP_RESET
                st["last"] = t
                zero = hr is not None and hr / 1000 < cfg["not_mining_ths"]
                up = num(info.get("uptimeSeconds"))
                st["low"] = st["low"] + 1 if zero and not gap and not (up is not None and up < REBOOT_GRACE_S) else 0
                if acc is None or st["shares"] is None or gap or acc != st["shares"]:
                    st["shares"], st["since"] = acc, t
                if st["low"] >= LOW_POLLS:
                    codes["NOT_MINING"] = ("red", "NOT MINING · hashrate is zero", "not mining, hashrate is zero")
                if acc is not None and t - st["since"] >= cfg["no_shares_minutes"] * 60:
                    codes["NO_SHARES"] = ("red", f"NO SHARES · none accepted for {int((t - st['since']) // 60)} min",
                                          f"no shares accepted for {cfg['no_shares_minutes']:g} minutes")
                if where(info) == "Backup pool":
                    codes["BACKUP_POOL"] = ("amber", "BACKUP POOL", "on its backup pool")
                temp = num(info.get("temp"))
                if temp is not None and temp >= (HOT_C - cfg["hot_clear_margin_c"] if "HOT" in st["active"] else HOT_C):
                    codes["HOT"] = ("red", f"HOT · {round(temp)} °C", f"running hot, {round(temp)} °C")
            return self.settle(m, t, codes, reading_ok=info is None or not zero)

    def log(self, t, name, code, kind, text):
        line = f"{datetime.fromtimestamp(t):%Y-%m-%d %H:%M:%S} | {clean(name)} | {code} | {kind} | {clean(text)}\n"
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            print(f"alert log failed: {e}", file=sys.stderr, flush=True)
            return
        self.trim_log()

    def trim_log(self):
        try:
            if os.path.getsize(self.log_path) <= self.log_max:
                return
            with open(self.log_path, "rb") as f:
                data = f.read()
            data = data[len(data) // 2:]
            data = data[data.find(b"\n") + 1:]
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.log_path), prefix=".alerts.", suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, self.log_path)
        except OSError as e:
            print(f"alert log trim failed: {e}", file=sys.stderr, flush=True)

    def started(self, t=None):
        self.log(t or now(), "Widget", "SERVER", "started", "server started")

    def say(self, body):
        if not self.settings["notify"]:
            return
        try:
            r = subprocess.run(["/usr/bin/osascript", "-e", applescript(body)], capture_output=True, timeout=5)
            if r.returncode:
                print(f"notification failed: osascript exit {r.returncode}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"notification failed: {e}", file=sys.stderr, flush=True)

    def alerts_for(self, m, t, info):
        try:
            return self.track(m, t, info)
        except Exception:
            traceback.print_exc()
            return []

    def one(self, m, my_nodes=()):
        t = now()
        if not m["host"]:
            return {"name": m["name"], "state": "not_set", "message": f"{m['name']}: not plugged in yet",
                    "ip": "", "history": [], "alerts": []}
        seen = self.seen.setdefault(m["host"], {})
        try:
            info = self.fetch(m["host"])
            row = read_row(m, info, t, my_nodes)
        except Exception:
            info = None
        if info is not None:
            seen["last_ok"], seen["first_fail"] = t, None
            self.record(m["host"], t, row["ths"])
            row["history"] = [list(p) for p in self.hist.get(m["host"], [])]
            row["alerts"] = self.alerts_for(m, t, info)
            return row
        if not seen.get("first_fail"):
            seen["first_fail"] = t
        since = seen.get("last_ok") or seen["first_fail"]
        return {"name": m["name"], "state": "not_answering",
                "message": f"Not answering since {clock(since)}", "ip": m["host"],
                "history": [list(p) for p in self.hist.get(m["host"], [])],
                "alerts": self.alerts_for(m, t, None)}

    def data(self):
        with self.lock:
            t = now()
            if self.cached is not None and t - self.cached_at < CACHE_S:
                return self.cached
            loaded = self.load()
            if loaded is None:
                result = {"error": "Miner list could not be read", "miners": [], "totals": None}
            else:
                miners, nodes = loaded
                with ThreadPoolExecutor(max_workers=max(1, len(miners))) as ex:
                    rows = list(ex.map(lambda m: self.one(m, nodes), miners))
                live = [r for r in rows if r["state"] in ("ok", "hot")]
                totals = None
                if len(live) >= 2:
                    totals = {"ths": round(sum(r["ths"] or 0 for r in live), 1),
                              "watts": round(sum(r["watts"] or 0 for r in live))}
                result = {"error": None, "miners": rows, "totals": totals}
            self.cached, self.cached_at = result, now()
            return result


def make_server(config_path, port, host="127.0.0.1"):
    poller = Poller(config_path)
    page_path = os.path.join(HERE, "miner_widget.html")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            port = self.server.server_address[1]
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if port == 80:
                allowed |= {"127.0.0.1", "localhost"}
            if self.headers.get("Host") not in allowed:
                body = b"Forbidden\n"
                self.send_response(403)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            path = self.path.split("?")[0]
            if path == "/data":
                body, ctype = json.dumps(poller.data()).encode(), "application/json"
            elif path == "/":
                with open(page_path, "rb") as f:
                    body = f.read()
                ctype = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer((host, port), H)
    srv.poller = poller
    return srv


if __name__ == "__main__":
    cfg = os.environ.get("MINER_CONFIG") or (sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "miners.json"))
    port = int(os.environ.get("MINER_PORT") or (sys.argv[2] if len(sys.argv) > 2 else 8757))
    srv = make_server(cfg, port)
    srv.poller.started()

    def keep_polling():
        while True:
            try:
                srv.poller.data()
            except Exception:
                traceback.print_exc()
            time.sleep(BACKGROUND_S)

    threading.Thread(target=keep_polling, daemon=True).start()
    srv.serve_forever()
