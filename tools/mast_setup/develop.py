"""
Make a development home: a MAST home with the layout, launchers and
interpreter of the installer, that runs the code of the checkout.

* The interpreter comes from the same cache as the installer, and the
  dependencies from the same exported uv.lock.
* MAST is installed editable, so the launchers run the code of the
  checkout.
* Every file that git tracks under `files/mast_home` is a symlink to the
  file in the checkout, so an edit to a template, to `etc/default` or to
  a SOMA spec is live. The `.empty` placeholders are not linked; their
  folders are made.
* Everything else is a real file or folder in the home: what MAST
  writes (`etc/local`, `var/log`, `var/run`, `tmp`, ...) never reaches
  the checkout.

Running it again is safe. It links new tracked files, removes the links
of files that are no longer tracked, updates the dependencies when
uv.lock or pyproject.toml changed, and does not touch anything else.
"""
import hashlib
import logging
import os
import shutil
from pathlib import Path

from . import assemble
from .python_dist import SetupError

log = logging.getLogger(__name__)

STATE_DIR = ".mast-setup"
MANAGED_FILE = "managed.txt"
STAMP_FILE = "packages.sha256"
PLACEHOLDER = ".empty"


def _read_lines(path):
    if path.exists():
        return [line for line in path.read_text().splitlines() if line]
    return []


def _same_link(path, target):
    return path.is_symlink() and os.readlink(path) == str(target)


class _Linker:
    """Make symlinks, and fall back to copies if symlinks fail."""

    def __init__(self, warn):
        self.copied = False
        self.warn = warn

    def link(self, target, path):
        try:
            os.symlink(target, path)
        except OSError as e:
            shutil.copy2(target, path)
            if not self.copied:
                self.copied = True
                self.warn(
                    f"Could not make a symlink ({e}). The files of "
                    "files/mast_home are copied instead, so a change to "
                    "them is not live: run ./mast-setup --development-mode "
                    "again after you change them. On Windows, turn on "
                    "developer mode to allow symlinks.")


def link_home_files(repo, home, warn=log.warning):
    """
    Link the tracked files of `files/mast_home` into the home, and
    remove the links that this function made before for files that are
    no longer tracked. Return the list of managed paths.

    A real file that MAST wrote at the path of a tracked file is left
    alone, with a warning.
    """
    repo = Path(repo).resolve()
    home = Path(home)
    source = repo / "files" / "mast_home"
    state = home / STATE_DIR
    state.mkdir(parents=True, exist_ok=True)
    managed_file = state / MANAGED_FILE
    previously = set(_read_lines(managed_file))

    linker = _Linker(warn)
    managed = []
    for relative in assemble.tracked_home_files(repo):
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if Path(relative).name == PLACEHOLDER:
            continue
        target = source / relative
        if _same_link(path, target):
            managed.append(relative)
            continue
        if path.is_symlink() or (path.exists() and relative in previously):
            # A link or copy that this script made before: refresh it.
            path.unlink()
        elif path.exists():
            warn(f"{path} is a file that is not managed by mast-setup. "
                 f"It is left as it is, and is not linked to {target}.")
            continue
        linker.link(target, path)
        managed.append(relative)

    for relative in sorted(previously - set(managed)):
        path = home / relative
        if path.is_symlink() or path.is_file():
            log.info(f"Removing {path}: it is no longer tracked")
            path.unlink()
    _remove_dangling_links(home, source)
    managed_file.write_text("".join(f"{m}\n" for m in managed))
    return managed


def _remove_dangling_links(home, source):
    """Remove links into files/mast_home whose target is gone."""
    for dirpath, dirnames, filenames in os.walk(home):
        if Path(dirpath) == home:
            # The interpreter is not linked. Do not walk it.
            dirnames[:] = [d for d in dirnames if d not in ("python", STATE_DIR)]
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            if path.is_symlink() and not path.exists():
                target = os.readlink(path)
                if target.startswith(str(source)):
                    log.info(f"Removing {path}: its target {target} is gone")
                    path.unlink()


def packages_stamp(repo):
    """A hash of what decides the installed packages."""
    sha256 = hashlib.sha256()
    for name in ("uv.lock", "pyproject.toml"):
        sha256.update((Path(repo) / name).read_bytes())
    return sha256.hexdigest()


def install_packages(repo, python, home):
    """
    Install the locked dependencies and an editable MAST into the
    interpreter of the home.
    """
    requirements = assemble.export_requirements(
        repo, Path(home) / STATE_DIR / "requirements.txt")
    assemble.install_requirements(python, requirements)
    assemble.install_mast(python, Path(repo).resolve(), editable=True)


def describe_deletion(home):
    """Return the lines that say what --recreate deletes."""
    home = Path(home)
    lines = [f"--recreate deletes the development home {home.resolve()}:"]
    for item in sorted(home.iterdir()):
        kind = "folder" if item.is_dir() and not item.is_symlink() else "file"
        lines.append(f"  {item.name} ({kind})")
    return lines


def develop(repo, home, platform, prepare_python, install=install_packages,
            recreate=False, say=print, warn=log.warning):
    """
    Make or update a development home and return the path of its
    interpreter.

    * `prepare_python(home)` puts the interpreter into the home, if it
      is not there yet, and returns `(path of the interpreter, version)`.
    * `install(repo, python, home)` installs the packages. It runs only
      when uv.lock or pyproject.toml changed since the last run.
    """
    repo = Path(repo).resolve()
    home = Path(home)
    if not repo.joinpath("files", "mast_home").is_dir():
        raise SetupError(f"{repo} is not a MAST checkout")
    if recreate and home.exists():
        for line in describe_deletion(home):
            say(line)
        shutil.rmtree(home)
    home.mkdir(parents=True, exist_ok=True)
    (home / STATE_DIR).mkdir(exist_ok=True)

    python, version = prepare_python(home)

    stamp_file = home / STATE_DIR / STAMP_FILE
    stamp = f"{packages_stamp(repo)} {version}"
    if not stamp_file.exists() or stamp_file.read_text().strip() != stamp:
        install(repo, python, home)
        stamp_file.write_text(stamp + "\n")
    else:
        log.info("uv.lock and pyproject.toml did not change: the packages are up to date")

    link_home_files(repo, home, warn=warn)
    assemble.write_launchers(repo, home, version, platform)
    return python


def usage(repo, home, platform):
    """Return the lines that say how to use the development home."""
    repo = Path(repo).resolve()
    home = Path(home).resolve()
    try:
        shown = home.relative_to(Path.cwd())
    except ValueError:
        shown = home
    if platform == "windows":
        command = f"{shown}\\mast-system.bat list-domains -a lab -c ..."
    else:
        command = f"{shown}/mast-system list-domains -a lab -c ..."
    return [
        f"Development home: {home} (code: {repo}, editable)",
        f"Run:  {command}",
        "Tests: uv run pytest mast/testsuite/unit mast/testsuite/regression tools"
        "   (they make their own home, see conftest.py)",
    ]
