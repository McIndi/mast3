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
Unit tests for the posture rules of the crypto inventory
(`mast.datapower.crypto.posture` and `mast.datapower.crypto.rules`).
Each rule has a test on a small snapshot made for it.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import select

from mast import jobs, registry
from mast.credentials import store as credentials
from mast.datapower.crypto import inventory, posture, rules
from mast.datapower.crypto.models import (
    DETAILS, CryptoException, CryptoFinding, CryptoSnapshot)
from mast.datapower.crypto.rules import Settings
from mast.jobs import db
from mast.jobs.auth import Forbidden, Principal
from mast.testsuite.unit.test_mast_credentials import ADMIN
from mast.testsuite.unit.test_mast_jobs import PostgresMixin, SqliteCase
from mast.testsuite.unit.web_helpers import WebCase

T0 = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)
D = "app1"
OPERATOR = Principal("otto", "operator")


def obj(cls, name, attrs=None, state="enabled", plaintext=False,
        running=True, saved=True, domain=D):
    return (domain, cls, name), {
        "admin_state": state, "attrs": {} if attrs is None else attrs,
        "has_plaintext_password": plaintext, "running": running,
        "saved": saved}


def cert(days=400, key_alg="RSA", key_bits=2048,
         sig_alg="sha256WithRSAEncryption", self_signed=False, subject=None,
         sha="a"):
    return {"sha256": sha * 64, "subject": subject or "CN=test",
            "issuer": "CN=test" if self_signed else "CN=CA",
            "serial": "1", "sig_alg": sig_alg, "key_alg": key_alg,
            "key_bits": key_bits, "curve": None,
            "not_before": (T0 - timedelta(days=10)).isoformat(),
            "not_after": (T0 + timedelta(days=days)).isoformat(),
            "sans": [], "self_signed": self_signed}


def snapshot(objects=(), certs=None, files=(), edges=(), problems=(),
             file_users=None):
    """What `inventory.collect` returns. Without `file_users`, as a
    snapshot from before MAST kept the users of each file."""
    found = {"firmware": "IDG.11.0.0.3", "spec_file": "etc/v11.0.0.3-C.xml",
             "method": DETAILS, "objects": dict(objects),
             "certs": dict(certs or {}), "files": list(files),
             "edges": set(edges), "problems": list(problems)}
    if file_users is not None:
        found["file_users"] = file_users
    return found


def certificate(name, filename=None, **kwargs):
    """A CryptoCertificate object and its parsed certificate."""
    return obj("CryptoCertificate", name,
               {"Filename": filename or "cert:///{}.pem".format(name),
                "PasswordAlias": "off"}), ((D, name), cert(**kwargs))


def baseline_only(case):
    """Turn off the pqc pack for the test `case`: the tests here count
    every finding of the baseline rules, and the pqc pack (tested in
    test_mast_datapower_crypto_pqc) would add its own."""
    off = Settings(disabled_packs=frozenset(("pqc", "cnsa")))
    for target in ("mast.datapower.crypto.posture.settings",
                   "mast.datapower.crypto.rules.settings"):
        patcher = mock.patch(target, return_value=off)
        patcher.start()
        case.addCleanup(patcher.stop)


class PostureCase(SqliteCase):
    # The tests of the pqc pack set it to True
    pqc = False

    def setUp(self):
        super().setUp()
        if not self.pqc:
            baseline_only(self)
        registry.add("dp1", "10.0.0.1")
        registry.add("dp2", "10.0.0.2")

    def keep(self, collected, appliance="dp1", when=T0):
        return inventory.save(registry.by_name(appliance).id, collected, when)

    def findings(self, collected, appliance="dp1", rule=None, now=T0):
        """Keep `collected`, run the rules, and return `(rule, severity,
        subject)` of its findings (of `rule`, if given)."""
        snapshot_id = self.keep(collected, appliance)
        posture.evaluate(snapshot_id, now=now)
        return sorted((f["rule_id"], f["severity"], f["subject"])
                      for f in posture.findings_of([snapshot_id], now=now)
                      if rule is None or f["rule_id"] == rule)


