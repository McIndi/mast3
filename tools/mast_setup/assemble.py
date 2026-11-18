"""
Lay out a MAST home in a folder: the interpreter, the dependencies,
MAST, the files of `files/mast_home` and the launchers.

The installer and the development home use the same steps. They differ
in how MAST is installed (a wheel, or editable) and in how the files of
`files/mast_home` get into the home (copies, or links). See develop.py.
"""
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from . import scripts
from .python_dist import SetupError

log = logging.getLogger(__name__)


class BundleError(SetupError):
    """The assembled folder must not be shipped."""
    code = 6


class HomeFilesError(SetupError):
    code = 5


def run(command, **kwargs):
    """Run a command, log it and fail on a non-zero exit status."""
    command = [str(part) for part in command]
    log.info("Running: " + " ".join(command))
    return subprocess.run(command, check=True, **kwargs)


def python_executable(home, version, platform):
    """Return the path of the interpreter of a home."""
    base = Path(home) / "python" / version
    if platform == "windows":
        return base / "python.exe"
    return base / "bin" / "python3"


def unpack_python(archive, home, version, platform):
    """
    Unpack a python-build-standalone archive into `home/python/<version>`
    and return the path of the interpreter.
    """
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    staging = home / "python.unpack"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.unpack_archive(str(archive), str(staging))
    # The archive has one top folder, "python". Move what is in it to
    # python/<version>, which is where set-env looks.
    target = home / "python" / version
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(target, ignore_errors=True)
    (staging / "python").rename(target)
    shutil.rmtree(staging)
    return python_executable(home, version, platform)


def upgrade_pip(python):
    """
    Upgrade pip in the bundle. pip stays in the installer: on a jump
    server it is how someone installs a wheel they carried in.
    """
    run([python, "-m", "pip", "install", "--upgrade", "--no-warn-script-location", "pip"])


