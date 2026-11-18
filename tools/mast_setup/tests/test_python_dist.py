"""
Tests for the choice, download and check of the bundled interpreter.

data/assets-20261110.txt has the file names of the python-build-standalone
release 20261110, from its SHA256SUMS file.
"""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mast_setup import python_dist
from mast_setup.python_dist import (
    AmbiguousRelease,
    HashMismatch,
    NoMatchingPython,
    choose_release_file,
)

DATA = Path(__file__).parent / "data"
TAG = "20261110"
NAMES = (DATA / "assets-20261110.txt").read_text().split()

# An older patch release, as a release that has two 3.14 versions lists it.
OLDER = [n.replace("3.14.10+", "3.14.9+") for n in NAMES if n.startswith("cpython-3.14.10+")]


def choose(version, platform="linux", machine="x86_64", names=NAMES, is_64bit=True):
    return choose_release_file(names, version, platform, machine, is_64bit, TAG)


class TestChooseReleaseFile(unittest.TestCase):

    def test_each_platform_gets_one_file(self):
        expected = {
            ("linux", "x86_64"): "cpython-3.14.10+20261110-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
            ("windows", "AMD64"): "cpython-3.14.10+20261110-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
            ("darwin", "x86_64"): "cpython-3.14.10+20261110-x86_64-apple-darwin-install_only_stripped.tar.gz",
            ("darwin", "arm64"): "cpython-3.14.10+20261110-aarch64-apple-darwin-install_only_stripped.tar.gz",
        }
        for (platform, machine), name in expected.items():
            with self.subTest(platform=platform, machine=machine):
                self.assertEqual(choose("3.14", platform, machine), (name, "3.14.10"))

    def test_minor_version_picks_the_newest_patch(self):
        name, version = choose("3.14", names=NAMES + OLDER)
        self.assertEqual(version, "3.14.10")
        self.assertIn("3.14.10+", name)

    def test_full_version_picks_that_patch(self):
        name, version = choose("3.14.9", names=NAMES + OLDER)
        self.assertEqual(version, "3.14.9")
        self.assertTrue(name.startswith("cpython-3.14.9+"))

    def test_a_version_must_match_whole_components(self):
        with self.assertRaises(NoMatchingPython) as cm:
            choose("3.1")
        self.assertIn("3.14.10", str(cm.exception))

    def test_a_pre_release_is_never_chosen(self):
        self.assertTrue(any(n.startswith("cpython-3.16.0a2+") for n in NAMES))
        with self.assertRaises(NoMatchingPython):
            choose("3.16")

    def test_freethreaded_and_microarchitecture_builds_are_never_chosen(self):
        for platform, machine in (("linux", "x86_64"), ("windows", "AMD64"),
                                  ("darwin", "x86_64"), ("darwin", "arm64")):
            for version in ("3.13", "3.14", "3.15"):
                name, _ = choose(version, platform, machine)
                self.assertNotIn("freethreaded", name)
                self.assertNotRegex(name, r"x86_64_v[234]")
                self.assertIn("install_only_stripped", name)

    def test_32_bit(self):
        name, _ = choose("3.14", "windows", "x86", is_64bit=False)
        self.assertIn("i686-pc-windows-msvc", name)

    def test_an_ambiguous_list_fails(self):
        extra = "cpython-3.14.10+20261110-x86_64-unknown-linux-gnu-install_only_stripped.tar.zst"
        with self.assertRaises(AmbiguousRelease):
            choose("3.14", names=NAMES + [extra])

    def test_unknown_mac_machine_fails(self):
        with self.assertRaises(python_dist.UnsupportedPlatform):
            choose("3.14", "darwin", "ppc")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class TestFetchPython(unittest.TestCase):

    name = "cpython-3.14.10+20261110-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
    content = b"not really an archive"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name)
        self.release_dir = self.cache / "python-build-standalone" / TAG

    def write_hashes(self, digest):
        self.release_dir.mkdir(parents=True, exist_ok=True)
        lines = ["{}  {}".format(digest if n == self.name else "0" * 64, n) for n in NAMES]
        (self.release_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n")

    def fetch(self):
        return python_dist.fetch_python(TAG, "3.14", self.cache, "linux", "x86_64")

    def test_cached_file_with_a_pinned_tag_needs_no_network(self):
        self.write_hashes(hashlib.sha256(self.content).hexdigest())
        (self.release_dir / self.name).write_bytes(self.content)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            archive, version = self.fetch()
        self.assertEqual(archive, self.release_dir / self.name)
        self.assertEqual(version, "3.14.10")

    def test_downloaded_file_is_checked_and_cached(self):
        self.write_hashes(hashlib.sha256(self.content).hexdigest())
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse(self.content)) as urlopen:
            archive, _ = self.fetch()
        url = urlopen.call_args.args[0].full_url
        self.assertEqual(url, "{}/{}/{}".format(
            python_dist.RELEASES_URL, TAG, self.name.replace("+", "%2B")))
        self.assertEqual(archive.read_bytes(), self.content)

    def test_hash_file_is_downloaded_once(self):
        digest = hashlib.sha256(self.content).hexdigest()
        sums = "{}  {}\n".format(digest, self.name).encode()
        responses = [FakeResponse(sums), FakeResponse(self.content)]
        with mock.patch("urllib.request.urlopen", side_effect=responses):
            self.fetch()
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            self.fetch()

    def test_hash_mismatch_of_a_download_fails(self):
        self.write_hashes("1" * 64)
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse(self.content)):
            with self.assertRaises(HashMismatch):
                self.fetch()
        self.assertFalse((self.release_dir / self.name).exists())

    def test_hash_mismatch_of_a_cached_file_fails(self):
        self.write_hashes("1" * 64)
        (self.release_dir / self.name).write_bytes(self.content)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            with self.assertRaises(HashMismatch):
                self.fetch()
        # The bad file is removed, so the next build downloads it again.
        self.assertFalse((self.release_dir / self.name).exists())

    def test_parse_hash_file(self):
        text = "abc  one.tar.gz\nDEF *two.tar.gz\n\n"
        self.assertEqual(python_dist.parse_hash_file(text),
                         {"one.tar.gz": "abc", "two.tar.gz": "def"})


if __name__ == "__main__":
    unittest.main()
