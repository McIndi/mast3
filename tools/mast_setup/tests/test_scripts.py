"""
Tests for scripts.py: console scripts that work after the installer is
unzipped somewhere else.

The Windows launchers can only be changed on Windows. The Windows build
in the release workflow checks them: check_bundle fails if a launcher
still runs the interpreter of the build folder.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mast_setup import assemble, scripts

posix_only = unittest.skipIf(sys.platform == "win32", "Linux and macOS scripts")

SCRIPT_BODY = (
    "# -*- coding: utf-8 -*-\n"
    "import sys\n"
    "print('ran with', sys.argv[1:])\n"
)


@posix_only
class TestPosixScripts(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name)) / "assemble"
        self.bin = self.root / "python" / "3.14.10" / "bin"
        self.bin.mkdir(parents=True)
        # A stand-in for the bundled interpreter: a link to this one.
        os.symlink(sys.executable, self.bin / "python3")

    def write(self, name, first_line, body=SCRIPT_BODY, mode=0o755):
        path = self.bin / name
        path.write_text(first_line + "\n" + body)
        path.chmod(mode)
        return path

    def test_absolute_first_line_is_replaced(self):
        script = self.write("mast-version", "#!{}".format(self.bin / "python3"), mode=0o750)
        self.assertEqual(scripts.relocate_posix(self.bin), ["mast-version"])
        text = script.read_text()
        self.assertTrue(text.startswith(scripts.POSIX_HEADER.format(name="python3")))
        self.assertTrue(text.endswith(SCRIPT_BODY))
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o750)
        self.assertEqual(scripts.check(self.root), [])

    def test_other_scripts_are_not_changed(self):
        unchanged = {
            "env-script": "#!/usr/bin/env python3",
            "shell": "#!/bin/sh",
            "elsewhere": "#!/usr/bin/python3",
        }
        for name, line in unchanged.items():
            self.write(name, line)
        (self.bin / "binary").write_bytes(b"\x7fELF\x00\x01")
        os.symlink("shell", self.bin / "link")
        before = {p.name: p.read_bytes() for p in self.bin.iterdir() if not p.is_symlink()}
        self.assertEqual(scripts.relocate_posix(self.bin), [])
        after = {p.name: p.read_bytes() for p in self.bin.iterdir() if not p.is_symlink()}
        self.assertEqual(before, after)

    def test_check_finds_an_absolute_first_line(self):
        self.write("flask", "#!{}".format(self.bin / "python3"))
        problems = scripts.check(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("flask: its first line names", problems[0])
        with self.assertRaises(assemble.BundleError):
            assemble.check_bundle(self.root)

    def test_relocated_script_runs_after_a_move(self):
        self.write("tool", "#!{}".format(self.bin / "python3"))
        scripts.relocate_posix(self.bin)
        moved = self.root.parent / "unzipped"
        shutil.move(self.root, moved)
        script = moved / "python" / "3.14.10" / "bin" / "tool"
        result = subprocess.run([str(script), "a", "b c"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ran with ['a', 'b c']")
        # Through a symlink in another folder, as in /usr/local/bin.
        link = self.root.parent / "tool-link"
        os.symlink(script, link)
        result = subprocess.run([str(link)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class TestScriptsDirectory(unittest.TestCase):

    def test_scripts_directory(self):
        self.assertEqual(scripts.scripts_directory(Path("h/python/3.14.10/bin/python3"), "linux"),
                         Path("h/python/3.14.10/bin"))
        self.assertEqual(scripts.scripts_directory(Path("h/python/3.14.10/python.exe"), "windows"),
                         Path("h/python/3.14.10/Scripts"))


if __name__ == "__main__":
    unittest.main()
