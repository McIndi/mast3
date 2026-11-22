[Back to index](./index.html)
<h1>MAST for IBM DataPower Version {0}</h1>
<h2>Operator's Guide</h2>

[TOC]

# Introduction

This guide is for the people who run MAST for a team: install it as a
service, keep it secure, back it up and upgrade it. To install MAST,
see the [Installation Guide](./installation.html). To learn the
commands, see the [Getting Started Guide](./getting-started.html).

# The MAST home

The folder where you unzipped MAST is `$MAST_HOME`. Everything MAST
needs is in it, and everything MAST writes goes into it.

| Folder | What it holds | Yours or MAST's |
|---|---|---|
| `mast-*`, `mastd`, `test-mast` (`.bat` on Windows) | The launchers. | MAST's |
| `python/<version>` | The Python of MAST, with every library. | MAST's |
| `etc/default` | The default configuration. Do not change it. An upgrade replaces it. | MAST's |
| `etc/local` | Your configuration, and the keys that MAST makes (`jobs.key`, `server.key`). | Yours |
| `etc/crypto` | The certificate and private key of the web GUI. | Yours |
| `etc/crontab` | The scheduled tasks of `mastd`, if you made the file. | Yours |
| `var/jobs` | The job queue, when it is the default SQLite database. | Yours |
| `var/log` | The logs. | Yours |
| `var/run` | The process id of `mastd`. | MAST's |
| `var/www` | The web GUI. | MAST's |
| `tmp` | Downloads, uploads and backups that commands write. | Yours |
| `contrib` | Scripts that come with MAST, and the sources of this guide. | MAST's |
| `doc` | This documentation. | MAST's |

The launchers set `MAST_HOME`, change to it and start the Python of
MAST, so a launcher works from any directory. MAST runs with its home
as the current directory, and a relative path in the configuration is
relative to the home.

# Configuration

Each configuration file has a copy in `etc/default` that explains every
setting. To change a setting, put it in the file of the same name in
`etc/local`, in the same section. You only need the settings that you
change:

    :::ini
    # etc/local/server.conf
    [server]
    host = 0.0.0.0
    port = 8443

MAST reads the files again each time a program starts. Restart `mastd`,
`mast-web` and `mast-worker` after a change.

| File | What it configures |
|---|---|
| `appliances.conf` | The defaults for every appliance (`[global]`): ports, the SOMA spec file and so on. The appliances themselves, their own settings and the environments are in the database (`mast-appliances`). |
| `jobs.conf` | The job queue: its database, the workers, the lease, how often a running job looks for a cancel. |
| `server.conf` | The web GUI: host, port, TLS, the secret key, HSTS, sessions (`[auth]`). |
| `logging.conf` | The level and the size of the logs. |
| `ssh.conf` | `mast-ssh`: the timeout, and whether host keys that are not pinned are accepted. |
| `service-config.conf` | `git-deploy`, see the [git-deploy Guide](./git-deploy.html). |
| `xor.conf` | The key of `mast-system xor`. |

# Running MAST as a service

`mastd` hosts three parts of MAST:

* the web GUI,
* a worker, which runs the jobs of the queue,
* the scheduler, which runs the lines of `etc/crontab`.

The worker and the web GUI can also run alone, as `mast-worker` and
`mast-web`.

## Linux

`mastd start` starts `mastd` in the background and writes its process
id to `var/run/mastd.pid`. `mastd status` says whether it runs (exit
status 0 if it runs, 3 if it does not, 1 if it does not but its pid
file remains). `mastd restart` stops it and starts it again.

`mastd stop` sends `mastd` SIGTERM and waits until it has stopped.
`mastd` stops gracefully: it takes no new job and starts no new
scheduled task, and it stops when the running ones end. The web GUI
stops last, so it shows the jobs that `mastd` waits for. To stop it at
once, press Ctrl-C while `mastd stop` waits, or send `mastd` a second
SIGTERM: its running jobs are cut off and become `lost`, and its
running tasks are not waited for.

