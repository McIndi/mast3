"""
Find, download and check a python-build-standalone interpreter.

The installer ships an interpreter from
https://github.com/astral-sh/python-build-standalone. A release is a
date tag (for example 20261110) with one archive for each Python
version, platform and build. The SHA256SUMS file of a release lists
every archive with its hash, so the archive is chosen from that list.
No GitHub API request is made.

Downloads go to a cache folder. A file that is already in the cache is
checked against its hash and used without the network.
"""
import hashlib
import json
import logging
import platform as _platform
import re
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

RELEASES_URL = "https://github.com/astral-sh/python-build-standalone/releases/download"
LATEST_URL = (
    "https://raw.githubusercontent.com/astral-sh/python-build-standalone/"
    "latest-release/latest-release.json"
)
HASH_FILE = "SHA256SUMS"
HASH_BUFF_SIZE = 65536


class SetupError(Exception):
    """An error that stops mast-setup. `code` is the exit status."""
    code = 10


class UnsupportedPlatform(SetupError):
    code = 1


class AmbiguousRelease(SetupError):
    code = 2


class HashMismatch(SetupError):
    code = 3


class NoMatchingPython(SetupError):
    code = 4


def current_platform():
    """Return 'linux', 'windows' or 'darwin' for this machine."""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith("win32"):
        return "windows"
    if sys.platform.startswith("darwin"):
        return "darwin"
    raise UnsupportedPlatform(f"Unsupported platform: {sys.platform}")


def current_machine():
    """Return the machine name, for example 'x86_64' or 'arm64'."""
    return _platform.machine()


def _version_key(version):
    return tuple(int(part) for part in version.split("."))


def choose_release_file(names, requested_version, platform, machine,
                        is_64bit=True, tag="?"):
    """
    Return `(file name, Python version)` of the archive to use from the
    file names of a release.

    `requested_version` is a minor version (3.14), which picks the newest
    patch release, or a full version (3.14.10), which must exist.
    Pre-releases, freethreaded builds and the x86_64_v2/v3/v4 builds are
    never chosen.
    """
    # Filter to the install_only build configuration, without debug
    # symbols.
    names = [n for n in names if "install_only_stripped" in n]
    # Exclude freethreaded builds; MAST targets the regular CPython build.
    names = [n for n in names if "freethreaded" not in n]
    # The requested version must match whole version components, so 3.1
    # does not match 3.14.10. Pre-releases (3.16.0a1) never match because
    # the patch number must be digits only.
    version_pattern = re.compile(
        rf"^cpython-({re.escape(requested_version)}(?:\.\d+)?)\+")
    available = sorted(
        {m.group(1) for m in (re.match(r"^cpython-(\d+\.\d+\.\d+)\+", n)
                              for n in names) if m},
        key=_version_key,
    )

    def no_match():
        return NoMatchingPython(
            f"Unable to find a Python build for version '{requested_version}' "
            f"on {platform} in python-build-standalone release {tag}. "
            f"Versions in that release: {', '.join(available) or 'none'}. "
            "Pass --python-version with one of these (a minor version such "
            "as 3.14 is enough).")

    names = [n for n in names if version_pattern.match(n)]
    if not names:
        raise no_match()
    # Several patch releases can match a minor version. Use the newest.
    version = max({version_pattern.match(n).group(1) for n in names},
                  key=_version_key)
    names = [n for n in names if version_pattern.match(n).group(1) == version]

    # Platform
    if platform == "windows":
        names = [n for n in names if "windows-msvc" in n]
    elif platform == "linux":
        names = [n for n in names if "unknown-linux-gnu" in n]
    elif platform == "darwin":
        names = [n for n in names if "apple-darwin" in n]
    else:
        raise UnsupportedPlatform(f"Unsupported platform: {platform}")

    # Architecture
    if platform in ("windows", "linux"):
        if is_64bit:
            names = [n for n in names if "x86_64" in n]
            names = [n for n in names
                     if not re.search(r"x86_64_v[234]", n)]
        else:
            names = [n for n in names if "i686" in n]
    elif platform == "darwin":
        if machine == "x86_64":
            names = [n for n in names if "x86_64" in n]
        elif machine == "arm64":
            names = [n for n in names if "aarch64" in n]
        else:
            raise UnsupportedPlatform(f"Unsupported macOS machine: {machine}")

    if not names:
        raise no_match()
    if len(names) > 1:
        raise AmbiguousRelease(
            f"Unable to determine correct Python build out of: {', '.join(names)}")
    return names[0], version


def parse_hash_file(text):
    """Return a dict of file name to SHA256 from a SHA256SUMS file."""
    hashes = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            hashes[parts[1].lstrip("*")] = parts[0].lower()
    return hashes


def file_sha256(path):
    sha256 = hashlib.sha256()
    with Path(path).open("rb") as fp:
        while True:
            data = fp.read(HASH_BUFF_SIZE)
            if not data:
                break
            sha256.update(data)
    return sha256.hexdigest()


def check_hash(path, expected):
    actual = file_sha256(path)
    if actual != expected:
        raise HashMismatch(
            f"{path} does not match its hash. Expected: '{expected}', "
            f"Actual: '{actual}'")


def download(url, destination):
    """Download `url` to `destination`, through a temporary file."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    log.info(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "mast-setup"})
    with urllib.request.urlopen(request) as response, partial.open("wb") as fp:
        shutil.copyfileobj(response, fp)
    partial.replace(destination)
    return destination


def latest_tag():
    """Return the tag of the latest python-build-standalone release."""
    request = urllib.request.Request(LATEST_URL, headers={"User-Agent": "mast-setup"})
    with urllib.request.urlopen(request) as response:
        tag = json.load(response)["tag"]
    log.info(f"The latest python-build-standalone release is {tag}")
    return tag


def asset_url(tag, name):
    return f"{RELEASES_URL}/{tag}/{urllib.parse.quote(name)}"


def fetch_python(tag, requested_version, cache_directory,
                 platform=None, machine=None):
    """
    Return `(path to the archive, Python version)` for the interpreter of
    release `tag`, from the cache or downloaded to it. The hash of the
    archive is always checked.
    """
    platform = platform or current_platform()
    machine = machine or current_machine()
    release_dir = Path(cache_directory) / "python-build-standalone" / tag
    hash_file = release_dir / HASH_FILE
    if not hash_file.exists():
        download(asset_url(tag, HASH_FILE), hash_file)
    hashes = parse_hash_file(hash_file.read_text())

    name, version = choose_release_file(
        hashes, requested_version, platform, machine,
        is_64bit=sys.maxsize > 2**32, tag=tag)
    log.info(f"Using Python {version} (requested {requested_version}) "
             f"from python-build-standalone {tag}: {name}")

    archive = release_dir / name
    if archive.exists():
        log.info(f"Using the cached {archive}")
    else:
        download(asset_url(tag, name), archive)
    try:
        check_hash(archive, hashes[name])
    except HashMismatch:
        # Do not keep a bad file in the cache.
        archive.unlink()
        raise
    log.info("The Python archive matches its expected hash")
    return archive, version
