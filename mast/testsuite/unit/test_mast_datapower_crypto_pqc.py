# This file is part of McIndi's Automated Solutions Tool (MAST).
#
# MAST is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3
# as published by the Free Software Foundation.
#
# MAST is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MAST.  If not, see <https://www.gnu.org/licenses/>.
#
# Copyright 2015-2026, McIndi Solutions, All rights reserved.
"""
Unit tests for `mast.datapower.crypto.pqc`: `effective_groups` against
every raw result of the container tests of 2026-11-21
(`mast/testsuite/data/pqc-research/`), inbound (DataPower as the
server) and outbound (DataPower as the client).
"""
import glob
import os
import re
import unittest

from mast.datapower.crypto import pqc

DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data",
                    "pqc-research")
_KEM = re.compile(r"<KEMAlgs>([^<]*)</KEMAlgs>")
_PROTOCOL = re.compile(r"<(TLSv1d\d)>(on|off)</")

# The offers of the inbound probe (probe.py): (label, what it needs)
_INBOUND = {
    "X25519MLKEM768 only": "X25519MLKEM768",
    "SecP256r1MLKEM768 only": "SecP256r1MLKEM768",
    "SecP384r1MLKEM1024 only": "SecP384r1MLKEM1024",
    "MLKEM768 only": "MLKEM768",
    "X25519 only (TLS1.3)": pqc.CLASSICAL,
    "P-256 only (TLS1.3)": pqc.CLASSICAL,
    "hybrid first, then X25519": "either",
    "X25519MLKEM768 on TLS1.2": "tls12",
}


def inbound_cases(path):
    """`(firmware, kem_algs, protocols, label, ok, group)` of each probe
    of a configured profile in a result file. The built-in web-mgmt TLS
    at the top is left out: it is not a profile."""
    firmware, protocols, kem, cases = None, None, None, []
    with open(path) as fin:
        for line in fin:
            line = line.rstrip("\n")
            if line.startswith("##### DataPower "):
                firmware = "IDG." + line.split()[-1]
            elif line.startswith("########## protocols:"):
                protocols = dict(_PROTOCOL.findall(line))
            elif line.startswith("=== KEMAlgs:"):
                kem = None
            elif line.startswith("stored:"):
                kem = _KEM.findall(line)
            elif protocols is not None and kem is not None:
                for label in _INBOUND:
                    if line.startswith(label + " "):
                        rest = line[len(label):].split()
                        group = next((p[6:] for p in rest
                                      if p.startswith("group=")), None)
                        cases.append((firmware, kem, protocols, label,
                                      rest[0] == "OK", group))
    return cases


def outbound_cases(path):
    """`(firmware, kem_algs, protocols, server, ok, group, down)`."""
    firmware, cases, current = None, [], None
    head = re.compile(r"=== server groups \[(\w+)\], client protocols "
                      r"\[([\d.+]+)\], client KEMAlgs")
    with open(path) as fin:
        for line in fin:
            line = line.strip()
            if line.startswith("##### outbound DataPower "):
                firmware = "IDG." + line.split()[3]
            found = head.match(line)
            if found:
                versions = found.group(2).split("+")
                current = {"server": found.group(1), "protocols": {
                    "TLSv1d2": "on" if "1.2" in versions else "off",
                    "TLSv1d3": "on" if "1.3" in versions else "off"}}
            elif current is not None and line.startswith("stored:"):
                current["kem"] = _KEM.findall(line)
            elif current is not None and line.startswith("status:"):
                current["down"] = "<OpState>down" in line
            elif current is not None and line.startswith("server:"):
                rest = line.split()[1:]
                group = next((p[6:] for p in rest if p.startswith("group=")),
                             None)
                cases.append((firmware, current.get("kem", []),
                              current["protocols"], current["server"],
                              bool(rest) and rest[0] == "OK", group,
                              current.get("down", False)))
                current = None
    return cases


def _expected_inbound(groups, need):
    if need == "tls12":
        return groups.tls12
    if need == "either":
        return "X25519MLKEM768" in groups.tls13 or \
            pqc.CLASSICAL in groups.tls13
    return need in groups.tls13


