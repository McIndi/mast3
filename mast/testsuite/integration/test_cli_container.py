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
Integration tests for the MAST command line programs.

These tests run the same commands as `manual-tests.md`, each one in a
new Python process, against the shared DataPower container. Each test
then asks the gateway whether the command did what it reported.

The commands run with a private MAST_HOME, so that the tests can add
the container to `hosts.conf`, `appliances.conf` and
`environments.conf` and leave the real configuration alone.

By default the commands run as `python -m mast.datapower.<module>` with
the interpreter of the tests. If MAST_TEST_HOME names a MAST home (a
development home made by `./mast-setup --development-mode`, or an
unzipped installer), the commands run through the launchers of that
home (`<home>/mast-<module>`). The private home is then a copy of that
home without its runtime files. The interpreter is linked, not copied.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from mast.testsuite.integration.container import (
    PASSWORD,
    SOMA_PORT,
    SSH_PORT,
    USERNAME,
    WEB_PORT,
)
from mast.testsuite.integration.homes import TEST_HOME, copy_home
from mast.testsuite.integration.test_datapower_container import (
    ContainerTestCase,
    GOOD_PASSWORD,
)

ALIAS = "dp1"
ENVIRONMENT = "lab"
CREDENTIALS = "{}:{}".format(USERNAME, PASSWORD)