`mast-worker` stops the same way. On a server, you can run the worker
as a service of its own, so that you can stop and start the web GUI
without waiting for the jobs. Then turn off the worker of `mastd` in
`etc/local/jobs.conf`:

    :::ini
    [worker]
    mastd = false

With systemd, the two services can look like this. They run as the
user `mast`, from `/opt/mast`:

    :::ini
    # /etc/systemd/system/mast-worker.service
    [Unit]
    Description=MAST worker
    Wants=network-online.target
    After=network-online.target

    [Service]
    User=mast
    ExecStart=/opt/mast/mast-worker
    # A worker stops when its running jobs end. Give it as long as your
    # longest job (a firmware upgrade, a large backup).
    TimeoutStopSec=1h
    Restart=on-failure

    [Install]
    WantedBy=multi-user.target

    :::ini
    # /etc/systemd/system/mastd.service
    [Unit]
    Description=MAST web GUI and scheduler
    Wants=network-online.target
    After=network-online.target

    [Service]
    User=mast
    Type=forking
    PIDFile=/opt/mast/var/run/mastd.pid
    ExecStart=/opt/mast/mastd start
    ExecStop=/opt/mast/mastd stop
    # mastd stops when its running jobs and scheduled tasks end
    TimeoutStopSec=1h
    Restart=on-failure

    [Install]
    WantedBy=multi-user.target

Then run `systemctl daemon-reload` and
`systemctl enable --now mast-worker mastd`. For more jobs at the same
time, raise `concurrency` in the `[worker]` section of `jobs.conf`, or
run more workers.

Each launcher replaces itself with the Python process, so systemd,
`kill` and `docker stop` reach MAST directly.

## Windows

`mastd.bat install` installs `mastd` as a Windows service, and
`mastd.bat start` starts it. `mastd.bat install /?` lists the options,
for example the account that runs the service and whether it starts
with Windows. The service hosts the web GUI, a worker and the
scheduler.

## The scheduler

The scheduler reads `etc/crontab` at the start of every minute. Each
line is a cron expression and a command, for example a backup at 02:00
every day:

    :::text
    0 2 * * * ./mast-backups normal-backup -a prod -c <credentials> -n -o tmp/nightly

* The times are in UTC, not in the time zone of the server.
* The command runs in `$MAST_HOME`, with the shell.
* Each task runs in its own process, at the same time as the other
tasks that are due. A task is not started again while it still runs
from an earlier minute; that is logged.
* The exit status and the output of each task are logged in the log
`mast.cron`.
* If the scheduler checks late, because the server was busy or
suspended or its clock jumped, it starts the tasks of the minutes that
it missed, once each, up to an hour back. If the clock goes back, a
minute that was already checked is not checked again.
* Empty lines and lines that start with `#` are ignored. A line that is
not a valid task is logged, and the other lines still run.
* A change to `etc/crontab` takes effect at the next minute. You do not
have to restart `mastd`.

Credentials in `etc/crontab` are in clear text, or in the form of
`mast-system xor`, which is not encryption (see "Credentials" below).
Make the file readable only by the user that runs `mastd`.

# The job queue

Each command of the DataPower plugins, and each form of the web GUI, is
a job. `mast-jobs` shows them:

| Command | What it shows or does |
|---|---|
| `mast-jobs list` | The newest jobs. `-s failed` shows only failed jobs, `-n 50` shows more. |
| `mast-jobs show -j <id>` | One job: its parameters, its progress and its result. `-H` adds the requests and responses. |
| `mast-jobs follow -j <id>` | A job until it ends. |
| `mast-jobs cancel -j <id>` | Cancel a job. |
| `mast-jobs workers` | The workers that are alive. |

The first characters of an id are enough, as long as they are unique.

## SQLite or PostgreSQL