class TestEffectiveGroupsAgainstTheContainers(unittest.TestCase):

    def test_there_are_results_for_every_tested_level(self):
        found = sorted(os.path.basename(p) for p in
                       glob.glob(os.path.join(DATA, "result-*.txt")))
        self.assertEqual(11, len(found), found)
        self.assertEqual(11, len(glob.glob(os.path.join(DATA,
                                                        "outbound-*.txt"))))

    def test_inbound(self):
        total = 0
        for path in sorted(glob.glob(os.path.join(DATA, "result-*.txt"))):
            cases = inbound_cases(path)
            self.assertGreater(len(cases), 100, path)
            for firmware, kem, protocols, label, ok, group in cases:
                groups = pqc.effective_groups(firmware, kem, protocols)
                with self.subTest(firmware=firmware, kem=kem,
                                  protocols=protocols, offer=label):
                    self.assertEqual(
                        ok, _expected_inbound(groups, _INBOUND[label]),
                        groups)
                    if ok and label == "hybrid first, then X25519":
                        self.assertEqual(
                            "X25519MLKEM768" if "X25519MLKEM768"
                            in groups.tls13 else "x25519", group)
                total += 1
        self.assertGreater(total, 1500)

    def test_outbound(self):
        total = 0
        for path in sorted(glob.glob(os.path.join(DATA, "outbound-*.txt"))):
            cases = outbound_cases(path)
            self.assertGreater(len(cases), 20, path)
            for firmware, kem, protocols, server, ok, group, down in cases:
                groups = pqc.effective_groups(firmware, kem, protocols)
                with self.subTest(firmware=firmware, kem=kem,
                                  protocols=protocols, server=server):
                    self.assertEqual(down, groups.down)
                    # The servers speak TLS 1.3 only
                    if server == "X25519MLKEM768":
                        expected = "X25519MLKEM768" in groups.tls13
                    elif server == "X25519":
                        expected = pqc.CLASSICAL in groups.tls13
                    else:
                        expected = bool(groups.tls13 & {
                            "X25519MLKEM768", pqc.CLASSICAL})
                    self.assertEqual(expected, ok, groups)
                    if ok:
                        self.assertEqual(
                            "X25519MLKEM768" in groups.tls13 and
                            server != "X25519",
                            group == "X25519MLKEM768", group)
                total += 1
        self.assertGreater(total, 250)