class CLITestCase(ContainerTestCase):
    """Run MAST commands against the container."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mast_home = os.path.join(cls.workdir, "mast_home")
        if TEST_HOME:
            copy_home(os.path.abspath(TEST_HOME), cls.mast_home)
        else:
            shutil.copytree(
                os.path.join(os.environ["MAST_HOME"], "etc"),
                os.path.join(cls.mast_home, "etc"),
                ignore=shutil.ignore_patterns("local"))
        local = os.path.join(cls.mast_home, "etc", "local")
        for name in (local, "tmp", os.path.join("var", "log"),
                     os.path.join("var", "run")):
            os.makedirs(os.path.join(cls.mast_home, name), exist_ok=True)
        ports = cls.container.ports
        cls._write(local, "hosts.conf", "[hosts]\n{}: 127.0.0.1\n".format(ALIAS))
        cls._write(local, "environments.conf", "[{}]\nappliances: {}\n".format(
            ENVIRONMENT, ALIAS))
        cls._write(local, "appliances.conf", (
            "[{}]\nsoma_port: {}\nweb_port: {}\nssh_port: {}\n".format(
                ALIAS, ports[SOMA_PORT], ports[WEB_PORT], ports[SSH_PORT])))
        cls._import()

    @classmethod
    def _import(cls):
        """Move the three files into the appliance registry of the home,
        as a user of an older MAST does once."""
        if TEST_HOME:
            command = [os.path.join(cls.mast_home, "mast"
                                    + (".bat" if sys.platform == "win32"
                                       else "")), "targets"]
            env = {k: v for k, v in os.environ.items() if k != "MAST_HOME"}
        else:
            command = [sys.executable, "-m", "mast", "targets"]
            env = dict(os.environ, MAST_HOME=cls.mast_home)
        proc = subprocess.run(
            command + ["import"], cwd=cls.mast_home, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=120)
        if proc.returncode != 0 or "Added the environment" not in proc.stdout:
            raise AssertionError("mast targets import failed:\n"
                                 + proc.stdout)

    def launcher(self, name):
        """The path of a launcher of the private home."""
        if sys.platform == "win32":
            name += ".bat"
        return os.path.join(self.mast_home, name)

    @staticmethod
    def _write(directory, name, contents):
        with open(os.path.join(directory, name), "w") as fout:
            fout.write(contents)

    def mast(self, *args, credentials=CREDENTIALS, appliance=ENVIRONMENT):
        """Run `mast datapower <args>` and return its output. The test
        fails if the command does not exit with status 0."""
        if TEST_HOME:
            # The launcher sets MAST_HOME and changes to the home.
            command = [self.launcher("mast"), "datapower"]
            env = dict(os.environ)
            env.pop("MAST_HOME", None)
        else:
            command = [sys.executable, "-m", "mast", "datapower"]
            env = dict(os.environ, MAST_HOME=self.mast_home)
        name = "mast datapower {}".format(" ".join(args))
        command += list(args)
        if appliance:
            command += ["-a", appliance, "-c", credentials, "-n"]
        proc = subprocess.run(
            command,
            cwd=self.mast_home,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=300)
        self.assertEqual(
            0, proc.returncode,
            "{} failed:\n{}".format(name, proc.stdout))
        return proc.stdout

    def path(self, *parts):
        return os.path.join(self.mast_home, *parts)

    def domain_names(self):
        status = self.dp.get_status("DomainStatus")
        return [x.text for x in status.xml.findall(".//Domain")]


class TestSystemCLI(CLITestCase):

    @unittest.skipUnless(TEST_HOME, "MAST_TEST_HOME is not set")
    def test_commands_run_through_the_launchers(self):
        # The launcher `mast-python` runs the interpreter of the home
        # with its arguments, after it changed to the home.
        proc = subprocess.run(
            [self.launcher("mast-python"), "-c",
             "import os, sys; print(sys.executable); print(os.getcwd())"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stdout)
        executable, cwd = proc.stdout.strip().splitlines()[-2:]
        home = os.path.realpath(self.mast_home)
        self.assertTrue(
            os.path.realpath(os.path.dirname(executable)).startswith(
                os.path.realpath(os.path.join(TEST_HOME, "python"))),
            executable)
        self.assertTrue(executable.startswith(self.mast_home), executable)
        self.assertEqual(os.path.realpath(cwd), home)

    def test_xor(self):
        encoded = self.mast(
            "xor", "-s", CREDENTIALS, appliance=None).strip()
        self.assertNotIn(PASSWORD, encoded)
        # The encoded form is accepted in place of the credentials.
        out = self.mast("list-domains", credentials=encoded)
        self.assertIn("default", out)

    def test_domain_commands(self):
        self.addCleanup(self.dp.del_domain, "cli_domain")
        if "cli_domain" in self.domain_names():
            self.dp.del_domain("cli_domain")

        self.mast("add-domain", "-d", "cli_domain", "-s")
        self.assertIn("cli_domain", self.domain_names())
        out = self.mast("list-domains")
        self.assertIn("cli_domain", out)
        self.assertIn("Common", out)

        out = self.mast("save", "-D", "cli_domain")
        self.assertIn("OK", out)

        self.mast("del-domain", "-D", "cli_domain", "-s")
        self.assertNotIn("cli_domain", self.domain_names())

    def test_file_commands(self):
        self.fresh_domain("cli_files")
        local = self.local_file("readme.txt", b"read me\n")

        out = self.mast(
            "create-dir", "-D", "cli_files", "-d", "local:///dir/")
        self.assertIn("OK", out)
        self.mast(
            "set-file", "-D", "cli_files",
            "-f", local, "-d", "local:///dir/")
        self.assertEqual(
            b"read me\n",
            self.dp.getfile("cli_files", "local:///dir/readme.txt"))

        self.mast(
            "get-filestore", "-D", "cli_files",
            "-l", "local:", "-o", "tmp")
        listings = glob.glob(self.path("tmp", "*", ALIAS + "-get-filestore.xml"))
        self.assertEqual(1, len(listings))
        with open(listings[0]) as fin:
            self.assertIn("readme.txt", fin.read())

        self.mast(
            "get-file", "-D", "cli_files",
            "-l", "local:///dir/readme.txt", "-o", "tmp/got")
        found = glob.glob(self.path("tmp", "got", ALIAS + "-*-readme.txt"))
        self.assertEqual(1, len(found))
        with open(found[0], "rb") as fin:
            self.assertEqual(b"read me\n", fin.read())

        self.mast(
            "del-file", "-D", "cli_files",
            "-f", "local:///dir/readme.txt", "-b", "-o", "tmp/deleted")
        self.assertFalse(
            self.dp.file_exists("local:///dir/readme.txt", "cli_files"))
        backups = glob.glob(
            self.path("tmp", "deleted", "**", "readme.txt"), recursive=True)
        self.assertEqual(1, len(backups))


class TestAccountsCLI(CLITestCase):

    def test_user_and_group_commands(self):
        self.remove_leftovers(
            users=["cli_user", "cli_priv"], groups=["cli_group"])

        self.mast("add-group", "-N", "cli_group", "-s")
        self.assertIn("cli_group", self.dp.groups)

        self.mast(
            "add-user", "-u", "cli_user",
            "-p", GOOD_PASSWORD, "-g", "cli_group", "-s")
        self.mast(
            "add-user", "-u", "cli_priv",
            "-p", GOOD_PASSWORD, "-P", "-s")
        out = self.mast("list-users")
        self.assertIn("cli_user", out)
        self.assertIn("cli_priv", out)

        out = self.mast("force-change-password", "-U", "cli_user")
        self.assertIn("OK", out)

        self.mast("del-user", "-U", "cli_user", "-s")
        self.mast("del-user", "-U", "cli_priv", "-s")
        self.assertNotIn("cli_user", self.dp.users)
        self.assertNotIn("cli_priv", self.dp.users)

        self.mast("del-group", "-U", "cli_group", "-s")
        self.assertNotIn("cli_group", self.dp.groups)


class TestBackupsCLI(CLITestCase):

    def test_normal_backup_and_restore(self):
        self.fresh_domain("cli_backup")
        out = self.mast(
            "normal-backup", "-D", "cli_backup",
            "-C", "Daily Backup", "-o", "tmp")
        self.assertIn("Verified", out)
        backups = glob.glob(self.path(
            "tmp", ALIAS, "NormalBackup", "*", "*-cli_backup.zip"))
        self.assertEqual(1, len(backups))
        with zipfile.ZipFile(backups[0]) as archive:
            self.assertIsNone(archive.testzip())

        self.mast(
            "restore-normal-backup",
            "-f", backups[0], "-D", "cli_backup")
        self.assertIn("cli_backup", self.domain_names())

    def test_checkpoint_commands(self):
        self.fresh_domain("cli_chk")
        out = self.mast(
            "set-checkpoint", "-D", "cli_chk", "-C", "cli")
        self.assertIn("Successful", out)
        checkpoints = self.wait_for(
            lambda: self.dp.get_existing_checkpoints("cli_chk"))
        name = list(checkpoints)[0]
        self.assertTrue(name.startswith("cli-cli_chk-"))

        out = self.mast("list-checkpoints", "-D", "cli_chk")
        self.assertIn(name, out)

        self.mast(
            "remove-checkpoint", "-D", "cli_chk", "-C", name)
        self.wait_for(
            lambda: not self.dp.get_existing_checkpoints("cli_chk"))


class TestDeveloperCLI(CLITestCase):

    def test_export_and_import(self):
        self.fresh_domain("cli_dev")
        self.mast(
            "export", "-D", "cli_dev",
            "-o", "all-objects", "-O", "all-classes",
            "-C", "cli export", "--out-dir", "tmp/export")
        exports = glob.glob(
            self.path("tmp", "export", "**", "*.zip"), recursive=True)
        self.assertEqual(1, len(exports))
        with zipfile.ZipFile(exports[0]) as archive:
            self.assertIn(b"cli export", archive.read("export.xml"))

        self.mast(
            "import", "-D", "cli_dev",
            "-f", exports[0], "-o", "tmp/import")


class TestDeploymentCLI(CLITestCase):

    def test_get_file(self):
        self.fresh_domain("cli_deploy")
        local = self.local_file("deployed.txt", b"deployed\n")
        self.dp.set_file(local, "local:", "cli_deploy")
        self.mast(
            "get-file", "-D", "cli_deploy",
            "-l", "local:///deployed.txt", "-o", "tmp/deploy")
        found = glob.glob(
            self.path("tmp", "deploy", "**", "*deployed.txt"), recursive=True)
        self.assertEqual(1, len(found))
        with open(found[0], "rb") as fin:
            self.assertEqual(b"deployed\n", fin.read())


class TestNetworkCLI(CLITestCase):

    def test_check_connectivity(self):
        out = self.mast("check-connectivity")
        row = [line for line in out.splitlines() if line.startswith(ALIAS)]
        self.assertEqual(1, len(row))
        self.assertEqual([ALIAS, "True", "True", "True"], row[0].split())

    def test_host_alias_commands(self):
        self.addCleanup(self.dp.del_host_alias, "cli_alias")
        self.mast(
            "add-host-alias", "-N", "cli_alias", "-i", "127.0.0.1")
        out = self.mast("list-host-aliases")
        # One row for the appliance and one for what is common
        self.assertRegex(
            out, r"(?m)^{} +cli_alias +127\.0\.0\.1 +enabled$".format(ALIAS))
        self.assertRegex(out, r"(?m)^common +cli_alias +127\.0\.0\.1 +enabled$")

        self.mast("del-host-alias", "-H", "cli_alias")
        names = [alias[0] for alias in self.dp.get_host_aliases()]
        self.assertNotIn("cli_alias", names)


class _TLSProxy(object):
    """A TLS server on 127.0.0.1 with a certificate for that address,
    signed by a certificate authority of the test, that passes each
    connection on to the SOMA port of the container (whose certificate
    is self-signed and not for 127.0.0.1). A client that trusts the
    authority (SSL_CERT_FILE) so checks the certificate as it would
    for a real appliance, until MAST can pin a certificate."""

    def __init__(self, directory, target_port):
        import datetime
        import ipaddress
        import socket
        import ssl
        import threading
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        def name(text):
            return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])

        now = datetime.datetime.now(datetime.timezone.utc)
        ca_key = ec.generate_private_key(ec.SECP256R1())
        ca = (x509.CertificateBuilder()
              .subject_name(name("mast test CA")).issuer_name(name("mast test CA"))
              .public_key(ca_key.public_key())
              .serial_number(x509.random_serial_number())
              .not_valid_before(now - datetime.timedelta(minutes=5))
              .not_valid_after(now + datetime.timedelta(days=1))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
              .add_extension(x509.KeyUsage(
                  digital_signature=True, key_cert_sign=True, crl_sign=True,
                  content_commitment=False, key_encipherment=False,
                  data_encipherment=False, key_agreement=False,
                  encipher_only=False, decipher_only=False), True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(
                  ca_key.public_key()), False)
              .sign(ca_key, hashes.SHA256()))
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (x509.CertificateBuilder()
                .subject_name(name("127.0.0.1")).issuer_name(ca.subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                .add_extension(x509.ExtendedKeyUsage(
                    [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()), False)
                .sign(ca_key, hashes.SHA256()))
        self.ca_file = os.path.join(directory, "ca.pem")
        cert_file = os.path.join(directory, "proxy.pem")
        with open(self.ca_file, "wb") as fout:
            fout.write(ca.public_bytes(serialization.Encoding.PEM))
        with open(cert_file, "wb") as fout:
            fout.write(cert.public_bytes(serialization.Encoding.PEM))
            fout.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
        self._server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._server.load_cert_chain(cert_file)
        self._client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._client.check_hostname = False
        self._client.verify_mode = ssl.CERT_NONE
        self._target = target_port
        self._socket = socket.create_server(("127.0.0.1", 0))
        self.port = self._socket.getsockname()[1]
        self._socket_module = socket
        self._threading = threading
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                raw, _ = self._socket.accept()
            except OSError:
                return
            self._threading.Thread(
                target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw):
        # One thread moves the data both ways. A TLS socket must not be
        # read in one thread while another thread writes to it: that
        # can stop the connection at random.
        import select
        import ssl
        raw.settimeout(30)
        try:
            inbound = self._server.wrap_socket(raw, server_side=True)
        except OSError:
            raw.close()
            return
        try:
            outbound = self._client.wrap_socket(
                self._socket_module.create_connection(
                    ("127.0.0.1", self._target), timeout=30))
        except OSError:
            inbound.close()
            return
        peers = {inbound: outbound, outbound: inbound}
        wait = (ssl.SSLWantReadError, ssl.SSLWantWriteError)

        def send(sink, data):
            view = memoryview(data)
            while view:
                try:
                    view = view[sink.send(view):]
                except wait:
                    select.select([sink], [sink], [], 30)

        try:
            for end in peers:
                end.setblocking(False)
            while True:
                # Data that TLS has already read is not seen by select
                ready = [end for end in peers if end.pending()]
                if not ready:
                    ready = select.select(list(peers), [], [], 300)[0]
                    if not ready:
                        return
                for source in ready:
                    try:
                        data = source.recv(65536)
                    except wait:
                        continue
                    if not data:
                        return
                    send(peers[source], data)
        except OSError:
            pass
        finally:
            for end in peers:
                try:
                    end.close()
                except OSError:
                    pass

    def close(self):
        self._socket.close()


class StoredCredentialCase(CLITestCase):
    """A DataPower user that may only read, the TLS proxy, and helpers
    to run the MAST programs."""

    USER = "mast_collector"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.proxy = _TLSProxy(cls.workdir, cls.container.ports[SOMA_PORT])
        cls.addClassCleanup(cls.proxy.close)

    def setUp(self):
        self.remove_leftovers(users=[self.USER], groups=[self.USER])
        self.assertTrue(self.dp.add_group(
            self.USER, access_policies=["*/*/*?Access=r"]))
        self.assertTrue(self.dp.add_user(
            self.USER, GOOD_PASSWORD, user_group=self.USER,
            supress_force_password_change=True))

    def program(self, root, *args, stdin=None, trust=True, status=0):
        """Run a MAST program and return its output. With `trust`, it
        trusts the authority of the proxy."""
        if TEST_HOME:
            command = [self.launcher("mast"), root]
            env = dict(os.environ)
            env.pop("MAST_HOME", None)
        else:
            command = [sys.executable, "-m", "mast", root]
            env = dict(os.environ, MAST_HOME=self.mast_home)
        env.pop("SSL_CERT_FILE", None)
        if trust:
            env["SSL_CERT_FILE"] = self.proxy.ca_file
        proc = subprocess.run(
            command + list(args), cwd=self.mast_home, env=env, input=stdin,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=300)
        self.assertEqual(status, proc.returncode, "mast {} {} exited with {}:\n{}".format(
            root, " ".join(args), proc.returncode, proc.stdout))
        self.assertNotIn(GOOD_PASSWORD, proc.stdout)
        return proc.stdout

    def credentials(self, *args, **kwargs):
        return self.program("credentials", *args,
                            **kwargs)

    def leak_check(self):
        """The password is in no file of the home but the database,
        where it is encrypted: not as it is, and not in an Authorization
        header."""
        import base64
        forms = (GOOD_PASSWORD.encode(), base64.b64encode(
            "{}:{}".format(self.USER, GOOD_PASSWORD).encode()))
        for root, _, names in os.walk(self.mast_home):
            for name in names:
                path = os.path.join(root, name)
                if os.path.islink(path) or "python" in path.split(os.sep):
                    continue
                try:
                    with open(path, "rb") as fin:
                        data = fin.read()
                except OSError:
                    continue
                self.assertFalse(any(form in data for form in forms),
                                 "The password is in " + path)


class TestStoredCredentialCLI(StoredCredentialCase):
    """An admin stores a credential for a user that may only read; a job
    then uses it by name, and the password is nowhere but in the
    database (encrypted) and the memory of the job."""

    APPLIANCE = "dp-verified"

    def test_a_job_uses_a_stored_credential(self):
        self.program("targets", "add", "-n",
                     self.APPLIANCE, "-k", "datapower", "-A", "127.0.0.1",
                     "-s", "soma_port={}".format(self.proxy.port))
        self.addCleanup(self.program, "targets", "remove", "-n",
                        self.APPLIANCE)
        self.credentials("add", "-n", "collector", "-u", self.USER, "-p",
                         "collect", "-b", self.APPLIANCE, "--password-stdin",
                         stdin=GOOD_PASSWORD + "\n")
        self.addCleanup(self.credentials, "delete", "-n", "collector", "-y")

        # The test logs in, reads, and finds that the user cannot change
        # the appliance
        out = self.credentials("test", "-n", "collector", "-a", self.APPLIANCE)
        self.assertIn("works on {}".format(self.APPLIANCE), out)
        self.assertNotIn("WARNING", out)
        listed = self.credentials("list")
        self.assertRegex(listed, r"(?m)^collector .* works +no$")

        # A command that only reads runs with it
        out = self.program("datapower", "list-domains", "-a", self.APPLIANCE,
                           "--credential", "collector")
        self.assertIn("default", out)
        uses = self.credentials("uses", "-n", "collector")
        self.assertEqual(2, len([line for line in uses.splitlines()
                                 if self.APPLIANCE in line]))

        # A command that changes the appliance is refused before it is
        # queued
        out = self.program("datapower", "save",
                           "-a", self.APPLIANCE, "--credential", "collector",
                           status=1)
        self.assertIn("only by commands that only read", out)

        # Without a certificate that it can check, the password is not
        # sent: the job fails on the certificate
        out = self.program("datapower", "list-domains", "-a", self.APPLIANCE,
                           "--credential", "collector", trust=False,
                           status=1)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", out)
        self.leak_check()


class TestPinnedCLI(StoredCredentialCase):
    """An admin pins the self-signed certificate and the SSH host key of
    the container, and a stored credential then works with them, over
    SOMA and over SSH. Another certificate or host key stops it before
    the password is sent."""

    APPLIANCE = "dp-pinned"

    def appliances(self, *args, **kwargs):
        return self.program("targets", *args, **kwargs)

    def test_pinned_certificate_and_host_key(self):
        ports = self.container.ports
        self.appliances("add", "-n", self.APPLIANCE, "-k", "datapower",
                        "-A", "127.0.0.1",
                        "-s", "soma_port={}".format(ports[SOMA_PORT]),
                        "-s", "ssh_port={}".format(ports[SSH_PORT]),
                        "-s", "web_port={}".format(ports[WEB_PORT]))
        self.addCleanup(self.appliances, "remove", "-n", self.APPLIANCE)
        # A credential for changing, of a user that may only read: the
        # SSH check of check-connectivity is a command that changes
        self.credentials("add", "-n", "pinned", "-u", self.USER, "-p",
                         "change", "-b", self.APPLIANCE, "--password-stdin",
                         stdin=GOOD_PASSWORD + "\n")
        self.addCleanup(self.credentials, "delete", "-n", "pinned", "-y")

        # Not pinned yet: the self-signed certificate is refused
        out = self.program("datapower", "list-domains", "-a", self.APPLIANCE,
                           "--credential", "pinned", trust=False, status=1)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", out)

        # Every endpoint: the SOMA certificate and the SSH host key
        out = self.appliances("pin", "-n", self.APPLIANCE, "-y", trust=False)
        self.assertIn("Subject:", out)
        self.assertIn("Fingerprint: SHA256:", out)
        self.assertIn("Pinned soma of {}.".format(self.APPLIANCE), out)
        self.assertIn("Pinned ssh of {}.".format(self.APPLIANCE), out)
        self.assertRegex(self.appliances("list"), r"(?m)^{} .* soma:pin "
                         r"ssh:pin(?! CHANGED)".format(self.APPLIANCE))

        # SOMA with the pin, and nothing to trust but the pin
        out = self.program("datapower", "list-domains", "-a", self.APPLIANCE,
                           "--credential", "pinned", trust=False)
        self.assertIn("default", out)
        # SSH with the pinned host key
        out = self.program("datapower", "check-connectivity", "-a",
                           self.APPLIANCE, "--credential", "pinned",
                           trust=False)
        self.assertRegex(out, r"(?m)^{} +True +True +True".format(
            self.APPLIANCE))

        # Another port: the pin must be reviewed, and nothing is sent
        out = self.appliances("update", "-n", self.APPLIANCE, "-s",
                              "soma_port={}".format(self.proxy.port))
        self.assertIn("REVIEW: the port changed", out)
        out = self.program("datapower", "list-domains", "-a", self.APPLIANCE,
                           "--credential", "pinned", trust=False, status=1)
        self.assertIn("must be reviewed", out)
        # Behind it is another certificate (the proxy's): the review
        # refuses to keep the pin
        out = self.appliances("review", "-n", self.APPLIANCE, "-e", "soma",
                              "-y", trust=False, status=1)
        self.assertIn("This is not the pinned certificate", out)
        # Back on the port of the appliance, the review keeps it
        self.appliances("update", "-n", self.APPLIANCE, "-s",
                        "soma_port={}".format(ports[SOMA_PORT]))
        out = self.appliances("review", "-n", self.APPLIANCE, "-e", "soma",
                              "-y", trust=False)
        self.assertIn("Kept the pin of soma", out)
        out = self.program("datapower", "list-domains", "-a", self.APPLIANCE,
                           "--credential", "pinned", trust=False)
        self.assertIn("default", out)

        # A pin that the appliance does not match: the job fails before
        # the password is sent, and the endpoint is flagged
        self.appliances("pin", "-n", self.APPLIANCE, "-e", "soma", "-f",
                        "00" * 32)
        out = self.program("datapower", "list-domains", "-a", self.APPLIANCE,
                           "--credential", "pinned", trust=False, status=1)
        self.assertIn("The certificate of {} changed".format(self.APPLIANCE),
                      out)
        self.assertIn("pin again", out)
        self.assertRegex(self.appliances("list"),
                         r"(?m)^{} .* soma:pin CHANGED".format(self.APPLIANCE))
        self.appliances("pin", "-n", self.APPLIANCE, "-e", "soma", "-y",
                        trust=False)

        # Another SSH host key: the login is not tried
        self.appliances("pin", "-n", self.APPLIANCE, "-e", "ssh", "-f",
                        "SHA256:" + "A" * 43)
        out = self.program("datapower", "check-connectivity", "-a",
                           self.APPLIANCE, "--credential", "pinned",
                           trust=False)
        self.assertRegex(out, r"(?m)^{} +True +True +False".format(
            self.APPLIANCE))
        self.assertRegex(self.appliances("list"),
                         r"(?m)^{} .* ssh:pin CHANGED".format(self.APPLIANCE))
        self.leak_check()


if __name__ == "__main__":
    unittest.main()
