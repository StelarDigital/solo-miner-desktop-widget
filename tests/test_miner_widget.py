import json, os, sys, tempfile, threading, unittest, socket
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "widget"))
import miner_widget_server as mws

BASE = {"hashRate": 9500.0, "power": 150.0, "temp": 55.0, "stratumURL": "192.168.1.10",
        "stratumUser": "bc1qexampleexampleexample.worker1", "hostname": "miner-1",
        "macAddr": "AA:BB:CC:00:11:22", "ssid": "home-wifi",
        "fallbackStratumURL": "pool.example.org", "uptimeSeconds": 2 * 86400 + 13 * 3600 + 120,
        "sharesAccepted": 10000, "sharesRejected": 5, "bestDiff": 123456789,
        "stratum": {"usingFallback": False}}


def stub(payload):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            b = json.dumps(payload).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b)
    s = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def dead_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class T(unittest.TestCase):
    def setUp(self):
        self.stubs = []
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        for s in self.stubs: s.shutdown(); s.server_close()

    def host(self, payload):
        s = stub(payload); self.stubs.append(s)
        return f"127.0.0.1:{s.server_address[1]}"

    def run_cfg(self, miners=None, raw=None):
        path = os.path.join(self.tmp, "m.json")
        with open(path, "w") as f:
            f.write(raw if raw is not None else json.dumps(miners))
        return mws.Poller(path).data()

    def test_ok_row(self):
        d = self.run_cfg({"miners": [{"name": "A", "host": self.host(BASE)}], "my_node_hosts": ["192.168.1.10"]})
        r = d["miners"][0]
        self.assertEqual(r["state"], "ok")
        self.assertEqual((r["ths"], r["watts"], r["temp"]), (9.5, 150, 55))
        self.assertEqual((r["mining"], r["best"], r["running"]), ("Your node", "123M", "2d 13h"))
        self.assertEqual((r["accepted"], r["rejected"]), (10000, 5))
        self.assertIsNone(d["totals"])

    def test_node_hosts_config(self):
        m = [{"name": "A", "host": self.host(BASE)}]
        self.assertEqual(self.run_cfg(m)["miners"][0]["mining"], "192.168.1.10")
        self.assertEqual(self.run_cfg({"miners": m})["miners"][0]["mining"], "192.168.1.10")
        self.assertEqual(self.run_cfg({"miners": m, "my_node_hosts": ["192.168.1.99"]})["miners"][0]["mining"], "192.168.1.10")
        self.assertEqual(self.run_cfg({"miners": m, "my_node_hosts": ["192.168.1.10"]})["miners"][0]["mining"], "Your node")
        fb = [{"name": "A", "host": self.host(dict(BASE, stratum={"usingFallback": True}))}]
        self.assertEqual(self.run_cfg({"miners": fb, "my_node_hosts": ["192.168.1.10"]})["miners"][0]["mining"], "Backup pool")
        self.assertTrue(self.run_cfg({"miners": 5})["error"])

    def test_fallback(self):
        p = dict(BASE, stratum={"usingFallback": True})
        self.assertEqual(self.run_cfg([{"name": "A", "host": self.host(p)}])["miners"][0]["mining"], "Backup pool")

    def test_public_host_name(self):
        p = dict(BASE, stratumURL="solo.example.org")
        self.assertEqual(self.run_cfg([{"name": "A", "host": self.host(p)}])["miners"][0]["mining"], "solo.example.org")

    def test_hot(self):
        self.assertEqual(self.run_cfg([{"name": "A", "host": self.host(dict(BASE, temp=71))}])["miners"][0]["state"], "hot")
        self.assertEqual(self.run_cfg([{"name": "A", "host": self.host(dict(BASE, temp=69.9))}])["miners"][0]["state"], "ok")

    def test_dead(self):
        r = self.run_cfg([{"name": "A", "host": f"127.0.0.1:{dead_port()}"}])["miners"][0]
        self.assertEqual(r["state"], "not_answering")
        self.assertTrue(r["message"].startswith("Not answering since "))
        for k in ("ths", "watts", "temp"): self.assertNotIn(k, r)

    def test_dead_after_live_never_shows_old_numbers(self):
        s = stub(BASE); port = s.server_address[1]
        path = os.path.join(self.tmp, "m.json")
        json.dump([{"name": "A", "host": f"127.0.0.1:{port}"}], open(path, "w"))
        p = mws.Poller(path)
        self.assertEqual(p.data()["miners"][0]["state"], "ok")
        s.shutdown(); s.server_close(); p.cached = None
        r = p.data()["miners"][0]
        self.assertEqual(r["state"], "not_answering"); self.assertNotIn("ths", r)

    def test_not_set(self):
        r = self.run_cfg([{"name": "Miner 2", "host": ""}])["miners"][0]
        self.assertEqual(r["state"], "not_set")
        self.assertEqual(r["message"], "Miner 2: not plugged in yet")

    def test_totals(self):
        d = self.run_cfg([{"name": "A", "host": self.host(BASE)}, {"name": "B", "host": self.host(dict(BASE, hashRate=5000, power=100))}])
        self.assertEqual(d["totals"], {"ths": 14.5, "watts": 250})

    def test_bad_config(self):
        for raw in ("not json", '{"a":1}', "[1,2]"):
            d = self.run_cfg(raw=raw)
            self.assertTrue(d["error"]); self.assertEqual(d["miners"], [])
        d = mws.Poller(os.path.join(self.tmp, "missing.json")).data()
        self.assertTrue(d["error"])

    def test_missing_keys(self):
        r = self.run_cfg([{"name": "A", "host": self.host({})}])["miners"][0]
        self.assertEqual(r["state"], "ok"); self.assertIsNone(r["ths"]); self.assertEqual(r["mining"], "-")
        r = self.run_cfg([{"name": "A", "host": self.host({"stratum": "x", "hashRate": "bad"})}])["miners"][0]
        self.assertIsNone(r["ths"])

    def test_http_endpoints(self):
        import urllib.request
        path = os.path.join(self.tmp, "m.json"); json.dump([{"name": "A", "host": ""}], open(path, "w"))
        srv = mws.make_server(path, 0); port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read()
            self.assertIn(b"MINERS", page)
            self.assertIn(b'class="btc"', page)
            self.assertIn(b"#f7931a", page)
            for needle in (b"messageHandlers.drag", b"dragstart", b"dragmove", b"dragend", b"#070b16", b"COMBINED"):
                self.assertIn(needle, page)
            for needle in (b'id="tabs"', b'class="tabs"', b"function segDigits", b"data-tab", b'data-tab="all"', b"localStorage", b"onTabs"):
                self.assertIn(needle, page)
            self.assertEqual(json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/data"))["miners"][0]["state"], "not_set")
        finally:
            srv.shutdown(); srv.server_close()

    def test_host_header_validation(self):
        import http.client
        path = os.path.join(self.tmp, "m.json"); json.dump([{"name": "A", "host": ""}], open(path, "w"))
        srv = mws.make_server(path, 0); port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def get(url_path, host):
            c = http.client.HTTPConnection("127.0.0.1", port)
            c.putrequest("GET", url_path, skip_host=True, skip_accept_encoding=True)
            if host is not None:
                c.putheader("Host", host)
            c.endheaders()
            r = c.getresponse(); body = r.read(); h = r.getheader("Access-Control-Allow-Origin"); c.close()
            return r.status, body, h
        try:
            for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
                for p in ("/", "/data"):
                    st, _, cors = get(p, host)
                    self.assertEqual(st, 200); self.assertIsNone(cors)
            for host in (f"evil.example:{port}", "evil.example", "localhost", "127.0.0.1", f"localhost.evil.example:{port}", None):
                for p in ("/", "/data"):
                    st, body, _ = get(p, host)
                    self.assertEqual(st, 403, (host, p)); self.assertNotIn(b"miners", body)
        finally:
            srv.shutdown(); srv.server_close()

    def poller(self, hosts_payloads):
        path = os.path.join(self.tmp, "h.json")
        json.dump([{"name": f"M{i}", "host": self.host(pl) if pl is not None else ""} for i, pl in enumerate(hosts_payloads)], open(path, "w"))
        return mws.Poller(path)

    def test_history_field_and_existing_fields_kept(self):
        d = self.poller([BASE, None]).data()
        r = d["miners"][0]
        self.assertEqual(len(r["history"]), 1)
        self.assertEqual(r["history"][0][1], 9.5)
        for k in ("name", "state", "ths", "watts", "temp", "mining", "best", "running", "accepted", "rejected", "last_answered"):
            self.assertIn(k, r)
        self.assertEqual(r["fan"], None)
        self.assertEqual(d["miners"][1]["history"], [])
        self.assertEqual(d["miners"][1]["state"], "not_set")
        self.assertEqual(self.poller([dict(BASE, fanrpm=1620)]).data()["miners"][0]["fan"], 1620)

    def test_history_spacing(self):
        p = self.poller([BASE])
        p.data()
        for _ in range(3):
            p.cached = None; p.data()
        self.assertEqual(len(p.data()["miners"][0]["history"]), 1)
        host = next(iter(p.hist))
        p.hist[host][0][0] -= mws.HIST_GAP + 1
        p.cached = None
        self.assertEqual(len(p.data()["miners"][0]["history"]), 2)

    def test_history_cap(self):
        p = self.poller([BASE])
        p.data()
        host = next(iter(p.hist))
        p.hist[host] = [[i, 9.0] for i in range(mws.HIST_MAX + 30)]
        p.cached = None
        h = p.data()["miners"][0]["history"]
        self.assertEqual(len(h), mws.HIST_MAX)
        self.assertEqual(h[-1][1], 9.5)
        self.assertEqual(h[0][0], 30 + 1)

    def test_history_kept_while_not_answering_and_no_sample_without_hashrate(self):
        s = stub(BASE); port = s.server_address[1]
        path = os.path.join(self.tmp, "k.json")
        json.dump([{"name": "A", "host": f"127.0.0.1:{port}"}], open(path, "w"))
        p = mws.Poller(path); p.data()
        s.shutdown(); s.server_close(); p.cached = None
        r = p.data()["miners"][0]
        self.assertEqual(r["state"], "not_answering"); self.assertEqual(len(r["history"]), 1)
        d = self.poller([{}]).data()["miners"][0]
        self.assertEqual(d["history"], [])


import re
from datetime import datetime
from unittest import mock
import miner_alerts_summary as mas


class Clock:
    def __init__(self): self.t = 1800000000.0
    def __call__(self): return self.t


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.clock = Clock()
        self.cur = dict(BASE)
        self.notes = []
        self.run_effect = None

        def fake_run(argv, **kw):
            self.notes.append(argv)
            if self.run_effect: raise self.run_effect
            return mock.Mock(returncode=0)
        for p in (mock.patch.object(mws, "now", self.clock), mock.patch.object(mws.subprocess, "run", fake_run)):
            p.start(); self.addCleanup(p.stop)
        self.cfgpath = os.path.join(self.tmp, "m.json")
        self.setup_poller([{"name": "Miner 1", "host": "x"}])

    def setup_poller(self, cfg):
        json.dump(cfg, open(self.cfgpath, "w"))
        self.p = mws.Poller(self.cfgpath)

        def fetch(host):
            if self.cur is None: raise OSError("down")
            return dict(self.cur)
        self.p.fetch = fetch

    def poll(self, secs=15, move=True, **change):
        self.clock.t += secs
        if self.cur is not None:
            self.cur.update(change)
            if move: self.cur["sharesAccepted"] += 1
        return self.p.data()["miners"][0]

    def span(self, secs, step=15, **kw):
        row = None
        for _ in range(int(secs // step)): row = self.poll(step, **kw)
        return row

    def codes(self, row): return [a["code"] for a in row["alerts"]]
    def bodies(self): return [re.search(r'display notification "(.*)" with title', n[2]).group(1) for n in self.notes]
    def lines(self): return open(self.p.log_path, encoding="utf-8").read().splitlines() if os.path.exists(self.p.log_path) else []

    def test_normal_has_no_alerts_and_stays_quiet(self):
        r = self.span(120)
        self.assertEqual(r["alerts"], []); self.assertEqual(self.notes, []); self.assertEqual(self.lines(), [])
        self.assertEqual(self.p.data()["miners"][0]["state"], "ok")

    def test_not_mining_needs_two_consecutive_polls(self):
        self.poll()
        self.assertEqual(self.poll(hashRate=0)["alerts"], [])
        self.assertEqual(self.poll(hashRate=9500)["alerts"], [])
        self.assertEqual(self.poll(hashRate=0)["alerts"], [])
        r = self.poll(hashRate=0)
        self.assertEqual(r["alerts"], [{"code": "NOT_MINING", "level": "red", "text": "NOT MINING · hashrate is zero"}])
        self.assertEqual(self.bodies(), ["Miner 1: not mining, hashrate is zero"])

    def test_not_mining_threshold_and_clear(self):
        self.poll(); self.poll(hashRate=100); self.assertEqual(self.poll(hashRate=100)["alerts"], [])
        self.poll(hashRate=99)
        self.assertEqual(self.codes(self.poll(hashRate=99)), ["NOT_MINING"])
        self.assertEqual(self.poll(hashRate=9500)["alerts"], [])
        self.assertEqual(self.bodies(), ["Miner 1: not mining, hashrate is zero", "Miner 1: back to normal"])

    def test_no_shares_after_ten_minutes_only(self):
        self.poll()
        self.assertEqual(self.span(585, move=False)["alerts"], [])
        r = self.poll(15, move=False)
        self.assertEqual(r["alerts"], [{"code": "NO_SHARES", "level": "red", "text": "NO SHARES · none accepted for 10 min"}])
        self.assertEqual(self.bodies(), ["Miner 1: no shares accepted for 10 minutes"])
        self.assertEqual(self.poll(30, move=False)["alerts"][0]["text"], "NO SHARES · none accepted for 10 min")
        self.assertEqual(self.poll(60, move=False)["alerts"][0]["text"], "NO SHARES · none accepted for 11 min")
        self.assertEqual(self.poll(15)["alerts"], [])
        self.assertEqual(len(self.notes), 2)

    def test_no_shares_needs_a_baseline_first(self):
        self.assertEqual(self.poll(move=False)["alerts"], [])
        self.assertEqual(self.span(300, move=False)["alerts"], [])
        self.assertEqual(self.span(285, move=False)["alerts"], [])
        self.assertEqual(self.codes(self.poll(15, move=False)), ["NO_SHARES"])

    def test_no_shares_baseline_restarts_after_a_gap_and_after_offline(self):
        self.poll()
        self.assertEqual(self.poll(3000, move=False)["alerts"], [])
        self.assertEqual(self.span(585, move=False)["alerts"], [])
        self.assertEqual(self.codes(self.poll(15, move=False)), ["NO_SHARES"])
        self.cur = None
        self.span(180)
        self.cur = dict(BASE, sharesAccepted=500)
        self.assertEqual(self.poll(move=False)["alerts"], [])
        self.assertEqual(self.span(570, move=False)["alerts"], [])
        self.assertEqual(self.codes(self.poll(30, move=False)), ["NO_SHARES"])

    def test_share_count_reset_is_movement_not_a_stall(self):
        self.poll()
        self.span(540, move=False)
        self.assertEqual(self.poll(15, sharesAccepted=3, move=False)["alerts"], [])
        self.assertEqual(self.span(570, move=False)["alerts"], [])
        self.assertEqual(self.codes(self.poll(30, move=False)), ["NO_SHARES"])

    def test_backup_pool_is_amber_and_immediate(self):
        self.poll()
        r = self.poll(stratum={"usingFallback": True})
        self.assertEqual(r["alerts"], [{"code": "BACKUP_POOL", "level": "amber", "text": "BACKUP POOL"}])
        self.assertEqual(self.bodies(), ["Miner 1: on its backup pool"])
        self.assertEqual(self.poll(stratum={"usingFallback": False})["alerts"], [])
        self.assertEqual(self.bodies()[-1], "Miner 1: back to normal")

    def test_hot_at_seventy(self):
        self.assertEqual(self.poll(temp=69.9)["alerts"], [])
        r = self.poll(temp=71)
        self.assertEqual(r["alerts"], [{"code": "HOT", "level": "red", "text": "HOT · 71 °C"}])
        self.assertEqual(self.poll(temp=70)["alerts"][0]["code"], "HOT")
        self.assertEqual(self.poll(temp=69.9)["alerts"], [])

    def test_offline_alert_waits_two_minutes(self):
        self.poll()
        self.cur = None
        r = self.poll()
        self.assertEqual(r["state"], "not_answering"); self.assertEqual(r["alerts"], [])
        self.assertEqual(self.span(105)["alerts"], [])
        self.assertEqual(self.notes, [])
        r = self.poll()
        self.assertEqual(r["alerts"], [{"code": "OFFLINE", "level": "red", "text": "OFFLINE · no answer for 2 min"}])
        self.assertEqual(self.bodies(), ["Miner 1: not answering"])
        self.assertEqual(self.span(60)["alerts"][0]["text"], "OFFLINE · no answer for 3 min")
        self.cur = dict(BASE)
        self.assertEqual(self.poll()["alerts"], [])
        self.assertEqual(self.bodies(), ["Miner 1: not answering", "Miner 1: back to normal"])

    def test_blip_keeps_active_alert_and_adds_no_notice(self):
        self.poll(hashRate=0); self.poll(hashRate=0)
        self.assertEqual(len(self.notes), 1)
        cur, self.cur = self.cur, None
        r = self.poll()
        self.assertEqual(r["state"], "not_answering"); self.assertEqual(self.codes(r), ["NOT_MINING"])
        self.cur = cur
        self.poll(hashRate=0)
        self.assertEqual(len(self.notes), 1)

    def test_notice_on_transition_only_then_hourly_reminder(self):
        self.poll(hashRate=0); self.poll(hashRate=0)
        self.assertEqual(len(self.notes), 1)
        self.span(60 * 60 - 15, hashRate=0)
        self.assertEqual(len(self.notes), 1)
        self.span(15, hashRate=0)
        self.assertEqual(self.bodies(), ["Miner 1: not mining, hashrate is zero", "Miner 1: still not mining, hashrate is zero"])
        self.span(59 * 60, hashRate=0)
        self.assertEqual(len(self.notes), 2)
        self.span(60, hashRate=0)
        self.assertEqual(len(self.notes), 3)
        self.assertEqual([l.split(" | ")[3] for l in self.lines()], ["raised", "reminder", "reminder"])
        for n in self.notes: self.assertEqual(n[:2], ["/usr/bin/osascript", "-e"])

    def test_back_to_normal_only_when_every_alert_clears(self):
        self.poll(); self.poll(hashRate=0, stratum={"usingFallback": True}); self.poll(hashRate=0)
        self.assertEqual(self.codes(self.poll(hashRate=0)), ["NOT_MINING", "BACKUP_POOL"])
        self.assertEqual(len(self.notes), 2)
        self.assertEqual(self.codes(self.poll(hashRate=9500)), ["BACKUP_POOL"])
        self.assertEqual(len(self.notes), 2)
        self.poll(stratum={"usingFallback": False})
        self.assertEqual(self.bodies()[2:], ["Miner 1: back to normal"])

    def test_hostile_miner_name_cannot_break_out_of_the_applescript_string(self):
        name = 'Miner" & (do shell script "id") & "'
        self.setup_poller([{"name": name, "host": "x"}])
        self.poll(stratum={"usingFallback": True})
        argv = self.notes[0]
        self.assertEqual(argv[:2], ["/usr/bin/osascript", "-e"]); self.assertEqual(len(argv), 3)
        self.assertEqual(argv[2], 'display notification "Miner\\" & (do shell script \\"id\\") & \\": on its backup pool" with title "Miner alert"')
        self.assertEqual(re.sub(r'\\.', "", argv[2]).count('"'), 4)
        self.assertEqual(mws.applescript("a\\"), 'display notification "a\\\\" with title "Miner alert"')
        self.assertEqual(mws.applescript("a\nb\tc"), 'display notification "a b c" with title "Miner alert"')
        self.assertEqual(mws.applescript("x" * 500).count("x"), 200)

    def test_notify_false_keeps_the_log_but_posts_nothing(self):
        self.setup_poller({"miners": [{"name": "Miner 1", "host": "x"}], "notify": False})
        self.poll(hashRate=0); self.poll(hashRate=0)
        self.assertEqual(self.notes, [])
        self.assertEqual(len(self.lines()), 1)

    def test_osascript_failure_is_logged_and_never_crashes_the_poller(self):
        self.run_effect = OSError("no osascript")
        self.poll(hashRate=0); r = self.poll(hashRate=0)
        self.assertEqual(self.codes(r), ["NOT_MINING"]); self.assertEqual(r["state"], "ok")
        self.run_effect = None
        with mock.patch.object(mws.subprocess, "run", return_value=mock.Mock(returncode=1)):
            self.poll(stratum={"usingFallback": True})
        self.assertEqual(self.codes(self.poll()), ["NOT_MINING", "BACKUP_POOL"])

    def test_alert_log_lines(self):
        self.poll(hashRate=0); self.poll(hashRate=0)
        stamp = datetime.fromtimestamp(self.clock.t).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(self.lines(), [f"{stamp} | Miner 1 | NOT_MINING | raised | NOT MINING · hashrate is zero"])
        self.poll(hashRate=9500)
        self.assertEqual(self.lines()[1].split(" | ")[1:], ["Miner 1", "NOT_MINING", "cleared", "NOT MINING · hashrate is zero"])
        self.assertEqual(os.path.dirname(self.p.log_path), self.tmp)

    def test_alert_log_name_cannot_forge_lines(self):
        self.setup_poller([{"name": "A | B\n2026-01-01 00:00:00 | X | HOT | raised | forged", "host": "x"}])
        self.poll(stratum={"usingFallback": True})
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(len(self.lines()[0].split(" | ")), 5)

    def test_thresholds_are_configurable_and_bad_values_fall_back(self):
        self.setup_poller({"miners": [{"name": "Miner 1", "host": "x"}],
                           "alerts": {"no_shares_minutes": 3, "offline_minutes": 1, "not_mining_ths": 5, "reminder_minutes": 10}})
        self.cur = None
        self.poll(); self.assertEqual(self.span(30)["alerts"], []); self.assertEqual(self.poll()["alerts"], [])
        self.assertEqual(self.codes(self.poll()), ["OFFLINE"])
        self.cur = dict(BASE)
        self.poll(); self.poll(hashRate=4000)
        self.assertEqual(self.codes(self.poll(hashRate=4000)), ["NOT_MINING"])
        self.span(60, hashRate=9500)
        self.assertEqual(self.span(165, move=False)["alerts"], [])
        self.assertEqual(self.codes(self.poll(15, move=False)), ["NO_SHARES"])
        s = mws.parse_settings({"notify": "yes", "alerts": {"no_shares_minutes": -1, "offline_minutes": "x", "not_mining_ths": True}})
        self.assertEqual(s, mws.DEFAULTS)
        self.assertEqual(mws.parse_settings([1]), mws.DEFAULTS)

    def test_rows_always_carry_alerts_and_keep_old_fields(self):
        self.setup_poller([{"name": "A", "host": "x"}, {"name": "B", "host": ""}])
        d = self.p.data()
        self.assertEqual([m["alerts"] for m in d["miners"]], [[], []])
        for k in ("name", "state", "ths", "watts", "temp", "mining", "best", "running", "accepted", "rejected", "last_answered", "fan", "ip", "history"):
            self.assertIn(k, d["miners"][0])

    def test_page_has_the_alert_banner_and_tab_dot(self):
        page = open(os.path.join(os.path.dirname(mws.__file__), "miner_widget.html"), encoding="utf-8").read()
        for needle in ("function alertOf", 'class="alert ', 'class="dot ', "class=\"ma ", ".alert.red", ".alert.amb"):
            self.assertIn(needle, page)


class SummaryTests(unittest.TestCase):
    NOW = datetime(2026, 11, 13, 7, 30)

    def summary(self, lines):
        path = os.path.join(tempfile.mkdtemp(), "alerts.log")
        open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        return mas.summary(path, self.NOW)

    def test_none(self):
        self.assertEqual(mas.summary(os.path.join(tempfile.mkdtemp(), "missing.log"), self.NOW), "Miner alerts in the last 24 hours: none.")
        self.assertEqual(self.summary([""]), "Miner alerts in the last 24 hours: none.")
        self.assertEqual(self.summary(["2026-11-11 02:14:05 | Miner 1 | HOT | raised | x", "2026-11-11 02:20:05 | Miner 1 | HOT | cleared | x"]),
                         "Miner alerts in the last 24 hours: none.")

    def test_one(self):
        self.assertEqual(self.summary(["2026-11-13 02:14:05 | Miner 1 | NOT_MINING | raised | x", "2026-11-13 02:31:40 | Miner 1 | NOT_MINING | cleared | x"]),
                         "Miner alerts in the last 24 hours: Miner 1 not mining 2:14am to 2:31am.")

    def test_several(self):
        self.assertEqual(self.summary([
            "2026-11-13 02:14:05 | Miner 1 | NOT_MINING | raised | x",
            "2026-11-13 02:31:40 | Miner 1 | NOT_MINING | cleared | x",
            "2026-11-13 03:05:00 | Miner 1 | BACKUP_POOL | raised | BACKUP POOL",
            "2026-11-13 04:05:00 | Miner 1 | BACKUP_POOL | reminder | BACKUP POOL",
            "garbage line",
            "2026-11-12 23:05:00 | Miner 2 | OFFLINE | raised | x",
            "2026-11-13 00:10:00 | Miner 2 | OFFLINE | cleared | x"]),
            "Miner alerts in the last 24 hours: Miner 2 offline yesterday 11:05pm to 12:10am; "
            "Miner 1 not mining 2:14am to 2:31am; Miner 1 on backup pool since 3:05am.")

    def test_open_alert_from_before_the_window_still_shows(self):
        self.assertEqual(self.summary(["2026-11-11 22:00:00 | Miner 1 | NO_SHARES | raised | x"]),
                         "Miner alerts in the last 24 hours: Miner 1 no shares accepted since Nov 11 10:00pm.")


if __name__ == "__main__":
    unittest.main()