class TestEffectiveGroups(unittest.TestCase):
    BOTH = {"TLSv1d2": "on", "TLSv1d3": "on"}
    TLS13 = {"TLSv1d2": "off", "TLSv1d3": "on"}

    def test_the_default_by_firmware(self):
        for firmware, hybrid in (("IDG.10.5.0.23", ()),
                                 ("IDG.10.6.0.10", ()),
                                 ("IDG.10.6.0.11", ("X25519MLKEM768",)),
                                 ("IDG.10.6.2.0", ()),
                                 ("IDG.10.6.4.0", ("X25519MLKEM768",)),
                                 ("IDG.11.0.0.3", ("X25519MLKEM768",)),
                                 ("IDG.2018.4.1.26", ())):
            with self.subTest(firmware=firmware):
                groups = pqc.effective_groups(firmware, [], self.BOTH)
                self.assertEqual(hybrid, groups.hybrid)
                self.assertTrue(groups.classical)
                self.assertFalse(groups.down or groups.configured)

    def test_kem_algs_and_tls12_take_it_down_until_11_0_0_2(self):
        for firmware, down in (("IDG.10.6.2.0", True), ("IDG.10.6.6.0", True),
                               ("IDG.11.0.0.1", True), ("IDG.11.0.0.2", False),
                               ("IDG.11.0.0.3", False)):
            with self.subTest(firmware=firmware):
                groups = pqc.effective_groups(
                    firmware, ["X25519MLKEM768", "classical"], self.BOTH)
                self.assertEqual(down, groups.down)
                self.assertEqual(not down, groups.tls12)

    def test_tls13_off(self):
        groups = pqc.effective_groups("IDG.11.0.0.3", [],
                                      {"TLSv1d2": "on", "TLSv1d3": "off"})
        self.assertEqual(frozenset(), groups.tls13)
        self.assertTrue(groups.tls12)
        self.assertEqual("TLS 1.3 is off", groups.reason)

    def test_pqc_only_and_the_expanded_classical(self):
        groups = pqc.effective_groups("IDG.11.0.0.3", ["X25519MLKEM768"],
                                      self.TLS13)
        self.assertFalse(groups.classical)
        # TLS 1.2 on too: refused all the same (seen on 11.0.0.2, 11.0.0.3)
        groups = pqc.effective_groups("IDG.11.0.0.3", ["X25519MLKEM768"],
                                      self.BOTH)
        self.assertFalse(groups.tls12 or groups.down)
        expanded = ["X25519MLKEM768", "brainpoolP512", "ffdhe2048", "x25519",
                    "p256", "secp384r1"]
        self.assertEqual(("X25519MLKEM768", "classical"),
                         pqc.normalize(expanded))
        self.assertTrue(pqc.effective_groups("IDG.11.0.0.3", expanded,
                                             self.TLS13).classical)

    def test_kem_algs_on_firmware_without_control_is_ignored(self):
        groups = pqc.effective_groups("IDG.10.6.0.11", ["classical"],
                                      self.BOTH)
        self.assertEqual(("X25519MLKEM768",), groups.hybrid)
        self.assertFalse(groups.configured)

    def test_preview_names(self):
        self.assertTrue(pqc.is_preview("x448_mlkem768"))
        self.assertTrue(pqc.is_preview("mlkem768"))
        self.assertFalse(pqc.is_preview("MLKEM768"))
        self.assertFalse(pqc.is_preview("X25519MLKEM768"))

    def test_firmware_support(self):
        self.assertEqual(
            ["none", "none", "default-only", "none", "preview", "preview",
             "control", "control", "unknown"],
            [pqc.firmware_support(f) for f in (
                "IDG.10.5.0.23", "IDG.10.6.0.10", "IDG.10.6.0.11",
                "IDG.10.6.1.0", "IDG.10.6.2.0", "IDG.10.6.3.0",
                "IDG.10.6.4.0", "IDG.11.0.0.3", "")])

    def test_kem_algs_of(self):
        self.assertEqual((), pqc.kem_algs_of({}))
        self.assertEqual(("X25519MLKEM768",),
                         pqc.kem_algs_of({"KEMAlgs": "X25519MLKEM768"}))
        self.assertEqual(("X25519MLKEM768", "classical"), pqc.kem_algs_of(
            {"KEMAlgs": ["X25519MLKEM768", "brainpoolP512", "x25519"]}))


# The rules of the pqc and cnsa packs
# ===================================

from datetime import datetime, timedelta, timezone  # noqa: E402
from unittest import mock  # noqa: E402

from mast import registry  # noqa: E402
from mast.datapower.crypto import inventory, posture  # noqa: E402
from mast.datapower.crypto import probe as probe_module  # noqa: E402
from mast.datapower.crypto.rules import Settings  # noqa: E402
from mast.testsuite.unit.test_mast_datapower_crypto_posture import (  # noqa: E402
    D, T0, PostureCase, certificate, obj, snapshot)

BOTH = {"TLSv1d0": "off", "TLSv1d1": "off", "TLSv1d2": "on", "TLSv1d3": "on"}
TLS13 = dict(BOTH, TLSv1d2="off")


def profile(name, kem=None, protocols=None, cls="SSLServerProfile",
            op_state=None, error=None):
    attrs = {"Protocols": dict(protocols or BOTH)}
    if kem is not None:
        attrs["KEMAlgs"] = kem
    key, item = obj(cls, name, attrs)
    item["op_state"], item["status_error"] = op_state, error
    return key, item


def on(firmware, *objects, certs=None, edges=(), listen=None):
    found = snapshot(objects, certs, edges=edges)
    found["firmware"] = firmware
    for key, value in (listen or {}).items():
        found["objects"][key]["listen"] = value
    return found


