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
Start and stop an IBM DataPower container for the integration tests.

The tests run against a real DataPower Gateway, not a mock. IBM
publishes "limited" images for non-production use. This module starts
one of them with podman or docker, enables the XML management
interface, the web GUI and SSH, and waits until the gateway answers.

Environment variables:

* `MAST_TEST_DATAPOWER_IMAGE`: The image to run. The default is
`icr.io/cpopen/datapower/datapower-limited:11.0.0.3`.
* `MAST_TEST_CONTAINER_RUNTIME`: `podman` or `docker`. The default is
the first one found on the PATH.
* `MAST_TEST_DATAPOWER_ACCEPT_LICENSE`: Must be `true`. The image does
not start until you accept the IBM license, and only you can do that.
* `MAST_TEST_DATAPOWER_TIMEOUT`: Seconds to wait for the gateway to
start. The default is 300.
* `MAST_TEST_DATAPOWER_KEEP`: Set to `true` to keep the shared
container after the tests end, and to use it again in the next run.
Remove it with `podman rm -f mast-test-keep` when you want a clean
gateway.

Most test classes use one shared container, which `shared_container`
starts on first use and removes when Python exits. A test class that
changes something no other class can tolerate, such as the password
of the admin account, starts a private `DataPowerContainer`.
"""
import atexit
import os
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_IMAGE = "icr.io/cpopen/datapower/datapower-limited:11.0.0.3"

# The limited images start with this account.
USERNAME = "admin"
PASSWORD = "admin"

# The name of the container that MAST_TEST_DATAPOWER_KEEP keeps.
KEEP_NAME = "mast-test-keep"

SOMA_PORT = 5550
WEB_PORT = 9090
SSH_PORT = 2222

CONFIG_DIR = "/opt/ibm/datapower/drouter/config"

# The image has an empty configuration directory. DataPower reads this
# file at startup.
STARTUP_CONFIG = """top; configure terminal;

web-mgmt
  admin-state enabled
  local-address 0.0.0.0 {web_port}
exit

xml-mgmt
  admin-state enabled
  local-address 0.0.0.0 {soma_port}
exit