class TestCertificates(PostureCase):

    def test_expired(self):
        old, parsed = certificate("old", days=-3)
        ignored = obj("CryptoCertificate", "ignored",
                      {"Filename": "cert:///i.pem", "IgnoreExpiration": "on"})
        found = self.findings(snapshot(
            [old, ignored], dict([parsed, ((D, "ignored"), cert(days=-1))])),
            rule="cert-expired")
        self.assertEqual(found, [
            ("cert-expired", "critical", D + "/CryptoCertificate/ignored"),
            ("cert-expired", "critical", D + "/CryptoCertificate/old")])
        messages = [f["message"] for f in posture.findings_of(
            [s.id for s in self.snapshots()])]
        self.assertTrue(any("IgnoreExpiration is on" in m for m in messages))
        self.assertTrue(any("3 days before" in m for m in messages))

    def snapshots(self):
        with db.session(write=False) as session:
            return list(session.scalars(select(CryptoSnapshot)))

    def test_expiring_by_how_soon(self):
        items = [certificate(name, days=days, sha=name[-1]) for name, days in
                 (("c1", 10), ("c2", 45), ("c3", 80), ("c4", 120))]
        found = self.findings(snapshot([i[0] for i in items],
                                       dict(i[1] for i in items)),
                              rule="cert-expiring")
        self.assertEqual([(f[1], f[2].rsplit("/", 1)[1]) for f in found],
                         [("high", "c1"), ("low", "c3"), ("medium", "c2")])

    def test_the_thresholds_come_from_crypto_conf(self):
        item, parsed = certificate("c1", days=100)
        values = dict(Settings().values)
        values[("expiry", "low_days")] = 120
        with mock.patch.object(posture, "settings",
                               return_value=Settings(values)):
            found = self.findings(snapshot([item], dict([parsed])),
                                  rule="cert-expiring")
        self.assertEqual([f[1] for f in found], ["low"])

    def test_a_disabled_certificate_is_left_out(self):
        (key, item), parsed = certificate("off", days=-3)
        item["admin_state"] = "disabled"
        self.assertEqual(self.findings(snapshot([(key, item)], dict([parsed])),
                                       rule="cert-expired"), [])

    def test_short_rsa_key(self):
        weak, weak_cert = certificate("weak", key_bits=1024)
        ec, ec_cert = certificate("ec", key_alg="EC", key_bits=256, sha="b")
        found = self.findings(snapshot([weak, ec], dict([weak_cert, ec_cert])),
                              rule="weak-rsa-key")
        self.assertEqual(found, [("weak-rsa-key", "high",
                                  D + "/CryptoCertificate/weak")])

    def test_sha1_or_md5_signature(self):
        sha1, sha1_cert = certificate("sha1", sig_alg="sha1WithRSAEncryption")
        md5, md5_cert = certificate("md5", sig_alg="md5WithRSAEncryption",
                                    sha="b")
        ecdsa, ecdsa_cert = certificate("ecdsa", sig_alg="ecdsa-with-SHA1",
                                        sha="c")
        # A trust anchor's own signature is not checked by anybody
        root, root_cert = certificate("root", sig_alg="sha1WithRSAEncryption",
                                      self_signed=True, sha="d")
        found = self.findings(snapshot(
            [sha1, md5, ecdsa, root],
            dict([sha1_cert, md5_cert, ecdsa_cert, root_cert])),
            rule="weak-signature")
        self.assertEqual([f[2].rsplit("/", 1)[1] for f in found],
                         ["ecdsa", "md5", "sha1"])

    def test_self_signed_on_production(self):
        own, own_cert = certificate("own", self_signed=True)
        ca, ca_cert = certificate("ca", self_signed=True, sha="b")
        objects = [own, ca, obj("CryptoIdentCred", "idcred"),
                   obj("CryptoValCred", "valcred")]
        edges = {(D, "CryptoIdentCred", "idcred", "CryptoCertificate", "own",
                  "Certificate"),
                 (D, "CryptoValCred", "valcred", "CryptoCertificate", "ca",
                  "Certificate")}
        collected = snapshot(objects, dict([own_cert, ca_cert]), edges=edges)
        self.assertEqual(self.findings(collected,
                                       rule="self-signed-production"), [])
        registry.env_add("prod", production=True, members=["dp2"])
        self.assertEqual(
            self.findings(collected, appliance="dp2",
                          rule="self-signed-production"),
            [("self-signed-production", "medium",
              D + "/CryptoCertificate/own")])


class TestTLS(PostureCase):

    def profile(self, name, state="enabled", **protocols):
        values = {"SSLv3": "off", "TLSv1d0": "off", "TLSv1d1": "off",
                  "TLSv1d2": "on", "TLSv1d3": "on"}
        values.update(protocols)
        return obj("SSLServerProfile", name, {"Protocols": values},
                   state=state)

    def test_old_protocols(self):
        found = self.findings(snapshot([
            self.profile("good"), self.profile("tls10", TLSv1d0="on"),
            self.profile("ssl3", SSLv3="on", TLSv1d1="on"),
            self.profile("off", TLSv1d0="on", state="disabled"),
            obj("CryptoProfile", "legacy", {"SSLOptions": {
                "Disable-SSLv2": "on", "Disable-SSLv3": "on",
                "Disable-TLSv1": "off", "Disable-TLSv1d1": "on"}}),
            obj("CryptoProfile", "strict", {"SSLOptions": {
                "Disable-SSLv2": "on", "Disable-SSLv3": "on",
                "Disable-TLSv1": "on", "Disable-TLSv1d1": "on"}})]),
            rule="tls-old-protocol")
        self.assertEqual(
            [(f[1], f[2].rsplit("/", 1)[1]) for f in found],
            [("critical", "ssl3"), ("high", "legacy"), ("high", "tls10")])
        message = [f for f in posture.findings_of(
            [s.id for s in TestCertificates.snapshots(self)])
            if f["name"] == "ssl3"][0]["message"]
        self.assertEqual(message, "SSL 3.0 and TLS 1.1 are enabled in "
                                  "Protocols.")

    def test_tls13_off_where_the_firmware_has_it(self):
        older = obj("SSLClientProfile", "older", {"Protocols": {
            "SSLv3": "off", "TLSv1d0": "off", "TLSv1d1": "off",
            "TLSv1d2": "on"}})
        found = self.findings(snapshot([
            self.profile("on"), self.profile("off13", TLSv1d3="off"), older]),
            rule="tls13-off")
        self.assertEqual(found, [("tls13-off", "low",
                                  D + "/SSLServerProfile/off13")])


class TestPasswords(PostureCase):

    def test_in_use_and_left_over(self):
        found = self.findings(snapshot([
            obj("CryptoKey", "plain", {"PasswordAlias": "off"},
                plaintext=True),
            obj("CryptoKey", "both", {"PasswordAlias": "on", "Alias": {
                "class": "PasswordAlias", "name": "alias1"}}, plaintext=True),
            obj("CryptoKey", "alias", {"PasswordAlias": "on", "Alias": {
                "class": "PasswordAlias", "name": "alias1"}}),
            obj("CryptoCertificate", "pcert", {"PasswordAlias": "off"},
                plaintext=True),
            obj("CryptoKey", "nopass", {"PasswordAlias": "off"})]))
        self.assertEqual(found, [
            ("plaintext-password", "high", D + "/CryptoCertificate/pcert"),
            ("plaintext-password", "high", D + "/CryptoKey/plain"),
            ("plaintext-password-unused", "medium", D + "/CryptoKey/both")])
        leftover = [f for f in posture.findings_of(
            [s.id for s in TestCertificates.snapshots(self)])
            if f["rule_id"] == "plaintext-password-unused"][0]
        self.assertIn("alias1", leftover["message"])


