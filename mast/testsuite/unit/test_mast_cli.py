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
Unit tests for mast.cli: commands, which declare their parameters with
a model.
"""
import contextlib
import io
import unittest
from typing import Literal

from pydantic import field_validator

from mast.cli import (
    Cli, Option, Params, Result, Table, Text, captured, say)
from mast.cli import params as cli_params


class Greet(Params):
    name: str = Option(short="n", help="Who to greet")
    times: int = Option(1, short="t", help="How many times")
    shout: bool = Option(False, short="s", help="Use capitals")
    polite: bool = Option(True, help="Do not say please")
    tags: list[str] = Option(default_factory=list, short="T", help="Tags")
    style: Literal["plain", "fancy"] = Option("plain", help="The style")
    title: str | None = Option(None, help="A title, 100% optional")
    password: str = Option("", secret=True, help="A secret")


def make_cli():
    cli = Cli()
    calls = []

    @cli.command("greet", category="words")
    def greet(params: Greet):
        """Greet someone.

        More detail that the list of commands does not show."""
        calls.append(params)
        return Text("hello " + params.name)

    @cli.command("part", category="words")
    def part(params: Greet):
        """Say goodbye."""
        calls.append(params)

    return cli, calls, greet


def run(cli, *argv):
    """Run cli and return (exit status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    status = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.run(list(argv))
        except SystemExit as exc:
            status = exc.code
    return status, out.getvalue(), err.getvalue()


class TestModelCommand(unittest.TestCase):

    def test_defaults_come_from_the_model(self):
        cli, calls, _ = make_cli()
        status, out, _ = run(cli, "greet", "-n", "World")
        self.assertEqual(0, status)
        self.assertEqual("hello World\n", out)
        self.assertEqual(Greet(name="World"), calls[0])
        self.assertEqual([], calls[0].tags)
        self.assertIsNone(calls[0].title)

    def test_every_kind_of_option(self):
        cli, calls, _ = make_cli()
        status, _, err = run(
            cli, "greet", "--name", "World", "-t", "3", "-s", "--no-polite",
            "-T", "a", "-T", "b", "--style", "fancy", "--title", "Dr")
        self.assertEqual(0, status, err)
        params = calls[0]
        self.assertEqual(3, params.times)
        self.assertTrue(params.shout)
        self.assertFalse(params.polite)
        self.assertEqual(["a", "b"], params.tags)
        self.assertEqual("fancy", params.style)
        self.assertEqual("Dr", params.title)

    def test_bad_arguments_exit_with_status_2(self):
        cli, calls, _ = make_cli()
        for argv in (
                ["greet"],                               # --name is required
                ["greet", "-n", "x", "-t", "many"],      # not an int
                ["greet", "-n", "x", "--style", "loud"],  # not a choice
                ["greet", "-n", "x", "--unknown"],
                []):                                     # no command
            status, _, err = run(cli, *argv)
            self.assertEqual(2, status, argv)
            self.assertIn("error:", err)
        self.assertEqual([], calls)

    def test_model_validation_errors_name_the_option(self):
        class Checked(Params):
            retry_count: int = Option(1, help="Retries")

            @field_validator("retry_count")
            @classmethod
            def small(cls, value):
                if value > 5:
                    raise ValueError("must be 5 or less")
                return value

        cli = Cli()

        @cli.command("check")
        def check(params: Checked):
            raise AssertionError("must not be called")

        status, _, err = run(cli, "check", "--retry-count", "9")
        self.assertEqual(2, status)
        self.assertIn("--retry-count", err)
        self.assertIn("must be 5 or less", err)

    def test_help_lists_commands_and_options(self):
        cli, _, _ = make_cli()
        status, out, _ = run(cli, "--help")
        self.assertEqual(0, status)
        self.assertIn("Greet someone.", out)
        self.assertNotIn("More detail", out)
        status, out, _ = run(cli, "greet", "--help")
        self.assertEqual(0, status)
        self.assertIn("-n, --name", out)
        self.assertIn("Who to greet", out)
        self.assertIn("--no-polite", out)
        self.assertIn("100% optional", out)
        self.assertIn("(default: 1)", out)

    def test_command_can_be_called_from_python(self):
        _, calls, greet = make_cli()
        self.assertEqual("hello a", greet(name="a").text())
        self.assertEqual("hello b", greet(Greet(name="b", times="2")).text())
        self.assertEqual(2, calls[1].times)
        with self.assertRaises(ValueError):
            greet(name="a", times="many")
        with self.assertRaises(ValueError):
            greet(name="a", no_such_parameter=1)
        with self.assertRaises(TypeError):
            greet("a")

    def test_secret_is_not_in_repr(self):
        params = Greet(name="a", password="hunter2")
        self.assertNotIn("hunter2", repr(params))
        self.assertEqual("hunter2", params.password)

    def test_registry(self):
        cli, _, _ = make_cli()
        self.assertEqual(["greet", "part"], list(cli.commands))
        self.assertEqual(
            ["greet", "part"],
            [c.key for c in cli.categories()["words"]])
        self.assertIs(Greet, cli.find("greet").model)
        with self.assertRaises(KeyError):
            cli.find("missing")
        self.assertIn("* `-n, --name`: Who to greet", cli.find("greet").doc)

    def test_main_function_with_a_model(self):
        seen = []

        def main(params: Greet):
            """The whole program."""
            seen.append(params.name)

        status, _, err = run(Cli(main=main), "-n", "World")
        self.assertEqual(0, status, err)
        self.assertEqual(["World"], seen)


class TestDeclarationErrors(unittest.TestCase):

    def register(self, model):
        cli = Cli()

        @cli.command("x")
        def command(params: model):
            pass

    def test_two_options_with_the_same_short_letter(self):
        class Clash(Params):
            alpha: str = Option("", short="a")
            again: str = Option("", short="a")

        with self.assertRaises(Exception) as caught:
            self.register(Clash)
        self.assertIn("-a", str(caught.exception))

    def test_short_must_be_one_letter(self):
        with self.assertRaises(ValueError):
            Option("", short="ab")

    def test_unsupported_types(self):
        class Mapping(Params):
            values: dict = Option(default_factory=dict)

        class RequiredFlag(Params):
            flag: bool = Option()

        for model in (Mapping, RequiredFlag):
            with self.assertRaises(TypeError) as caught:
                self.register(model)
            self.assertIn(model.__name__, str(caught.exception))

    def test_duplicate_command_name(self):
        cli, _, _ = make_cli()
        with self.assertRaises(ValueError):
            @cli.command("greet")
            def other(params: Greet):
                pass


class TestNotACommand(unittest.TestCase):

    def test_a_function_without_a_model_is_refused(self):
        cli, _, _ = make_cli()

        def options_from_the_signature(name="", count=1):
            pass

        def not_a_model(params: dict):
            pass

        def two_parameters(params: Greet, other: Greet):
            pass

        for fn in (options_from_the_signature, not_a_model, two_parameters):
            with self.subTest(fn=fn.__name__):
                with self.assertRaises(TypeError) as caught:
                    cli.command()(fn)
                self.assertIn(fn.__name__, str(caught.exception))
                self.assertIn("mast.cli.Params", str(caught.exception))
                with self.assertRaises(TypeError):
                    Cli(main=fn)
        self.assertEqual(["greet", "part"], list(cli.commands))


class TestResults(unittest.TestCase):

    def test_say_prints_unless_captured(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            say("one", 2)
            with captured() as messages:
                say("hidden")
            say("three")
        self.assertEqual("one 2\nthree\n", out.getvalue())
        self.assertEqual(["hidden"], messages)

    def test_table(self):
        table = Table(("Appliance", "Result"), [("dp1", "ok"), ("<b>", "a\nb")])
        self.assertEqual(
            "Appliance  Result\n"
            "---------  ------\n"
            "dp1        ok\n"
            "<b>        a\n"
            "           b",
            table.text())
        self.assertIn("&lt;b&gt;", table.html())
        self.assertNotIn("<b>", table.html())

    def test_result_defaults(self):
        self.assertEqual("", Result().text())
        self.assertEqual("<pre>a &amp; b</pre>", Text("a & b").html())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            Result().emit()
            Text("shown").emit()
        self.assertEqual("shown\n", out.getvalue())


class TestOptions(unittest.TestCase):

    def test_specs(self):
        specs = {spec.name: spec for spec in cli_params.options(Greet)}
        self.assertEqual(("-n", "--name"), specs["name"].flags)
        self.assertTrue(specs["name"].required)
        self.assertEqual(("--no-polite",), specs["polite"].flags)
        self.assertEqual("list", specs["tags"].kind)
        self.assertEqual([], specs["tags"].default)
        self.assertEqual(("plain", "fancy"), specs["style"].choices)
        self.assertIs(int, specs["times"].type)
        self.assertTrue(specs["password"].secret)


if __name__ == "__main__":
    unittest.main()
