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
_module_: `mast.web.server`

Serve the web UI with CherryPy, on the host and port of `server.conf`.

Before it starts, it checks two things, and refuses to start with a
message that says how to fix them:

* With `secure = true`, the certificate and the key must be there
(`CertificateNotFound`).
* With `secure = false`, the host must be `127.0.0.1` or `::1`, so
that passwords and session cookies never cross a network without TLS
(`InsecureServer`).
"""
import getpass
import os
import sys

import cherrypy
from cheroot.ssl.builtin import BuiltinSSLAdapter
from cheroot.wsgi import WSGIServer

from mast.config import MAST_HOME
from mast.logging import make_logger

__all__ = [
    "main", "stop_server", "check_certificate", "check_transport",
    "StartupError", "CertificateNotFound", "InsecureServer", "LOOPBACK",
]

LOOPBACK = ("127.0.0.1", "::1")


class StartupError(Exception):
    """The web UI cannot start. The message says how to fix it."""


class CertificateNotFound(StartupError):
    """
    _class_: `mast.web.server.CertificateNotFound`

    The web UI uses TLS (`secure = true`), but a file of its certificate
    is not there or cannot be read. The message says how to fix it.
    """


class InsecureServer(StartupError):
    """
    _class_: `mast.web.server.InsecureServer`

    The web UI would listen without TLS (`secure = false`) on an
    address that other hosts can reach.
    """


def _in_home(path):
    return path if os.path.isabs(path) else os.path.join(MAST_HOME, path)


def check_transport(secure, host):
    """
    _function_: `mast.web.server.check_transport(secure, host)`

    Raise `InsecureServer` if `secure` is False and `host` is not a
    loopback address (`127.0.0.1` or `::1`).
    """
    if secure or host in LOOPBACK:
        return
    raise InsecureServer(
        "The web UI does not use TLS (secure = false in server.conf), "
        "but it would listen on {}, where other hosts can reach it. "
        "Passwords and session cookies would cross the network in plain "
        "text. Set secure = true (with key and cert) in the [server] "
        "section of etc/local/server.conf, or set host = 127.0.0.1 to "
        "use the web UI only from this host.".format(host))


def check_certificate(server):
    """
    _function_: `mast.web.server.check_certificate(server)`

    If the web UI uses TLS, raise `CertificateNotFound` when the
    `cert`, the `key` or the `cacert` (if set) of the `[server]` stanza
    `server` is not there or cannot be read. A fresh install has no
    certificate.
    """
    if not server.get("secure"):
        return
    files = [("cert", server.get("cert")), ("key", server.get("key"))]
    if server.get("cacert"):
        files.append(("cacert", server.get("cacert")))
    problems = []
    for name, path in files:
        if not path:
            problems.append("{} is not set".format(name))
            continue
        full = _in_home(path)
        if not os.path.isfile(full):
            problems.append("{} = {} is not there".format(name, path))
        elif not os.access(full, os.R_OK):
            problems.append("{} = {} cannot be read".format(name, path))
    if not problems:
        return
    if sys.platform == "win32":
        command = "mast.bat contrib\\gen-cert.py"
    else:
        command = "./mast contrib/gen-cert.py"
    raise CertificateNotFound(
        "The web GUI uses TLS (secure = true in server.conf), but {}.\n"
        "Set key and cert in the [server] section of "
        "etc/local/server.conf to the files of your private key and "
        "certificate, or make a self-signed certificate with this "
        "command in {}:\n\n    {}\n\nThen start the web GUI again.".format(
            "; ".join(problems), MAST_HOME, command))


def main():
    """
    _function_: `mast.web.server.main()`

    Serve the web UI on the configured host and port, and block until
    a SIGTERM or equivalent (Ctrl + C) is received. Raises a
    `StartupError` if it cannot start.

    Logs to: `gui.main.log`
    """
    from mast.web.app import create_app, settings
    logger = make_logger("gui.main")
    logger.debug("Running as user {}".format(getpass.getuser()))
    logger.debug("Running in directory {}".format(os.getcwd()))
    values = settings()
    server = values["server"]
    check_transport(values["secure"], values["host"])
    check_certificate(dict(server, secure=values["secure"]))
    app = create_app()

    cherrypy.tree.graft(app, "/")
    cherrypy.config.update({
        "engine.autoreload.on": False,
        "log.screen": True,
        "server.socket_port": values["port"],
        "server.socket_host": values["host"],
        "server.max_request_body_size": int(
            server.get("max_file_upload_size") or 536870912),
    })
    if values["secure"]:
        logger.debug("Configuring TLS")
        cherrypy.server.ssl_module = "builtin"
        cacert = server.get("cacert")
        WSGIServer.ssl_adapter = BuiltinSSLAdapter(
            _in_home(server["cert"]), _in_home(server["key"]),
            _in_home(cacert) if cacert else None)
    try:
        engine = cherrypy.engine
        engine.signal_handler.subscribe()
        if hasattr(engine, "console_control_handler"):
            engine.console_control_handler.subscribe()
        print("MAST Web listening on {}://{}:{}".format(
            "https" if values["secure"] else "http",
            values["host"], values["port"]))
        cherrypy.engine.start()
        cherrypy.engine.block()
    except KeyboardInterrupt:
        cherrypy.engine.exit()
    except Exception:
        logger.exception(
            "Sorry, an unhandled exception occurred while starting CherryPy")


def stop_server():
    """
    _function_: `mast.web.server.stop_server()`

    Stop the web server. `main` then returns.
    """
    cherrypy.engine.exit()