class TestFiles(PostureCase):

    def file(self, name, location="cert:", domain=D):
        return {"domain": domain, "location": location, "name": name,
                "size": 1, "modified": None}

    def test_unused_files(self):
        objects = [
            obj("CryptoCertificate", "used", {"Filename": "cert:///used.pem"}),
            obj("CryptoKey", "shared", {"Filename": "sharedcert:///s.pem"},
                domain="other")]
        files = [self.file("cert:///used.pem"), self.file("cert:///old.pem"),
                 # Another domain's cert: does not count for app1
                 self.file("cert:///used.pem", domain="other"),
                 self.file("sharedcert:///s.pem", "sharedcert:", "default"),
                 self.file("sharedcert:///t.pem", "sharedcert:", "default"),
                 self.file("pubcert:///Root.pem", "pubcert:", "default"),
                 # The firmware's own, for the WebGUI
                 self.file("cert:///webgui-privkey.pem", domain="default"),
                 self.file("cert:///webgui-sscert.pem", domain="default")]
        found = self.findings(snapshot(objects, files=files),
                              rule="unused-file")
        self.assertEqual([f[2] for f in found], [
            "app1/cert:///old.pem", "default/sharedcert:///t.pem",
            "other/cert:///used.pem"])

    def test_any_object_that_names_a_file_uses_it(self):
        # An AAA policy with an LTPA key: not a crypto object, but it
        # uses the file
        files = [self.file("cert:///ltpa.key"), self.file("cert:///old.pem"),
                 self.file("sharedcert:///s.pem", "sharedcert:", "default")]
        users = {"cert:///ltpa.key": {D + "/AAAPolicy/sso"},
                 # The same name in another domain does not count
                 "cert:///old.pem": {"other/AAAPolicy/x"},
                 "sharedcert:///s.pem": {"other/CryptoKey/k"}}
        found = self.findings(snapshot(files=files, file_users=users),
                              rule="unused-file")
        self.assertEqual([f[2] for f in found], ["app1/cert:///old.pem"])

    def test_nothing_is_said_about_a_domain_not_read(self):
        files = [self.file("cert:///a.pem"),
                 self.file("cert:///b.pem", domain="other"),
                 self.file("sharedcert:///t.pem", "sharedcert:", "default")]
        found = self.findings(snapshot(files=files, problems=[
            {"domain": D, "problem": "AuthenticationFailure"}]),
            rule="unused-file")
        self.assertEqual([f[2] for f in found], ["other/cert:///b.pem"])


class TestConfiguration(PostureCase):

    def test_running_and_saved_differ(self):
        found = self.findings(snapshot([
            obj("CryptoKey", "same"),
            obj("CryptoKey", "changed", saved=False),
            obj("CryptoKey", "gone", running=False),
            obj("CryptoKey", "elsewhere", saved=False, domain="other")]),
            rule="not-saved")
        # One finding for each domain
        self.assertEqual([f[2] for f in found], [D, "other"])
        message = [f["message"] for f in posture.findings_of(
            [s.id for s in TestCertificates.snapshots(self)])
            if f["subject"] == D][0]
        self.assertEqual(message, "2 objects of the domain app1 differ from "
                                  "the saved configuration: CryptoKey "
                                  "changed, CryptoKey gone (saved, not "
                                  "running).")

    def test_a_disabled_object_still_referenced(self):
        objects = [obj("CryptoCertificate", "off", state="disabled"),
                   obj("CryptoCertificate", "off2", state="disabled"),
                   obj("CryptoIdentCred", "idcred"),
                   obj("CryptoIdentCred", "idcred-off", state="disabled")]
        edges = {(D, "CryptoIdentCred", "idcred", "CryptoCertificate", "off",
                  "Certificate"),
                 (D, "CryptoIdentCred", "idcred-off", "CryptoCertificate",
                  "off2", "Certificate")}
        found = self.findings(snapshot(objects, edges=edges),
                              rule="disabled-in-use")
        self.assertEqual(found, [("disabled-in-use", "medium",
                                  D + "/CryptoCertificate/off")])


