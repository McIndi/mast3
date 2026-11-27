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
The one command registry (`mast.jobs.registry`), the plugins
(`mast.plugins`), the `mast` command (`mast.main`) and the names of 3.x
(`mast.v3`).
"""
import contextlib
import importlib.metadata
import io
import os
import subprocess
import sys
import types
import unittest
from unittest import mock

from mast import plugins, roots, v3
from mast.cli import ANY_TARGET, Cli, Option, Params
from mast.jobs import registry
from mast.web import catalog


class Nothing(Params):
    pass


def run_mast(*argv):
    """Run the mast command: (exit status, stdout, stderr)."""
    from mast import main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class OnTargets(Params):
    targets: list[str] = Option(default_factory=list, target=True)


def fake_plugin(provider, commands=("look",), manifest_provider=None,
                cli_provider=None, broken=False, api=1, core=">=4.0.0.dev0",
                target_kinds=(), effect="change", targets=(), pages=()):
    """An entry point of `mast_plugin` whose manifest and commands are in
    a module made here. Its commands take the parameter `targets` if
    they accept `targets`. Return (entry point, module name)."""
    name = "mast_test_plugin_{}".format(provider)
    module = types.ModuleType(name)
    module.manifest = plugins.Manifest(
        provider=manifest_provider or provider, title=provider.title(),
        version="1.0", api=api, core=core, commands="{}:cli".format(name),
        target_kinds=target_kinds, pages=pages)
    if broken:
        module.manifest = plugins.Manifest(
            provider=provider, title="Broken", version="1.0", api=1,
            core=">=4.0.0.dev0", commands="mast_no_such_module:cli")
    cli = Cli(provider=cli_provider or provider, targets=targets)
    for command in commands:
        if targets:
            def fn(params: OnTargets):
                pass
        else:
            def fn(params: Nothing):
                pass
        fn.__name__ = command.replace("-", "_")
        cli.command(command, effect=effect)(fn)
    module.cli = cli
    sys.modules[name] = module
    entry_point = importlib.metadata.EntryPoint(
        provider, "{}:manifest".format(name), plugins.GROUP)
    return entry_point, name


class PluginsCase(unittest.TestCase):
    """Replace the installed plugins with fakes. The real DataPower
    plugin is kept unless `keep_datapower` is False."""

    keep_datapower = True

    def use(self, *entry_points):
        real = [ep for ep in importlib.metadata.entry_points(group=plugins.GROUP)
                if self.keep_datapower]
        found = real + list(entry_points)
        patch = mock.patch.object(
            importlib.metadata, "entry_points",
            lambda group=None: found if group == plugins.GROUP else [])
        patch.start()
        self.addCleanup(patch.stop)
        registry.reset()
        self.addCleanup(registry.reset)


class TestIdentity(unittest.TestCase):
    """One mapping: id PROVIDER.NAME, path mast/PROVIDER/NAME, command
    line `mast PROVIDER NAME`."""

    def test_every_plugin_command(self):
        commands = registry.commands("datapower")
        self.assertGreater(len(commands), 90)
        for command in commands:
            with self.subTest(command=command.name):
                self.assertEqual(command.id, "datapower." + command.name)
                self.assertEqual(command.path, "mast/datapower/" + command.name)
                self.assertEqual(command.invocation,
                                 "mast datapower " + command.name)
                self.assertIs(registry.resolve(command.id), command)

    def test_examples_of_the_design(self):
        for command_id, invocation, path in (
                ("datapower.get-status", "mast datapower get-status",
                 "mast/datapower/get-status"),
                ("datapower.normal-backup", "mast datapower normal-backup",
                 "mast/datapower/normal-backup"),
                ("datapower.cert-audit", "mast datapower cert-audit",
                 "mast/datapower/cert-audit"),
                ("core.crypto-inventory", "mast crypto inventory",
                 "mast/core/crypto-inventory"),
                ("core.add-target", "mast targets add", "mast/core/add-target")):
            with self.subTest(command=command_id):
                command = registry.resolve(command_id)
                self.assertEqual((command.invocation, command.path),
                                 (invocation, path))

    def test_categories_are_not_part_of_the_name(self):
        self.assertEqual(registry.resolve("datapower.normal-backup").category,
                         "backups")
        self.assertEqual(registry.resolve("datapower.cert-audit").category,
                         "crypto")
        self.assertEqual(registry.resolve("datapower.flush-aaa-cache").group,
                         "caches")

    def test_each_core_command_names_its_id(self):
        names = [c.id_name for c in registry.commands(roots.CORE)]
        self.assertEqual(len(names), len(set(names)))
        for command in registry.commands(roots.CORE):
            with self.subTest(command=command.invocation):
                root = command.invocation.split()[1]
                self.assertIn(root, roots.ROOTS)
                self.assertFalse(roots.ROOTS[root].program)
                # The management commands of core run directly
                if root != "crypto":
                    self.assertFalse(command.queued)

    def test_the_queue_refuses_a_command_that_is_not_a_job(self):
        from mast.jobs import queue
        with self.assertRaises(queue.NotQueued):
            queue.submit("core.list-targets", {})


class TestRegistration(PluginsCase):

    def test_a_plugin_is_found_by_its_entry_point(self):
        entry_point, _ = fake_plugin("acme", ("get-status", "reboot"))
        self.use(entry_point)
        self.assertEqual(registry.providers(), ["core", "acme", "datapower"])
        self.assertEqual(registry.resolve("acme.reboot").invocation,
                         "mast acme reboot")
        # The same name in another provider is another command
        self.assertIsNot(registry.resolve("acme.get-status"),
                         registry.resolve("datapower.get-status"))

    def test_a_reserved_name_is_refused(self):
        entry_point, _ = fake_plugin("targets")
        self.use(entry_point)
        self.assertNotIn("targets", registry.providers())
        (failure,) = [f for f in plugins.failures() if f.name == "targets"]
        self.assertIn("is a name of core", failure.reason)

    def test_the_manifest_must_name_its_entry_point(self):
        entry_point, _ = fake_plugin("acme", manifest_provider="other")
        self.use(entry_point)
        self.assertNotIn("acme", registry.providers())
        self.assertIn("names the provider 'other'",
                      plugins.failures()[0].reason)

    def test_a_broken_plugin_leaves_the_others(self):
        broken, _ = fake_plugin("broken", broken=True)
        self.use(broken)
        commands = registry.commands()
        self.assertTrue(any(c.provider == "datapower" for c in commands))
        self.assertFalse(any(c.provider == "broken" for c in commands))
        (failure,) = [f for f in plugins.failures() if f.name == "broken"]
        self.assertIn("cannot be loaded", failure.reason)
        with self.assertRaises(registry.UnknownCommand):
            registry.resolve("broken.look")

    def test_a_cli_of_another_provider_is_refused(self):
        entry_point, _ = fake_plugin("acme", cli_provider="other")
        self.use(entry_point)
        registry.commands()
        self.assertIn("acme", [f.name for f in plugins.failures()])
        self.assertIn("datapower", registry.providers())

    def test_a_bad_command_registers_none_of_the_plugin(self):
        # A Cli refuses two commands with one name, so a plugin cannot
        # have two ids that are the same. A command whose id is not its
        # name is refused, and the whole plugin with it.
        entry_point, name = fake_plugin("acme", ("look",))
        cli = sys.modules[name].cli

        def other(params: Nothing):
            pass
        cli.command("other", effect="change")(other)
        cli.commands["other"].id_name = "look"
        self.use(entry_point)
        commands = registry.commands()
        self.assertFalse(any(c.provider == "acme" for c in commands))
        (failure,) = [f for f in plugins.failures() if f.name == "acme"]
        self.assertIn("the id of a plugin command is its name", failure.reason)

    def test_a_duplicate_core_id_fails_registration(self):
        cli = Cli(provider="core", prog="mast extra", queued=False)

        def again(params: Nothing):
            pass
        cli.command("again", effect="change", id="add-target")(again)
        clis = registry._core_clis()
        clis.append(cli)
        self.addCleanup(clis.remove, cli)
        registry._invalidate()
        self.addCleanup(registry._invalidate)
        with self.assertRaises(registry.RegistrationError) as caught:
            registry.commands()
        self.assertIn("core.add-target", str(caught.exception))

    def test_two_plugins_with_one_provider(self):
        first, _ = fake_plugin("acme", ("look",))
        second = importlib.metadata.EntryPoint(
            "acme", first.value, plugins.GROUP)
        self.use(first, second)
        self.assertIn("acme", registry.providers())
        self.assertIn("already given", [f for f in plugins.failures()
                                        if f.name == "acme"][0].reason)


class TestPluginApi(PluginsCase):
    """Plugin API 1: the manifest, and what every command declares. A
    plugin with one fault is left out whole, with what to do."""

    def failure(self, name):
        registry.commands()
        found = plugins.failure(name)
        self.assertIsNotNone(found, plugins.failures())
        self.assertNotIn(name, registry.providers())
        self.assertFalse(registry.commands(name))
        # Core and the other plugins stay
        self.assertTrue(registry.commands("core"))
        self.assertTrue(registry.commands("datapower"))
        return found

    def test_a_good_plugin(self):
        entry_point, _ = fake_plugin(
            "acme", ("get-status",), target_kinds=("acme",),
            effect="read", targets=("acme",))
        self.use(entry_point)
        command = registry.resolve("acme.get-status")
        self.assertEqual((command.effect, command.targets,
                          command.target_params), ("read", ("acme",),
                                                   ("targets",)))
        self.assertIsNone(plugins.failure("acme"))

    def test_another_plugin_api(self):
        entry_point, _ = fake_plugin("acme", api=2)
        self.use(entry_point)
        found = self.failure("acme")
        self.assertEqual(found.kind, plugins.INCOMPATIBLE)
        self.assertIn("plugin API 2", found.reason)
        self.assertIn("plugin API 1", found.fix)

    def test_another_version_of_core(self):
        entry_point, _ = fake_plugin("acme", core=">=5")
        self.use(entry_point)
        found = self.failure("acme")
        self.assertEqual(found.kind, plugins.INCOMPATIBLE)
        self.assertIn("it needs MAST >=5", found.reason)
        self.assertIn("or a MAST >=5", found.fix)

    def test_core_versions_that_are_not_a_specifier(self):
        entry_point, _ = fake_plugin("acme", core="four")
        self.use(entry_point)
        found = self.failure("acme")
        self.assertEqual(found.kind, plugins.INVALID)
        self.assertIn("Report this to the authors", found.fix)

    def test_a_target_kind_of_core(self):
        entry_point, _ = fake_plugin("acme", target_kinds=("endpoint",))
        self.use(entry_point)
        found = self.failure("acme")
        self.assertEqual(found.kind, plugins.CONFLICT)
        self.assertIn("a kind of core", found.reason)

    def test_a_target_kind_of_two_plugins_is_of_neither(self):
        first, _ = fake_plugin("acme", target_kinds=("widget",))
        second, _ = fake_plugin("bolt", target_kinds=("widget", "bolt"))
        self.use(first, second)
        for name in ("acme", "bolt"):
            with self.subTest(plugin=name):
                found = self.failure(name)
                self.assertEqual(found.kind, plugins.CONFLICT)
                self.assertIn("the target kind 'widget' is given by the "
                              "plugin acme and the plugin bolt", found.reason)
                self.assertIn("Uninstall all but one of", found.fix)

    def test_a_command_without_an_effect(self):
        entry_point, _ = fake_plugin("acme", ("look", "reboot"), effect=None)
        self.use(entry_point)
        found = self.failure("acme")
        self.assertEqual(found.kind, plugins.INVALID)
        self.assertIn("mast acme look: it declares no effect", found.reason)
        self.assertIn("mast acme reboot", found.reason)

    def test_a_command_with_a_kind_that_the_plugin_does_not_give(self):
        entry_point, _ = fake_plugin("acme", target_kinds=("acme",),
                                     targets=("datapower",))
        self.use(entry_point)
        self.assertIn("the target kind 'datapower'",
                      self.failure("acme").reason)

    def test_a_plugin_command_cannot_take_any_kind(self):
        entry_point, _ = fake_plugin("acme", targets=(ANY_TARGET,))
        self.use(entry_point)
        self.assertIn("the target kind '*'", self.failure("acme").reason)

    def test_a_command_of_core_names_no_kind_of_a_plugin(self):
        cli = Cli(provider="core", prog="mast extra")

        def extra(params: OnTargets):
            pass
        cli.command("extra", effect="read", id="extra",
                    targets=("datapower",))(extra)
        clis = registry._core_clis()
        clis.append(cli)
        self.addCleanup(clis.remove, cli)
        registry._invalidate()
        self.addCleanup(registry._invalidate)
        with self.assertRaises(registry.RegistrationError) as caught:
            registry.commands()
        self.assertIn("which core does not give", str(caught.exception))

    def test_a_missing_plugin(self):
        self.use()
        with self.assertRaises(registry.UnknownCommand) as caught:
            registry.resolve("linux.get-status")
        self.assertIn("no installed plugin gives the provider 'linux'",
                      str(caught.exception))
        self.assertIn("Install the plugin", str(caught.exception))

    def test_the_job_of_a_plugin_that_was_left_out(self):
        entry_point, _ = fake_plugin("acme", api=2)
        self.use(entry_point)
        with self.assertRaises(registry.UnknownCommand) as caught:
            registry.resolve("acme.look")
        message = str(caught.exception)
        self.assertIn("the plugin acme was left out", message)
        self.assertIn("plugin API 1", message)

    def test_a_plugin_whose_page_does_not_load(self):
        from mast.web.page import load_pages
        entry_point, _ = fake_plugin(
            "acme", pages=("mast_no_such_module:page",))
        self.use(entry_point)
        names = [page.name for page in load_pages()]
        self.assertIn("appliances", names)
        found = self.failure("acme")
        self.assertEqual(found.kind, plugins.BROKEN)
        self.assertIn("its pages cannot be loaded", found.reason)

    def test_a_plugin_page_that_takes_the_name_of_another(self):
        import flask
        from mast.web.page import Page, load_pages
        entry_point, name = fake_plugin("acme", pages=("{}:page".format(
            "mast_test_plugin_acme"),))
        sys.modules[name].page = Page(
            name="jobs", title="Not jobs", order=1, min_role="viewer",
            blueprint=flask.Blueprint("acme_jobs", __name__))
        self.use(entry_point)
        pages = {page.name: page for page in load_pages()}
        self.assertEqual(pages["jobs"].title, "Jobs")
        found = self.failure("acme")
        self.assertEqual(found.kind, plugins.CONFLICT)
        self.assertIn("takes the name 'jobs'", found.reason)

    def test_pages_that_are_not_module_attributes(self):
        entry_point, _ = fake_plugin("acme", pages="mast.web.pages.jobs:page")
        self.use(entry_point)
        self.assertEqual(self.failure("acme").kind, plugins.INVALID)

    def test_the_command_line_says_what_to_do(self):
        entry_point, _ = fake_plugin("acme", api=2)
        self.use(entry_point)
        code, out, err = run_mast("plugins", "list")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"acme .*left out")
        self.assertIn("plugin API 1", out)
        code, _, err = run_mast("acme", "look")
        self.assertEqual(code, 2)
        self.assertIn("the plugin acme was left out", err)
        self.assertIn("plugin API 1", err)


class TestDeclarations(unittest.TestCase):
    """What every real command declares."""

    def test_every_command_declares_an_effect(self):
        for command in registry.commands():
            with self.subTest(command=command.id):
                self.assertIn(command.effect, ("read", "change"))
                self.assertEqual(command.problems(), [])

    def test_the_effect_of_the_commands_that_matter(self):
        # A wildcard grant such as mast/datapower/get-* rests on these
        # (D16 in docs/design/mast-4.0.md)
        for name, effect in (
                ("get-status", "read"), ("get-config", "read"),
                ("get-file", "read"), ("get-filestore", "read"),
                ("get-encrypted-filesystem", "read"),
                ("get-temporary-filesystem", "read"),
                ("get-error-reports", "read"),
                # It makes an error report on the appliance
                ("get-pmr-info", "change"),
                ("generate-error-report", "change"),
                # Each certificate is written to temporary: first
                ("export-certs", "change"),
                # The backup is written on the appliance
                ("secure-backup", "change"),
                ("normal-backup", "read"),
                ("export", "read"), ("import", "change"),
                ("terminal", "change"), ("ssh", "change"),
                ("list-users", "read"), ("change-password", "change")):
            with self.subTest(command=name):
                self.assertEqual(
                    registry.resolve("datapower." + name).effect, effect)

    def test_the_get_wildcard_of_the_design(self):
        matched = sorted(c.name for c in registry.commands("datapower")
                         if c.name.startswith("get-"))
        self.assertEqual(matched, [
            "get-config", "get-encrypted-filesystem", "get-error-reports",
            "get-file", "get-filestore", "get-pmr-info", "get-status",
            "get-temporary-filesystem"])

    def test_datapower_commands_take_datapower_targets(self):
        for command in registry.commands("datapower"):
            with self.subTest(command=command.id):
                expected = () if command.name == "xor" else ("datapower",)
                self.assertEqual(command.targets, expected)

    def test_core_commands_name_no_kind_of_a_plugin(self):
        for command in registry.commands("core"):
            with self.subTest(command=command.id):
                self.assertTrue(set(command.targets) <= {ANY_TARGET})

    def test_secret_parameters(self):
        self.assertEqual(
            registry.resolve("datapower.get-status").secret_params,
            ("credentials",))
        self.assertIn("password",
                      registry.resolve("datapower.add-user").secret_params)
        self.assertEqual(
            registry.resolve("datapower.get-status").target_params,
            ("appliances",))

    def test_the_help_says_what_the_command_does(self):
        help_text = registry.resolve("datapower.get-pmr-info") \
            .parser.format_help()
        self.assertIn("Effect: it changes its targets or MAST. "
                      "Targets: datapower.", " ".join(help_text.split()))
        help_text = registry.resolve("core.list-targets").parser.format_help()
        self.assertIn("Effect: it only reads.", help_text)

    def test_a_target_parameter_needs_a_kind_and_the_other_way(self):
        cli = Cli(provider="acme")

        def one(params: OnTargets):
            pass

        def two(params: Nothing):
            pass
        cli.command("one", effect="read")(one)
        cli.command("two", effect="read", targets=("acme",))(two)
        self.assertIn("accepts no kind of target",
                      cli.commands["one"].problems()[0])
        self.assertIn("no parameter names them",
                      cli.commands["two"].problems()[0])

    def test_a_bad_effect_is_refused_at_once(self):
        cli = Cli(provider="acme")

        def look(params: Nothing):
            pass
        with self.assertRaises(ValueError):
            cli.command("look", effect="write")(look)
        with self.assertRaises(ValueError):
            cli.command("look", effect="read", targets="acme")(look)


class TestCatalog(unittest.TestCase):

    def test_what_the_web_offers(self):
        offered = {c["id"] for c in catalog.all_commands()}
        self.assertIn("datapower.get-status", offered)
        self.assertIn("core.crypto-inventory", offered)
        # Not a job, but a plugin command: it runs now
        self.assertIn("datapower.xor", offered)
        # Core management, a terminal, and what MAST runs itself
        for command_id in ("core.add-target", "core.add-user",
                           "datapower.ssh", "datapower.terminal"):
            self.assertNotIn(command_id, offered)
            with self.assertRaises(catalog.CommandNotFound):
                catalog.find(command_id)


class TestMastCommand(unittest.TestCase):

    def run_mast(self, *argv):
        return run_mast(*argv)

    def test_help_lists_the_roots_and_the_plugins(self):
        for argv in ((), ("--help",), ("-h",)):
            code, out, _ = self.run_mast(*argv)
            self.assertEqual(code, 0)
            self.assertIn("usage: mast [--home PATH] ROOT", out)
            for name in roots.ROOTS:
                self.assertRegex(out, r"(?m)^  {} ".format(name))
            self.assertRegex(out, r"(?m)^  datapower +IBM DataPower")

    def test_an_unknown_root(self):
        code, _, err = self.run_mast("nothing")
        self.assertEqual(code, 2)
        self.assertIn("'nothing' is not a root of mast", err)

    def test_an_unknown_option(self):
        code, _, err = self.run_mast("--nope")
        self.assertEqual(code, 2)
        self.assertIn("unknown option --nope", err)

    def test_a_plugin_root(self):
        code, out, _ = self.run_mast("datapower", "xor", "-s", "user:pass")
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())

    def test_a_core_root(self):
        code, out, _ = self.run_mast("plugins", "list")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"(?m)^datapower +IBM DataPower .* loaded")

    def test_no_home_is_needed_for_the_help_and_the_version(self):
        env = {k: v for k, v in os.environ.items() if k != "MAST_HOME"}
        outside = os.path.abspath(os.sep)
        for argv, expected in (([], "plugins:"), (["version"], "Version")):
            with self.subTest(argv=argv):
                done = subprocess.run(
                    [sys.executable, "-c",
                     "import sys; sys.executable = sys.argv[1]; "
                     "import mast.main; sys.exit(mast.main.main(sys.argv[2:]))",
                     os.path.join(outside, "python3")] + argv,
                    cwd=outside, env=env, capture_output=True, text=True,
                    timeout=60)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn(expected, done.stdout)


class TestV3Names(unittest.TestCase):

    def test_every_3x_command_has_a_4_0_command(self):
        self.assertEqual(len(v3.COMMANDS), 119)
        for old, new in sorted(v3.COMMANDS.items()):
            with self.subTest(command=old):
                self.assertIsNotNone(registry.resolve(new))

    def test_the_merged_commands(self):
        for name in ("flush-aaa-cache", "flush-arp-cache",
                     "flush-document-cache"):
            sources = sorted(old for old, new in v3.COMMANDS.items()
                             if new == "datapower." + name)
            self.assertEqual(len(sources), 2, sources)
        self.assertEqual(v3.command("deployment.get-file"),
                         v3.command("system.get-file"))
        self.assertEqual(v3.command("crypto.inventory-appliance"),
                         "datapower.collect-crypto")
        self.assertEqual(v3.command("status.collect"),
                         "datapower.collect-status")
        self.assertEqual(v3.command("crypto.inventory"),
                         "core.crypto-inventory")
        self.assertIsNone(v3.command("nothing.at-all"))

    def test_every_datapower_command_came_from_3x_but_ssh(self):
        targets = set(v3.COMMANDS.values())
        new = [c.id for c in registry.commands("datapower")
               if c.id not in targets]
        self.assertEqual(new, ["datapower.ssh"])

    def test_renamed_parameters_exist(self):
        for old, renames in v3.PARAMETERS.items():
            command = registry.resolve(v3.command(old))
            for _, new in renames.items():
                self.assertIn(new, command.model.model_fields)


if __name__ == "__main__":
    unittest.main()