By default the queue is a SQLite database in `var/jobs`. That is right
for one MAST server. Use PostgreSQL when several MAST installations on
several hosts share one queue, for example web servers and workers on
different hosts:

    :::ini
    # etc/local/jobs.conf
    [database]
    url = postgresql+psycopg://mast:secret@db.example.com/mast

Every installation that uses the same database must have the same
`etc/local/jobs.key`: it encrypts the credentials of the jobs that
wait. Copy the file from the first installation to the others.

MAST makes and updates the tables of the queue itself. After an
upgrade, the first program that uses the queue updates them.

## Leases and lost jobs

A worker keeps each job that it runs with a heartbeat, every third of
`lease_seconds` (60 seconds by default). If the heartbeats stop for
longer than the lease (the worker was killed, or it cannot reach the
database), the queue declares the job `lost` and gives its appliances
to the next job. Nobody knows how much of a lost job was done: look at
the appliances before you run it again.

## Cancel

A job that waits is cancelled at once. A job that runs stops at its
next step, before its next request to an appliance: at once after
Ctrl-C in the program that runs it, and within about a second
(`cancel_poll_seconds`) after `mast-jobs cancel` or the Cancel link of
the web GUI. A request that has started is completed first. What the
job did before it stopped is not undone.

## Old jobs

MAST does not delete old jobs. Their parameters (without the
credentials, which are removed when a job ends), progress and results
stay in the database. Watch its size: the file `var/jobs/jobs.db`, or
the database in PostgreSQL.

# Appliances and environments

The appliances and the environments are in the job database, the
*appliance registry*. Manage them with `mast-appliances` on the MAST
server (it needs no login), or as an admin in the web UI under
Admin → Appliances. Everybody who can log in can see them; the target
bar of the Commands page suggests their names.

    :::bash
    ./mast-appliances add -n dp1 -A 10.0.0.1 --soma-port 5551
    ./mast-appliances update -n dp1 --clear soma_port --rename dp-one
    ./mast-appliances env-add -n prod -p -m dp-one
    ./mast-appliances list
    ./mast-appliances env-list

A job records the name of each registered appliance that it talks to.
An address that a registered appliance has counts as that appliance, so
that two jobs for one appliance wait for each other whatever name they
used. Every registered appliance also has a flag to collect its
status (`--poll`), which a later release uses, and a TLS check (see
below).

## Checking who the appliance is

Before MAST sends a password to an appliance, it checks that it talks
to that appliance and not to someone in between. Each registered
appliance has a TLS check for SOMA (`--tls-mode`):

* `ca` (the default): the certificate must be signed by an authority
that MAST trusts and be for the address of the appliance. That is the
trust store of the system, or the authorities of
`--ca-bundle-file FILE` (a PEM file).
* `pin`: the certificate must be the one that was pinned. This is for
the self-signed certificates that DataPower appliances often have.
* `none`: nothing is checked. Stored credentials are never sent this
way.

Pin an appliance with one command. MAST connects without a credential,
shows the certificate (subject, issuer, validity, SHA-256) and the SSH
host key (type, fingerprint), and asks before it pins them:

    :::bash
    ./mast-appliances pin -n dp1 --both
    ./mast-appliances pin -n dp1 -f 3E:6E:D2:...   # a SHA-256 you checked
    ./mast-appliances pin -n dp1 --ssh -f SHA256:IhWD...
    ./mast-appliances unpin -n dp1 --both

Compare what it shows with the appliance itself (its console, or a
colleague who can see it) before you confirm. With `-f` it pins the
fingerprint that you give and does not connect. In the web UI, the
**Pin** button of Admin → Appliances does the same.

An appliance that shows another certificate or host key than the
pinned one gets nothing: the job fails with "The certificate of NAME
changed (expected ..., got ...). An admin must check it and pin
again.", before any request is sent. `mast-appliances list` and
Admin → Appliances mark it CHANGED until an admin pins it again.

`--no-check-hostname` (`-n`) turns off every check of the certificate,
not only of its name, for an appliance that is not pinned. A pinned
certificate is checked anyway.