class TestSelfChecks(PostureCase):

    def setUp(self):
        super().setUp()
        key_file = os.path.join(tempfile.mkdtemp(dir=self.tmp), "k")
        patcher = mock.patch("mast.jobs.config.credentials_key_file",
                             return_value=key_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def checks(self, now=None):
        return sorted((f["rule_id"], f["subject"])
                      for f in posture.self_check_findings(now=now))

    def test_the_setup_of_mast(self):
        registry.update("dp2", tls_mode="none")
        credentials.create("collector", "reader", "Read-0nly-pass", "collect",
                           ["dp1"], principal=ADMIN)
        credentials.create("changer", "admin", "Ch4nge-the-pass", "change",
                           ["dp1"], principal=ADMIN)
        self.assertEqual(self.checks(), [("mast-tls-none", "dp2")])
        credentials.record_test("collector", True, can_write=True,
                                principal=ADMIN)
        credentials.record_test("changer", True, can_write=True,
                                principal=ADMIN)
        self.assertEqual(self.checks(), [("mast-collect-can-write",
                                          "collector"),
                                         ("mast-tls-none", "dp2")])
        later = datetime.now(timezone.utc) + timedelta(days=181)
        self.assertEqual(
            [f for f in self.checks(later) if f[0] == "mast-credential-unused"],
            [("mast-credential-unused", "changer"),
             ("mast-credential-unused", "collector")])

    def test_a_self_check_counts_in_the_score_of_its_appliance(self):
        self.keep(snapshot([obj("CryptoKey", "k")]), appliance="dp2")
        posture.evaluate(self.latest("dp2"))
        self.assertEqual(posture.summary()[registry.by_name("dp2").id]["score"],
                         100)
        registry.update("dp2", tls_mode="none")
        found = posture.summary()[registry.by_name("dp2").id]
        self.assertEqual((found["score"], found["counts"]["high"]), (85, 1))

    def latest(self, name):
        with db.session(write=False) as session:
            return posture.latest_snapshots(
                session, [registry.by_name(name).id])[registry.by_name(name).id]


class TestEvaluate(PostureCase):

    def test_a_failed_snapshot_has_no_findings(self):
        snapshot_id = inventory.save(registry.by_name("dp1").id, None, T0,
                                     error="RuntimeError: away")
        self.assertEqual(posture.evaluate(snapshot_id), 0)

    def test_again_replaces_what_was_found(self):
        item, parsed = certificate("old", days=-3)
        snapshot_id = self.keep(snapshot([item], dict([parsed])))
        posture.evaluate(snapshot_id, now=T0)
        posture.evaluate(snapshot_id, now=T0)
        self.assertEqual(len(posture.findings_of([snapshot_id])), 1)

    def test_a_rule_that_fails_does_not_stop_the_others(self):
        item, parsed = certificate("old", days=-3)
        snapshot_id = self.keep(snapshot([item], dict([parsed])))
        broken = rules.RULES["weak-rsa-key"]
        with mock.patch.object(broken, "function", side_effect=RuntimeError):
            with self.assertLogs("mast.crypto", "ERROR"):
                posture.evaluate(snapshot_id, now=T0)
        self.assertEqual([f["rule_id"] for f in posture.findings_of(
            [snapshot_id])], ["cert-expired"])

    def test_a_rule_turned_off(self):
        item, parsed = certificate("old", days=-3)
        with mock.patch.object(posture, "settings", return_value=Settings(
                disabled=frozenset({"cert-expired"}),
                disabled_packs=frozenset({"pqc", "cnsa"}))):
            self.assertEqual(self.findings(snapshot([item], dict([parsed]))),
                             [])

    def test_every_rule_has_a_title_and_a_description(self):
        rules._load()
        self.assertGreaterEqual(len(rules.RULES), 15)
        for item in rules.RULES.values():
            self.assertTrue(item.title and item.doc, item.id)
            self.assertIn(item.severity, rules.SEVERITIES)


class TestExceptions(PostureCase):

    def setUp(self):
        super().setUp()
        item, parsed = certificate("old", days=-3)
        self.snapshot_id = self.keep(snapshot(
            [item, obj("CryptoKey", "plain", {"PasswordAlias": "off"},
                       plaintext=True)], dict([parsed])))
        posture.evaluate(self.snapshot_id)

    def open_findings(self):
        return sorted(f["rule_id"] for f in posture.findings_of(
            [self.snapshot_id]) if not f["exception"])

    def test_an_exception_covers_its_findings(self):
        self.assertEqual(self.open_findings(),
                         ["cert-expired", "plaintext-password"])
        score = posture.summary()[registry.by_name("dp1").id]["score"]
        self.assertEqual(score, 100 - 40 - 15)
        found = posture.add_exception(
            "cert-expired", "ol*", "Replaced in the change of next week",
            "30d", ADMIN, appliance="dp1", domain=D)
        self.assertEqual((found["created_by"], found["expired"]),
                         (ADMIN.name, False))
        self.assertEqual(self.open_findings(), ["plaintext-password"])
        summary = posture.summary()[registry.by_name("dp1").id]
        self.assertEqual((summary["score"], summary["counts"]["excepted"]),
                         (85, 1))
        # Another appliance, domain or pattern: not covered
        for kwargs in ({"appliance": "dp2"}, {"domain": "other"},
                       {"pattern": "new*"}):
            posture.remove_exception(found["id"], ADMIN)
            values = dict({"appliance": "dp1", "domain": D, "pattern": "ol*"},
                          **kwargs)
            found = posture.add_exception(
                "cert-expired", values["pattern"],
                "Replaced in the change of next week", "30d", ADMIN,
                appliance=values["appliance"], domain=values["domain"])
            self.assertEqual(self.open_findings(),
                             ["cert-expired", "plaintext-password"], kwargs)

    def test_an_expired_exception_no_longer_applies(self):
        found = posture.add_exception(
            "plaintext-password", "*", "Being moved to an alias this week",
            "7d", ADMIN)
        self.assertEqual(self.open_findings(), ["cert-expired"])
        later = datetime.now(timezone.utc) + timedelta(days=8)
        self.assertEqual(sorted(f["rule_id"] for f in posture.findings_of(
            [self.snapshot_id], now=later) if not f["exception"]),
            ["cert-expired", "plaintext-password"])
        self.assertTrue([e for e in posture.list_exceptions(now=later)
                         if e["id"] == found["id"]][0]["expired"])
        # A new snapshot after the expiry is not covered either
        posture.evaluate(self.snapshot_id, now=later)
        with db.session(write=False) as session:
            self.assertIsNone(session.scalars(select(CryptoFinding).where(
                CryptoFinding.rule_id == "plaintext-password")).one()
                .exception_id)

    def test_removing_it_uncovers_the_findings(self):
        found = posture.add_exception(
            "plaintext-password", "plain", "Being moved to an alias this week",
            "2027-01-31", ADMIN)
        self.assertEqual(self.open_findings(), ["cert-expired"])
        posture.remove_exception(found["id"], ADMIN)
        self.assertEqual(self.open_findings(),
                         ["cert-expired", "plaintext-password"])
        with db.session(write=False) as session:
            self.assertEqual(session.scalars(select(CryptoException)).all(), [])

    def test_what_an_exception_needs(self):
        good = {"rule_id": "cert-expired", "object_pattern": "old",
                "reason": "Replaced in the change of next week",
                "expires": "30d", "principal": ADMIN}
        for change, text in (
                ({"principal": OPERATOR}, None),
                ({"rule_id": "nothing"}, "no rule"),
                ({"object_pattern": " "}, "pattern"),
                ({"reason": "because"}, "reason"),
                ({"expires": ""}, "expiry"),
                ({"expires": "soon"}, "expiry is a date"),
                ({"expires": "2020-01-01"}, "future"),
                ({"expires": "400d"}, "at most 365 days"),
                ({"expires": "2027-01-01T00:00:00"}, "time zone")):
            kwargs = dict(good, **change)
            expected = Forbidden if text is None else posture.ExceptionError
            with self.assertRaises(expected) as caught:
                posture.add_exception(**kwargs)
            if text:
                self.assertIn(text, str(caught.exception), change)
        with self.assertRaises(posture.ExceptionError):
            posture.add_exception(**dict(good, appliance="nothing"))
        with self.assertRaises(Forbidden):
            posture.remove_exception(1, OPERATOR)
        with self.assertRaises(posture.ExceptionError):
            posture.remove_exception(99, ADMIN)

    def test_a_date_ends_at_the_end_of_that_day(self):
        self.assertEqual(posture.expiry_of("2027-03-31"),
                         datetime(2027, 3, 31, 23, 59, 59, tzinfo=timezone.utc))
        self.assertEqual(posture.expiry_of("90d", now=T0),
                         T0 + timedelta(days=90))


class TestSummary(PostureCase):

    def test_score_and_nearest_expiry(self):
        soon, soon_cert = certificate("soon", days=20)
        late, late_cert = certificate("late", days=300, sha="b")
        off = obj("CryptoCertificate", "off", state="disabled")
        snapshot_id = self.keep(snapshot(
            [soon, late, off],
            dict([soon_cert, late_cert, ((D, "off"), cert(days=1, sha="c"))])))
        posture.evaluate(snapshot_id, now=T0)
        found = posture.summary(now=T0)
        self.assertEqual(list(found), [registry.by_name("dp1").id])
        mine = found[registry.by_name("dp1").id]
        self.assertEqual(mine["nearest_expiry"]["name"], "soon")
        self.assertEqual((mine["score"], mine["counts"]["high"]), (85, 1))
        # A newer snapshot that failed does not hide the last good one
        inventory.save(registry.by_name("dp1").id, None, T0, error="away")
        self.assertEqual(posture.summary(now=T0)[registry.by_name("dp1").id]
                         ["snapshot_id"], snapshot_id)


class TestCommands(PostureCase):

    def setUp(self):
        super().setUp()
        item, parsed = certificate("old", days=-3)
        self.snapshot_id = self.keep(snapshot([item], dict([parsed])))

    def test_posture_runs_the_rules(self):
        result = posture.posture()
        rows = result.data()["rows"]
        self.assertEqual([r[:4] for r in rows],
                         [["dp1", "critical", "cert-expired",
                           D + "/CryptoCertificate/old"]])
        with self.assertRaises(Exception):
            posture.posture(snapshot=self.snapshot_id, appliances=["dp1"])
        with self.assertRaises(ValueError):
            posture.posture(appliances=["nothing"])

    def test_exceptions_through_the_queue(self):
        from mast.auth import users
        users.add("ada", "a-long-password-1", "admin")
        users.add("otto", "a-long-password-2", "operator")
        posture.evaluate(self.snapshot_id)
        job = jobs.submit("crypto.exception-add", {
            "rule": "cert-expired", "pattern": "old", "expires": "30d",
            "reason": "Replaced in the change of next week"},
            principal=Principal("ada", "admin"))
        jobs.run_next("w1")
        self.assertEqual(jobs.get(job).status, jobs.SUCCEEDED, jobs.get(job).error)
        self.assertEqual(jobs.result(job).data["rows"][0][1], "cert-expired")
        self.assertTrue(posture.findings_of([self.snapshot_id])[0]["exception"])
        # An operator may not
        job = jobs.submit("crypto.exception-add", {
            "rule": "cert-expired", "pattern": "x", "expires": "30d",
            "reason": "Replaced in the change of next week"},
            principal=OPERATOR)
        jobs.run_next("w1")
        self.assertEqual(jobs.get(job).status, jobs.FAILED)
        self.assertIn("manage-exception", jobs.get(job).error)
        listed = posture.exceptions()
        self.assertEqual(len(listed.data()["rows"]), 1)
        removed = posture.exception_remove(id=int(listed.data()["rows"][0][0]))
        self.assertEqual(removed.data()["rows"], [])

    def test_the_rules(self):
        rows = posture.list_rules().data()["rows"]
        self.assertIn(["cert-expired", "baseline", "critical"],
                      [r[:3] for r in rows])
        # Here the pqc pack is off (baseline_only); the cnsa pack is off
        # by default
        self.assertEqual({r[1] for r in rows if r[4] == "no"},
                         {"pqc", "cnsa"})
        self.assertTrue(all(r[4] == "yes" for r in rows
                            if r[1] in ("baseline", "mast")))

    def test_the_inventory_runs_the_rules(self):
        # mast-crypto inventory evaluates each snapshot it keeps
        with mock.patch.object(posture, "evaluate", return_value=4) as run:
            from mast.testsuite.unit.test_mast_datapower_crypto_inventory import (
                FakeEnvironment)
            with mock.patch("mast.plugin_utils.params.Environment",
                            FakeEnvironment):
                result = inventory.inventory(appliances=["dp1"],
                                             credentials=["u:p"],
                                             request_delay=0)
        run.assert_called_once()
        self.assertEqual(result.data()["rows"][0][8], "4")


class TestExport(PostureCase):

    def test_the_findings_sheet(self):
        from mast.datapower.crypto import export
        item, parsed = certificate("old", days=-3)
        snapshot_id = self.keep(snapshot([item], dict([parsed])))
        posture.evaluate(snapshot_id)
        posture.add_exception("cert-expired", "old",
                              "Replaced in the change of next week", "30d",
                              ADMIN)
        found = export.rows(export.load())["Findings"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][:5], ["dp1", D, D + "/CryptoCertificate/old",
                                        "cert-expired", "critical"])
        self.assertIn("Replaced in the change", found[0][7])