class TestPqcRules(PostureCase):
    pqc = True

    def rule(self, collected, rule, now=T0):
        return self.findings(collected, rule=rule, now=now)

    def test_no_hybrid_by_firmware_and_kem(self):
        found = self.rule(on("IDG.10.6.4.0", profile("default"),
                             profile("classical", ["classical"], TLS13),
                             profile("hybrid", ["X25519MLKEM768", "classical"],
                                     TLS13)), "pqc-no-hybrid")
        self.assertEqual([f[2] for f in found],
                         [D + "/SSLServerProfile/classical"])
        # The default of 10.6.2 and 10.5 is classical
        for firmware in ("IDG.10.6.2.0", "IDG.10.5.0.23"):
            with self.subTest(firmware=firmware):
                found = self.rule(on(firmware, profile("default")),
                                  "pqc-no-hybrid")
                self.assertEqual([f[1] for f in found], ["high"])
        # 10.6.0.11 negotiates the hybrid by default
        self.assertEqual(self.rule(on("IDG.10.6.0.11", profile("default")),
                                   "pqc-no-hybrid"), [])

    def test_tls13_off(self):
        off = profile("off", protocols=dict(BOTH, TLSv1d3="off"))
        self.assertEqual(
            [f[2] for f in self.rule(on("IDG.11.0.0.3", off), "pqc-tls13-off")],
            [D + "/SSLServerProfile/off"])
        # It is not also a no-hybrid finding
        self.assertEqual(self.rule(on("IDG.11.0.0.3", off), "pqc-no-hybrid"),
                         [])

    def test_pqc_only(self):
        found = self.rule(on("IDG.11.0.0.3",
                             profile("only", ["X25519MLKEM768"]),
                             profile("both", ["X25519MLKEM768", "x25519",
                                              "brainpoolP512"], TLS13)),
                          "pqc-only")
        self.assertEqual([f[2] for f in found], [D + "/SSLServerProfile/only"])

    def test_profile_down_from_the_status_or_predicted(self):
        down = profile("down", ["X25519MLKEM768"], op_state="down",
                       error="KEM algorithm selection is only allowed in "
                             "TLSv1.3")
        predicted = profile("predicted", ["classical"])
        other = profile("other", op_state="down", error="no identification "
                        "credential", cls="SSLClientProfile")
        fine = profile("fine", ["classical"], op_state="up")
        found = self.rule(on("IDG.10.6.4.0", down, predicted, other, fine),
                          "pqc-profile-down")
        self.assertEqual(sorted(f[2] for f in found), [
            D + "/SSLClientProfile/other", D + "/SSLServerProfile/down",
            D + "/SSLServerProfile/predicted"])
        # 11.0.0.2 allows KEMAlgs with TLS 1.2
        self.assertEqual(self.rule(on("IDG.11.0.0.2", predicted),
                                   "pqc-profile-down"), [])

    def test_preview_names_pure_mlkem_and_firmware(self):
        found = self.rule(on("IDG.10.6.2.0",
                             profile("preview", ["x448_mlkem768"], TLS13)),
                          "pqc-preview-names")
        self.assertEqual(len(found), 1)
        found = self.rule(on("IDG.11.0.0.3", profile("pure", ["MLKEM768"],
                                                     TLS13)),
                          "pqc-pure-mlkem")
        self.assertEqual([f[1] for f in found], ["info"])
        for firmware, severity in (("IDG.10.5.0.23", "medium"),
                                   ("IDG.10.6.0.11", "low"),
                                   ("IDG.10.6.3.0", "low"),
                                   ("IDG.10.6.4.0", None),
                                   ("IDG.2018.4.1.26", "medium")):
            with self.subTest(firmware=firmware):
                found = self.rule(on(firmware), "pqc-firmware")
                self.assertEqual([f[1] for f in found],
                                 [severity] if severity else [])
                if severity:
                    self.assertEqual(found[0][2], "dp1")

    def test_classical_certificates_by_nist_date(self):
        rsa2048, c1 = certificate("rsa2048", days=3000)
        rsa4096, c2 = certificate("rsa4096", key_bits=4096, days=4000,
                                  sha="b")
        short, c3 = certificate("short", days=100, sha="c")
        items = dict([c1, c2, c3])
        found = self.rule(on("IDG.11.0.0.3", rsa2048, rsa4096, short,
                             certs=items), "pqc-classical-certificate")
        self.assertEqual({f[2].rsplit("/", 1)[1]: f[1] for f in found},
                         {"rsa2048": "info", "rsa4096": "info",
                          "short": "info"})
        # In 2029 the RSA 2048 one (deprecated after 2030) is medium, in
        # 2031 high; the 4096 one waits for 2035
        for year, severities in ((2029, ("medium", "info")),
                                 (2031, ("high", "info")),
                                 (2032, ("high", "low")),
                                 (2036, ("high", "high"))):
            with self.subTest(year=year):
                when = T0.replace(year=year)
                snapshot_id = inventory.save(
                    registry.by_name("dp1").id,
                    on("IDG.11.0.0.3", rsa2048, rsa4096, short, certs={
                        key: dict(value, not_after=(when + timedelta(
                            days=3000)).isoformat())
                        for key, value in items.items()}), when)
                posture.evaluate(snapshot_id, now=when)
                got = {f["name"]: f["severity"] for f in
                       posture.findings_of([snapshot_id], now=when)
                       if f["rule_id"] == "pqc-classical-certificate"}
                self.assertEqual((got["rsa2048"], got["rsa4096"]), severities)

    def test_legacy_proxy_profile(self):
        # The firmware's own (seen on 10.6.4) is left out
        system = obj("SSLProxyProfile", "system-wsgw-management-loopback",
                     {"Direction": "forward"}, domain="default")
        found = self.rule(on("IDG.11.0.0.3", obj("SSLProxyProfile", "legacy",
                                                 {"Direction": "reverse"}),
                             system), "pqc-legacy-profile")
        self.assertEqual([f[2] for f in found],
                         [D + "/SSLProxyProfile/legacy"])

    def test_probe_mismatch(self):
        handler = obj("HTTPSSourceProtocolHandler", "https")
        handler[1]["attrs"] = None
        edges = {(D, "HTTPSSourceProtocolHandler", "https",
                  "SSLServerProfile", "server", "SSLServerProfile")}
        collected = on("IDG.11.0.0.3", profile("server", ["classical"],
                                               TLS13), handler, edges=edges,
                       listen={(D, "HTTPSSourceProtocolHandler", "https"):
                               "0.0.0.0:8443"})
        snapshot_id = self.keep(collected)
        appliance = registry.by_name("dp1")
        offers = {name: False for name, _, _ in probe_module.OFFERS}
        offers.update({"TLSv1.3": True, "classical": True,
                       "X25519MLKEM768": True})
        probe_module.save(appliance.id, snapshot_id, "10.0.0.1", 8443, {
            "ok": True, "offers": offers, "target": {
                "kind": "listener", "domain": D,
                "class": "HTTPSSourceProtocolHandler", "name": "https"}},
            T0 + timedelta(hours=1))
        posture.evaluate(snapshot_id, now=T0)
        found = [f for f in posture.findings_of([snapshot_id], now=T0)
                 if f["rule_id"] == "pqc-probe-mismatch"]
        self.assertEqual(len(found), 1)
        self.assertIn("X25519MLKEM768 accepted (the configuration says "
                      "refused)", found[0]["message"])
        # A probe that is too old is not compared
        values = dict(Settings().values)
        values[("pqc", "probe_max_age_days")] = 0
        with mock.patch.object(posture, "settings",
                               return_value=Settings(values)):
            later = self.keep(collected, when=T0 + timedelta(days=2))
            posture.evaluate(later, now=T0)
        self.assertFalse([f for f in posture.findings_of([later], now=T0)
                          if f["rule_id"] == "pqc-probe-mismatch"])

    def test_the_cnsa_pack_is_off_by_default(self):
        collected = on("IDG.11.0.0.3", profile("server", ["X25519MLKEM768"],
                                               TLS13))
        self.assertEqual(self.rule(collected, "cnsa-key-exchange"), [])
        with mock.patch.object(posture, "settings", return_value=Settings(
                disabled_packs=frozenset())):
            found = self.rule(collected, "cnsa-key-exchange")
            self.assertEqual([f[1] for f in found], ["medium"])
            ok = on("IDG.11.0.0.3", profile("server", ["SecP384r1MLKEM1024"],
                                            TLS13))
            self.assertEqual(self.rule(ok, "cnsa-key-exchange"), [])
        # And the pqc pack can be turned off
        with mock.patch.object(posture, "settings", return_value=Settings(
                disabled_packs=frozenset(("pqc", "cnsa")))):
            self.assertEqual(self.rule(on("IDG.10.5.0.23", profile("x")),
                                       "pqc-no-hybrid"), [])