The SSH host key: a registered appliance with a pinned host key
accepts only that key, before the login. Without a pin, MAST accepts
any host key with a warning (or none, with `auto_add_keys = false` in
`ssh.conf`), and never with a stored credential.

`mast-appliances import` moves `hosts.conf`, `environments.conf` and
the sections of `appliances.conf` for single appliances of MAST 3.15.2
into the registry once; `mast-appliances export` writes them back for a
return to an older MAST. Back up the database (see "Backups") to keep
the registry.

# Stored credentials

An admin can store a user name and a password for appliances once, and
operators then run jobs with it by name: they never see the password.
Manage them with `mast-credentials` on the MAST server (it needs no
login), or as an admin in the web UI under Admin → Credentials.

    :::bash
    ./mast-credentials add -n collector -u mastread -p collect -b prod
    ./mast-credentials test -n collector -a dp1
    ./mast-credentials list
    ./mast-system list-domains -a prod --credential collector

* `add` asks for the password twice; `--password-stdin` reads it from
the first line of standard input, for scripts. The password is never an
option, and no command, page or answer of the API shows it again.
* `-b` binds the credential to an appliance or an environment (give it
once for each). A job can use it only on **registered** appliances that
it is bound to, directly or through an environment. A hostname that is
not in the registry is refused, so that nobody can send it to a host of
their own.
* The purpose `collect` is for a DataPower user that may only read: it
is used only by commands that only read (those that the Commands page
marks "This command only reads"). The purpose `change` is for any
command, and only admins may use it.
* A stored credential is sent only to an appliance whose certificate is
checked (`ca` or `pin`, see "Checking who the appliance is"): not with
`--no-check-hostname`, not to an appliance with `--tls-mode none`, not
over plain `http`, and over SSH only to a pinned host key. To use one
with a self-signed appliance, pin it first.
* `test` runs `mast-system check-credentials` with the credential, through
the job queue: it reads the firmware version, and asks the appliance to
ping itself, an action that changes nothing, to find out whether the
user can change the appliance. A `collect` credential that can is
reported with a warning. A DataPower user that an admin just made must
change its password once before DataPower gives it any data.
* `replace` gives a new password (and user name), `rebind` new
bindings, `update` a new description or purpose, `delete` removes it.
* `uses -n NAME` lists the jobs that used it, and `audit` every change
that an admin made. Neither has a secret in it.

In the web UI, the target bar of the Commands page offers the stored
credentials that can be used on all of its appliances. With one chosen,
the form sends its name and no password.

The passwords are encrypted in the job database with the keys in
`etc/local/credentials.key` (`[credentials] key_file` in `jobs.conf`),
which MAST makes the first time it is needed. On Linux and macOS, the
file and its folder must be readable only by the account that runs MAST:
MAST makes the folder private when it makes the key, and refuses the
credential store if the group or others can read either of them, with
the `chmod` that fixes it. `./mast-credentials rotate-key` makes a new
key, encrypts every credential again with it, and drops the keys older
than the one before it.

Keep a copy of `credentials.key` with your backups, but not in the same
place as the backup of the database: whoever has both has the
passwords. Without the key file, the stored passwords cannot be read;
store them again.

What this protects against, and what it does not:

* A copy of the database, or of a backup of it, without the key file
does not give the passwords.
* A user of the web UI who is not an admin cannot get a password, and
cannot point a stored credential at a host of their own.
* Someone who can read the files of the account that runs MAST has the
database and the key file, and so the passwords. So does someone who
can read the memory of a worker while a job runs.

# The web GUI

The web GUI listens on `127.0.0.1:5000` by default, so only the MAST
server can reach it. Set `host` and `port` in the `[server]` section
of `etc/local/server.conf` to change that.

Without TLS (`secure = false`), the host must be `127.0.0.1` or `::1`:
`mast-web` and `mastd` refuse to serve the web GUI without TLS on an
address that other hosts can reach, and say why.

