# -*- coding: utf-8 -*-
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
Tests for the certificate of the web GUI: the message when it is
missing, and `contrib/gen-cert.py`, which makes one.
"""
import importlib.util
import ipaddress
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from mast.config import MAST_HOME
from mast.datapower.web import gui

GEN_CERT = os.path.join(MAST_HOME, "contrib", "gen-cert.py")


class TestCheckCertificate(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mast-cert-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        for name, value in (("secure", True), ("MAST_HOME", self.home),
                            ("cert", "etc/crypto/cert/mast.crt"),
                            ("key", "etc/crypto/private/mast.key"),
                            ("cacert", None)):
            patcher = mock.patch.object(gui, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def touch(self, path):
        full = os.path.join(self.home, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").close()
        return full

    def test_a_fresh_install_gets_a_message_with_both_ways_out(self):
        with self.assertRaises(gui.CertificateNotFound) as caught:
            gui.check_certificate()
        message = str(caught.exception)
        self.assertIn("cert = etc/crypto/cert/mast.crt is not there", message)
        self.assertIn("key = etc/crypto/private/mast.key is not there", message)
        self.assertIn("etc/local/server.conf", message)
        self.assertIn("contrib", message)
        self.assertIn("gen-cert.py", message)
        self.assertIn(self.home, message)

    def test_only_the_missing_file_is_named(self):
        self.touch("etc/crypto/cert/mast.crt")
        with self.assertRaises(gui.CertificateNotFound) as caught:
            gui.check_certificate()
        self.assertNotIn("cert = ", str(caught.exception))
        self.assertIn("key = ", str(caught.exception))

    @unittest.skipIf(sys.platform == "win32" or os.geteuid() == 0,
                     "POSIX permissions, not as root")
    def test_a_key_that_cannot_be_read(self):
        self.touch("etc/crypto/cert/mast.crt")
        os.chmod(self.touch("etc/crypto/private/mast.key"), 0)
        with self.assertRaises(gui.CertificateNotFound) as caught:
            gui.check_certificate()
        self.assertIn("mast.key cannot be read", str(caught.exception))

    def test_a_cacert_that_is_set_is_checked(self):
        self.touch("etc/crypto/cert/mast.crt")
        self.touch("etc/crypto/private/mast.key")
        with mock.patch.object(gui, "cacert", "etc/crypto/cert/ca.crt"):
            with self.assertRaises(gui.CertificateNotFound) as caught:
                gui.check_certificate()
        self.assertIn("cacert = etc/crypto/cert/ca.crt", str(caught.exception))

    def test_no_error_when_the_files_are_there(self):
        self.touch("etc/crypto/cert/mast.crt")
        self.touch("etc/crypto/private/mast.key")
        gui.check_certificate()

    def test_absolute_paths(self):
        cert = self.touch("elsewhere/c.crt")
        key = self.touch("elsewhere/k.key")
        with mock.patch.object(gui, "cert", cert), mock.patch.object(gui, "key", key):
            gui.check_certificate()

    def test_no_check_without_tls(self):
        with mock.patch.object(gui, "secure", False):
            gui.check_certificate()


class TestMastdPlugin(unittest.TestCase):

    def test_mastd_logs_the_message_and_goes_on_without_the_web_gui(self):
        import mast.datapower.web as web
        problem = gui.CertificateNotFound("no certificate here")
        with mock.patch.object(web, "main", side_effect=problem), \
                self.assertLogs("mast.datapower.web", "ERROR") as logs:
            web.Plugin().run()
        self.assertIn("no certificate here", "\n".join(logs.output))


class TestGenCert(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mast-gen-cert-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.key = os.path.join(self.dir, "private", "mast.key")
        self.cert = os.path.join(self.dir, "cert", "mast.crt")

    def gen_cert(self, *args):
        return subprocess.run(
            [sys.executable, GEN_CERT, "-K", self.key, "--CERT-FILE", self.cert] + list(args),
            cwd=MAST_HOME, capture_output=True, text=True, timeout=120)

    def load(self):
        with open(self.cert, "rb") as fin:
            cert = x509.load_pem_x509_certificate(fin.read())
        with open(self.key, "rb") as fin:
            key = serialization.load_pem_private_key(fin.read(), password=None)
        return cert, key

    def test_it_makes_a_certificate_and_its_key(self):
        proc = self.gen_cert("-a", "mast.example.com", "-a", "10.0.0.5", "-C", "US")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Restart the web GUI", proc.stdout)
        cert, key = self.load()
        public = serialization.PublicFormat.SubjectPublicKeyInfo
        self.assertEqual(
            cert.public_key().public_bytes(serialization.Encoding.PEM, public),
            key.public_key().public_bytes(serialization.Encoding.PEM, public))
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertIn("localhost", names.get_values_for_type(x509.DNSName))
        self.assertIn("mast.example.com", names.get_values_for_type(x509.DNSName))
        addresses = names.get_values_for_type(x509.IPAddress)
        for address in ("127.0.0.1", "::1", "10.0.0.5"):
            self.assertIn(ipaddress.ip_address(address), addresses)
        self.assertEqual(cert.subject.get_attributes_for_oid(
            x509.oid.NameOID.COUNTRY_NAME)[0].value, "US")
        # Only the fields that were given are in the subject
        self.assertEqual(cert.subject.get_attributes_for_oid(
            x509.oid.NameOID.ORGANIZATION_NAME), [])
        self.assertGreater(cert.serial_number, 0)

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_only_the_owner_can_read_the_key(self):
        self.assertEqual(self.gen_cert().returncode, 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.key).st_mode), 0o600)

    def test_a_key_that_is_there_is_not_replaced(self):
        self.assertEqual(self.gen_cert().returncode, 0)
        with open(self.key, "rb") as fin:
            before = fin.read()
        proc = self.gen_cert()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--force", proc.stderr)
        with open(self.key, "rb") as fin:
            self.assertEqual(fin.read(), before)

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_force_replaces_them_and_protects_an_old_key_file(self):
        os.makedirs(os.path.dirname(self.key))
        with open(self.key, "w") as fout:
            fout.write("old")
        os.chmod(self.key, 0o644)
        proc = self.gen_cert("--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(self.key).st_mode), 0o600)
        self.load()

    def test_a_bad_option_exits_2(self):
        self.assertEqual(self.gen_cert("--serialNumber", "x").returncode, 2)

    def test_a_subject_field_that_is_too_long_exits_2_and_names_the_option(self):
        proc = self.gen_cert("--commonName", "a" * 65)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("--commonName", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertFalse(os.path.exists(self.key))

    def test_the_default_common_name_fits_in_a_certificate(self):
        # The fully qualified name of a computer can be longer than the 64
        # characters of a common name (GitHub runners have such names).
        spec = importlib.util.spec_from_file_location("gen_cert", GEN_CERT)
        gen_cert = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen_cert)
        long_name = "runner-" + "x" * 60 + ".example.net"
        self.assertEqual(gen_cert.default_common_name("dp.example.net", "dp"),
                         "dp.example.net")
        self.assertEqual(gen_cert.default_common_name(long_name, "runner"), "runner")
        self.assertEqual(gen_cert.default_common_name(long_name, "y" * 65), "localhost")


if __name__ == "__main__":
    unittest.main()
