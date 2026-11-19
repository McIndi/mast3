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
Unit tests for the commands of mast.datapower.crypto, with the
appliances replaced by fakes.
"""
import contextlib
import io
import os
import tempfile
import unittest
from xml.etree import ElementTree as etree

from unittest import mock
import openpyxl

import mast.datapower.crypto as crypto
from mast.cli import Table, captured
from mast.plugin_utils.results import Report, SeeDownload

ENVELOPE = (
    '<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">'
    '<env:Body>'
    '<dp:response xmlns:dp="http://www.datapower.com/schemas/management">'
    '<dp:{kind}>{body}</dp:{kind}>'
    '</dp:response></env:Body></env:Envelope>')

CERTS = (
    '<CryptoCertificate name="enabled-cert">'
    '<mAdminState>enabled</mAdminState>'
    '<Filename>cert:///enabled.pem</Filename></CryptoCertificate>'
    '<CryptoCertificate name="disabled-cert">'
    '<mAdminState>disabled</mAdminState>'
    '<Filename>cert:///disabled.pem</Filename></CryptoCertificate>')

FILESTORE = (
    '<location name="cert:"><file name="a.pem"><size>10</size>'
    '<modified>2026-01-01</modified></file>'
    '<directory name="cert:/sub"><file name="b.pem"><size>20</size>'
    '<modified>2026-01-02</modified></file></directory></location>')


class FakeResponse(object):
    def __init__(self, xml):
        self.xml = etree.fromstring(xml)


class FakeAppliance(object):
    hostname = "dp1"
    history = "request: x\nresponse: y"
    domains = ["default", "app"]

    def __init__(self):
        self.calls = []

    def get_config(self, _class, domain, persisted=True):
        self.calls.append(("get_config", domain))
        return FakeResponse(ENVELOPE.format(kind="config", body=CERTS))

    def get_certificate_details(self, domain, certificate_name):
        raise RuntimeError("no details in this test")

    def get_filestore(self, domain, location):
        if location != "cert:":
            return FakeResponse(ENVELOPE.format(kind="filestore", body=""))
        return FakeResponse(ENVELOPE.format(kind="filestore", body=FILESTORE))

    def CryptoExport(self, **kwargs):
        self.calls.append(("CryptoExport", kwargs["ObjectName"]))

    def getfile(self, domain, filename):
        return b"<crypto-export><certificate>" + b"A" * 100 + \
            b"</certificate></crypto-export>"

    def DeleteFile(self, domain, File):
        self.calls.append(("DeleteFile", File))


class FakeEnvironment(object):
    def __init__(self, hostnames, *args, **kwargs):
        self.appliances = [FakeAppliance()]


class TestCryptoCommands(unittest.TestCase):

    def setUp(self):
        for target, new in (
                ("mast.plugin_utils.params.Environment", FakeEnvironment),
                ("mast.datapower.crypto.make_logger", mock.Mock()),
                ("mast.datapower.crypto.sleep", mock.Mock())):
            patch = mock.patch(target, new)
            patch.start()
            self.addCleanup(patch.stop)
        self.out_dir = tempfile.mkdtemp(dir=os.path.join(
            os.environ["MAST_HOME"], "tmp"))

    def call(self, command, **kwargs):
        with captured() as messages:
            result = command(appliances=["dp1"], **kwargs)
        return result, messages

    def test_cert_audit(self):
        for extension in (".xlsx", ".csv"):
            with self.subTest(extension=extension):
                out_file = os.path.join(self.out_dir, "audit" + extension)
                result, messages = self.call(
                    crypto.cert_audit, domains=["all-domains"],
                    out_file=out_file)
                self.assertIsInstance(result, Report)
                # The terminal gets the progress and the name of the file
                self.assertEqual("", result.text())
                self.assertIn("dp1", messages)
                self.assertIn(out_file, messages[-1])
                # Only the enabled certificate, once for each domain
                html = result.html()
                self.assertEqual(2, html.count(">enabled-cert<"))
                self.assertNotIn("disabled-cert", html)
                self.assertIn(">certificate-object<", html)
                self.assertIn("request: x", result.history)
                self.assertTrue(os.path.getsize(out_file))
        workbook = openpyxl.load_workbook(
            os.path.join(self.out_dir, "audit.xlsx"))
        rows = list(workbook.active.values)
        self.assertEqual("appliance", rows[0][0])
        self.assertEqual(
            ("dp1", "default", "enabled-cert", None, "cert:///enabled.pem"),
            rows[1][:5])

        with self.assertRaises(ValueError):
            self.call(crypto.cert_audit, domains=["app"],
                      out_file=os.path.join(self.out_dir, "audit.txt"))

    def test_cert_file_audit(self):
        out_file = os.path.join(self.out_dir, "files.xlsx")
        result, _ = self.call(crypto.cert_file_audit, out_file=out_file)
        self.assertIsInstance(result, Table)
        self.assertEqual(
            ["appliance", "domain", "directory", "filename", "size",
             "modified"], result.header)
        self.assertEqual(
            [["dp1", "default", "cert:", "a.pem", "10", "2026-01-01"],
             ["dp1", "default", "cert:/sub", "b.pem", "20", "2026-01-02"]],
            result.rows)
        self.assertEqual(
            3, len(list(openpyxl.load_workbook(out_file).active.values)))

    def test_export_certs(self):
        # export-certs only makes its directories under a relative out_dir
        result, messages = self.call(
            crypto.export_certs, domains=["app"],
            out_dir=os.path.relpath(self.out_dir))
        self.assertIsInstance(result, SeeDownload)
        self.assertIn("dp1-export-certs", result.html())
        path = os.path.join(self.out_dir, "dp1", "app", "cert", "enabled.pem")
        with open(path) as fin:
            lines = fin.read().splitlines()
        self.assertEqual("-----BEGIN CERTIFICATE-----", lines[0])
        self.assertEqual(["A" * 64, "A" * 36], lines[1:3])
        self.assertEqual(
            [("get_config", "app"), ("CryptoExport", "enabled-cert"),
             ("DeleteFile", "temporary:///enabled-cert")],
            result.env.appliances[0].calls)
        self.assertIn("\t\t enabled-cert", messages)

    def test_command_line(self):
        out_file = os.path.join(self.out_dir, "files.xlsx")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            crypto.cli.run(["cert-file-audit", "-a", "dp1", "-o", out_file])
            crypto.cli.run(["cert-audit", "-a", "dp1", "-d", "app", "-D", "0",
                            "-l", "--days-only", "-o",
                            os.path.join(self.out_dir, "audit.csv")])
        text = out.getvalue()
        self.assertRegex(text, r"dp1 +default +cert:/sub +b\.pem +20")
        self.assertIn("Certificate Report (available at", text)

    def test_no_command_takes_a_web_parameter(self):
        self.assertEqual(3, len(crypto.cli.commands))
        for command in crypto.cli.commands.values():
            self.assertIsNotNone(command.model, command.name)
            self.assertNotIn("web", command.model.model_fields)