The web UI is at `/`, and the classic GUI at `/classic/`. Both need
the same login. The web UI has these pages, each for a role and up:

* **Jobs** (viewer): every job, newest first, with filters (status,
mine, command, appliance), and the workers. A job shows its
parameters (secrets as `********`), its progress as it runs, its
result as a table that can be sorted and filtered, its child jobs,
and its downloads. An operator can cancel their own jobs, an admin
any job.
* **Commands** (operator): every command, with a form made from its
options. The target bar at the top holds the appliances and the
credentials for the next command. The browser keeps only the names
of the appliances (in the tab, until it is closed); a password stays
in the page and is sent only when you run a command. Running a
command opens its job.
* **Admin → Users** (admin): add a user with a temporary password,
change a role, disable, enable, set a new temporary password, and
delete. An admin cannot change their own role, disable or delete
themselves there; `mast-users` can.

The web UI has a JSON API under `/api/v1` that its pages use. It needs
the same login and the CSRF token, so it is not yet meant for scripts.

What only the classic GUI has for now: the `ssh` tab, and the status
tab with its charts. The `ssh` tab gets a page of its own later.

## Users and roles

Every page needs a login. The users are local to MAST, and are kept in
the job database. There is no sign-up page: an admin adds the users on
the MAST server with `mast-users`. Until there is a user, the web GUI
shows only how to add the first admin:

    :::bash
    ./mast-users add -u alice -r admin

`mast-users` asks for the password; it is never an option. A new user's
password is temporary, and they choose their own at the first login.
A password has at least 12 characters and is not the user name.

| Role | May |
|---|---|
| `viewer` | See the jobs, with their progress and results. |
| `operator` | Also run commands, and cancel their own jobs. |
| `admin` | Also cancel any job, manage the users, the appliances and the stored credentials in the web UI (Admin), and use credentials for changing. Operators may use the stored credentials for collecting. |

The other commands:

    :::bash
    ./mast-users list
    ./mast-users set-role -u alice -r operator
    ./mast-users set-password -u alice          # add -t to make it temporary
    ./mast-users disable -u alice               # and enable
    ./mast-users delete -u alice

The command line of MAST needs no login. Whoever can run it on the MAST
server can read its database and its key files, so it is an admin. The
jobs that it submits are recorded as `os:<account>`, the operating
system account that ran it. Jobs from MAST 3.15.2 and older are
recorded as `local`; an admin can cancel them, an operator cannot.

## Sessions

A session ends:

* at a logout, which also ends the other sessions of the user (a copy
of the session cookie does not work after it),
* when the user gets a new password or another role, or is disabled,
* `idle_minutes` (30) after it was last used, and `max_hours` (12)
after the login, from the `[auth]` section of `server.conf`.

Five failed logins in a row lock the user for 15 minutes. The error
does not say whether the user exists. `var/log/mast.web.auth.log`
records every login, failed login, lockout and logout, with the user
name and the address of the client, never the password.

## TLS

The web GUI uses TLS by default (`secure = true`), with the
certificate `etc/crypto/cert/mast.crt` and the private key
`etc/crypto/private/mast.key`. A fresh install has neither. The web GUI
does not start until it has them, and it says so. Either set `cert` and
`key` in `etc/local/server.conf` to the files of a certificate from
your certificate authority, or make a self-signed certificate:

    :::bash
    ./mast contrib/gen-cert.py

`./mast contrib/gen-cert.py -h` shows its options, for example
`--altName` for the names by which people reach the server.

Over TLS, the web GUI tells browsers to use only HTTPS for it for a
year (HSTS). If you later turn TLS off, set `hsts_max_age = 0` first
and open the web GUI once over HTTPS, or browsers keep asking for HTTPS.

## What the browser does

The web GUI signs its session cookie with the key in
`etc/local/server.key`, which MAST makes the first time. Each request
that changes something must carry the token of the session, so another
web site cannot submit a form of the web GUI. The web GUI only runs
scripts and style sheets that it serves itself, and other sites cannot
put it in a frame. A script that posts to the web GUI must first load
the page and send the token back, see the release notes.