ssh 0.0.0.0 {ssh_port}
""".format(web_port=WEB_PORT, soma_port=SOMA_PORT, ssh_port=SSH_PORT)


class ContainerUnavailable(Exception):
    """The integration tests cannot start a DataPower container."""


def find_runtime():
    """Return the path of the container runtime, or raise
    `ContainerUnavailable`."""
    names = [os.environ.get("MAST_TEST_CONTAINER_RUNTIME")]
    if not names[0]:
        names = ["podman", "docker"]
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    raise ContainerUnavailable(
        "No container runtime found (looked for {})".format(", ".join(names)))


def license_accepted():
    value = os.environ.get("MAST_TEST_DATAPOWER_ACCEPT_LICENSE", "")
    return value.lower() == "true"


class DataPowerContainer(object):
    """One running DataPower container.

    Usage:

        :::python
        container = DataPowerContainer()
        container.start()
        try:
            dp = container.datapower()
            print(dp.domains)
        finally:
            container.stop()
    """

    def __init__(self, image=None, name=None):
        self.image = image or os.environ.get(
            "MAST_TEST_DATAPOWER_IMAGE", DEFAULT_IMAGE)
        self.name = name or "mast-test-{}".format(uuid.uuid4().hex[:8])
        self.timeout = int(os.environ.get("MAST_TEST_DATAPOWER_TIMEOUT", 300))
        self.runtime = None
        self.ports = {}
        self.started = False

    def _run(self, *args, check=True):
        proc = subprocess.run(
            [self.runtime] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True)
        if check and proc.returncode != 0:
            raise ContainerUnavailable(
                "`{} {}` failed: {}".format(
                    os.path.basename(self.runtime),
                    " ".join(args),
                    proc.stdout.strip()))
        return proc.stdout

    def start(self):
        """Create the container, add the management configuration,
        start it and wait until the XML management interface answers."""
        if not license_accepted():
            raise ContainerUnavailable(
                "Set MAST_TEST_DATAPOWER_ACCEPT_LICENSE=true to accept the "
                "IBM DataPower license and run the integration tests")
        self.runtime = find_runtime()
        if self._attach():
            return
        self._run(
            "create",
            "--name", self.name,
            "--env", "DATAPOWER_ACCEPT_LICENSE=true",
            "--env", "DATAPOWER_LOG_LEVEL=3",
            "--publish", "127.0.0.1::{}".format(SOMA_PORT),
            "--publish", "127.0.0.1::{}".format(WEB_PORT),
            "--publish", "127.0.0.1::{}".format(SSH_PORT),
            self.image)
        self.started = True
        try:
            self._add_startup_config()
            self._run("start", self.name)
            self._read_ports()
            self._wait_until_ready()
        except Exception:
            self.stop()
            raise

    def _attach(self):
        """Use a container of this name that is already running, if
        there is one and it runs the image that was asked for. Remove a
        container of this name that cannot be used."""
        found = self._run(
            "inspect", "--format", "{{.State.Status}} {{.ImageName}}",
            self.name, check=False).strip().split()
        if len(found) != 2 or "no such" in " ".join(found).lower():
            return False
        self.started = True
        if found != ["running", self.image]:
            self.stop()
            return False
        try:
            self._read_ports()
            self._wait_until_ready()
        except ContainerUnavailable:
            self.stop()
            return False
        return True

    def _read_ports(self):
        for port in (SOMA_PORT, WEB_PORT, SSH_PORT):
            out = self._run("port", self.name, str(port))
            self.ports[port] = int(
                out.strip().splitlines()[0].rsplit(":", 1)[1])

    def _add_startup_config(self):
        """Copy the management configuration into the container."""
        remote = "{}:{}/auto-startup.cfg".format(self.name, CONFIG_DIR)
        tmp = tempfile.mkdtemp(prefix="mast-test-dp-")
        try:
            local = os.path.join(tmp, "auto-startup.cfg")
            with open(local, "w") as fout:
                fout.write(STARTUP_CONFIG)
            self._run("cp", local, remote)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _wait_until_ready(self):
        """Wait until the XML management interface answers a request."""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        url = "https://127.0.0.1:{}/service/mgmt/current".format(
            self.ports[SOMA_PORT])
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            state = self._run(
                "inspect", "--format", "{{.State.Status}}", self.name,
                check=False).strip()
            if state not in ("running", "created", "configured"):
                raise ContainerUnavailable(
                    "The DataPower container stopped ({}): {}".format(
                        state, self.logs()[-2000:]))
            try:
                urllib.request.urlopen(url, timeout=5, context=context)
                return
            except urllib.error.HTTPError:
                # An HTTP error status is still an answer.
                return
            except (urllib.error.URLError, OSError):
                time.sleep(2)
        raise ContainerUnavailable(
            "DataPower did not answer in {} seconds: {}".format(
                self.timeout, self.logs()[-2000:]))

    def logs(self):
        return self._run("logs", self.name, check=False)

    def stop(self):
        """Remove the container. Safe to call more than once."""
        if self.started:
            self._run("rm", "--force", "--volumes", self.name, check=False)
            self.started = False

    def datapower(self, domain="default"):
        """Return a `mast.datapower.datapower.DataPower` object that is
        connected to this container."""
        from mast.datapower.datapower import DataPower
        return DataPower(
            "127.0.0.1",
            "{}:{}".format(USERNAME, PASSWORD),
            domain=domain,
            port=self.ports[SOMA_PORT],
            web_port=self.ports[WEB_PORT],
            ssh_port=self.ports[SSH_PORT],
            check_hostname=False)


_shared = None


def shared_container():
    """Return the container that the test classes share. The first call
    starts it. It is removed when Python exits, unless
    MAST_TEST_DATAPOWER_KEEP is `true`."""
    global _shared
    if _shared is None:
        keep = os.environ.get("MAST_TEST_DATAPOWER_KEEP", "").lower() == "true"
        container = DataPowerContainer(name=KEEP_NAME if keep else None)
        container.start()
        if not keep:
            atexit.register(container.stop)
        _shared = container
    return _shared
