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
# Copyright 2015-2024, McIndi Solutions, All rights reserved.
"""
_module_: `mast.cli`

Provide an easy way to convert a function to a Command Line Program.

There are two main methods in the one class provided (Cli):

* `command`: A decorator which converts a function to a CLI
* `run`: A method which parses the command line arguments and
executes the appropriate function

A command is a function that takes one parameter, which is annotated
with a subclass of `mast.cli.Params`. The options, their help and their
validation come from that model. See `mast.cli.params`.
"""
import sys
import inspect
import argparse
import functools
import typing
from mast import __version__
from mast.logging import make_logger
from mast.cli import params as _params
from mast.cli.params import Params, Option
from mast.cli.results import (
    Result, Text, Table, say, captured, listening)

from collections import defaultdict
from pydantic import ValidationError

__all__ = [
    "Cli", "Command", "Params", "Option",
    "Result", "Text", "Table", "say", "captured", "listening",
]


def params_model(fn):
    """
    _function_: `mast.cli.params_model(fn)`

    Return the `Params` subclass that `fn` takes, or `None` if `fn`
    is not a command.
    """
    parameters = list(inspect.signature(fn).parameters.values())
    if len(parameters) != 1:
        return None
    try:
        annotation = typing.get_type_hints(fn).get(parameters[0].name)
    except Exception:
        return None
    if isinstance(annotation, type) and issubclass(annotation, Params):
        return annotation
    return None


class Command(object):
    """
    _class_: `mast.cli.Command`

    One registered command.

    * `name`: The name on the command line, for example `fetch-logs`.
    * `key`: The name of the function, which the web GUI uses.
    * `category`: The heading under which the web GUI lists the command.
    * `fn`: The function to call. It takes either one `Params`
    instance or the fields of the model as keyword arguments.
    * `model`: The `Params` subclass.
    * `parser`: The `argparse` parser for this command.
    """
    def __init__(self, name, fn, category, model, parser):
        self.name = name
        self.key = fn.__name__
        self.category = category
        self.fn = fn
        self.model = model
        self.parser = parser

    @property
    def doc(self):
        """The help for the command, as markdown."""
        return _params.describe(self.model, self.fn.__doc__)


