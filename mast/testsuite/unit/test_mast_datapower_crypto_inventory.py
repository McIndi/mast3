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
Unit tests for the crypto inventory (`mast.datapower.crypto.inventory`),
on what a DataPower 11.0.0.3 container answered for the domain of
`mast.testsuite.integration.crypto_lab` (`mast/testsuite/data/crypto`).
"""
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from lxml import etree
from sqlalchemy import select, text

from mast import jobs, registry
from mast.datapower.crypto import inventory
from mast.datapower.crypto.models import (
    DETAILS, EXPORT, CryptoCert, CryptoCertCache, CryptoEdge, CryptoFile,
    CryptoObject, CryptoSnapshot)
from mast.jobs import db
from mast.testsuite.integration.crypto_lab import (
    ALIAS_PASSWORD, PLAINTEXT_PASSWORD)
from mast.testsuite.unit.test_mast_jobs import PostgresMixin, SqliteCase

DATA = Path(__file__).resolve().parents[1] / "data" / "crypto"
DOMAIN = "cryptolab"
T0 = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)

FIRMWARE = (
    '<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">'
    '<env:Body><dp:response xmlns:dp="http://www.datapower.com/schemas/'
    'management"><dp:status><FirmwareVersion3><Serial>1</Serial><Version>'
    '{}</Version></FirmwareVersion3></dp:status></dp:response></env:Body>'
    '</env:Envelope>')


def data(name):
    return (DATA / name).read_bytes()


class Response(object):
    def __init__(self, raw):
        self.text = raw
        self.xml = etree.fromstring(raw)


class FakeAppliance(object):
    """Answers as the container did. `fail_getfile` makes the read of
    an exported file fail."""
    history = ""

    def __init__(self, hostname="dp1", firmware="IDG.11.0.0.3",
                 fail_getfile=False):
        self.hostname = hostname
        self._record = registry.by_name(hostname)
        self.domains = ["default", DOMAIN]
        self.firmware = firmware
        self.fail_getfile = fail_getfile
        self.calls = []
        self.exported = {}

    def get_status(self, provider, domain="default"):
        self.calls.append(("get_status", provider))
        return Response(FIRMWARE.format(self.firmware).encode())

    def get_config(self, domain="default", persisted=True, **kwargs):
        self.calls.append(("get_config", domain, persisted))
        if domain != DOMAIN:
            return Response(b"<x/>")
        return Response(data("config-saved.xml" if persisted
                             else "config-running.xml"))

    def get_certificate_details(self, domain, name):
        self.calls.append(("details", domain, name))
        return Response(data("details-{}.xml".format(name)))

    def do_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "CryptoExport":
            der, _ = inventory.certificate_from_details(
                data("details-{}.xml".format(kwargs["ObjectName"])))
            import base64
            self.exported["temporary:///" + kwargs["OutputFilename"]] = (
                '<crypto-export version="1"><certificate version="1">{}'
                '</certificate></crypto-export>'.format(
                    base64.b64encode(der).decode())).encode()
        if action == "DeleteFile":
            self.exported.pop(kwargs["File"], None)
        return Response(b"<ok/>")

    def getfile(self, domain, path):
        self.calls.append(("getfile", domain, path))
        if self.fail_getfile:
            raise RuntimeError("the file is away")
        return self.exported[path]

    def get_filestore(self, domain="default", location="local:"):
        self.calls.append(("filestore", location))
        return Response(data("filestore-{}.xml".format(location.rstrip(":"))))


class TestParsing(SqliteCase):

    def test_objects_without_secrets(self):
        found = inventory.objects_of(data("config-running.xml"))
        names = {n for (_, n) in found}
        for name in ("lab-ca", "lab-server-cert", "lab-old-cert",
                     "lab-server-key", "lab-old-key", "lab-idcred",
                     "lab-valcred", "lab-server-profile",
                     "lab-client-profile", "lab-alias"):
            self.assertIn(name, names)
        old = found[("CryptoKey", "lab-old-key")]
        self.assertTrue(old["has_plaintext_password"])
        self.assertNotIn("Password", old["attrs"])
        self.assertEqual(old["attrs"]["PasswordAlias"], "off")
        self.assertEqual(old["admin_state"], "enabled")
        self.assertFalse(found[("CryptoKey", "lab-server-key")]
                         ["has_plaintext_password"])
        # A password alias: its name and state only
        alias = found[("PasswordAlias", "lab-alias")]
        self.assertEqual((alias["attrs"], alias["admin_state"]), ({}, "enabled"))
        profile = found[("SSLServerProfile", "lab-server-profile")]["attrs"]
        self.assertEqual(profile["Idcred"],
                         {"class": "CryptoIdentCred", "name": "lab-idcred"})
        self.assertEqual(profile["Protocols"]["TLSv1d3"], "on")
        self.assertEqual(profile["Ciphers"],
                         ["AES_256_GCM_SHA384", "ECDHE_RSA_WITH_AES_256_GCM_SHA384"])
        everything = repr(found)
        for secret in (PLAINTEXT_PASSWORD, ALIAS_PASSWORD, "EncryptedPassword"):
            self.assertNotIn(secret, everything)

    def test_the_references(self):
        running = data("config-running.xml")
        start = set(inventory.objects_of(running))
        edges, referrers = inventory.edges_of(running, start)
        self.assertLessEqual({
            ("CryptoIdentCred", "lab-idcred", "CryptoKey", "lab-server-key", "Key"),
            ("CryptoIdentCred", "lab-idcred", "CryptoCertificate",
             "lab-server-cert", "Certificate"),
            ("CryptoIdentCred", "lab-idcred", "CryptoCertificate", "lab-ca", "CA"),
            ("CryptoValCred", "lab-valcred", "CryptoCertificate", "lab-ca",
             "Certificate"),
            ("SSLServerProfile", "lab-server-profile", "CryptoIdentCred",
             "lab-idcred", "Idcred"),
            ("SSLClientProfile", "lab-client-profile", "CryptoValCred",
             "lab-valcred", "Valcred"),
            ("HTTPSSourceProtocolHandler", "lab-https", "SSLServerProfile",
             "lab-server-profile", "SSLServer")}, edges)
        self.assertIn(("HTTPSSourceProtocolHandler", "lab-https", "enabled"),
                      referrers)
        # Nothing refers to the unused certificate or to the alias
        self.assertFalse([e for e in edges if e[3] == "lab-old-cert"])

    def test_a_reference_of_a_reference(self):
        config = (
            '<config><CryptoCertificate name="c"/>'
            '<CryptoIdentCred name="i"><Certificate class="CryptoCertificate">'
            'c</Certificate></CryptoIdentCred>'
            '<SSLServerProfile name="p"><Idcred class="CryptoIdentCred">i'
            '</Idcred></SSLServerProfile>'
            '<HTTPSSourceProtocolHandler name="h"><SSLServer '
            'class="SSLServerProfile">p</SSLServer></HTTPSSourceProtocolHandler>'
            '<MultiProtocolGateway name="g"><FrontProtocol '
            'class="HTTPSSourceProtocolHandler">h</FrontProtocol>'
            '</MultiProtocolGateway></config>')
        start = {("CryptoCertificate", "c"), ("CryptoIdentCred", "i"),
                 ("SSLServerProfile", "p")}
        edges, referrers = inventory.edges_of(config, start)
        self.assertIn(("MultiProtocolGateway", "g", "HTTPSSourceProtocolHandler",
                       "h", "FrontProtocol"), edges)
        self.assertEqual({(c, n) for c, n, _ in referrers},
                         {("HTTPSSourceProtocolHandler", "h"),
                          ("MultiProtocolGateway", "g")})

    def test_both_methods_read_the_same_certificate(self):
        der, details = inventory.certificate_from_details(
            data("details-lab-server-cert.xml"))
        exported = inventory.certificate_from_export(
            data("export-lab-server-cert.xml"))
        self.assertEqual(der, exported)
        parsed = inventory.parse_certificate(der, details)
        self.assertEqual(parsed["sha256"], hashlib.sha256(der).hexdigest())
        self.assertEqual((parsed["subject"], parsed["issuer"]),
                         ("CN=lab.example.test", "CN=MAST lab CA"))
        self.assertEqual((parsed["key_alg"], parsed["key_bits"], parsed["curve"]),
                         ("RSA", 2048, None))
        self.assertEqual(parsed["sig_alg"], "ecdsa-with-SHA256")
        self.assertEqual(parsed["sans"], ["lab.example.test", "lab2.example.test"])
        self.assertFalse(parsed["self_signed"])
        ca = inventory.parse_certificate(*inventory.certificate_from_details(
            data("details-lab-ca.xml")))
        self.assertEqual((ca["key_alg"], ca["key_bits"], ca["curve"],
                          ca["self_signed"]), ("EC", 256, "secp256r1", True))

    def test_a_key_that_cryptography_cannot_read(self):
        """An ML-DSA key, for example: the details of DataPower say what
        it is."""
        from cryptography import x509
        der, details = inventory.certificate_from_details(
            data("details-lab-server-cert.xml"))
        details.find("SubjectPublicKeyAlgorithm").text = "ML-DSA-65"
        details.find("SubjectPublicKeyBitLength").text = "15616"
        real = x509.load_der_x509_certificate

        class Unreadable(object):
            def __init__(self, cert):
                self._cert = cert

            def __getattr__(self, name):
                return getattr(self._cert, name)

            def public_key(self):
                raise ValueError("unknown key type")

        with mock.patch.object(x509, "load_der_x509_certificate",
                               lambda raw: Unreadable(real(raw))):
            parsed = inventory.parse_certificate(der, details)
        self.assertEqual((parsed["key_alg"], parsed["key_bits"]),
                         ("ML-DSA-65", 15616))
        self.assertEqual(parsed["subject"], "CN=lab.example.test")

    def test_files(self):
        found = inventory.files_of(data("filestore-cert.xml"), "cert:")
        by_name = {(f["domain"], f["name"]) for f in found}
        self.assertIn(("default", "cert:///webgui-sscert.pem"), by_name)
        self.assertIn((DOMAIN, "cert:///lab-server.key"), by_name)
        self.assertEqual(len([f for f in found if f["domain"] == DOMAIN]), 6)
        self.assertTrue(all(f["size"] for f in found))
        for location in ("pubcert:", "sharedcert:"):
            for item in inventory.files_of(
                    data("filestore-{}.xml".format(location[:-1])), location):
                self.assertEqual(item["domain"], "default")
                self.assertTrue(item["name"].startswith(location + "///"))

    def test_the_spec_and_the_method(self):
        etc = os.path.join(tempfile.mkdtemp(dir=self.tmp), "etc")
        os.mkdir(etc)
        for name, has in (("v7000-xi52.xml", False), ("v7500-xi52.xml", True),
                          ("v2018.4.1.10-C.xml", True), ("v10.5.0.23-C.xml", True),
                          ("v11.0.0.3-C.xml", True)):
            Path(etc, name).write_text(
                "<x>{}</x>".format("do-view-certificate-details" if has else ""))
        for text, version in (("IDG.10.6.0.11", (10, 6, 0, 11)),
                              ("XI52.7.6.0.0", (7, 6, 0, 0)),
                              ("IDG.2018.4.1.12", (9, 4, 1, 12)),
                              ("unknown", None)):
            self.assertEqual(inventory.firmware_version(text), version)
        for firmware, expected in (
                ("IDG.11.0.0.3", ("etc/v11.0.0.3-C.xml", DETAILS)),
                ("IDG.10.6.0.11", ("etc/v10.5.0.23-C.xml", DETAILS)),
                ("IDG.2018.4.1.12", ("etc/v2018.4.1.10-C.xml", DETAILS)),
                ("XI52.7.6.0.0", ("etc/v7500-xi52.xml", DETAILS)),
                ("XI52.7.2.0.4", ("etc/v7000-xi52.xml", EXPORT))):
            with self.subTest(firmware=firmware):
                self.assertEqual(inventory.spec_for(firmware, etc), expected)
        self.assertEqual(inventory.spec_for("IDG.11.0.0.3", etc,
                                            "etc/v7000-xi52.xml"),
                         ("etc/v7000-xi52.xml", EXPORT))


class InventoryCase(SqliteCase):

    def setUp(self):
        super().setUp()
        registry.add("dp1", "10.0.0.1")
        registry.add("dp2", "10.0.0.2")

    def collect(self, appliance, **kwargs):
        etc = Path(os.environ["MAST_HOME"]) / "etc"
        return inventory.collect(appliance, delay=0, etc_dir=str(etc), **kwargs)


class TestCollect(InventoryCase):

    def test_both_methods_find_the_same(self):
        by_details = self.collect(FakeAppliance())
        with mock.patch.object(inventory, "spec_for",
                               return_value=("etc/v7000-xi52.xml", EXPORT)):
            exporter = FakeAppliance()
            by_export = self.collect(exporter, job_id="0123456789abcdef")
        self.assertEqual(by_details["method"], DETAILS)
        self.assertEqual(by_export["method"], EXPORT)
        self.assertEqual(set(by_details["objects"]), set(by_export["objects"]))
        self.assertEqual(by_details["certs"], by_export["certs"])
        self.assertEqual(by_details["edges"], by_export["edges"])
        self.assertEqual(len(by_details["certs"]), 3)
        # Each export had a name of this job, and was deleted
        exports = [c[1]["OutputFilename"] for c in exporter.calls
                   if c[0] == "CryptoExport"]
        self.assertEqual(len(exports), 3)
        self.assertTrue(all(e.startswith("mast-0123456789ab-") for e in exports))
        self.assertEqual(len(set(exports)), 3)
        self.assertEqual(exporter.exported, {})
        # The saved configuration does not have the lab yet
        item = by_details["objects"][(DOMAIN, "CryptoKey", "lab-old-key")]
        self.assertEqual((item["running"], item["saved"]), (True, False))
        self.assertEqual(by_details["problems"], [])

    def test_temporary_files_are_deleted_when_the_read_fails(self):
        with mock.patch.object(inventory, "spec_for",
                               return_value=("etc/v7000-xi52.xml", EXPORT)):
            appliance = FakeAppliance(fail_getfile=True)
            found = self.collect(appliance, job_id="j1")
        deleted = [c[1]["File"] for c in appliance.calls if c[0] == "DeleteFile"]
        exported = [c[1]["OutputFilename"] for c in appliance.calls
                    if c[0] == "CryptoExport"]
        self.assertEqual(deleted, ["temporary:///" + e for e in exported])
        self.assertEqual(appliance.exported, {})
        self.assertEqual(len(found["problems"]), 3)
        self.assertEqual(found["certs"], {})

    def test_the_cache(self):
        self.collect(FakeAppliance())
        with db.session(write=False) as session:
            self.assertEqual(len(session.scalars(select(CryptoCertCache)).all()), 3)
        with mock.patch.object(inventory, "parse_certificate") as parse:
            again = self.collect(FakeAppliance())
        parse.assert_not_called()
        self.assertEqual(len(again["certs"]), 3)

    def test_a_snapshot_is_kept_without_secrets(self):
        found = self.collect(FakeAppliance())
        snapshot = inventory.save(registry.by_name("dp1").id, found, T0,
                                  job_id="j1")
        with db.session(write=False) as session:
            row = session.get(CryptoSnapshot, snapshot)
            self.assertEqual((row.ok, row.method, row.firmware, row.job_id),
                             (True, DETAILS, "IDG.11.0.0.3", "j1"))
            objects = session.scalars(select(CryptoObject).where(
                CryptoObject.snapshot_id == snapshot)).all()
            certs = session.scalars(select(CryptoCert)).all()
            edges = session.scalars(select(CryptoEdge)).all()
            files = session.scalars(select(CryptoFile)).all()
        self.assertIn("lab-https", {o.name for o in objects})
        self.assertEqual(len(certs), 3)
        self.assertGreaterEqual(len(edges), 7)
        self.assertEqual(len([f for f in files if f.domain == DOMAIN]), 6)
        with db.engine().connect() as connection:
            for table in ("crypto_objects", "crypto_certs", "crypto_snapshots",
                          "crypto_cert_cache", "crypto_edges", "crypto_files"):
                dump = repr(connection.execute(
                    text("SELECT * FROM " + table)).fetchall())
                for secret in (PLAINTEXT_PASSWORD, ALIAS_PASSWORD,
                               "EncryptedPassword"):
                    self.assertNotIn(secret, dump, table)

    def test_a_failure_is_kept_too(self):
        snapshot = inventory.save(registry.by_name("dp1").id, None, T0,
                                  error="RuntimeError: away")
        with db.session(write=False) as session:
            row = session.get(CryptoSnapshot, snapshot)
        self.assertEqual((row.ok, row.error), (False, "RuntimeError: away"))


class TestCollectPostgres(PostgresMixin, TestCollect):
    pass


class FakeEnvironment(object):
    def __init__(self, hostnames, credentials=None, timeout=120,
                 check_hostname=True):
        self.appliances = [FakeAppliance(name) for name in hostnames]


class TestRetention(InventoryCase):

    def test_old_snapshots_go_but_the_newest(self):
        from datetime import timedelta
        from mast.jobs import retention
        found = self.collect(FakeAppliance())
        dp1 = registry.by_name("dp1").id
        # In the order of time, as runs make them
        old = [inventory.save(dp1, found, T0 - timedelta(days=501 - n))
               for n in range(2)]
        inventory.save(registry.by_name("dp2").id, found,
                       T0 - timedelta(days=600))
        with mock.patch.object(retention, "_prune_logs"), \
                mock.patch.object(retention, "_prune_uploads"):
            self.assertEqual(retention.prune(
                now=T0, dry_run=True)["crypto_snapshots_days"], 1)
            # dp1: the newer of its two old ones stays; dp2: its only one
            done = retention.prune(now=T0)
        self.assertEqual(done["crypto_snapshots_days"], 1)
        with db.session(write=False) as session:
            kept = {s.id for s in session.scalars(select(CryptoSnapshot))}
            objects = {o.snapshot_id for o in session.scalars(select(CryptoObject))}
        self.assertNotIn(old[0], kept)
        self.assertIn(old[1], kept)
        self.assertEqual(objects, kept)


class TestCommands(InventoryCase):

    def setUp(self):
        super().setUp()
        patcher = mock.patch("mast.plugin_utils.params.Environment",
                             FakeEnvironment)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_child_for_each_appliance(self):
        parent = jobs.submit("crypto.inventory", {"credentials": ["u:p"],
                                                  "request_delay": 0})
        self.assertTrue(jobs.get(parent).passive)
        self.assertEqual(jobs.get(parent).params["appliances"], ["dp1", "dp2"])
        jobs.run_next("w1")
        info = jobs.get(parent)
        self.assertEqual(info.status, jobs.SUCCEEDED, info.error)
        children = jobs.list_jobs(parent_id=parent)
        self.assertEqual(sorted(c.targets[0] for c in children), ["dp1", "dp2"])
        self.assertTrue(all(c.status == jobs.SUCCEEDED and c.passive
                            for c in children))
        rows = jobs.result(parent).data["rows"]
        self.assertEqual([r[0] for r in rows], ["dp1", "dp2"])
        self.assertEqual(rows[0][4:7], ["details", "10", "3"])
        with db.session(write=False) as session:
            snapshots = session.scalars(select(CryptoSnapshot)).all()
        self.assertEqual({s.job_id for s in snapshots}, {c.id for c in children})

    def test_without_a_job(self):
        result = inventory.inventory(appliances=["dp1"], credentials=["u:p"],
                                     request_delay=0)
        self.assertEqual(result.data()["rows"][0][:2], ["dp1", "yes"])

    def test_no_appliance_is_registered(self):
        registry.remove("dp1")
        registry.remove("dp2")
        with self.assertRaises(Exception) as found:
            jobs.submit("crypto.inventory", {"credentials": ["u:p"]})
        self.assertIn("no registered appliance", str(found.exception))


class TestMigration(InventoryCase):

    def tables(self):
        from sqlalchemy import inspect
        engine = db._create_engine(self.url)
        self.addCleanup(engine.dispose)
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())

    def test_down_and_up(self):
        found = self.collect(FakeAppliance())
        inventory.save(registry.by_name("dp1").id, found, T0)
        db.downgrade("0008")
        self.assertEqual(db.revision(), "0008")
        self.assertFalse({t for t in self.tables() if t.startswith("crypto_")})
        db.configure(self.url)
        self.assertEqual([a.name for a in registry.list_appliances()],
                         ["dp1", "dp2"])
        self.assertEqual(db.revision(), db.HEAD)
        inventory.save(registry.by_name("dp1").id, found, T0)

    def test_removing_an_appliance_removes_its_snapshots(self):
        found = self.collect(FakeAppliance())
        inventory.save(registry.by_name("dp1").id, found, T0)
        registry.remove("dp1")
        with db.session(write=False) as session:
            self.assertEqual(session.scalars(select(CryptoSnapshot)).all(), [])
            self.assertEqual(session.scalars(select(CryptoObject)).all(), [])


class TestMigrationPostgres(PostgresMixin, TestMigration):
    pass


SCHEMAS = Path(__file__).resolve().parents[1] / "data" / "cyclonedx"


def cyclonedx_errors(document):
    """The errors of `document` against the CycloneDX 1.6 schema."""
    import json
    from jsonschema import Draft7Validator
    from referencing import Registry, Resource
    schemas = {name: json.loads((SCHEMAS / name).read_text())
               for name in ("bom-1.6.schema.json", "spdx.schema.json",
                            "jsf-0.82.schema.json")}
    resources = [(n, Resource.from_contents(s)) for n, s in schemas.items()]
    resources += [(s["$id"], Resource.from_contents(s))
                  for s in schemas.values() if "$id" in s]
    validator = Draft7Validator(schemas["bom-1.6.schema.json"],
                                registry=Registry().with_resources(resources))
    return list(validator.iter_errors(document))


class TestExport(InventoryCase):

    def setUp(self):
        super().setUp()
        self.first = inventory.save(registry.by_name("dp1").id,
                                    self.collect(FakeAppliance()), T0)
        self.newest = inventory.save(registry.by_name("dp1").id,
                                     self.collect(FakeAppliance()), T0)
        # A failed run is not the newest snapshot to export
        inventory.save(registry.by_name("dp1").id, None, T0, error="away")

    def test_a_cbom_that_the_schema_accepts(self):
        from mast.datapower.crypto import export
        snapshots = export.load()
        self.assertEqual([s["snapshot"].id for s in snapshots], [self.newest])
        document = export.cbom(snapshots)
        self.assertEqual(cyclonedx_errors(document), [])
        by_name = {c["name"]: c for c in document["components"]}
        cert = by_name["lab-server-cert"]["cryptoProperties"]
        self.assertEqual(cert["assetType"], "certificate")
        self.assertEqual(cert["certificateProperties"]["subjectName"],
                         "CN=lab.example.test")
        refs = {c["bom-ref"] for c in document["components"]}
        self.assertEqual(cert["certificateProperties"]["subjectPublicKeyRef"],
                         "algorithm/RSA-2048")
        self.assertIn("algorithm/RSA-2048", refs)
        profile = by_name["lab-server-profile"]["cryptoProperties"]
        self.assertEqual(profile["protocolProperties"]["version"], "1.3")
        self.assertEqual(by_name["lab-old-key"]["cryptoProperties"]
                         ["relatedCryptoMaterialProperties"]["type"],
                         "private-key")
        self.assertIn({"name": "mast:plaintextPassword", "value": "true"},
                      by_name["lab-old-key"]["properties"])
        self.assertEqual(by_name["lab-https"]["type"], "application")
        dependencies = {d["ref"]: d["dependsOn"] for d in document["dependencies"]}
        self.assertEqual(dependencies["dp1/cryptolab/HTTPSSourceProtocolHandler/"
                                      "lab-https"],
                         ["dp1/cryptolab/SSLServerProfile/lab-server-profile"])
        # The check of the schema does find a broken document
        by_name["lab-server-cert"]["cryptoProperties"]["assetType"] = "magic"
        self.assertTrue(cyclonedx_errors(document))

    def test_each_format_through_the_queue(self):
        import csv
        import json
        import openpyxl
        directory = tempfile.mkdtemp(dir=self.tmp)
        for name in ("cbom", "xlsx", "csv"):
            out = os.path.join(directory, "inventory")
            job = jobs.submit("crypto.export", {"format": name, "out_file": out,
                                                "snapshot": self.first})
            jobs.run_next("w1")
            self.assertEqual(jobs.get(job).status, jobs.SUCCEEDED,
                             jobs.get(job).error)
            path = out + inventory_export().FORMATS[name]
            with open(path, "rb") as fin:
                raw = fin.read()
            for secret in (PLAINTEXT_PASSWORD.encode(), ALIAS_PASSWORD.encode()):
                self.assertNotIn(secret, raw)
            if name == "cbom":
                self.assertEqual(cyclonedx_errors(json.loads(raw)), [])
            elif name == "xlsx":
                book = openpyxl.load_workbook(path)
                self.assertEqual(book.sheetnames,
                                 ["Certificates", "Keys", "Credentials",
                                  "TLS profiles", "Files", "Findings"])
                self.assertEqual(book["Certificates"].max_row, 4)
            else:
                with open(path, newline="") as fin:
                    found = list(csv.reader(fin))
                self.assertEqual(found[0][:3], ["Appliance", "Domain", "Object"])
                self.assertEqual(sorted(r[2] for r in found[1:]),
                                 ["lab-ca", "lab-old-cert", "lab-server-cert"])

    def test_nothing_to_export(self):
        with self.assertRaises(Exception):
            jobs.submit("crypto.export", {"format": "pdf"})
        registry.remove("dp1")
        job = jobs.submit("crypto.export", {"format": "csv"})
        jobs.run_next("w1")
        self.assertIn("no snapshot", jobs.get(job).error)


def inventory_export():
    from mast.datapower.crypto import export
    return export
