"""
Make the console scripts of a home relocatable.

uv writes the absolute path of the interpreter into each console script
(`mast-system`, `flask`, `pip` ...): the first line of a script on Linux
and macOS, and a resource of the `.exe` launcher on Windows. That path
is in the build folder, so the scripts fail once the installer is
unzipped somewhere else. This module replaces it with a path relative
to the script:

* Linux and macOS: the first line becomes the `/bin/sh` header that
  python-build-standalone uses for its own scripts (`pip`, `pydoc3`). It
  runs the interpreter next to the script, following a symlink to the
  script.
* Windows: the launchers are uv trampolines. They read the interpreter
  from the `UV_PYTHON_PATH` resource and resolve a relative path from
  their own folder, so the resource becomes `..\\python.exe`. This can
  only be done on Windows, where the Windows installer is built.
"""
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

POSIX_HEADER = (
    "#!/bin/sh\n"
    "'''exec' \"$(dirname -- \"$(realpath -- \"$0\")\")/{name}\" \"$0\" \"$@\"\n"
    "' '''\n"
)

RESOURCE_PYTHON_PATH = "UV_PYTHON_PATH"
RT_RCDATA = 10


def scripts_directory(python, platform):
    """Return the folder of the console scripts of an interpreter."""
    python = Path(python)
    if platform == "windows":
        return python.parent / "Scripts"
    return python.parent


def _absolute_shebang(path):
    """Return the interpreter of an absolute `#!` line, or None."""
    try:
        with open(path, "rb") as fp:
            first = fp.readline(4096)
    except OSError:
        return None
    if not first.startswith(b"#!/"):
        return None
    target = first[2:].strip()
    # "#!/usr/bin/env python3" and "#!/bin/sh" have arguments or point
    # outside the interpreter folder; only a bare path is a candidate.
    if b" " in target:
        return None
    try:
        return target.decode("utf-8")
    except UnicodeDecodeError:
        return None


