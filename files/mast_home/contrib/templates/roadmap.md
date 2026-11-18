[Back to index](./index.html)
<h1>MAST for IBM DataPower Version {0}</h1>
<h2>Road Map</h2>

[TOC]

# Introduction

In this document we keep track of where MAST is heading: the next
steps, in order, and the known issues of this version. We give no
dates. Each step lists what it depends on, and the order can change
when we learn more. Please check back for updates, and tell us what
you need on the
[issue tracker](https://github.com/McIndi/mast3/issues).

# Direction

MAST does work that must be done the same way every time, on many
systems at once, with an audit trail of who did what and how it went.
IBM DataPower is the first kind of system that MAST manages, and it
will stay the one that we know best. The next steps make room for
others:

* MAST will be installable with `pip`, as `mcindi-mast` for the core
and `mcindi-mast[datapower]` with the DataPower plugin, as well as
with the self-contained installer that we ship today.
* MAST will work as a shared service for a team: with users,
permissions, and appliance credentials that MAST stores, so that
scheduled and long-running work does not need a person to type them.

# Next Steps

1. **A solid release**
    * Documentation for operators and contributors (this guide is part
    of it).
    * Tests that run the installed product: the launchers, the exit
    statuses, cancelled jobs and workers that stop.
    * A secret key for the web GUI that is made when MAST first runs,
    as the key of the job queue is.
    * Support for Python 3.13, 3.14 and 3.15, tested on each release.
    * A review of the dependencies.
2. **The core and DataPower apart, on PyPI**
    * Two packages: `mcindi-mast` (the command line, the job queue,
    the workers, the scheduler, the web GUI and the plugin system) and
    `mcindi-mast-datapower` (appliances, environments, the DataPower
    commands and the SOMA specs).
    `pip install mcindi-mast[datapower]` installs both. The commands
    keep their names.
    * `mast-setup install PATH` makes a MAST home from the installed
    packages, and updates it after an upgrade without touching your
    configuration (`etc/local`) or your data (`var`).
    * Each release is published on PyPI and on GitHub, next to the
    self-contained installers.
3. **Crypto inventory** (needs step 2)
    * A map of the cryptography across your DataPower domains, not
    only the certificates: every certificate and key (expiry, issuer,
    key type, key size), the credentials that group them, and the TLS
    profiles that use them, with their protocol versions and cipher
    suites. Legacy crypto profiles and unused files are flagged.
    * It runs as one job per appliance, so a large estate is read in
    parallel, and you see the progress and the results of each
    appliance as they come in.
    * Ready for public TLS certificates of at most 100 days from March
    15, 2027, and for post-quantum readiness work.
4. **A shared service** (needs step 2)
    * Users and permissions: who may run which commands on which
    appliances, and who may see and cancel which jobs.
    * Appliance credentials stored by MAST, separate from the
    identities of its users.
    * A page for the job queue (jobs, their progress and results,
    cancel, workers), the first page of a redesigned web GUI.
5. **Work that runs without a person** (needs step 4)
    * An MCP server, so that an assistant can use MAST. It starts
    local and read-only. Appliance credentials stay in MAST and are
    never given to the assistant.
    * The history of the status of your appliances, collected on a
    schedule through the job queue.
    * Scheduled crypto inventories.

# Under Consideration

* `git-deploy` stays as it is for now. A redesign may describe a
deployment with one file (JSON, YAML or XML) at the root of the
service repository, instead of the layout of its directories. Nothing
is decided.
* Plugins for systems other than IBM DataPower.

# Known Issues

* Commands whose result is a download or a table of their own
(`fetch-logs`, `check-connectivity` and others), and the steps of
`git-deploy`, do not report a failure in their exit status yet.
* The web GUI has no users or permissions yet (see step 4). Everybody
who can open it can see and cancel every job. Run it only where the
people who can reach it may do that.
* The default secret key of the web GUI is not secret. Until step 1,
set your own `secret_key` in the `[server]` section of
`etc/local/server.conf`.
* `git-deploy` needs the `git` program on the `PATH`.

# History

* Version 1
    * A product that eases the common pains of administering IBM
    DataPower appliances, as a group.
    * A command line, a web GUI and a Python API for the same
    commands, with plugins for the web GUI.
    * Installers for Linux and Windows.
* Version 2
    * `mastd`: a daemon on Linux and a service on Windows, which hosts
    the web GUI and a cron-style scheduler.
    * The Anaconda Python distribution, one logging system for all of
    MAST, and contributed scripts in `contrib`.
    * Documentation made from the source code.
* Version 3.0
    * Python 3.
* Versions 3.13 and 3.14
    * An installer with its own Python from python-build-standalone,
    which replaces Anaconda.
    * Installers for Windows, Linux and macOS, built and published by
    GitHub Actions for each release.
    * SOMA spec files for current DataPower versions, and integration
    tests against a DataPower container.
* Version 3.15.2 - Complete
    * The job queue: every command is a job with its progress, its
    result and its status, run by workers or by the command itself.
    * A new command line: each command declares its parameters with a
    model, and the command line, the web form and the help are made
    from it.
    * An installer built from locked versions with `./mast-setup`,
    with Python 3.15.2 and no need for internet access.

# Conclusion

This document is living and breathing. Please check back to see where
we are heading.