def export_requirements(repo, destination):
    """
    Write the runtime dependencies of uv.lock, with hashes and platform
    markers, to `destination`. `--frozen` uses the lock as it is and
    fails if there is none.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(["uv", "export", "--frozen", "--no-dev", "--no-emit-project",
         "--format", "requirements-txt", "--output-file", destination],
        cwd=repo, stdout=subprocess.DEVNULL)
    return destination


def linux_platform(glibc, machine):
    """
    Return the uv target (`--python-platform`) for Linux wheels that run
    on glibc `glibc` (for example 2.28) and newer.
    """
    return f"{machine}-manylinux_{glibc.replace('.', '_')}"


def install_requirements(python, requirements, python_platform=None):
    """
    Install exported requirements into the interpreter of a home.

    Without `python_platform`, uv picks the newest wheels that the build
    machine can run. On Linux, those can need a newer glibc than the
    machines MAST runs on, so the installer passes a target.
    """
    command = ["uv", "pip", "install", "--python", python,
               "--require-hashes", "--link-mode", "copy", "--compile-bytecode"]
    if python_platform:
        command += ["--python-platform", python_platform]
    run(command + ["--requirements", requirements])


def build_wheel(repo, out_dir):
    """
    Build the MAST wheel and return its path. uv builds the sdist first
    and the wheel from the sdist, in a clean folder. A wheel built in the
    checkout can contain stale modules from the `build/lib` folder of
    setuptools.
    """
    out_dir = Path(out_dir)
    shutil.rmtree(out_dir, ignore_errors=True)
    run(["uv", "build", "--out-dir", out_dir, repo])
    wheels = list(out_dir.glob("mast-*.whl"))
    if len(wheels) != 1:
        raise SetupError(f"Expected one MAST wheel in {out_dir}, found {wheels}")
    return wheels[0]


def install_mast(python, source, editable=False):
    """
    Install MAST into the interpreter of a home, without dependencies.
    `source` is a wheel, or the checkout when `editable` is true. Call
    `scripts.make_relocatable` after the last install.
    """
    command = ["uv", "pip", "install", "--python", python, "--no-deps",
               "--link-mode", "copy", "--compile-bytecode", "--reinstall-package", "mast"]
    if editable:
        command += ["--editable", source]
    else:
        command += [source]
    run(command)


def tracked_home_files(repo):
    """
    Return the paths, relative to `files/mast_home`, of the files that
    git tracks there (the index, so a file that was just added counts).
    """
    output = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "files/mast_home"], cwd=repo)
    prefix = "files/mast_home/"
    return sorted(name[len(prefix):]
                  for name in output.decode("utf-8").split("\0")
                  if name.startswith(prefix))


def copy_home_files(repo, home):
    """
    Copy the files of `files/mast_home` that git tracks into the home.
    Files that are not tracked (logs and caches of a local run) are not
    shipped. A file at the top of `files/mast_home` is an error: the top
    of a home has only folders and the launchers.
    """
    tracked = tracked_home_files(repo)
    top_files = [p for p in tracked if "/" not in p]
    if top_files:
        raise HomeFilesError(
            f"Non-directory item in files/mast_home: {', '.join(top_files)}")
    source = Path(repo) / "files" / "mast_home"
    for relative in tracked:
        destination = Path(home) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)


def write_launchers(repo, home, version, platform):
    """
    Write the launchers of the platform into the home, with the Python
    version in set-env, and make them executable off Windows.
    """
    script_dir = Path(repo) / "files" / "invocation_scripts" / platform
    written = []
    for item in sorted(script_dir.iterdir()):
        destination = Path(home) / item.name
        if destination.is_symlink():
            destination.unlink()
        if item.name.startswith("set-env"):
            contents = item.read_text()
            destination.write_text(contents.replace("{PYTHON_VERSION}", version))
        else:
            shutil.copy(item, destination)
        if platform != "windows":
            destination.chmod(0o750)
        written.append(destination)
    return written


def read_mast_version(python, home):
    """Return the version of MAST that is installed in the home."""
    output = subprocess.check_output(
        [str(python), "-c", "import mast; print(mast.__version__)"],
        cwd=home)
    return output.strip().decode()


def archive_name(mast_version, python_version, platform, machine):
    """
    Return the file name of the installer, without `.zip`. Windows and
    Linux are built for x86_64 only. macOS is built for Intel and for
    Apple silicon, so the macOS name has the architecture.
    """
    archive_platform = platform
    if platform == "darwin":
        archive_platform = f"{platform}-{machine}"
    return f"MAST-{mast_version}_py{python_version}_{archive_platform}"


def _is_inside(path, root):
    try:
        Path(path).relative_to(root)
        return True
    except ValueError:
        return False


def check_bundle(root):
    """
    Raise `BundleError` if the folder refers to anything outside itself,
    so it cannot be shipped. This is what makes a development home
    (develop.py) impossible to ship by mistake. The checks are:

    * a symlink that is absolute or points outside the folder (the
      interpreter has relative links inside itself, which zip stores as
      files);
    * an editable install, or an install from a local folder, in a
      `*.dist-info/direct_url.json` (a wheel that was installed from a
      local file is fine: its code is in the folder);
    * a `__editable__` file of setuptools;
    * a path in a `*.pth` file that is outside the folder;
    * a console script that runs the interpreter of the folder by its
      absolute path (see scripts.py), which breaks once it is unzipped.
    """
    root = Path(root).resolve()
    problems = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            relative = path.relative_to(root)
            if path.is_symlink():
                target = os.readlink(path)
                if os.path.isabs(target) or not _is_inside(
                        (path.parent / target).resolve(), root):
                    problems.append(f"{relative}: a symlink to {target}")
                continue
            if name.startswith("__editable__"):
                problems.append(f"{relative}: an editable install")
            if name == "direct_url.json" and path.parent.name.endswith(".dist-info"):
                problems.extend(_check_direct_url(path, relative))
            if name.endswith(".pth") and path.is_file():
                problems.extend(_check_pth(path, relative, root))
    problems.extend(scripts.check(root))
    if problems:
        raise BundleError(
            f"{root} refers to files outside itself and must not be shipped:\n  "
            + "\n  ".join(problems))


def _check_direct_url(path, relative):
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return [f"{relative}: not valid JSON"]
    url = data.get("url", "")
    dir_info = data.get("dir_info")
    if dir_info is not None and dir_info.get("editable"):
        return [f"{relative}: an editable install of {url}"]
    if dir_info is not None and url.startswith("file:"):
        return [f"{relative}: installed from the folder {url}"]
    return []


def _check_pth(path, relative, root):
    problems = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        # Lines that start with "import" are code, not paths.
        if not line or line.startswith("#") or line.startswith(("import ", "import\t")):
            continue
        target = (path.parent / line).resolve()
        if not _is_inside(target, root):
            problems.append(f"{relative}: a path outside the bundle: {line}")
    return problems


def make_zip(home, dist_directory, name):
    """Zip the home into `dist_directory/<name>.zip` and return its path."""
    archive = shutil.make_archive(
        str(Path(dist_directory) / name), format="zip",
        root_dir=str(home), base_dir=".")
    return Path(archive)