# Credentials

MAST does not store appliance credentials. Each command gets them on
its command line, or in the form of the web GUI.

* The credentials of a job that waits in the queue are encrypted with
`etc/local/jobs.key`, and they are removed when the job ends.
* MAST removes the passwords that it recognizes from its logs, and the
values of secret parameters from the progress and the results of a
job. Treat the logs as sensitive all the same.
* `mast-system xor` turns credentials into a form that is not plain
text, for scripts and for `etc/crontab`. It is not encryption: anybody
who has MAST can turn it back. Protect such a file as you would protect
the password.

On a shared server, the command line of a process can be seen by other
users. Prefer the web GUI, or the form of `mast-system xor` in a file
that only you can read.

# Logs

Each MAST program writes its logs into a folder of its own:

    :::text
    var/log/<user>/<date and time>-<process id>/<logger>-<date and time>.log

The level and the size of the logs are in `etc/local/logging.conf`
(`level`, `max_bytes`, `backup_count`). A log file is rolled over when
it reaches `max_bytes`. MAST does not delete old log folders. Delete
them on a schedule, for example the folders older than 30 days:

    :::bash
    find /opt/mast/var/log -mindepth 2 -maxdepth 2 -type d -mtime +30 -exec rm -rf {{}} +

The web server also writes `var/log/flask.log`.

# Backups

Back up what is yours in the table of the MAST home:

* `etc/local`, with `jobs.key` and `server.key`, and `credentials.key`
(keep that copy apart from the copy of the database),
* `etc/crypto`,
* `etc/crontab`, if you have one,
* `var/jobs` (SQLite), or the PostgreSQL database with `jobs.key`,
* `tmp`, if commands write backups of your appliances there.

Without `jobs.key`, the jobs that wait in the queue cannot be run.
Without `credentials.key`, the stored credentials cannot be used.

# Upgrades

Install the new version next to the old one, and move your files to it:

1. Unzip the new version into a new folder, for example
`/opt/mast-new`.
2. Stop `mastd`, `mast-web` and the workers. A worker that stops
gracefully finishes its running jobs first.
3. Copy `etc/local`, `etc/crypto`, `etc/crontab`, `var/jobs` and what
you keep in `tmp` from the old home to the new one.
4. Rename the folders, so that the new home has the old path, or change
the paths of your services.
5. Start the services again. The first program that uses the job queue
updates its tables.

Read the release notes first: "Changes that need your attention" lists
what can change for your scripts. Keep the old folder until you know
that the new version works.

# Troubleshooting

**`MAST_HOME is not set and neither ... is in a MAST home`**: a program
of MAST was started outside its home without a launcher. Use the
launchers in `$MAST_HOME`, or set `MAST_HOME`.

**`mast-web: The web GUI uses TLS (secure = true in server.conf), but
...`**: the web GUI has no certificate. See "TLS" above.

**`This request has no valid CSRF token. Reload the web GUI and try
again.`**: a page of the web GUI was open while the web server
restarted, or a script posts without the token. Reload the page.

**`No worker is running. The job waits until one starts`**: a job that
was submitted with `--detach` waits for a worker. Start `mast-worker`
or `mastd`, or cancel the job with `mast-jobs cancel`.

**A job is `lost`**: its worker stopped or could not reach the database
for longer than the lease. Look at the appliances, then run the command
again. `mast-jobs show -j <id> -H` shows how far it got.

**The exit status of a command**: 0 if it succeeded, 1 if it failed on
some or all appliances, 2 for a bad option, 130 after Ctrl-C. See the
README for every status.

**More detail**: set `level = 10` in the `[logging]` section of
`etc/local/logging.conf`, run the command again, and look at its log
folder in `var/log/<user>`. Lower the level again afterwards: level 10
logs much more.
