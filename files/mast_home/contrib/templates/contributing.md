[Back to index](./index.html)
<h1>MAST for IBM DataPower Version {0}</h1>
<h2>Contributor's Guide</h2>

[TOC]

# Introduction

This guide is for the people who change MAST: how we work, how MAST is
put together, how to add a command, and how to test, document and
release a change. The work is done in the
[mast3 repository](https://github.com/McIndi/mast3).

# How we work

1. Every change starts with an issue in the
[mast3 repository](https://github.com/McIndi/mast3/issues).
2. The issue is where the problem and the possible solutions are
discussed.
3. A pull request is normally not considered until its issue is at
least 24 hours old, so that the people who are interested have time to
comment.
4. When a solution is agreed, make a branch, make the change, and open
a pull request. Link it to its issue.
5. The pull request is where the code is reviewed. It is normally not
merged until 24 hours have passed, so that there is time for a review.
6. Every change has a test. Every pull request runs the tests in GitHub
Actions, and it must pass them.
7. A change that users notice updates the release notes, and a change
to the plan updates the road map (see "Documentation" below).
8. Use [@mentions](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#mentioning-people-and-teams)
to tell the people who are interested in an issue or a pull request,
and
[watch](https://docs.github.com/en/account-and-profile/managing-subscriptions-and-notifications-on-github/managing-subscriptions-for-activity-on-github/viewing-your-subscriptions)
the repository for updates.

# Getting set up

You need [uv](https://docs.astral.sh/uv/) and git. uv installs the
Python that MAST needs.

    :::bash
    git clone https://github.com/McIndi/mast3
    cd mast3
    uv sync
    uv run pytest mast/testsuite/unit mast/testsuite/regression tools

To run MAST as users do, make a development home. It has the layout,
the launchers and the Python of the installer, with the code of your
checkout, so a change to the code is live:

    :::bash
    ./mast-setup --development-mode
    .dev/mast_home/mast-system list-domains -a <appliance> -c <credentials> -n

`./mast-setup` with no options builds the installer in `dist`. The
"Development" section of the README explains both environments.

# How MAST is put together

| Part | Where | What it does |
|---|---|---|
| The home | `mast/home.py`, `files/mast_home` | Finds `MAST_HOME` and enters it. `files/mast_home` is the layout of a home: the default configuration, the web GUI, `contrib`. |
| The command line | `mast/cli` | `Cli` turns functions into sub-commands. A `Params` model declares the options of a command; the command line, the web form and the help are made from it. A command returns a `Result`. |
| The job queue | `mast/jobs` | Every invocation of a DataPower command is a job. `queue` stores the jobs (SQLite or PostgreSQL, with Alembic migrations), `runner` runs one, `worker` is `mast-worker`, `client` submits a job and follows it, `secrets` encrypts the credentials of a waiting job. |
| DataPower | `mast/datapower/datapower` | `DataPower` talks to one appliance through its XML management interface (SOMA and AMP) and SSH. `Environment` is a group of appliances. The SOMA spec files are in `files/mast_home/etc`. |
| The commands | `mast/datapower/<plugin>` | `accounts`, `backups`, `crypto`, `deployment`, `developer`, `network`, `ssh`, `status`, `system`. Each has a module-level `cli` and a web plugin. |
| Shared pieces of commands | `mast/plugin_utils` | `params` (the appliance options and the widgets of the web form), `results` (the results that DataPower commands return), `steps` (perform an action on each appliance), `plugin_functions` (the web forms). |
| The web GUI | `mast/datapower/web` | A Flask application served by CherryPy. Each web plugin is a tab. |
| `mastd` | `mast/_daemon`, `mast/cron` | Hosts the web GUI, a worker and the scheduler. |
| The installer | `tools/mast_setup`, `files/invocation_scripts` | `./mast-setup`: builds the installer and the development home from `uv.lock`, with Python from python-build-standalone. The launchers of each platform. |

A command goes through these steps: the launcher starts
`python -m mast.datapower.<plugin>`. `Cli` parses the options with the
`Params` model and submits a job with the id `<plugin>.<command>`. A
worker, or the program itself when no worker is alive, claims the job
and runs the function. What the function reports with `say` is stored
as progress, and the `Result` that it returns is stored and shown.

The plugins are found through entry points in `pyproject.toml`:
`mast_web_plugin` for the tabs of the web GUI and `mastd_plugin` for
the threads of `mastd`. A `mastd_plugin` that has running work should
have `stop()` and `abort()`, so that `mastd` can wait for that work
when it stops (see `mast._daemon.plugins`). A worker finds the code of a job by importing
`mast.datapower.<plugin>` and taking its `cli`. A command that is
queued must therefore be in one of those modules.

# Adding a command

A command is a function with one parameter, annotated with a `Params`
model. This is `list-domains`, a little shortened:

    :::python
    from mast.cli import Cli, say
    from mast.plugin_utils.params import ApplianceParams
    from mast.plugin_utils.results import CommonItems

    cli = Cli(namespace="system")


    @cli.command("list-domains", category="domains", exclusive=False)
    def list_domains(params: ApplianceParams):
        """Lists the domains on the specified appliances as well as all
        common domains."""
        env = params.environment()
        domains = {{}}
        for appliance in env.appliances:
            say("Asking {{}} for its domains".format(appliance.hostname))
            domains[appliance.hostname] = sorted(appliance.domains)
        return CommonItems("Domains", domains, env)

**The parameters.** `ApplianceParams` has the options that every
DataPower command has: `-a` (appliances), `-c` (credentials), `-t`
(timeout) and `-n` (no hostname check). Add your own in a subclass:

    :::python
    from mast.cli import Option
    from mast.plugin_utils.params import SaveConfigParams


    class AddDomain(SaveConfigParams):
        domain_name: str = Option(
            short="d", help="The name of the domain to add")

* An option without a default is required.
* `short` is the one-letter option. Do not change it later: scripts use
it.
* `secret=True` keeps the value out of the logs and out of what the job
reports. The queue stores it encrypted while the job waits, and removes
it when the job ends.
* `widget` tells the web form how to ask for the value; see the
widgets in `mast/plugin_utils/params.py`, for example `UPLOAD` for a
file and `configured("Domain")` for a choice of domains.
* `help` is shown in `--help`, in the web GUI and in the CLI Reference.

**`@cli.command`.**

* `exclusive=False` for a command that only reads from the appliances.
The queue then runs it while other jobs use the same appliances. A
command that changes something keeps the default, so that two such
jobs never change one appliance at the same time.
* `resources` names what else the command must not share with another
job, for example a directory that it writes.
* `queued=False` for a command that talks to no appliance and whose
parameters or result must not be stored, such as `xor`.

**Progress and steps.** Call `say` between the steps of a command, so
the user sees the progress. A job is cancelled at its next step: at a
`say`, and before each request to an appliance. Use `Report`, `perform`
and `save_config` of `mast.plugin_utils` for a command that does
several things on each appliance, as `add-domain` does.

**The result.** Return a `Result`: `Text` and `Table` from `mast.cli`,
or a result of `mast.plugin_utils.results`. The command line prints its
text, and the web GUI shows its HTML. The outcome of the result
(`succeeded`, `partial` or `failed`) is the status of the job and
decides the exit status of the program.

The new command is on the command line, in the web GUI and in the
CLI Reference at once. Add a test for it (see "Tests" below), and, if
it changes an appliance, an integration test that checks the change on
the DataPower container.

# Adding a web plugin

A web plugin is a class with a `route`, `html`, `css` and `js`, listed
under `mast_web_plugin` in `pyproject.toml`. The DataPower plugins use
`mast.plugin_utils.plugin_functions`, which makes the forms from the
`Params` models, so a new command of an existing plugin needs nothing
more. The web GUI has a strict Content Security Policy:

* Put the JavaScript of a plugin in its `js()`, which is served as
`/plugins.js`. No inline scripts, no `on...` attributes.
* Put its styles in its `css()`. No `<style>` elements and no `style`
attributes; use a class.
* Do not load anything from another site.
* jQuery sends the CSRF token with every request. A form that the
browser submits itself needs the field `csrf_token`, see
`fileupload.html`.

# Conventions

* **Code in `mast`.** Use `str.format()`, not f-strings, in new code.
Document each public function with the docstring format of the API
Reference:

        :::python
        def find_home():
            """
            _function_: `mast.home.find_home()`

            Return the path of the MAST home ...
            """

* **Code in `tools`** (the build tool) uses f-strings and `pathlib`.
* **Secrets.** Never log a credential or a key. Mark secret options
with `secret=True`.
* **Compatibility.** Commands, options and their short forms, exit
statuses and configuration settings are what scripts depend on. Change
them only with an entry under "Changes that need your attention" in the
release notes.
* **Dependencies** are in `pyproject.toml`, with a comment that says
why. After a change, run `uv lock` and commit `uv.lock`. Every
dependency is checked for known vulnerabilities on each pull request.
* **Commits** say what changed and why, in plain words.

# Tests

| Tests | Where | Needs | Run |
|---|---|---|---|
| Unit and regression | `mast/testsuite/unit`, `mast/testsuite/regression` | Nothing | `uv run pytest mast/testsuite/unit mast/testsuite/regression` |
| The build tool | `tools` | Nothing (the slow build tests need `MAST_SETUP_TEST_BUILD=true`) | `uv run pytest tools` |
| Against DataPower | `mast/testsuite/integration` | podman or docker, and `MAST_TEST_DATAPOWER_ACCEPT_LICENSE=true` | `uv run pytest mast/testsuite/integration` |
| The installed product | `mast/testsuite/integration/test_installed.py` | A home in `MAST_TEST_HOME` (Linux and macOS) | `MAST_TEST_HOME=.dev/mast_home uv run pytest mast/testsuite/integration/test_installed.py` |

The tests make their own MAST home (`conftest.py`), so they do not
change your development home. The job queue tests also run against
PostgreSQL when `MAST_TEST_JOBS_DB_URL` names an empty database. The
README lists every variable.

A test of a bug fails without the fix. Before you push, make sure that
the new test fails on the old code. An installed MAST runs its own unit
tests with `test-mast --unit`.

# Documentation

* **The README** is the front page of the repository: what MAST does,
the quick start, the job queue, development and tests.
* **This documentation** is made from the files in
`files/mast_home/contrib/templates`. `contrib/gendocs.py` turns them
into HTML, and adds the CLI Reference and the API Reference from the
code:

        :::bash
        .dev/mast_home/mast contrib/gendocs.py -o /tmp/mast-doc

    The templates are passed through `str.format()` to put in the version
    (`{{0}}`), so a literal brace in a template is written twice.
* **The release notes** (`releasenotes.md`) get an entry under "Next
Release" for every change that users notice. Changes that can break a
script go under "Changes that need your attention".
* **The road map** (`roadmap.md`) lists the next steps and the known
issues. Remove an item when it is done, and add a known issue when you
find one that you do not fix.

Write in plain, short sentences, for readers whose first language is
not English.

# CI and releases

GitHub Actions runs three workflows:

* `tests.yml` on each pull request and each push to `main`: the unit
tests on Python 3.13, 3.14 and 3.15 (and with PostgreSQL), the
integration tests against a DataPower container, and a build of the
installer with the tests of the installed product.
* `audit.yml` on each pull request, each push to `main`, each week and
before each release: `pip-audit` on every package of `uv.lock`.
* `release.yml` when a tag `v*` is pushed: the tests and the audit,
then the installers for Windows, Linux and macOS, each one tested,
then a GitHub release with the installers, the audit report and a
software bill of materials.

To release, a maintainer sets `version` in `pyproject.toml` (the
version of MAST follows the Python that the installer bundles, set in
`[tool.mast-setup]`), moves "Next Release" in the release notes to the
new version, merges that, and pushes the tag `v<version>` on `main`.
The release workflow refuses a tag that does not match the version.
