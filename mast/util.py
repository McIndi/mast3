def _s(s):
    if isinstance(s, str):
        return s
    elif isinstance(s, bytes):
        return s.decode()
    else:
        return str(s)

def _b(b):
    if isinstance(b, str):
        return b.encode()
    elif isinstance(b, bytes):
        return b
    else:
        return bytes(b)


def require_optional(module_name):
    """
    Import the module `module_name` and return it.

    Use this in a script that needs a library which MAST does not
    install. If the library is not there, this function writes what is
    missing and how to install it to the error log and to stderr, and
    then stops the script with exit status 1.

    Usage:

        :::python
        from mast.util import require_optional

        pd = require_optional("pandas")
    """
    import importlib
    import os
    import sys
    try:
        return importlib.import_module(module_name)
    except ImportError:
        from mast.logging import make_logger
        script = os.path.basename(sys.argv[0]) or "This script"
        message = (
            "{script} needs the Python library '{name}', which MAST does "
            "not install. To install it, run this command in your MAST "
            "directory and then try again: mast -m pip install {name}"
        ).format(script=script, name=module_name)
        make_logger("error").error(message)
        sys.exit(message)
