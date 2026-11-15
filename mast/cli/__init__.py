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

A command declares its parameters in one of two ways:

* With a model (preferred). The function takes one parameter, which
is annotated with a subclass of `mast.cli.Params`. The options, their
help and their validation come from the model. See `mast.cli.params`.
* With its signature (legacy). The options are derived from the default
values of the function's parameters. Commands move from this form to
the model form one plugin at a time, and this form will then be removed.
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
from mast.cli.results import Result, Text, Table, say, captured

from collections import defaultdict
from pydantic import ValidationError

__all__ = [
    "Cli", "Command", "Params", "Option",
    "Result", "Text", "Table", "say", "captured",
]


def params_model(fn):
    """
    _function_: `mast.cli.params_model(fn)`

    Return the `Params` subclass that `fn` takes, or `None` if `fn`
    declares its parameters with its signature (the legacy form).
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
    * `fn`: The function to call. For a model command it takes either
    one `Params` instance or the fields as keyword arguments.
    * `model`: The `Params` subclass, or `None` for a legacy command.
    * `parser`: The `argparse` parser for this command.
    """
    def __init__(self, name, fn, category=None, model=None, parser=None):
        self.name = name
        self.key = fn.__name__
        self.category = category
        self.fn = fn
        self.model = model
        self.parser = parser

    @property
    def doc(self):
        """The help for the command, as markdown."""
        if self.model is None:
            return str(self.fn.__doc__)
        return _params.describe(self.model, self.fn.__doc__)


def _accept_keywords(fn, model):
    """Wrap a model command so that it can also be called with the
    fields of the model as keyword arguments. Other Python code calls
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

    The preferred way to declare the parameters of a function is a
    `mast.cli.Params` model:

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

    The function returns a `mast.cli.Result` (or `None`) and `run`
    prints it. The function can also be called from Python as
    `greet(name="World")`.

    The rest of this description is about the legacy form, in which the
    options are derived from the function's signature.

    To define an argument to the command line program, you must set
    a default value for the function's parameter. The supported types
    of default arguments are:

    * `str` - When the default argument is a string, a string will be
    expected from the command line
    * `list` - When the default argument is a list, multiple values
    will be accepted from the command line
    * `int` - When the default argument is an integer, an integer
    will be expected from the command line
    * `float` - When the default argument is a float, a float will
    be expected from the command line
    * `bool` - When the default argument is a boolean value, the option
    will be a flag, which when present will provide the oposite value
    as the default argument

    There are two main ways to use this library:

    Many functions as sub-commands:

        :::python
        from mast.cli import Cli

        cli = Cli()

        @cli.command
        def subcommand_one(arg_1="", arg_2=10):
            do_something(arg_1, arg_2)

        @cli.command
        def subcommand_two(arg_1=20, flag_1=False):
            do_something_else(arg_1, flag_1)

        if __name__ == "__main__":
            try:
                cli.run()
            except Exception:
                log_something()
                raise

    One `main` function as the program:

        :::python
        from mast.cli import Cli

        def main(arg_1=[], arg_2=2.0, flag_1=True):
            do_something()

        if __name__ == "__main__":
            cli = Cli(main=main)
            try:
                cli.run()
            except Exception:
                log_something()
                raise
    """
    def __init__(self, description="", main=None, optionals=None):
        """
        _method_: `mast.cli.Cli.__init__(self, description="", main=None, optionals=None)`

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
        * `optionals`: If provided, `optionals` should be a `dict`
        containing the name and value of optional arguments. Any parameter
        not in this `dict` will be considered required by your program.
        """
        self.main = main
        self.optionals = optionals or {}
        self.functions = {}
        self.commands = {}
        self._command_list = defaultdict(list)
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
        subparser then returns the function to register. A legacy
        function is returned unchanged. A model function is returned
        wrapped, so that it also accepts keyword arguments.

        Parameters:

        * `fn`: The function to create a sub-command for.
        """
        if not name:
            name = fn.__name__
        name = name.replace("_", "-")
        if name in self.commands:
            raise ValueError("the command {} is already defined".format(name))
        model = params_model(fn)
        if model is not None:
            fn = _accept_keywords(fn, model)
        self.functions[name] = fn
        self._command_list[category].append(fn)

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

        if model is not None:
            _params.add_arguments(_parser, model)
            return fn

        args, _, __, defaults, ___, ____, _____ = inspect.getfullargspec(fn)

        args = [] if not args else args
        defaults = [] if not defaults else defaults

        positionals = args[:len(args) - len(defaults)]
        keywords = [x for x in zip(args[len(args) - len(defaults):], defaults)]

        for arg in positionals:
            if arg in self.optionals:
                _parser.add_argument(
                    arg, nargs='?', default=self.optionals[arg])
            else:
                _parser.add_argument(arg)

        for arg, default in keywords:
            _arg = arg.replace("_", "-")
            # Try the lower case first letter for the short option first
            params = _parser._option_string_actions
            if '-{}'.format(arg[0]) not in params:
                flag = ('-{}'.format(arg[0]), '--{}'.format(_arg))
            # Then the upper-case first letter for the short option
            elif '-{}'.format(arg[0]).upper() not in params:
                flag = ('-{}'.format(arg[0]).upper(), '--{}'.format(_arg))
            # otherwise no short option
            else:
                flag = ('--{}'.format(_arg), )
            if isinstance(default, (str, type(None))):
                _parser.add_argument(*flag, type=str, default=default)
            elif isinstance(default, list):
                _parser.add_argument(*flag, action='append')
            elif isinstance(default, bool):
                if default:
                    _parser.add_argument(
                        '--no-{}'.format(_arg), dest=arg, action='store_false', default=default)
                else:
                    _parser.add_argument(
                        *flag, action='store_true', dest=arg, default=default)
            elif isinstance(default, int):
                _parser.add_argument(*flag, type=int, dest=arg, default=default)
            elif isinstance(default, float):
                _parser.add_argument(*flag, type=float, dest=arg, default=default)
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

        Usage

            :::python
            from mast.cli import Cli

            cli = Cli()

            @cli.command
            def function_1(param=""):
                do_something()

            @cli.command
            def function_2(param=""):
                do_something()

            if __name__ == "__main__":
                try:
                    cli.run()
                except Exception:
                    logger.exception()
                    raise
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
        if command.model is not None:
            return self._run_model_command(command, args, log)
        func = args.func
        log.debug(f"Found func: {func}")
        _args, _, __, defaults, ___, ____, _____ = inspect.getfullargspec(func)
        log.debug(f"Found argspec: {_args}, {_}, {__}, {defaults}, {___}, {____}, {_____}")
        kwargs = {}
        for arg in _args:
            kwargs[arg] = getattr(args, arg)
        # Uncomment for debugging purposes only, will log credentials
        # log.debug(f"Built kwargs: {kwargs}")
        log.info(f"Attempting to execute func: {func}")
        try:
            func(**kwargs)
        except Exception as e:
            log.exception(f"Found exception: {e}")
            raise

    def _run_model_command(self, command, args, log):
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
