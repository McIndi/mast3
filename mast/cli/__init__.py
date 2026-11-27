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

A `Cli` that has a `provider` does not call its commands itself. It
puts each invocation in the job queue (`mast.jobs`) and shows the
progress and the result of the job. A command that is registered with
`queued=False` is the exception: it is always called directly, and
nothing about it is stored.

Each command of a provider has a canonical id, `PROVIDER.NAME`, and a
permission path, `mast/PROVIDER/NAME`. For a plugin, `NAME` is the name
on the command line: `datapower.get-status` is
`mast datapower get-status`. The commands of core (provider `core`) are
under several roots of the `mast` command, so each one gives its
canonical name: `mast crypto inventory` is `core.crypto-inventory`.

Every command of a provider declares what it does (plugin API 1, see
`mast.plugins`): its `effect` (`read` or `change`), the kinds of
target that it accepts or the capabilities that it needs, its target
and secret parameters (from its `Params` model), and its scheduling
locks.
"""
import sys
import inspect
import argparse
import functools
import typing
from mast import __version__
from mast import plugins as _plugins
from mast.logging import make_logger
from mast.cli import params as _params
from mast.cli.params import Params, Option
from mast.cli.results import (
    Result, Text, Table, say, captured, listening, checkpoint, guarded,
    SUCCEEDED, PARTIAL, FAILED)

from collections import defaultdict
from pydantic import ValidationError

__all__ = [
    "Cli", "Command", "Params", "Option", "READ", "CHANGE", "EFFECTS",
    "Result", "Text", "Table", "say", "captured", "listening",
    "checkpoint", "guarded",
    "SUCCEEDED", "PARTIAL", "FAILED",
]


# The effects of a command. `change` is anything that changes a target
# or MAST, even for a moment: a command that writes a temporary file on
# an appliance changes it. A permission to change is needed as well as
# one to execute (see docs/design/mast-4.0.md).
READ = "read"
CHANGE = "change"
EFFECTS = (READ, CHANGE)


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
    * `provider`: The provider of the command (`datapower`, or `core`),
    or `None` for a `Cli` without one.
    * `id_name`: The canonical name of the command in its provider. It
    is `name`, unless core gives another one.
    * `id`: `PROVIDER.ID_NAME`, the id that jobs and schedules store.
    * `path`: `mast/PROVIDER/ID_NAME`, the permission path.
    * `invocation`: The command line that runs it, for example
    `mast datapower get-status`.
    * `category`: The part of the product that it belongs to (for
    DataPower: `system`, `backups`, `crypto`, ...). The catalog and the
    web GUI group commands by it. It is not part of the id, the path or
    the command line.
    * `group`: A finer heading within the category, such as `caches`.
    * `fn`: The function to call. It takes either one `Params`
    instance or the fields of the model as keyword arguments.
    * `model`: The `Params` subclass.
    * `parser`: The `argparse` parser for this command.
    * `effect`: `read` or `change` (see `EFFECTS`): whether the command
    changes a target or MAST. A command that reads secrets (`get-file`)
    still only reads. `None` only for a `Cli` without a provider.
    * `targets`: The kinds of target that the command accepts, such as
    `("datapower",)`. It also accepts every kind that extends one of
    them (see `mast.plugins.Kind`). A plugin may name the kinds of other
    plugins.
    * `needs`: The capabilities that a target must have, such as
    `("collect-crypto",)`. A command of core names no kind of a plugin:
    it gives `needs`, and accepts every kind with those capabilities,
    whichever plugin gives it. A command that acts on no target has
    neither `targets` nor `needs`.
    * `target_params`, `secret_params`: The names of the parameters
    that name targets, and of those that hold secrets (`Option(target=
    True)` and `Option(secret=True)`).
    * `exclusive`: False if the command can share its targets with
    other jobs that are not exclusive. It is not the effect: a command
    can read and still need its targets for itself.
    * `locks`: The names of what the command must not share with
    another job, besides its targets: a directory that it changes,
    for example. Or a function that takes the `Params` instance and
    returns the names. The job queue locks them as it locks the
    targets.
    * `queued`: False if the command must never go through the job
    queue. Such a command talks to no appliance, and its parameters
    or its result must not be stored.
    * `passive`: True if its jobs neither wait for nor hold up other
    jobs (see `mast.jobs.queue.submit`): a poll that only reads, such
    as `mast datapower collect-status`. Only with `exclusive=False`.
    * `internal`: True for a command whose jobs a part of MAST makes
    and runs itself (the Terminal page's `datapower.terminal`, a record
    of a session). The Commands page does not offer it, and it cannot be
    submitted through the web API or scheduled.
    * `interactive`: True for a command that needs a terminal (`mast
    datapower ssh`). It runs only on the command line.
    """
    def __init__(self, name, fn, category, model, parser, exclusive=True,
                 locks=None, queued=True, passive=False, internal=False,
                 group=None, provider=None, id_name=None, interactive=False,
                 effect=None, targets=(), needs=()):
        _check_declarations(name, exclusive, passive, effect, targets, needs)
        self.name = name
        self.key = fn.__name__
        self.category = category
        self.fn = fn
        self.model = model
        self.parser = parser
        self.exclusive = exclusive
        self.locks = locks
        self.effect = effect
        self.targets = tuple(targets)
        self.needs = tuple(needs)
        self.queued = queued
        self.passive = passive
        self.internal = internal
        self.group = group
        self.interactive = interactive
        self.provider = provider
        self.id_name = id_name or name

    @property
    def id(self):
        """`PROVIDER.ID_NAME`, or `None` without a provider."""
        if self.provider is None:
            return None
        return "{}.{}".format(self.provider, self.id_name)

    @property
    def path(self):
        """The permission path, `mast/PROVIDER/ID_NAME`."""
        if self.provider is None:
            return None
        return "mast/{}/{}".format(self.provider, self.id_name)

    @property
    def invocation(self):
        """The command line that runs the command."""
        return self.parser.prog

    @property
    def target_params(self):
        """The names of the parameters that name targets."""
        return tuple(spec.name for spec in _params.options(self.model)
                     if spec.target)

    @property
    def secret_params(self):
        """The names of the parameters that hold secrets."""
        return tuple(spec.name for spec in _params.options(self.model)
                     if spec.secret)

    def problems(self):
        """
        _method_: `mast.cli.Command.problems(self)`

        Return what is wrong with the declarations of the command (plugin
        API 1), as a list of messages. The registry refuses a provider
        with a command that has one.
        """
        found = []
        if self.effect is None:
            found.append("it declares no effect (effect=\"read\" or "
                         "effect=\"change\")")
        takes = self.targets or self.needs
        if takes and not self.target_params:
            found.append("it accepts targets ({}) but no parameter names "
                         "them (Option(target=True))".format(
                             ", ".join(takes)))
        if self.target_params and not takes:
            found.append("its parameter {} names targets, but it accepts "
                         "no kind of target".format(self.target_params[0]))
        return found

    def describe(self):
        """
        _method_: `mast.cli.Command.describe(self)`

        Return the end of `--help`: the effect, and the kinds of target
        that the command accepts on this installation. It is made when
        the help is shown, from the manifests of the plugins.
        """
        text = _epilog(self.effect) or ""
        if self.targets or self.needs:
            kinds = _plugins.accepting(self.targets, self.needs)
            if kinds:
                text += " Targets: {}.".format(", ".join(kinds))
            elif self.needs:
                text += " Targets: no installed kind can {}.".format(
                    ", ".join(self.needs))
            else:
                text += " Targets: {}, which no installed plugin " \
                        "gives.".format(", ".join(self.targets))
        return text.strip() or None

    @property
    def doc(self):
        """The help for the command, as markdown."""
        return _params.describe(self.model, self.fn.__doc__)


