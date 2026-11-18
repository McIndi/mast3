"""
The command line of ./mast-setup.

With no mode, it builds the installer: a zip with a Python interpreter,
the locked dependencies, MAST, the files of `files/mast_home` and the
launchers. The zip needs nothing on the target machine.

With `--development-mode`, it makes a development home that runs the
code of the checkout. See develop.py.

It needs only the standard library and `uv` on the PATH.
"""
import argparse
import logging
import logging.config
import shutil
import sys
import tomllib
from pathlib import Path

from . import assemble, develop, python_dist
from .python_dist import SetupError

REPO = Path(__file__).resolve().parents[2]

log = logging.getLogger("mast_setup")


def read_settings(repo=REPO):
    """Return the [tool.mast-setup] table of pyproject.toml."""
    with (Path(repo) / "pyproject.toml").open("rb") as fp:
        return tomllib.load(fp).get("tool", {}).get("mast-setup", {})


def parser(settings):
    cli = argparse.ArgumentParser(
        prog="mast-setup",
        description="Build the MAST installer, or with --development-mode, "
                    "make a development home that runs the code of this checkout.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    cli.add_argument(
        "--development-mode", action="store_true",
        help="Make or update a development home instead of the installer")
    cli.add_argument(
        "--home", type=Path, default=REPO / ".dev" / "mast_home",
        help="The development home (with --development-mode)")
    cli.add_argument(
        "--recreate", action="store_true",
        help="Delete the development home first (with --development-mode)")
    cli.add_argument(
        "-p", "--python-version", default=settings.get("python", "3.14"),
        help="The version of CPython to bundle. Give a minor version (3.14) "
             "to use the newest patch release of the python-build-standalone "
             "release, or a full version (3.14.10) to require that patch.")
    cli.add_argument(
        "--python-build-standalone", default=settings.get("python-build-standalone", "latest"),
        help="The python-build-standalone release tag, or 'latest' to look "
             "up the newest release (to upgrade the interpreter)")
    cli.add_argument(
        "--linux-glibc", default=settings.get("linux-glibc", "2.28"),
        help="The oldest glibc that the Linux installer runs on. The "
             "dependencies are installed from wheels for that glibc, "
             "whatever the glibc of the build machine.")
    cli.add_argument(
        "--build-directory", type=Path, default=REPO / "build",
        help="The directory for the build. Its 'assemble' and 'wheel' "
             "folders are deleted at the start.")
    cli.add_argument(
        "--dist-directory", type=Path, default=REPO / "dist",
        help="The directory for the installer. WARNING: it is deleted at the start.")
    cli.add_argument(
        "--cache-directory", type=Path, default=REPO / "build-cache",
        help="The directory for downloads. It is kept between builds.")
    cli.add_argument(
        "--log-level", type=int, default=30,
        help="The level (0-50) at which to filter log messages (lower is more verbose)")
    return cli


def configure_logging(level, log_file):
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {"format": "%(levelname)-8s: %(asctime)s: %(message)s"},
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": "simple",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "class": "logging.FileHandler",
                "formatter": "simple",
                "level": level,
                "filename": str(log_file),
                "mode": "w",
            },
        },
        "root": {"level": level, "handlers": ["stdout", "file"]},
    })


def resolve_tag(tag):
    if tag == "latest":
        return python_dist.latest_tag()
    return tag


def build(args, platform, machine):
    """Build the installer and return the path of the zip."""
    build_directory = args.build_directory
    home = build_directory / "assemble"
    shutil.rmtree(home, ignore_errors=True)
    shutil.rmtree(args.dist_directory, ignore_errors=True)
    home.mkdir(parents=True)
    args.dist_directory.mkdir(parents=True)

    tag = resolve_tag(args.python_build_standalone)
    archive, version = python_dist.fetch_python(
        tag, args.python_version, args.cache_directory, platform, machine)
    python = assemble.unpack_python(archive, home, version, platform)
    assemble.upgrade_pip(python)
    requirements = assemble.export_requirements(
        REPO, build_directory / "requirements.txt")
    python_platform = None
    if platform == "linux":
        python_platform = assemble.linux_platform(args.linux_glibc, machine)
    assemble.install_requirements(python, requirements, python_platform)
    wheel = assemble.build_wheel(REPO, build_directory / "wheel")
    assemble.install_mast(python, wheel)
    assemble.copy_home_files(REPO, home)
    assemble.write_launchers(REPO, home, version, platform)
    mast_version = assemble.read_mast_version(python, home)
    assemble.check_bundle(home)
    name = assemble.archive_name(mast_version, version, platform, machine)
    zip_file = assemble.make_zip(home, args.dist_directory, name)
    log.warning(f"Built {zip_file}")
    return zip_file


def development(args, platform, machine):
    """Make or update the development home."""
    tag = None

    def prepare_python(home):
        nonlocal tag
        tag = resolve_tag(args.python_build_standalone)
        archive, version = python_dist.fetch_python(
            tag, args.python_version, args.cache_directory, platform, machine)
        python = assemble.python_executable(home, version, platform)
        if not python.exists():
            python = assemble.unpack_python(archive, home, version, platform)
            assemble.upgrade_pip(python)
        return python, version

    develop.develop(REPO, args.home, platform, prepare_python,
                    recreate=args.recreate)
    for line in develop.usage(REPO, args.home, platform):
        print(line)


def main(argv=None):
    settings = read_settings()
    args = parser(settings).parse_args(argv)
    args.build_directory.mkdir(parents=True, exist_ok=True)
    configure_logging(args.log_level, args.build_directory / "mast-setup.log")
    if shutil.which("uv") is None:
        log.critical("mast-setup needs uv on the PATH. See https://docs.astral.sh/uv/")
        return 7
    try:
        platform = python_dist.current_platform()
        machine = python_dist.current_machine()
        if args.development_mode:
            development(args, platform, machine)
        else:
            build(args, platform, machine)
    except SetupError as e:
        log.critical(str(e))
        return e.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