class TestMigration(PostureCase):

    def test_down_and_up(self):
        item, parsed = certificate("old", days=-3)
        snapshot_id = self.keep(snapshot([item], dict([parsed])))
        posture.evaluate(snapshot_id)
        posture.add_exception("cert-expired", "old",
                              "Replaced in the change of next week", "30d",
                              ADMIN)
        db.downgrade("0009")
        self.assertEqual(db.revision(), "0009")
        db.configure(self.url)
        self.assertEqual([a.name for a in registry.list_appliances()],
                         ["dp1", "dp2"])
        self.assertEqual(db.revision(), db.HEAD)
        # The snapshots stayed; the findings come back with a new run
        self.assertEqual(posture.evaluate(snapshot_id), 1)
        self.assertEqual(posture.list_exceptions(), [])

    def test_removing_an_appliance_removes_its_findings(self):
        item, parsed = certificate("old", days=-3)
        posture.evaluate(self.keep(snapshot([item], dict([parsed]))))
        registry.remove("dp1")
        with db.session(write=False) as session:
            self.assertEqual(session.scalars(select(CryptoFinding)).all(), [])


class TestMigrationPostgres(PostgresMixin, TestMigration):
    pass


class TestExceptionsPostgres(PostgresMixin, TestExceptions):
    pass