def _check_declarations(name, exclusive, passive, effect, targets, needs):
    """Raise `ValueError` for declarations of a command that can never
    be right."""
    if passive and exclusive:
        raise ValueError(
            "{}: a passive command shares its targets "
            "(exclusive=False)".format(name))
    if effect is not None and effect not in EFFECTS:
        raise ValueError("{}: the effect is one of {}, not {!r}".format(
            name, ", ".join(EFFECTS), effect))
    if isinstance(targets, str) or not all(
            _plugins.is_name(kind) for kind in targets):
        raise ValueError(
            "{}: targets is a tuple of kinds of target, not {!r}".format(
                name, targets))
    if isinstance(needs, str) or not all(
            _plugins.is_name(capability) for capability in needs):
        raise ValueError(
            "{}: needs is a tuple of capabilities, not {!r}".format(
                name, needs))


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
    prints it. If the result says that some or all of the command
    failed (see `Result.outcome`), the program exits with status 1. The function can also be called from Python as
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
    def __init__(self, description="", main=None, provider=None, prog=None,
                 queued=True, targets=()):
        """
        _method_: `mast.cli.Cli.__init__(self, description="", main=None,
        provider=None, prog=None, queued=True, targets=())`

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
        * `provider`: If given, `run` executes a command as a job in
        the job queue, with the id `<provider>.<name of the command>`
        (see `mast.jobs`). Each command then also has the options
        `--detach` and `--local`. A plugin gives its provider name
        (`datapower`), which is how a worker finds the command.
        * `prog`: The name of the program in the help. The default is
        `mast <provider>`, the command line of a plugin.
        * `queued`: The default of `queued` for the commands of this
        `Cli` (see `Command`). The management commands of core give
        `False`: they run directly, and are never jobs.
        * `targets`: The default of `targets` for the commands of this
        `Cli` (see `Command`): the DataPower plugin gives
        `("datapower",)`.
        """
        if provider is not None and main is not None:
            raise ValueError("a Cli with main cannot have a provider")
        self.provider = provider
        self.main = main
        self.queued = queued
        self.targets = tuple(targets)
        self.commands = {}
        if prog is None and provider is not None:
            prog = "mast {}".format(provider)
        self.parser = _Parser(
            prog=prog,
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

    def section(self, category):
        """
        _method_: `mast.cli.Cli.section(self, category)`

        Return an object whose `command` decorator registers commands in
        this `Cli` with `category`. A plugin whose commands are in
        several modules gives each module its section:

            :::python
            cli = provider.cli.section("backups")

            @cli.command("normal-backup", group="backups")
            def normal_backup(params: NormalBackup): ...
        """
        return _Section(self, category)

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

    def create_subparser(self, fn, name=None, category=None,
                         exclusive=True, locks=None, queued=None,
                         passive=False, internal=False, group=None, id=None,
                         interactive=False, effect=None, targets=None,
                         needs=()):
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
        * `category`, `group`: See `Command`.
        * `id`: The canonical name of the command, if it is not `name`.
        Only core gives one (see `Command.id_name`).
        * `exclusive`, `locks`, `queued`, `passive`, `internal`,
        `effect`, `targets`, `needs`: See `Command`.
        """
        if not name:
            name = fn.__name__
        name = name.replace("_", "-")
        if queued is None:
            queued = self.queued
        if targets is None:
            targets = self.targets
        if name in self.commands:
            raise ValueError("the command {} is already defined".format(name))
        # Before the parser is made: a refused command leaves nothing
        _check_declarations(name, exclusive, passive, effect, targets, needs)
        if id is not None and self.provider is None:
            raise ValueError("{}: only a Cli with a provider gives ids".format(
                name))
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
        command = Command(name, fn, category, model, _parser, exclusive,
                          locks, queued, passive, internal, group=group,
                          provider=self.provider, id_name=id,
                          interactive=interactive, effect=effect,
                          targets=targets, needs=needs)
        if self.main is None:
            _parser.describe = command.describe
        self.commands[name] = command
        _parser.set_defaults(func=fn, _command=command)

        _params.add_arguments(_parser, model)
        if self.provider is not None and queued:
            queue = _parser.add_argument_group("job queue")
            queue.add_argument(
                "--detach", dest="_detach", action="store_true",
                default=False,
                help="Put the job in the queue, print its id and return. "
                     "A worker must be running to do the job. See "
                     "mast jobs.")
            queue.add_argument(
                "--local", dest="_local", action="store_true",
                default=False,
                help="Do the job in this process, also if a worker is "
                     "running.")
        return fn

    def command(self, name=None, category=None, exclusive=True,
                locks=None, queued=None, passive=False, internal=False,
                group=None, id=None, interactive=False, effect=None,
                targets=None, needs=()):
        """
        _method_: `mast.cli.Cli.command(self)`

        This decorator allows you to decorate many functions which
        will then be available to the user as sub-commands. Please see
        above for how a function declares its parameters.

        Parameters:

        * `name`: The name of the sub-command. The default is the name
        of the function.
        * `category`, `group`: Where the catalog and the web GUI list
        the command. See `Command`.
        * `id`: The canonical name, if it is not `name` (core only).
        * `effect`: `"read"` or `"change"`. Every command of a provider
        gives it. See `Command`.
        * `targets`: The kinds of target that the command accepts. The
        default is the `targets` of the `Cli`; give `()` for a command
        that acts on no target. See `Command`.
        * `needs`: The capabilities that a target must have. Core gives
        this in place of `targets`. See `Command`.
        * `exclusive`: Give False for a command that can share its
        targets with other jobs. See `Command`.
        * `locks`: What the command must not share with another
        job, besides its targets. See `Command`.
        * `queued`: Give False for a command that must never go
        through the job queue. The default is the `queued` of the
        `Cli`. See `Command`.
        * `passive`: Give True for a poll whose jobs neither wait for
        nor hold up other jobs. See `Command`.
        * `internal`: Give True for a command whose jobs a part of MAST
        makes and runs itself. See `Command`.
        * `interactive`: Give True for a command that needs a terminal.
        See `Command`.
        """
        def inner(fn):
            if self.main is not None:
                print("The Cli decorator cannot be used when main is specified")
                sys.exit(-1)
            return self.create_subparser(
                fn, name, category, exclusive, locks, queued, passive,
                internal, group, id, interactive, effect, targets, needs)
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
        if self.provider is not None and command.queued:
            return self._run_job(command, args, values, log)
        log.info(f"Attempting to execute func: {command.fn}")
        try:
            result = command.fn(values)
        except Exception as e:
            log.exception(f"Found exception: {e}")
            raise
        if isinstance(result, Result):
            result.emit()
            if result.outcome != SUCCEEDED:
                sys.exit(1)

    def _run_job(self, command, args, values, log):
        """Put the command in the job queue and show the job, see
        `mast.jobs.client`."""
        # Imported here, because mast.jobs imports this module
        from mast.jobs import client, registry
        from mast.jobs.auth import Forbidden
        # A Cli that MAST does not know (in a test, or a script) is
        # made known, so that a worker in this process finds it
        registry.ensure(self)
        command_id = command.id
        log.info(f"Attempting to queue the command: {command_id}")
        try:
            status = client.run(
                command_id, values, detach=args._detach, local=args._local)
        except Forbidden as exc:
            # For example a stored credential that may not be used here.
            # Nothing was queued.
            log.warning("The job was refused: {}".format(exc))
            command.parser.exit(1, "{}: error: {}\n".format(
                command.parser.prog, exc))
        if status:
            sys.exit(status)


class _Section(object):
    """The commands of one category of a `Cli`, see `Cli.section`."""

    def __init__(self, cli, category):
        self.cli = cli
        self.category = category

    def command(self, name=None, **kwargs):
        """`Cli.command` with the category of this section."""
        kwargs.setdefault("category", self.category)
        return self.cli.command(name, **kwargs)

    def run(self, argv=None):
        return self.cli.run(argv)

    @property
    def commands(self):
        """The commands of this category, by name."""
        return {name: command for name, command in self.cli.commands.items()
                if command.category == self.category}


class _Parser(argparse.ArgumentParser):
    """An `argparse.ArgumentParser` whose epilog is made when the help
    is shown (`describe`), so that it names the kinds of target that are
    installed now. The parsers of the commands are of this class too."""

    describe = None

    def format_help(self):
        if self.describe is not None:
            self.epilog = self.describe()
        return super().format_help()


def _epilog(effect):
    """What `--help` says about the effect of a command (see
    `Command.describe`)."""
    if effect is None:
        return None
    return "Effect: {}.".format(
        "it only reads" if effect == READ else
        "it changes its targets or MAST")


def _summary(doc):
    """The first paragraph of a docstring on one line, for the list of
    commands in `--help`."""
    paragraph = (doc or "").strip().split("\n\n")[0]
    return " ".join(paragraph.split()).replace("%", "%%") or None