def _accept_keywords(fn, model):
    """Wrap a command so that it can also be called with the fields
    of its model as keyword arguments. Other Python code calls
    commands this way, and the arguments are validated the same as
    arguments from the command line."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if len(args) == 1 and not kwargs and isinstance(args[0], model):
            return fn(args[0])
        if args:
            raise TypeError(
                "{}() takes one {} or keyword arguments".format(
                    fn.__name__, model.__name__))
        return fn(model(**kwargs))
    return wrapper

class Cli(object):
    """
    _class_: `mast.cli.Cli()`: see `mast.cli.Cli.__init__` for arguments
    to the constructor.

    This is a class which generates a command line interface for
    functions.

    A function declares its parameters with a `mast.cli.Params` model:

        :::python
        from mast.cli import Cli, Params, Option, Text

        cli = Cli()

        class Greet(Params):
            name: str = Option(short="n", help="Who to greet")
            shout: bool = Option(False, short="s", help="Use capitals")

        @cli.command("greet")
        def greet(params: Greet):
            'Greet someone.'
            text = "Hello, {}".format(params.name)
            return Text(text.upper() if params.shout else text)

        if __name__ == "__main__":
            cli.run()

    The function returns a `mast.cli.Result` (or `None`) and `run`
    prints it. The function can also be called from Python as
    `greet(name="World")`.

    There are two ways to use this library. The example above has many
    functions as sub-commands (`program greet -n World`). A program
    that does one thing gives its one function as `main`, and has no
    sub-commands (`program -n World`):

        :::python
        from mast.cli import Cli, Params, Option

        class Main(Params):
            name: str = Option(short="n", help="Who to greet")

        def main(params: Main):
            print("Hello, {}".format(params.name))

        if __name__ == "__main__":
            cli = Cli(main=main)
            cli.run()
    """
    def __init__(self, description="", main=None):
        """
        _method_: `mast.cli.Cli.__init__(self, description="", main=None)`

        This is the constructor for this class. A few options are
        available which allows defining how the program will appear
        to the user.

        Parameters:

        * `description`: This is the description which will be
        displayed (along with details on the available parameters) to
        the user when they provide a `-h` or `--help` on the command
        line
        * `main`: If you provide an argument to this parameter,
        it should be a function, and this function will be treated
        as the main function and it will be executed when your program
        is called from the command line. __NOTE__ that if `main` is
        provided, you cannot create sub-commands using the `command`
        decorator.
        """
        self.main = main
        self.commands = {}
        self.parser = argparse.ArgumentParser(
            description=description,
            formatter_class=argparse.RawDescriptionHelpFormatter)
        if self.main is None:
            self.subparsers = self.parser.add_subparsers(
                title="commands", metavar="command")
        else:
            self.main = self.create_subparser(self.main)

    def categories(self):
        """
        _method_: `mast.cli.Cli.categories(self)`

        Return a `dict` that maps each category to the list of its
        `Command`s, in the order in which they were registered.
        """
        result = defaultdict(list)
        for command in self.commands.values():
            result[command.category].append(command)
        return dict(result)

    def find(self, key):
        """
        _method_: `mast.cli.Cli.find(self, key)`

        Return the `Command` whose function is named `key`. Raises
        `KeyError` if there is none.
        """
        for command in self.commands.values():
            if command.key == key:
                return command
        raise KeyError(key)

    def create_subparser(self, fn, name=None, category=None):
        """
        _method_: `mast.cli.Cli.create_subparser(self, fn)`

        __Internal use only__

        collects information about decorated function, builds a
        subparser then returns the function to register. The function
        is returned wrapped, so that it also accepts the fields of its
        model as keyword arguments.

        Parameters:

        * `fn`: The function to create a sub-command for.
        * `name`: The name of the sub-command. The default is the name
        of the function.
        * `category`: The heading under which the web GUI lists the
        command.
        """
        if not name:
            name = fn.__name__
        name = name.replace("_", "-")
        if name in self.commands:
            raise ValueError("the command {} is already defined".format(name))
        model = params_model(fn)
        if model is None:
            raise TypeError(
                "{}: a command takes one parameter, annotated with a "
                "subclass of mast.cli.Params".format(fn.__name__))
        fn = _accept_keywords(fn, model)

        desc = fn.__doc__

        if self.main is None:
            _parser = self.subparsers.add_parser(
                name,
                help=_summary(desc),
                description=desc,
                formatter_class=argparse.RawDescriptionHelpFormatter)
        else:
            _parser = self.parser
            if not _parser.description:
                _parser.description = desc
        command = Command(name, fn, category, model, _parser)
        self.commands[name] = command
        _parser.set_defaults(func=fn, _command=command)

        _params.add_arguments(_parser, model)
        return fn

    def command(self, name=None, category=None):
        """
        _method_: `mast.cli.Cli.command(self)`

        This decorator allows you to decorate many functions which
        will then be available to the user as sub-commands. Please see
        above for how a function declares its parameters.

        Parameters:

        * `name`: The name of the sub-command. The default is the name
        of the function.
        * `category`: The heading under which the web GUI lists the
        command.
        """
        def inner(fn):
            if self.main is not None:
                print("The Cli decorator cannot be used when main is specified")
                sys.exit(-1)
            return self.create_subparser(fn, name, category)
        return inner

    def run(self, argv=None):
        """
        _method_: `mast.cli.Cli.run(self, argv=None)`

        This method will attempt to parse the provided arguments and
        execute the required function.

        Parameters:

        * `argv`: The arguments to parse. The default is `sys.argv[1:]`.
        """
        log = make_logger("cli")
        # Uncomment for debugging purposes only, will log credentials
        # log.debug(f"In run, found sys.argv: {sys.argv}")
        args = self.parser.parse_args(argv)
        # Uncomment for debugging purposes only, will log credentials
        # log.debug(f"Parsed args: {args}")
        command = getattr(args, "_command", None)
        if command is None:
            self.parser.error("a command is required")
        return self._run_command(command, args, log)

    def _run_command(self, command, args, log):
        try:
            values = _params.from_namespace(command.model, args)
        except ValidationError as exc:
            command.parser.error("; ".join(
                _params.error_messages(command.model, exc)))
        log.info(f"Attempting to execute func: {command.fn}")
        try:
            result = command.fn(values)
        except Exception as e:
            log.exception(f"Found exception: {e}")
            raise
        if isinstance(result, Result):
            result.emit()


def _summary(doc):
    """The first paragraph of a docstring on one line, for the list of
    commands in `--help`."""
    paragraph = (doc or "").strip().split("\n\n")[0]
    return " ".join(paragraph.split()).replace("%", "%%") or None