def relocate_posix(bin_dir):
    """
    Rewrite the scripts in `bin_dir` whose first line names an
    interpreter in `bin_dir` by its absolute path. Return their names.
    """
    bin_dir = Path(bin_dir).resolve()
    changed = []
    for path in sorted(bin_dir.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        target = _absolute_shebang(path)
        if target is None or Path(target).parent.resolve() != bin_dir:
            continue
        data = path.read_bytes()
        rest = data.split(b"\n", 1)[1] if b"\n" in data else b""
        header = POSIX_HEADER.format(name=Path(target).name).encode("utf-8")
        mode = path.stat().st_mode
        path.write_bytes(header + rest)
        path.chmod(mode)
        changed.append(path.name)
    return changed


def check_posix(bin_dir, root):
    """Return the scripts in `bin_dir` whose first line names a file in `root`."""
    problems = []
    for path in sorted(Path(bin_dir).iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        target = _absolute_shebang(path)
        # Resolve the folder, not the interpreter: the interpreter can be
        # a link to another file.
        if target and Path(target).parent.resolve().is_relative_to(root):
            problems.append(f"{path.name}: its first line names {target}")
    return problems


class _Kernel32:
    """The Win32 functions that read and write resources of a PE file."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.LoadLibraryExW.restype = ctypes.c_void_p
        k.LoadLibraryExW.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD)
        k.FindResourceW.restype = ctypes.c_void_p
        k.FindResourceW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p)
        k.SizeofResource.restype = wintypes.DWORD
        k.SizeofResource.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        k.LoadResource.restype = ctypes.c_void_p
        k.LoadResource.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        k.LockResource.restype = ctypes.c_void_p
        k.LockResource.argtypes = (ctypes.c_void_p,)
        k.FreeLibrary.restype = wintypes.BOOL
        k.FreeLibrary.argtypes = (ctypes.c_void_p,)
        k.BeginUpdateResourceW.restype = ctypes.c_void_p
        k.BeginUpdateResourceW.argtypes = (wintypes.LPCWSTR, wintypes.BOOL)
        k.UpdateResourceW.restype = wintypes.BOOL
        k.UpdateResourceW.argtypes = (ctypes.c_void_p, ctypes.c_void_p, wintypes.LPCWSTR,
                                      wintypes.WORD, ctypes.c_void_p, wintypes.DWORD)
        k.EndUpdateResourceW.restype = wintypes.BOOL
        k.EndUpdateResourceW.argtypes = (ctypes.c_void_p, wintypes.BOOL)
        self.k = k

    def error(self, what, path):
        code = self.ctypes.get_last_error()
        return OSError(f"{what} failed for {path}: {self.ctypes.FormatError(code)}")

    def read(self, path, name):
        """Return the RCDATA resource `name` of a PE file, or None."""
        # LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE
        module = self.k.LoadLibraryExW(str(path), None, 0x2 | 0x20)
        if not module:
            return None
        try:
            resource = self.k.FindResourceW(module, name, RT_RCDATA)
            if not resource:
                return None
            size = self.k.SizeofResource(module, resource)
            pointer = self.k.LockResource(self.k.LoadResource(module, resource))
            return self.ctypes.string_at(pointer, size)
        finally:
            self.k.FreeLibrary(module)

    def write(self, path, name, data):
        """Replace the RCDATA resource `name` (language neutral) of a PE file."""
        handle = self.k.BeginUpdateResourceW(str(path), False)
        if not handle:
            raise self.error("BeginUpdateResource", path)
        buffer = self.ctypes.create_string_buffer(data, len(data))
        if not self.k.UpdateResourceW(handle, RT_RCDATA, name, 0, buffer, len(data)):
            self.k.EndUpdateResourceW(handle, True)
            raise self.error("UpdateResource", path)
        if not self.k.EndUpdateResourceW(handle, False):
            raise self.error("EndUpdateResource", path)


def relocate_windows(scripts_dir, python):
    """
    Point the uv launchers in `scripts_dir` that run `python` by its
    absolute path to it by a relative path. Return their names.
    """
    kernel32 = _Kernel32()
    python = Path(python)
    relative = os.path.relpath(python, scripts_dir).encode("utf-8")
    changed = []
    for path in sorted(Path(scripts_dir).glob("*.exe")):
        data = kernel32.read(path, RESOURCE_PYTHON_PATH)
        if data is None:
            continue
        target = Path(data.decode("utf-8"))
        if not target.is_absolute():
            continue
        if os.path.normcase(os.path.abspath(target)) != os.path.normcase(os.path.abspath(python)):
            continue
        kernel32.write(path, RESOURCE_PYTHON_PATH, relative)
        changed.append(path.name)
    return changed


def check_windows(scripts_dir, root):
    """Return the launchers in `scripts_dir` that name a file in `root`."""
    kernel32 = _Kernel32()
    problems = []
    for path in sorted(Path(scripts_dir).glob("*.exe")):
        data = kernel32.read(path, RESOURCE_PYTHON_PATH)
        if data is None:
            continue
        target = Path(data.decode("utf-8"))
        if target.is_absolute() and target.is_relative_to(root):
            problems.append(f"{path.name}: it runs {target}")
    return problems


def make_relocatable(python, platform):
    """Make the console scripts of the interpreter `python` relocatable."""
    directory = scripts_directory(python, platform)
    if not directory.is_dir():
        return []
    if platform == "windows":
        if sys.platform != "win32":
            raise RuntimeError("The Windows launchers can only be changed on Windows")
        changed = relocate_windows(directory, python)
    else:
        changed = relocate_posix(directory)
    log.info(f"Made {len(changed)} console scripts relocatable in {directory}")
    return changed


def check(root):
    """
    Return a problem for each console script under `root` that names an
    interpreter in `root` by its absolute path.
    """
    root = Path(root).resolve()
    problems = []
    python_root = root / "python"
    if not python_root.is_dir():
        return problems
    for version_dir in sorted(python_root.iterdir()):
        bin_dir = version_dir / "bin"
        if bin_dir.is_dir() and not bin_dir.is_symlink():
            problems += [f"{bin_dir.relative_to(root)}/{p}" for p in check_posix(bin_dir, root)]
        scripts_dir = version_dir / "Scripts"
        if scripts_dir.is_dir() and sys.platform == "win32":
            problems += [f"{scripts_dir.relative_to(root)}/{p}"
                         for p in check_windows(scripts_dir, root)]
    return problems
