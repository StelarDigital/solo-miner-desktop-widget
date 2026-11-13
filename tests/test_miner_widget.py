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


if __name__ == "__main__":
    unittest.main()