class TestTheCollectorReadsStatusAndPorts(unittest.TestCase):

    STATUS = (b'<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/'
              b'envelope/"><env:Body><dp:response xmlns:dp="http://www.'
              b'datapower.com/schemas/management"><dp:status>'
              b'<ObjectStatus><Class>SSLServerProfile</Class><OpState>down'
              b'</OpState><AdminState>enabled</AdminState><Name>p1</Name>'
              b'<EventCode>0x00</EventCode><ErrorCode>KEM algorithm selection'
              b' is only allowed in TLSv1.3</ErrorCode></ObjectStatus>'
              b'<ObjectStatus><Class>CryptoKey</Class><OpState>up</OpState>'
              b'<Name>k1</Name><ErrorCode/></ObjectStatus>'
              b'</dp:status></dp:response></env:Body></env:Envelope>')

    def test_status_of(self):
        self.assertEqual(inventory.status_of(self.STATUS), {
            ("SSLServerProfile", "p1"): (
                "down", "KEM algorithm selection is only allowed in TLSv1.3"),
            ("CryptoKey", "k1"): ("up", None)})

    def test_listen_of(self):
        config = (b'<config><HTTPSSourceProtocolHandler name="h"><LocalAddress>'
                  b'0.0.0.0</LocalAddress><LocalPort>8443</LocalPort>'
                  b'</HTTPSSourceProtocolHandler><WebGUI name="WebGUI-Settings">'
                  b'<LocalAddress>10.1.1.1</LocalAddress><LocalPort>9090'
                  b'</LocalPort></WebGUI><CryptoKey name="k"/></config>')
        self.assertEqual(inventory.listen_of(config), {
            ("HTTPSSourceProtocolHandler", "h"): "0.0.0.0:8443",
            ("WebGUI", "WebGUI-Settings"): "10.1.1.1:9090"})