class TestCryptoPage(WebCase):
    """The Crypto page and its API, as each role."""

    def setUp(self):
        super().setUp()
        baseline_only(self)
        self.key_file = os.path.join(tempfile.mkdtemp(dir=self.tmp), "k")
        patcher = mock.patch("mast.jobs.config.credentials_key_file",
                             return_value=self.key_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        registry.add("dp1", "10.0.0.1")
        registry.add("dp2", "10.0.0.2")
        credentials.create("collector", "reader", "Read-0nly-pass", "collect",
                           ["dp1", "dp2"], principal=ADMIN)
        old, old_cert = certificate("old", days=-3)
        soon, soon_cert = certificate("soon", days=20, sha="b")
        objects = [old, soon, obj("CryptoIdentCred", "idcred"),
                   obj("SSLServerProfile", "server", {"Protocols": {
                       "TLSv1d0": "on", "TLSv1d2": "on", "TLSv1d3": "on"},
                       "Idcred": {"class": "CryptoIdentCred",
                                  "name": "idcred"}}),
                   obj("HTTPSSourceProtocolHandler", "https")]
        objects[-1][1]["attrs"] = None
        edges = {(D, "CryptoIdentCred", "idcred", "CryptoCertificate", "soon",
                  "Certificate"),
                 (D, "SSLServerProfile", "server", "CryptoIdentCred", "idcred",
                  "Idcred"),
                 (D, "HTTPSSourceProtocolHandler", "https", "SSLServerProfile",
                  "server", "SSLServerProfile")}
        files = [{"domain": D, "location": "cert:", "name": "cert:///x.pem",
                  "size": 10, "modified": None}]
        now = datetime.now(timezone.utc)
        self.first = inventory.save(registry.by_name("dp1").id, snapshot(
            [old], dict([old_cert])), now - timedelta(days=1))
        posture.evaluate(self.first)
        self.second = inventory.save(registry.by_name("dp1").id, snapshot(
            objects, dict([old_cert, soon_cert]), files=files, edges=edges,
            file_users={}), now)
        posture.evaluate(self.second)

    def call(self, method, path, body=None, client=None):
        client = client or self.client
        return client.open("/api/v1" + path, method=method, json=body,
                           headers={"X-CSRF-Token": self.token(client)})

    def as_role(self, role):
        client = self.app.test_client()
        self.login(role, client=client)
        return client

    def object_id(self, name):
        with db.session(write=False) as session:
            from mast.datapower.crypto.models import CryptoObject
            return session.scalars(select(CryptoObject.id).where(
                CryptoObject.snapshot_id == self.second,
                CryptoObject.name == name)).one()

    def test_the_page_and_its_files(self):
        self.login("viewer")
        page = self.client.get("/crypto").get_data(as_text=True)
        self.assertIn('href="/crypto"', page)
        for name, kind in (("crypto.js", "javascript"),
                           ("crypto.css", "text/css")):
            self.assertIn("/static/crypto/" + name, page)
            answer = self.client.get("/static/crypto/" + name)
            self.assertEqual(answer.status_code, 200, name)
            self.assertIn(kind, answer.content_type)

    def test_the_overview(self):
        self.login("viewer")
        answer = self.call("GET", "/crypto/overview").get_json()
        rows = {a["name"]: a for a in answer["appliances"]}
        self.assertIsNone(rows["dp2"]["last"])
        mine = rows["dp1"]
        self.assertEqual(mine["last"]["id"], self.second)
        self.assertEqual(mine["posture"]["nearest_expiry"]["name"], "old")
        # cert-expired (40), cert-expiring high (15), TLS 1.0 (15),
        # unused file (1)
        self.assertEqual(mine["posture"]["score"], 100 - 40 - 15 - 15 - 1)
        self.assertEqual([e["name"] for e in answer["expiring"]],
                         ["old", "soon"])
        self.assertEqual(answer["may"], {"run": False, "schedule": False,
                                         "exceptions": False})
        self.assertEqual(self.call("GET", "/crypto/overview",
                                   client=self.as_role("admin"))
                         .get_json()["may"],
                         {"run": True, "schedule": True, "exceptions": True})

    def test_the_inventory_and_one_object(self):
        self.login("viewer")
        rows = self.call("GET", "/crypto/inventory").get_json()["rows"]
        by_name = {r["name"]: r for r in rows}
        # The HTTPS handler only refers: not a row of the inventory
        self.assertNotIn("https", by_name)
        self.assertEqual(by_name["cert:///x.pem"]["kind"], "file")
        self.assertEqual(by_name["cert:///x.pem"]["used_by"], 0)
        self.assertEqual(by_name["server"]["detail"],
                         "TLS 1.0, TLS 1.2, TLS 1.3 · idcred")
        self.assertEqual(by_name["old"]["findings"], 1)
        found = self.call("GET", "/crypto/objects/{}".format(
            self.object_id("soon"))).get_json()
        self.assertEqual(found["cert"]["subject"], "CN=test")
        self.assertEqual([u["name"] for u in found["used_by"]], ["idcred"])
        # What breaks: the idcred, its profile, the handler that uses it
        tree = found["breaks"]
        self.assertEqual(tree[0]["name"], "idcred")
        self.assertEqual(tree[0]["children"][0]["name"], "server")
        self.assertEqual(tree[0]["children"][0]["children"][0]["name"], "https")
        self.assertEqual([f["rule_id"] for f in found["findings"]],
                         ["cert-expiring"])
        self.assertEqual(self.call("GET", "/crypto/objects/999999")
                         .status_code, 404)

    def test_findings_and_exceptions(self):
        self.login("viewer")
        answer = self.call("GET", "/crypto/findings").get_json()
        self.assertEqual(sorted({f["rule_id"] for f in answer["findings"]}),
                         ["cert-expired", "cert-expiring", "tls-old-protocol",
                          "unused-file"])
        self.assertFalse(answer["may_manage"])
        body = {"rule_id": "unused-file", "object_pattern": "cert:///x.pem",
                "reason": "Kept for the rollback of next week",
                "expires": "30d", "appliance": "dp1", "domain": D}
        for role in ("viewer", "operator"):
            self.assertEqual(self.call("POST", "/crypto/exceptions", body,
                                       client=self.as_role(role))
                             .status_code, 403, role)
        admin = self.as_role("admin")
        self.assertEqual(self.call("POST", "/crypto/exceptions",
                                   dict(body, reason="no"), client=admin)
                         .status_code, 422)
        added = self.call("POST", "/crypto/exceptions", body, client=admin)
        self.assertEqual(added.status_code, 201, added.get_json())
        found = {f["rule_id"]: f for f in self.call(
            "GET", "/crypto/findings").get_json()["findings"]}
        self.assertEqual(found["unused-file"]["exception"]["created_by"],
                         "admin")
        listed = self.call("GET", "/crypto/exceptions").get_json()
        self.assertEqual(len(listed["exceptions"]), 1)
        exception_id = added.get_json()["id"]
        self.assertEqual(self.call("DELETE", "/crypto/exceptions/{}".format(
            exception_id)).status_code, 403)
        self.assertEqual(self.call("DELETE", "/crypto/exceptions/{}".format(
            exception_id), client=admin).status_code, 204)
        self.assertEqual(self.call("DELETE", "/crypto/exceptions/{}".format(
            exception_id), client=admin).status_code, 404)

    def test_the_history(self):
        self.login("viewer")
        answer = self.call("GET", "/crypto/snapshots?appliance=dp1").get_json()
        self.assertEqual([s["id"] for s in answer["snapshots"]],
                         [self.second, self.first])
        self.assertEqual(answer["snapshots"][0]["findings"]["critical"], 1)
        self.assertEqual(self.call("GET", "/crypto/snapshots?appliance=x")
                         .status_code, 404)
        diff = self.call("GET", "/crypto/diff?from={}&to={}".format(
            self.first, self.second)).get_json()
        self.assertEqual(sorted(a["name"] for a in diff["added"]),
                         ["idcred", "server", "soon"])
        self.assertEqual(diff["removed"], [])
        self.assertEqual(sorted(f["rule_id"] for f in diff["opened"]),
                         ["cert-expiring", "tls-old-protocol", "unused-file"])
        self.assertEqual(diff["closed"], [])
        other = inventory.save(registry.by_name("dp2").id, snapshot(), T0)
        self.assertEqual(self.call("GET", "/crypto/diff?from={}&to={}".format(
            self.first, other)).status_code, 422)
        self.assertEqual(self.call("GET", "/crypto/diff?from=x&to=1")
                         .status_code, 422)

    def test_running_and_scheduling_the_inventory(self):
        self.assertEqual(self.call("POST", "/crypto/run",
                                   {"credential": "collector"},
                                   client=self.as_role("viewer")).status_code,
                         403)
        operator = self.as_role("operator")
        self.assertEqual(self.call("POST", "/crypto/run", {},
                                   client=operator).status_code, 422)
        answer = self.call("POST", "/crypto/run", {"credential": "collector"},
                           client=operator)
        self.assertEqual(answer.status_code, 202, answer.get_json())
        job = jobs.get(answer.get_json()["job_id"])
        self.assertEqual((job.command, job.params["appliances"]),
                         ("crypto.inventory", ["dp1", "dp2"]))
        self.assertEqual(self.call("POST", "/crypto/schedule",
                                   {"credential": "collector"},
                                   client=operator).status_code, 403)
        admin = self.as_role("admin")
        answer = self.call("POST", "/crypto/schedule",
                           {"credential": "collector", "hour": 3},
                           client=admin)
        self.assertEqual(answer.get_json(), {"schedule": "crypto-inventory",
                                             "cron": "0 3 * * *",
                                             "enabled": True})
        self.assertEqual(self.call("POST", "/crypto/schedule",
                                   {"credential": "collector", "hour": 30},
                                   client=admin).status_code, 422)
        schedules = self.call("GET", "/crypto/overview", client=operator) \
            .get_json()["schedules"]
        self.assertEqual([(s["name"], s["credential"]) for s in schedules],
                         [("crypto-inventory", "collector")])
        # A viewer does not see the name of the credential
        self.assertIsNone(self.call("GET", "/crypto/overview",
                                    client=self.as_role("viewer"))
                          .get_json()["schedules"][0]["credential"])

    def test_the_pqc_readiness_of_the_overview(self):
        from mast.datapower.crypto import probe
        dp1 = registry.by_name("dp1").id
        probe.save(dp1, self.second, "10.0.0.1", 443, {
            "ok": True, "offers": {"X25519MLKEM768": True}}, T0)
        probe.save(dp1, self.second, "10.0.0.1", 5550, {
            "ok": True, "offers": {"X25519MLKEM768": None}}, T0)
        probe.save(dp1, self.second, "10.0.0.1", 9090, {"ok": False}, T0)
        found = self.call("GET", "/crypto/overview",
                          client=self.as_role("viewer")).get_json()["pqc"]
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertEqual((item["appliance"], item["support"]),
                         ("dp1", "control"))
        # The server profile has TLS 1.0 and 1.2 on and no KEMAlgs: the
        # default of 11.0.0.3 negotiates the hybrid
        self.assertEqual(item["profiles"], {"total": 1, "hybrid": 1, "down": 0})
        self.assertEqual({k: item["peers"][k] for k in
                          ("probed", "hybrid", "unknown")},
                         {"probed": 2, "hybrid": 1, "unknown": 1})

    def test_the_key_exchange_of_a_profile(self):
        self.login("viewer")
        item = self.client.get("/api/v1/crypto/objects/{}".format(
            self.object_id("server"))).get_json()
        self.assertEqual(item["groups"]["hybrid"], ["X25519MLKEM768"])
        self.assertTrue(item["groups"]["tls12"])
        self.assertIsNone(self.client.get("/api/v1/crypto/objects/{}".format(
            self.object_id("soon"))).get_json()["groups"])

    def test_probing_and_scheduling_the_probe(self):
        from mast.datapower.crypto import probe
        probe.save(registry.by_name("dp1").id, self.second, "10.0.0.1", 443, {
            "ok": True, "protocol": "TLSv1.3", "group": "X25519MLKEM768",
            "offers": {"X25519MLKEM768": True}, "notes": {}, "chain": [],
            "target": {"kind": "management", "name": "XML management"}}, T0)
        viewer = self.as_role("viewer")
        answer = self.call("GET", "/crypto/probes", client=viewer).get_json()
        self.assertEqual([(r["appliance"], r["port"], r["group"])
                          for r in answer["results"]],
                         [("dp1", 443, "X25519MLKEM768")])
        self.assertEqual(answer["may"], {"run": False, "schedule": False})
        self.assertEqual(self.call("POST", "/crypto/probe", {},
                                   client=viewer).status_code, 403)
        operator = self.as_role("operator")
        answer = self.call("POST", "/crypto/probe", {}, client=operator)
        self.assertEqual(answer.status_code, 202, answer.get_json())
        job = jobs.get(answer.get_json()["job_id"])
        self.assertEqual((job.command, job.params["appliances"]),
                         ("crypto.probe", ["dp1", "dp2"]))
        self.assertEqual(self.call("POST", "/crypto/probe-schedule", {},
                                   client=operator).status_code, 403)
        answer = self.call("POST", "/crypto/probe-schedule", {},
                           client=self.as_role("admin"))
        self.assertEqual(answer.get_json(), {"schedule": "crypto-probe",
                                             "cron": "0 3 * * 0",
                                             "enabled": True})
        self.assertEqual([s["name"] for s in self.call(
            "GET", "/crypto/probes", client=operator).get_json()["schedules"]],
            ["crypto-probe"])

    def test_the_appliances_page_shows_the_posture(self):
        self.login("viewer")
        rows = {a["name"]: a for a in self.client.get(
            "/api/v1/appliances-status").get_json()["appliances"]}
        self.assertIsNone(rows["dp2"]["crypto"])
        self.assertEqual(rows["dp1"]["crypto"]["score"], 29)
        self.assertEqual(rows["dp1"]["crypto"]["nearest_expiry"]["name"], "old")
