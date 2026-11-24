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
Tests for `mast.keyfile` and for the secret key of the web GUI.
"""
import base64
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

from cryptography.fernet import Fernet

from mast import keyfile


class TestReadOrCreate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mast-keyfile-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "etc", "local", "test.key")

    def test_the_first_call_makes_the_file_and_its_directory(self):
        key = keyfile.read_or_create(self.path)
        with open(self.path, "rb") as fin:
            self.assertEqual(fin.read(), key)

    def test_the_key_is_32_random_bytes_and_a_fernet_key(self):
        key = keyfile.read_or_create(self.path)
        self.assertEqual(len(base64.urlsafe_b64decode(key)), 32)
        Fernet(key)
        other = keyfile.read_or_create(os.path.join(self.tmp, "other.key"))
        self.assertNotEqual(key, other)

    def test_later_calls_return_the_same_key(self):
        key = keyfile.read_or_create(self.path)
        self.assertEqual(keyfile.read_or_create(self.path), key)

    def test_a_key_that_is_there_is_used(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as fout:
            fout.write(b"a key from before\n")
        self.assertEqual(keyfile.read_or_create(self.path), b"a key from before")

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_only_the_owner_can_read_the_file(self):
        keyfile.read_or_create(self.path)
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode & 0o077, 0)

    def test_an_empty_file_is_an_error(self):
        os.makedirs(os.path.dirname(self.path))
        open(self.path, "wb").close()
        with mock.patch.object(keyfile.time, "sleep"):
            with self.assertRaises(keyfile.KeyFileError):
                keyfile.read_or_create(self.path)

    def test_processes_that_start_together_get_one_key(self):
        keys = []
        barrier = threading.Barrier(8)

        def run():
            barrier.wait()
            keys.append(keyfile.read_or_create(self.path))
        threads = [threading.Thread(target=run) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(keys), 8)
        self.assertEqual(len(set(keys)), 1)


class TestWebSecretKey(unittest.TestCase):
    """`mast.web.security.secret_key` picks the key that signs the
    session cookie."""

    @classmethod
    def setUpClass(cls):
        from mast.web import security
        cls.security = security

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mast-web-key-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "server.key")

    def test_the_web_ui_has_a_key_from_its_key_file(self):
        # The default server.conf names etc/local/server.key, which the
        # web UI makes when it starts.
        from mast.config import MAST_HOME
        from mast.web.app import create_app
        app = create_app()
        path = os.path.join(MAST_HOME, "etc", "local", "server.key")
        self.assertEqual(app.secret_key, keyfile.read_or_create(path))
        self.assertNotIn(app.secret_key,
                         {b"INSECURE_SECRET_KEY_CHANGE_ME", b"changeme"})

    def test_without_a_secret_key_the_key_file_is_made(self):
        key = self.security.secret_key({"secret_key_file": self.path})
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(self.security.secret_key({"secret_key_file": self.path}), key)

    def test_a_relative_key_file_is_in_mast_home(self):
        with mock.patch.object(self.security, "MAST_HOME", self.tmp):
            key = self.security.secret_key({"secret_key_file": "etc/local/web.key"})
        path = os.path.join(self.tmp, "etc", "local", "web.key")
        self.assertEqual(keyfile.read_or_create(path), key)

    def test_a_secret_key_that_is_set_is_used(self):
        key = self.security.secret_key({"secret_key": "shared by our two servers",
                                   "secret_key_file": self.path})
        self.assertEqual(key, "shared by our two servers")
        self.assertFalse(os.path.exists(self.path))

    def test_the_old_default_keys_are_ignored(self):
        for old in ("INSECURE_SECRET_KEY_CHANGE_ME", "changeme"):
            with self.subTest(secret_key=old):
                with self.assertLogs("gui", "WARNING"):
                    key = self.security.secret_key({"secret_key": old,
                                               "secret_key_file": self.path})
                self.assertEqual(key, keyfile.read_or_create(self.path))

    def test_an_empty_secret_key_means_the_key_file(self):
        key = self.security.secret_key({"secret_key": "  ",
                                   "secret_key_file": self.path})
        self.assertEqual(key, keyfile.read_or_create(self.path))


if __name__ == "__main__":
    unittest.main()
