[Back to index](./index.html)
<h1>MAST for IBM DataPower Version {0}</h1>
<h2>Contributing Guide</h2>

[TOC]

# Introduction

This document will outline the basic flow of code and/or ideas to the MAST for IBM DataPower version {0}.

We will outline a process which, when followed, will lead to a much more auditable and controlable
process for making changes to our code.

# Basics

We will list several guidelines here. It should be the goal of any contributer to follow each step in
this section.

1. Every change needs to start with an issue (the mast3 repository is a good place to start).
2. The issue is where discussion should take place regarding the actual problem and possible solutions.
3. A pull request normally won't be considered until the original issue is at least 24 hours old. This
is to allow interested parties time to comment.
3. Once a solution is agreed upon, a branch should be made, the desired changes implementated and a pull
request should be submitted.
4. The pull request is where code review takes place. Normally, the pull-request will not be merged until 24 hours
has passed. This is meant to give interested parties time to submit a code review.
5. Any change should have a test associated with it. The tests run with pytest, and every pull request runs
them in GitHub Actions.
6. Wherever possible we should link to/from each issue and pull request and we should use
[@mentions](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#mentioning-people-and-teams) to notify
anyone who might be interested in a particular issue or pull request)
7. If you are interested in updates to our project, you should
[watch](https://docs.github.com/en/account-and-profile/managing-subscriptions-and-notifications-on-github/managing-subscriptions-for-activity-on-github/viewing-your-subscriptions)
the [mast3](https://github.com/McIndi/mast3) repository.

# Building

## Requirements

You must have the following software installed and on the `PATH` on the build machine:

* Python 3.11 or newer, to run the build script (the installer has its own Python)
* [uv](https://docs.astral.sh/uv/)
* git

## building

1. clone repository `git clone https://github.com/McIndi/mast3`
2. Change directory `cd mast3`
3. Execute build script `./mast-setup` (on Windows, `python mast-setup`)
4. The installation zip file will be in a subdirectory called `dist`

To run MAST from the checkout as users do, with live code, make a
development home with `./mast-setup --development-mode` and run the
launchers in `.dev/mast_home`. See the "Development" section of the
README.

## Installing

To install simply unzip the installation zip file.

## Running tests

From a checkout, with [uv](https://docs.astral.sh/uv/):

    :::bash
    uv sync
    uv run pytest mast/testsuite/unit mast/testsuite/regression tools

The integration tests start an IBM DataPower container and are skipped unless
you accept its license. See "Running the tests" in the README.

The tests of the installed product run a MAST home through its launchers,
without DataPower: the exit statuses, cancelled jobs and workers that stop.
Point `MAST_TEST_HOME` at an unzipped installer or at `.dev/mast_home`:

    :::bash
    MAST_TEST_HOME=.dev/mast_home uv run pytest mast/testsuite/integration/test_installed.py

An installed MAST can run its own unit tests with `test-mast --unit`
(`test-mast.bat --unit` on Windows).
