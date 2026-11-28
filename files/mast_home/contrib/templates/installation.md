[Back to index](./index.html)
<h1>MAST for IBM DataPower Version {0}</h1>
<h2>Installation Guide</h2>

[TOC]

# Introduction

The installation of MAST for IBM DataPower has been designed to be as easy as
possible. Let's first review the requirements:

# Requirements

* 4 CPUs
* 8 GB RAM
* 64 GB storage

The following platforms are supported:

* Linux, with glibc 2.28 or newer (for example RHEL 8, Debian 10 or Ubuntu 20.04)
* Windows
* macOS

The installation zip file has its own Python and every library that MAST
needs, so nothing else has to be installed and no internet access is needed.
The one exception is `git-deploy`, which needs the `git` program on the `PATH`.

## Security concerns

* Run MAST under an account of its own, on a server for MAST alone. Whoever can read
the files of that account can read the stored passwords of the appliances: the
database and the key files are there. The Operator's Guide says what MAST protects,
and what it does not, under "What MAST protects".
* The web GUI needs a TLS certificate. It does not start without one, see "First run".
* If you can, let the management interfaces of the appliances accept connections only
from the MAST server.

# Installing MAST

The easiest way to install MAST for IBM DataPower is to place the installation zip file
in the directory you want your MAST installation and extract it. That directory is
`$MAST_HOME`. Put your own configuration in `$MAST_HOME/etc/local`: a file there
overrides the settings of the file with the same name in `$MAST_HOME/etc/default`.

The launcher `mast` in `$MAST_HOME` (`mast.bat` on Windows) is the usual way to run
MAST: `./mast datapower list-domains ...`, `./mast targets list`, `./mast web`.
`./mast` alone lists its roots and the installed plugins. `./mast-python` is the
Python of the installation, for the scripts in `contrib`. The console script `mast` in
`$MAST_HOME/python/<version>/bin` (`Scripts` on Windows) also works from any directory,
for example through a link in `/usr/local/bin`. On macOS it needs macOS 13 or newer.

# The job queue

Each command of the DataPower plugins, and each form of the web GUI, is a job in the
job queue. You do not have to set anything up: by default the queue is a SQLite
database in `$MAST_HOME/var/jobs`, and a command with no worker to run it does its own
job.

* To use PostgreSQL instead, set `url` in the `[database]` section of
`$MAST_HOME/etc/local/jobs.conf`, for example
`postgresql+psycopg://user:password@host/database`. `$MAST_HOME/etc/default/jobs.conf`
explains every setting.
* The credentials of a waiting job are encrypted with the key in
`$MAST_HOME/etc/local/jobs.key`. MAST makes this file the first time it is needed.
Every process that uses the database must read the same key, so back it up with the
database, and copy it to every MAST installation that shares a PostgreSQL database.
* Workers run the jobs, queue the jobs of the schedules when they are due, and delete
old jobs each day. `mastd` hosts one, unless `mastd = false` is in the `[worker]`
section of `jobs.conf`. To run workers without `mastd`, start `mast worker` as a
service. A worker stops when it gets SIGTERM, after its running jobs end.
* A job of the web GUI is done by a worker if one is running, and otherwise by the web
server. If the web server (`mastd` or `mast web`) stops before such a job starts, the
job is cancelled.

# The secret key of the web GUI

The web GUI signs its session cookie with the key in
`$MAST_HOME/etc/local/server.key`. MAST makes this file with a random key the first
time the web GUI starts, and you do not have to set anything up. To change the key,
stop the web GUI and delete the file. If several web servers must share one key, set
`secret_key` in the `[server]` section of `$MAST_HOME/etc/local/server.conf` to the
same long random value on each. `$MAST_HOME/etc/default/server.conf` explains both
settings.

# The users of the web GUI

Every page of the web GUI needs a login, and so does the command line. Add the first
admin in `$MAST_HOME`; it asks for the password. Then log in:

    :::bash
    ./mast users add-first-admin -u NAME
    ./mast login -u NAME

A home that a script sets up makes a service account instead (`--service`), and runs
`mast --as NAME ...`. The users are in the job database, so every MAST server that
shares a PostgreSQL database has the same users. The Operator's Guide explains the
permission sets and groups.

# First run

In `$MAST_HOME` (on Windows, `mast.bat` for `./mast` and `mast-python.bat` for `./mast-python`):

1. Make a TLS certificate for the web GUI, or set `cert` and `key` in
`etc/local/server.conf` to the files of one from your certificate authority:

        :::bash
        ./mast-python contrib/gen-cert.py --altName mast.example.com

2. Add the first admin, log in, and give yourself the commands of DataPower. It asks
for the password:

        :::bash
        ./mast users add-first-admin -u NAME
        ./mast login -u NAME
        ./mast groups add-member -n datapower-operator -u NAME

3. Add the appliances (targets of the kind `datapower`) and the fleets, or import
them from MAST 3.15.2 or older:

        :::bash
        ./mast targets add -n prod-dp1 -k datapower -A 10.0.0.1
        ./mast fleets add -n prod -k datapower -p -m prod-dp1
        ./mast targets import --dry-run      # from etc/local/hosts.conf ...

4. Pin each appliance whose certificate is self-signed. MAST shows the certificate
and the SSH host key; compare them with the appliance before you confirm:

        :::bash
        ./mast targets pin -n prod-dp1

5. Store a credential for a DataPower user that may only read (the Operator's Guide
shows how to make that user), and test it:

        :::bash
        ./mast credentials add -n collector -u mastread -p collect -b prod
        ./mast credentials test -n collector -a prod-dp1

6. Start `mastd` (see "Installing mastd"), or `mast web` and `mast worker`, and log
in to `https://<server>:5000/`. The Operator's Guide has a short tour of the web UI.

7. Turn on the attribute `poll` of the appliances (Admin → Targets, or
`./mast targets update -n prod-dp1 -s poll=yes`), and start the polling with the
stored credential on the Appliances page. From then on it shows the status of each
appliance, as long as a worker runs.

# Installing mastd

The process of installing mastd as a service will vary depending on your target
operating system. Please follow the instructions for your particular platform.

## Windows

As part of a mast installation, a script is provided "mastd.bat". You can use this
script to install, control and remove the mastd service. You can install the mastd
service with the following command:

    :::batch
    mast daemon install
    mast daemon start

This will install mastd as a Windows service and it can be controlled and configured
as a standard Windows service. You can also provide certain parameters to control
how the service will run (ie which user to run as, or whether to start on boot),
please provide a "/?" option to mast daemon install to see all the available options.

## Linux

Because distributions vary greatly in how services are started (this is usually
called an init system and includes software such as SysVinit or systemd), we have
provided as much as we can to still be completely cross-platform. In the mast
installation directory, there is a script called mastd, which will start a
daemon. mastd accepts the sub-commands start, stop, restart and status. When
started it will create a "pid" file: "./var/run/mastd.pid" this will contain the
process id of the daemon. `mast daemon stop` waits until the running jobs and
scheduled tasks of `mastd` have ended.

The [Operator's Guide](./operations.html) has systemd units for `mastd` and for
`mast worker`.

# Conclusion

As you can see it is quite simple to install mast and get mastd installed as a service.
If you have any problems, questions or comments please email support@mcindi.com.
