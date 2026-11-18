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

It is important to place a key and cert in `$MAST_HOME/etc/crypto`.

For those with serious concerns over security, it may be a good idea to configure
the appliance's management interface to listen only to the IP Address of the MAST
server. This is a good idea if you really need to restrict access to the DataPower,
but if your organization embraces a more distributed or agile management strategy
there is the option to install MAST on your team's workstation.

# Installing MAST

The easiest way to install MAST for IBM DataPower is to place the installation zip file
in the directory you want your MAST installation and extract it. That directory is
`$MAST_HOME`. Put your own configuration in `$MAST_HOME/etc/local`: a file there
overrides the settings of the file with the same name in `$MAST_HOME/etc/default`.

The launchers in `$MAST_HOME` (`mast-system`, `mast-backups` and so on, with `.bat`
on Windows) are the usual way to run MAST. The console scripts in
`$MAST_HOME/python/<version>/bin` (`Scripts` on Windows) also work from any directory,
for example through a link in `/usr/local/bin`. On macOS they need macOS 13 or newer.

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
* Workers run the jobs. `mastd` hosts one, unless `mastd = false` is in the `[worker]`
section of `jobs.conf`. To run workers without `mastd`, start `mast-worker` as a
service. A worker stops when it gets SIGTERM, after its running jobs end.
* A job of the web GUI is done by a worker if one is running, and otherwise by the web
server. If the web server (`mastd` or `mast-web`) stops before such a job starts, the
job is cancelled.

# Installing mastd

The process of installing mastd as a service will vary depending on your target
operating system. Please follow the instructions for your particular platform.

## Windows

As part of a mast installation, a script is provided "mastd.bat". You can use this
script to install, control and remove the mastd service. You can install the mastd
service with the following command:

    :::batch
    mastd install
    mastd start

This will install mastd as a Windows service and it can be controlled and configured
as a standard Windows service. You can also provide certain parameters to control
how the service will run (ie which user to run as, or whether to start on boot),
please provide a "/?" option to mastd install to see all the available options.

## Linux

Because distributions vary greatly in how services are started (this is usually
called an init system and includes software such as SysVinit or systemd), we have
provided as much as we can to still be completely cross-platform. In the mast
installation directory, there is a script called mastd, which will start a
daemon. mastd accepts the following sub-commands: start, stop, restart and
status. When started it will create a "pid" file: "./var/run/mastd.pid" this
will contain the process id of the daemon.

Please consult your operating system's documentation on how to load this as a proper service.

# Conclusion

As you can see it is quite simple to install mast and get mastd installed as a service.
If you have any problems, questions or comments please email support@mcindi.com.
