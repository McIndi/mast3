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
# Copyright 2015-2026, McIndi Solutions, All rights reserved.
"""
_module_: `mast.cli.params`

Typed parameters for MAST commands.

A command declares its parameters as a `Params` model, with one
`Option` for each parameter:

    :::python
    from mast.cli import Params, Option

    class FetchLogs(Params):
        out_dir: str = Option("tmp", short="o", help="Where to save the logs")
        verbose: bool = Option(False, short="v", help="Print each file name")

The model is the only description of the parameters. The command line
parser, the web form and the help text are all made from it, so they
cannot disagree.

The supported types are `str`, `int`, `float`, `bool`, `pathlib.Path`,
`typing.Literal`, an `enum.Enum`, `Optional` of one of these, and a
`list` of one of these (except `bool`).
"""
import argparse
import enum
import pathlib
import types
import typing
from dataclasses import dataclass
from textwrap import dedent

from pydantic import BaseModel, ConfigDict, Field
from pydantic_core import PydanticUndefined

__all__ = ["Params", "Option", "OptionSpec", "options"]

_META = "mast"


class Params(BaseModel):
    """
    _class_: `mast.cli.params.Params`

    The base class for the parameters of a command. A parameter that
    the model does not declare is an error.
    """
    model_config = ConfigDict(extra="forbid")


def Option(default=PydanticUndefined, *, help="", short=None, widget=None,
           secret=False, default_factory=None):
    """
    _function_: `mast.cli.params.Option(default, help="", short=None,
    widget=None, secret=False, default_factory=None)`

    Declare one parameter of a command.

    Parameters:

    * `default`: The value to use when the parameter is not given. A
    parameter with no default is required.
    * `help`: The description shown in `--help` and in the web GUI.
    * `short`: One letter for the short option (`"o"` gives `-o`). A
    parameter with no `short` has only a long option. Short options do
    not change when other parameters are added.
    * `widget`: A hint for the web GUI about how to ask for the value.
    `mast.cli` does not interpret it.
    * `secret`: If True, the value is kept out of `repr()`, and so out
    of the logs.
    * `default_factory`: A function that returns the default. Use it
    for a `list`.
    """
    if short is not None and (len(short) != 1 or not short.isalpha()):
        raise ValueError(
            "short must be one letter, got {!r}".format(short))
    kwargs = {
        "description": help,
        "json_schema_extra": {_META: {"short": short, "widget": widget,
                                      "secret": secret}},
    }
    if secret:
        kwargs["repr"] = False
    if default_factory is not None:
        return Field(default_factory=default_factory, **kwargs)
    return Field(default, **kwargs)


@dataclass(frozen=True)
class OptionSpec(object):
    """
    _class_: `mast.cli.params.OptionSpec`

    One parameter of a `Params` model, in the form that the command
    line parser and the web GUI need.

    * `name`: The name of the field in the model.
    * `kind`: `"flag"` for a `bool`, `"list"` for a `list`, otherwise
    `"value"`.
    * `type`: `str`, `int` or `float`. For a list, the type of one item.
    * `default`: The default value. `None` if the parameter is required.
    * `required`: True if the parameter has no default.
    * `choices`: The permitted values, or `None` if any value is permitted.
    * `help`, `short`, `widget`, `secret`: As given to `Option`.
    """
    name: str
    kind: str
    type: type
    default: object
    required: bool
    choices: typing.Optional[tuple]
    help: str
    short: typing.Optional[str]
    widget: typing.Optional[str]
    secret: bool

    @property
    def long(self):
        """The long option. A flag that is on by default gets `--no-`,
        because giving the flag turns it off."""
        name = self.name.replace("_", "-")
        if self.kind == "flag" and self.default:
            return "--no-" + name
        return "--" + name

    @property
    def flags(self):
        """The option strings, short option first."""
        if self.short:
            return ("-" + self.short, self.long)
        return (self.long,)


