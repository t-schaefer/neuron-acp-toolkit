"""
Offline tests - no real device, no network beyond 127.0.0.1.

    python3 -m unittest discover -s router/tests -p "test_*.py" -v

Covers: the read-only ACP2 client against a simulated Neuron (tests/fake_neuron.py),
the routing walk + LiveModel, Case A/B analysis incl. mixed Main/Backup, the
SRC/DST list parser, and the HTTP service (state, gzip, guards, read, check,
Take refusal). The simulated device records every set-property attempt; every
test asserts there were none.
"""
import gzip
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROUTER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROUTER))
sys.path.insert(0, str(ROUTER.parent / "acp2"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import client as acp2client          # noqa: E402
import walk as acp2walk              # noqa: E402
from checks import analyse_channel   # noqa: E402
from fake_neuron import FakeNeuron   # noqa: E402
from live_model import LiveModel     # noqa: E402
from route_lists import normalize_device, normalize_paste, parse_list, stream_names  # noqa: E402

SRC_CSV = "_ID;_Mnemonic;_Alternate Mnemonic 1;_TL Inhibit;_Level ID 1;_Level ID 2;_Level ID 3;_Level ID 4\n" + "".join(
    f"{c};DCR.IPGT1-TX0{c};OUT {c};0;NMOS2|1|IPGT1|VTX-0{c};NMOS2|1|IPGT1|ATX-0{c};;\n" for c in range(1, 5))
DST_CSV = "_ID;_Mnemonic;_Alternate Mnemonic 1;_TL Inhibit;_TL Group;_Level ID 1;_Level ID 2;_Level ID 3\n" + "".join(
    f"{c};DCR.IPGT1-RX0{c};IN {c};0;0;NMOS2|1|IPGT1|VRX-0{c};NMOS2|1|IPGT1|ARX-0{c};\n" for c in range(1, 5))
DST_RTF = "{\\rtf1\\ansi{\\fonttbl\\f0 Helvetica;}\n\\f0 " + DST_CSV.replace(";", "\t").replace("\n", "\\\n") + "}"


class FakeDeviceCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeNeuron()
        cls._port = acp2client.AN2_PORT
        acp2client.AN2_PORT = cls.fake.port
        cls.tmp = Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        acp2client.AN2_PORT = cls._port
        cls.fake.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def tearDown(self):
        self.assertEqual(self.fake.set_attempts, [], "something tried to WRITE to the device")


class ClientTests(FakeDeviceCase):
    def test_an2_and_root(self):
        with acp2client.Acp2Client("127.0.0.1") as c:
            self.assertEqual(c.an2_version(), 1)
            self.assertEqual(c.an2_slot_count(), 2)
            self.assertEqual(c.an2_slot_info(1)["status"], "present")
            self.assertEqual(c.acp2_version(1), 2)
            root = c.get_object(1, 1)
            self.assertEqual(root.label, "ROOT_NODE_V2")
            self.assertEqual(root.type, "node")

    def test_enum_float_string_decoding(self):
        snap = acp2walk.walk("127.0.0.1", self.tmp / "dec.json", pace=0)
        by = {o["path"]: o for o in snap["objects"]}
        main = by["PROCESSING VIDEO/PATH A/CHANNEL 02/Main Input"]
        self.assertEqual(main["options"][str(main["value"])], "SDI 02")
        self.assertAlmostEqual(by["INPUT/IP/AUDIO/STREAM 1/Stream Delay"]["value"], 19.5, places=3)
        self.assertEqual(by["GENERAL/NMOS/Label"]["value"], "IPGT1")

    def test_client_cannot_write(self):
        with acp2client.Acp2Client("127.0.0.1") as c:
            with self.assertRaises(acp2client.Acp2Error):
                c._acp2_request(1, 3, 8, 5, 0)          # 3 = set property
            with self.assertRaises(acp2client.Acp2Error):
                c._send_an2(3)                          # enable events is not in the read-only set
        self.assertFalse(hasattr(acp2client.Acp2Client, "set_property"))

    def test_error_reply(self):
        with acp2client.Acp2Client("127.0.0.1") as c:
            with self.assertRaises(acp2client.Acp2Error) as cm:
                c.get_object(1, 999999)
            self.assertIn("invalid obj-id", str(cm.exception))


class ModelAndChecksTests(FakeDeviceCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        acp2walk.walk("127.0.0.1", cls.tmp / "IPGT1.json", pace=0)
        cls.model = LiveModel(cls.tmp / "IPGT1.json", "IPGT1")
        (cls.tmp / "SRC.csv").write_text(SRC_CSV)
        (cls.tmp / "DST.rtf").write_text(DST_RTF)
        cls.names = stream_names([parse_list(cls.tmp / "SRC.csv"), parse_list(cls.tmp / "DST.rtf")], "IPGT1")

    def test_walk_finds_slot_and_routing(self):
        self.assertEqual(self.model.snapshot_info["slot"], 1)
        self.assertEqual(len([s for s in self.model.sources.values() if s["kind"] == "ipin"]), 8 * 16)
        self.assertEqual(len([d for d in self.model.destinations.values() if d["kind"] == "emb"]), 4 * 16)
        self.assertEqual(self.model.destinations["bank:2:1"]["block"], 1)   # banks 1-8 = 16ch delay group 1
        self.assertEqual(self.model.destinations["bank:2:1"]["ch"], 3)
        self.assertTrue(all(o.oid for g in self.model.groups.values() for o in g.objects.values()))

    def test_channels(self):
        ch = {c["letter"]: c for c in self.model.channels()}
        self.assertEqual((ch["A2"]["main_kind"], ch["A2"]["main_num"], ch["A2"]["backup_kind"]), ("SDI", 2, "Stream"))
        self.assertEqual(ch["A4"]["backup_kind"], "Pattern")

    def test_lists(self):
        self.assertEqual(self.names["device"], "IPGT1")
        self.assertEqual(self.names["rx"][2]["refs"], [2])
        self.assertEqual(normalize_device("DCR.IPG 03A"), normalize_device("IPG03A"))

    def test_case_a_a(self):
        a = analyse_channel(self.model, self.names, 1)
        self.assertEqual(a["case"], "A/A")
        self.assertFalse([c for c in a["changes"] if c["key"].startswith("emb:")])   # already IP In 1

    def test_mixed_b_a(self):
        a = analyse_channel(self.model, self.names, 2)
        self.assertEqual(a["case"], "B/A")
        main = [c for c in a["changes"] if c["key"].startswith("emb:A2") and c["field"] == "main"]
        back = [c for c in a["changes"] if c["key"].startswith("emb:A2") and c["field"] == "backup"]
        self.assertEqual(len(main), 16)                 # main -> delay group of SDI 2
        self.assertEqual(len(back), 0)                  # backup stays on its own IP In 2
        self.assertTrue(any(c["key"].startswith("ipout:2:") for c in a["changes"]))   # IP out from the bank

    def test_b_b_and_delays(self):
        a = analyse_channel(self.model, self.names, 3)
        self.assertEqual(a["case"], "B/B")
        self.assertEqual(a["sides"]["main"]["group"], 3)          # fixed port scheme: SDI 3 -> group 3
        bank_delays = sorted(c["key"] for c in a["changes"] if c["field"] == "delay")
        self.assertEqual(bank_delays, sorted(f"bankdelay:{b}" for b in range(17, 25)))   # 0.0 -> 18.0, each bank once
        emb = [c for c in a["changes"] if c["key"].startswith("emb:A3")]
        self.assertEqual(len(emb), 32)                             # main AND backup -> group 3

    def test_pattern_backup_untouched(self):
        a = analyse_channel(self.model, self.names, 4)
        self.assertEqual(a["case"], "A/-")
        self.assertFalse([c for c in a["changes"] if c["field"] == "backup" and c["key"].startswith("emb:")])
        self.assertTrue(any(c["key"] == "streamdelay:4" and c["value"] == 19.5 for c in a["changes"]))


class ServiceTests(FakeDeviceCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import live_check
        import server
        cls.server_mod = server
        live_check.NMOS_REGISTRY = None
        base = cls.tmp / "svc"
        for d in ("cbf", "exports", "lists", "live"):
            (base / d).mkdir(parents=True)
        (base / "lists" / "SRC.csv").write_text(SRC_CSV)
        (base / "lists" / "DST.csv").write_text(DST_CSV)
        example = [{"ip": "127.0.0.1", "name": "IPGT1", "tag": "TEST", "type": "BRIDGE", "poll": True, "write": False},
                   {"ip": "127.0.0.2", "name": "IPGT2", "tag": "TEST", "type": "BRIDGE", "poll": False, "write": False}]
        (base / "devices.example.json").write_text(json.dumps(example))
        for name, val in (("DEVICES_FILE", base / "devices.json"), ("DEVICES_EXAMPLE_FILE", base / "devices.example.json"),
                          ("SEEDED_FILE", base / "devices.seeded.json"), ("CBF_DIR", base / "cbf"),
                          ("EXPORT_DIR", base / "exports"), ("LISTS_DIR", base / "lists"), ("LIVE_DIR", base / "live")):
            setattr(server, name, val)
        server.merge_new_seed_devices()
        server.POLLING_PAUSED.set()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        super().tearDownClass()

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base_url + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return r.status, json.loads(raw), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers

    def test_1_state_and_gzip(self):
        st, j, h = self.call("/api/state", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(st, 200)
        self.assertEqual(h.get("Content-Encoding"), "gzip")
        self.assertEqual(h.get("Cache-Control"), "no-store")
        self.assertEqual([d["name"] for d in j["devices"]], ["IPGT1", "IPGT2"])

    def test_2_guards(self):
        self.assertEqual(self.call("/api/devices", {"ip": "not-an-ip", "name": "X"})[0], 400)
        st, j, _ = self.call("/api/read", {"device": "IPGT2"})
        self.assertEqual(st, 400)
        self.assertIn("not released", j["error"])
        st, j, _ = self.call("/api/take", {"device": "IPGT1", "changes": [{"key": "emb:A1:1", "field": "main", "value": 2}]})
        self.assertEqual(st, 403)
        self.assertIn("Nothing was sent", j["error"])

    def test_2b_access_and_write_switches(self):
        # write needs access; withdrawing access turns writing off; IPGT2 ends as it started
        self.assertEqual(self.call("/api/devices/write", {"device": "IPGT2", "write": True})[0], 400)
        self.assertEqual(self.call("/api/devices/access", {"device": "IPGT2", "poll": True})[1]["poll"], True)
        self.assertEqual(self.call("/api/devices/write", {"device": "IPGT2", "write": True})[1]["write"], True)
        st, j, _ = self.call("/api/devices/access", {"device": "IPGT2", "poll": False})
        self.assertEqual((st, j["poll"], j["write"]), (200, False, False))
        import server
        d = [x for x in json.loads(server.DEVICES_FILE.read_text()) if x["name"] == "IPGT2"][0]
        self.assertEqual((d["poll"], d["write"]), (False, False))
        self.assertEqual(self.call("/api/devices/access", {"device": "NOPE", "poll": True})[0], 400)

    def test_3_read_model_check(self):
        st, job, _ = self.call("/api/read", {"device": "IPGT1"})
        self.assertEqual(st, 200)
        for _ in range(100):
            if self.server_mod._jobs["IPGT1"]["state"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(self.server_mod._jobs["IPGT1"]["state"], "done", self.server_mod._jobs["IPGT1"])
        st, m, _ = self.call("/api/model?device=IPGT1")
        self.assertEqual(st, 200)
        self.assertEqual(m["analysis"]["2"]["case"], "B/A")
        st, r, _ = self.call("/api/check", {"device": "IPGT1", "channel": 4})
        self.assertEqual(st, 200)
        self.assertGreater(r["acp2_requests"], 0)
        self.assertIn("NMOS check skipped", r["nmos_error"])


class TakeTests(unittest.TestCase):
    """The write path, against a simulated device that accepts writes (allow_write=True)."""

    def setUp(self):
        import take
        self.take = take
        self.fake = FakeNeuron(allow_write=True)
        self._port = acp2client.AN2_PORT
        acp2client.AN2_PORT = self.fake.port
        self.tmp = Path(tempfile.mkdtemp())
        acp2walk.walk("127.0.0.1", self.tmp / "T.json", pace=0)
        self.model = LiveModel(self.tmp / "T.json", "IPGT1")
        self.undo, self.log = self.tmp / "T.undo.json", self.tmp / "T.takes.jsonl"

    def tearDown(self):
        acp2client.AN2_PORT = self._port
        self.fake.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dev_value(self, key, field="main"):
        oid = self.model.target({"key": key, "field": field}).oid
        return self.fake.slots[1].objs[oid]["value"]

    def do_take(self, changes):
        return self.take.take(self.model, "127.0.0.1", changes, self.undo, self.log, 1)

    def test_take_and_undo_roundtrip(self):
        r = self.do_take([{"key": "emb:A1:1", "field": "main", "value": 2},
                          {"key": "bankdelay:1", "field": "delay", "value": 18.0}])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["written"], 2)
        self.assertEqual(self.dev_value("emb:A1:1"), 2)
        self.assertAlmostEqual(self.dev_value("bankdelay:1", "delay"), 18.0, places=3)
        self.assertTrue(self.undo.exists())
        self.assertEqual(self.model.destinations["emb:A1:1"]["main"], 2)   # model = new Active
        u = self.take.undo(self.model, "127.0.0.1", self.undo, self.log, 1)
        self.assertTrue(u["ok"], u)
        self.assertEqual(self.dev_value("emb:A1:1"), 1)
        self.assertAlmostEqual(self.dev_value("bankdelay:1", "delay"), 16.8, places=3)
        self.assertFalse(self.undo.exists())
        self.assertEqual(len(self.log.read_text().splitlines()), 2)

    def test_validation_writes_nothing(self):
        bad = [[{"key": "emb:A1:1", "field": "main", "value": 999999}],          # unknown source
               [{"key": "bankdelay:1", "field": "delay", "value": 5000}],        # above MAX 3000
               [{"key": "nope:1:1", "field": "main", "value": 2}],               # unknown target
               [{"key": "emb:A1:1", "field": "main", "value": 2}] * 2,           # same object twice
               []]
        for changes in bad:
            with self.assertRaises(self.take.TakeError):
                self.do_take(changes)
        self.assertEqual(self.fake.set_attempts, [])
        self.assertFalse(self.undo.exists())

    def test_stale_active_is_refused(self):
        oid = self.model.target({"key": "emb:A1:1", "field": "main"}).oid
        self.fake.slots[1].objs[oid]["value"] = 5          # someone changed the device after the read
        with self.assertRaises(self.take.TakeError) as cm:
            self.do_take([{"key": "emb:A1:1", "field": "main", "value": 2}])
        self.assertIn("Nothing was written", str(cm.exception))
        self.assertEqual(self.fake.set_attempts, [])

    def test_stream_delay_without_media_warns(self):
        r = self.do_take([{"key": "streamdelay:7", "field": "delay", "value": 19.0},
                          {"key": "streamdelay:1", "field": "delay", "value": 19.0}])
        self.assertTrue(r["ok"], r)
        by = {e["key"]: e for e in r["items"]}
        self.assertIn("NOT effective", by["streamdelay:7"].get("warning", ""))   # no media: stored, cannot act
        self.assertNotIn("warning", by["streamdelay:1"])                          # receiving: fine
        self.assertEqual(self.model.sources[(7 - 1) * 16 + 1]["stream_info"], "No media found")

    def test_writer_refuses_other_types(self):
        import writer
        with writer.Acp2Writer("127.0.0.1") as w:
            with self.assertRaises(acp2client.Acp2Error):
                w.set_value(1, 1, 1, 2)                     # s32 not allowed
        self.assertEqual(self.fake.set_attempts, [])


class PasteTests(unittest.TestCase):
    """Route master pasted out of Cerebrum, with and without the header row."""
    SRC = ("_ID\t_Mnemonic\t_Alternate Mnemonic 1\t_TL Inhibit\t_Level ID 1\t_Level ID 2\t_Level ID 3\n"
           "2564\tDCR.IPG03A-TX04\tCC1\t0\tNMOS2|95|IPG03A|VTX-04\tNMOS2|95|IPG03A|ATX-13\tNMOS2|95|IPG03A|ATX-14\n"
           "2565\tDCR.OTHER\tfoo\t0\t\t\t\n")

    def _parse(self, text, kind=None):
        tsv, info = normalize_paste(text, kind)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(tsv)
        try:
            return parse_list(f.name), info
        finally:
            Path(f.name).unlink()

    def test_with_and_without_header_agree(self):
        a, ia = self._parse(self.SRC)
        b, ib = self._parse(self.SRC.split("\n", 1)[1], "SRC")
        self.assertTrue(ia["with_header"])
        self.assertFalse(ib["with_header"])
        self.assertEqual(ib["skipped"], 1)
        for p in (a, b):
            self.assertEqual(p["kind"], "SRC")
            dev = p["devices"]["IPG3A"]
            self.assertEqual(dev["TX"][4]["refs"], [13, 14])
            self.assertEqual(dev["TX"][4]["alt"], "CC1")

    def test_rubbish_is_refused(self):
        with self.assertRaises(ValueError):
            normalize_paste("hello\tworld\n")
        with self.assertRaises(ValueError):
            normalize_paste("   ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