# The TLS probe
# =============

import socket  # noqa: E402
import ssl  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402

from mast.testsuite.unit.test_mast_jobs import (  # noqa: E402
    PostgresMixin, SqliteCase)
from mast.testsuite.unit.test_mast_datapower_tls import Certificates  # noqa: E402

GROUPS = hasattr(ssl.SSLContext, "set_groups")


class HandshakeServer(object):
    """A TLS server on 127.0.0.1 that does the handshake and closes:
    TLS 1.2 and 1.3, and only the `groups` if this Python can choose."""

    def __init__(self, cert_file, groups=None, minimum=ssl.TLSVersion.TLSv1_2):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert_file)
        self.context.minimum_version = minimum
        if groups and GROUPS:
            self.context.set_groups(groups)
        self.socket = socket.create_server(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                raw, _ = self.socket.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(raw,),
                             daemon=True).start()

    def _serve(self, raw):
        try:
            with self.context.wrap_socket(raw, server_side=True):
                pass
        except (OSError, ssl.SSLError):
            pass
        finally:
            raw.close()

    def close(self):
        self.socket.close()


class TestProbe(SqliteCase):
    # The tests that keep results run on Postgres too (TestProbePostgres)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.directory = tempfile.mkdtemp()
        cls.certificates = Certificates(cls.directory)

    def server(self, groups=None):
        found = HandshakeServer(self.certificates.files["self"], groups)
        self.addCleanup(found.close)
        return found

    def test_a_classical_server(self):
        server = self.server("X25519:P-256")
        result = probe_module.probe_port("127.0.0.1", server.port, timeout=5)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["protocol"], "TLSv1.3")
        self.assertEqual(result["chain"][0]["sha256"],
                         self.certificates.sha256("self").replace(":", "")
                         .lower())
        self.assertEqual(result["chain"][0]["key_alg"], "EC")
        offers = result["offers"]
        self.assertEqual((offers["TLSv1.0"], offers["TLSv1.1"],
                          offers["TLSv1.2"], offers["TLSv1.3"]),
                         (False, False, True, True))
        if GROUPS:
            self.assertEqual(offers["classical"], True)
            self.assertEqual(offers["X25519MLKEM768"], False)
            self.assertEqual(result["group"], "x25519")
        else:
            self.assertIsNone(offers["X25519MLKEM768"])
            self.assertEqual(result["notes"]["X25519MLKEM768"],
                             probe_module.NO_GROUPS)

    @unittest.skipUnless(GROUPS, "needs Python 3.15")
    def test_a_hybrid_server(self):
        server = self.server("X25519MLKEM768:X25519")
        result = probe_module.probe_port("127.0.0.1", server.port, timeout=5)
        self.assertEqual(result["group"], "X25519MLKEM768")
        self.assertTrue(result["offers"]["X25519MLKEM768"])
        self.assertTrue(result["offers"]["classical"])
        self.assertFalse(result["offers"]["SecP384r1MLKEM1024"])

    def test_without_set_groups(self):
        server = self.server()
        def no_groups(version=None, groups=None):
            # As on Python 3.13 and 3.14
            if groups is not None:
                raise LookupError(probe_module.NO_GROUPS)
            return _plain(version)
        with mock.patch.object(probe_module, "_context", no_groups):
            result = probe_module.probe_port("127.0.0.1", server.port, 5)
        self.assertTrue(result["ok"])
        self.assertTrue(result["offers"]["TLSv1.3"])
        for name in pqc.HYBRID + ("classical",):
            self.assertIsNone(result["offers"][name])
            self.assertEqual(result["notes"][name], probe_module.NO_GROUPS)

    def test_nothing_listens(self):
        free = socket.create_server(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        result = probe_module.probe_port("127.0.0.1", port, timeout=2)
        self.assertFalse(result["ok"])
        self.assertIn("ConnectionRefused", result["error"])
        self.assertEqual(result["offers"], {})

    def test_the_targets_of_an_appliance(self):
        registry.add("dp1", "10.0.0.1", soma_port=5550)
        handler = obj("HTTPSSourceProtocolHandler", "https")
        handler[1]["attrs"] = None
        plain = obj("HTTPSourceProtocolHandler", "plain")
        plain[1]["attrs"] = None
        web = obj("WebGUI", "WebGUI-Settings", domain="default")
        web[1]["attrs"] = None
        own = obj("HTTPSSourceProtocolHandler", "own")
        own[1]["attrs"] = None
        edges = {(D, "HTTPSSourceProtocolHandler", "https",
                  "SSLServerProfile", "server", "SSLServerProfile"),
                 (D, "HTTPSSourceProtocolHandler", "own",
                  "SSLServerProfile", "server", "SSLServerProfile")}
        collected = on("IDG.11.0.0.3", profile("server"), handler, plain, web,
                       own, edges=edges, listen={
                           (D, "HTTPSSourceProtocolHandler", "https"):
                               "0.0.0.0:8443",
                           (D, "HTTPSourceProtocolHandler", "plain"):
                               "0.0.0.0:8080",
                           (D, "HTTPSSourceProtocolHandler", "own"):
                               "10.9.9.9:443",
                           ("default", "WebGUI", "WebGUI-Settings"):
                               "0.0.0.0:9090"})
        snapshot_id = inventory.save(registry.by_name("dp1").id, collected, T0)
        targets, found_id = probe_module.targets_of(registry.by_name("dp1"))
        self.assertEqual(found_id, snapshot_id)
        self.assertEqual(sorted((h, p, t.get("name")) for h, p, t in targets), [
            ("10.0.0.1", 5550, "XML management"),
            ("10.0.0.1", 8443, "https"),
            ("10.0.0.1", 9090, "WebGUI-Settings"),
            ("10.9.9.9", 443, "own")])
        # Behind a port mapping, the WebGUI is probed on the port of the
        # registry, and is still the WebGUI object
        registry.update("dp1", web_port=19090)
        targets, _ = probe_module.targets_of(registry.by_name("dp1"))
        self.assertIn(("10.0.0.1", 19090, "WebGUI-Settings"),
                      [(h, p, t.get("name")) for h, p, t in targets])
        self.assertNotIn(9090, [p for _, p, _ in targets])

    def test_the_command_keeps_the_results(self):
        server = self.server()
        registry.add("dp1", "127.0.0.1", soma_port=server.port, web_port=1)
        result = probe_module.probe(appliances=["dp1"],
                                    targets=["127.0.0.1:{}".format(server.port)],
                                    timeout=3)
        rows = result.rows
        self.assertEqual(len(rows), 3)
        latest = probe_module.latest_results()
        self.assertEqual(len(latest), 3)
        by_target = {(r["appliance_id"], r["port"]): r["result"] for r in latest}
        dp1 = registry.by_name("dp1").id
        self.assertTrue(by_target[(dp1, server.port)]["ok"])
        self.assertEqual(by_target[(dp1, server.port)]["target"],
                         {"kind": "management", "name": "XML management"})
        self.assertFalse(by_target[(dp1, 1)]["ok"])
        self.assertEqual(by_target[(None, server.port)]["target"]["kind"],
                         "extra")
        # Probing again keeps both, and the newest is the one shown
        probe_module.probe(targets=["127.0.0.1:{}".format(server.port)],
                           timeout=3)
        self.assertEqual(len(probe_module.latest_results()), 3)
        # A port that the newest run of an appliance did not probe is
        # gone from what is shown
        registry.update("dp1", web_port=server.port)
        probe_module.probe(appliances=["dp1"], timeout=3)
        self.assertEqual([(r["appliance_id"], r["port"]) for r in
                          probe_module.latest_results()],
                         [(dp1, server.port), (None, server.port)])

    def test_a_target_must_be_host_and_port(self):
        from pydantic import ValidationError
        for bad in ("nohost", "host:", "host:99999", ":443"):
            with self.subTest(target=bad), self.assertRaises(ValidationError):
                probe_module.Probe(targets=[bad])
        self.assertEqual(probe_module._target("[::1]:443"), ("::1", 443))


class TestProbePostgres(PostgresMixin, TestProbe):
    pass


class TestPqcRulesPostgres(PostgresMixin, TestPqcRules):
    pass


def _plain(version):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    if version is not None:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            context.minimum_version = context.maximum_version = version
        if version < ssl.TLSVersion.TLSv1_2:
            context.set_ciphers("ALL:@SECLEVEL=0")
    return context


class TestMigrationAndSettings(PostureCase):
    pqc = True

    def test_down_to_0010_and_up(self):
        from sqlalchemy import create_engine, inspect

        from mast.jobs import db
        collected = on("IDG.10.6.4.0", profile("p", op_state="down",
                                               error="broken"))
        snapshot_id = self.keep(collected)
        probe_module.save(registry.by_name("dp1").id, snapshot_id, "h", 1,
                          {"ok": False}, T0)
        # The profile is down (pqc-profile-down)
        self.assertEqual(posture.evaluate(snapshot_id), 1)
        db.downgrade("0010")
        self.assertEqual(db.revision(), "0010")
        # Not db.engine(): it would upgrade the tables again
        plain = create_engine(self.url)
        self.addCleanup(plain.dispose)
        self.assertNotIn("crypto_probe_results",
                         inspect(plain).get_table_names())
        columns = {c["name"] for c in inspect(plain).get_columns(
            "crypto_objects")}
        self.assertFalse(columns & {"op_state", "status_error", "listen"})
        db.configure(self.url)
        # The first use upgrades the tables again
        self.assertEqual([a.name for a in registry.list_appliances()],
                         ["dp1", "dp2"])
        self.assertEqual(db.revision(), db.HEAD)
        # The snapshot stayed, without the state it had: nothing says the
        # profile is down any more
        self.assertEqual(posture.evaluate(snapshot_id), 0)
        self.assertEqual(probe_module.latest_results(), [])

    def test_the_shipped_crypto_conf(self):
        from mast.datapower.crypto import rules
        current = rules.settings()
        self.assertEqual(current.disabled_packs, frozenset(("cnsa",)))
        self.assertEqual(current.get("pqc", "deprecated_year"), 2030)
        self.assertEqual(current.get("cnsa", "exclusive_year"), 2033)