def _classify(annotation):
    """Return `(kind, type, choices)` for the annotation of a field."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        members = [a for a in typing.get_args(annotation)
                   if a is not type(None)]
        if len(members) != 1:
            raise TypeError("a union of several types is not supported")
        return _classify(members[0])
    if origin is list:
        kind, _type, choices = _classify(typing.get_args(annotation)[0])
        if kind != "value":
            raise TypeError("a list of that type is not supported")
        return "list", _type, choices
    if origin is typing.Literal:
        choices = typing.get_args(annotation)
        _type = type(choices[0])
        if _type not in (str, int, float) or any(
                type(c) is not _type for c in choices):
            raise TypeError(
                "a Literal must hold only str, only int or only float")
        return "value", _type, choices
    if annotation is bool:
        return "flag", bool, None
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return "value", str, tuple(str(m.value) for m in annotation)
    if annotation in (int, float):
        return "value", annotation, None
    if annotation in (str, pathlib.Path):
        return "value", str, None
    raise TypeError("the type {!r} is not supported".format(annotation))


def options(model):
    """
    _function_: `mast.cli.params.options(model)`

    Return a list with one `OptionSpec` for each field of `model`, in
    the order in which the model declares them.
    """
    specs = []
    for name, field in model.model_fields.items():
        try:
            kind, _type, choices = _classify(field.annotation)
        except TypeError as exc:
            raise TypeError("{}.{}: {}".format(model.__name__, name, exc))
        required = field.is_required()
        if kind == "flag" and required:
            raise TypeError(
                "{}.{}: a bool parameter needs a default".format(
                    model.__name__, name))
        extra = field.json_schema_extra
        meta = extra.get(_META, {}) if isinstance(extra, dict) else {}
        specs.append(OptionSpec(
            name=name,
            kind=kind,
            type=_type,
            default=None if required else field.get_default(
                call_default_factory=True),
            required=required,
            choices=choices,
            help=field.description or "",
            short=meta.get("short"),
            widget=meta.get("widget"),
            secret=bool(meta.get("secret")),
        ))
    return specs


def add_arguments(parser, model):
    """
    _function_: `mast.cli.params.add_arguments(parser, model)`

    Add one option to the `argparse` parser for each field of `model`.
    Two fields that ask for the same option string are an error.

    The parser does not fill in defaults. An option that is not on the
    command line is absent from the namespace, and the model supplies
    its default.
    """
    for spec in options(model):
        kwargs = {
            "dest": spec.name,
            "default": argparse.SUPPRESS,
            "help": _cli_help(spec),
        }
        if spec.kind == "flag":
            kwargs["action"] = "store_false" if spec.default else "store_true"
        else:
            kwargs["type"] = spec.type
            kwargs["metavar"] = spec.name.upper()
            if spec.choices:
                kwargs["choices"] = spec.choices
                kwargs["metavar"] = "{" + ",".join(
                    str(c) for c in spec.choices) + "}"
            if spec.kind == "list":
                kwargs["action"] = "append"
            kwargs["required"] = spec.required
        parser.add_argument(*spec.flags, **kwargs)


def _cli_help(spec):
    text = spec.help
    if spec.kind == "list":
        text += " Give the option again for each value."
    elif spec.kind == "value" and spec.default not in (None, "") \
            and not spec.secret:
        text += " (default: {})".format(spec.default)
    # argparse expands %-sequences in help strings.
    return text.strip().replace("%", "%%")


def from_namespace(model, namespace):
    """
    _function_: `mast.cli.params.from_namespace(model, namespace)`

    Build `model` from the namespace that `argparse` returned. Raises
    `pydantic.ValidationError` if a value is not valid.
    """
    values = vars(namespace)
    return model(**{
        name: values[name] for name in model.model_fields if name in values})


def error_messages(model, exc, flags=True):
    """
    _function_: `mast.cli.params.error_messages(model, exc, flags=True)`

    Return the problems in a `pydantic.ValidationError` as a list of
    strings. Each one names the command line option if `flags` is True,
    otherwise the field.
    """
    specs = {spec.name: spec for spec in options(model)}
    messages = []
    for error in exc.errors():
        location = error["loc"][0] if error["loc"] else None
        if location in specs:
            messages.append("{}: {}".format(
                specs[location].long if flags else location, error["msg"]))
        else:
            messages.append(error["msg"])
    return messages


def describe(model, doc=""):
    """
    _function_: `mast.cli.params.describe(model, doc="")`

    Return the help for a command as markdown: `doc`, then a list of
    the parameters of `model`.
    """
    lines = [dedent(doc or "").strip(), "", "Parameters:", ""]
    for spec in options(model):
        lines.append("* `{}`: {}".format(", ".join(spec.flags), spec.help))
    return "\n".join(lines).strip()
