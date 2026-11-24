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
| `crypto.conf` | The posture rules of the crypto inventory: their thresholds, the weights of the score, how long an exception may last, the rules and packs turned off, and the dates of the PQC and CNSA 2.0 rules. |
| `server.conf` | The web GUI: host, port, TLS, the secret key, HSTS, sessions (`[auth]`). |
| `logging.conf` | The level and the size of the logs. |
| `ssh.conf` | `mast-ssh` and the Terminal page: the timeouts, whether host keys that are not pinned are accepted, and how long a session of the Terminal page lasts. |
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
Make the file readable only by the user that runs `mastd`. For a
command of MAST, prefer a schedule of the job queue (see "Scheduled
jobs" below): it uses a stored credential, and its jobs are in the
Jobs page.

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
| `mast-jobs schedule-list` | The scheduled jobs (see "Scheduled jobs"). |
| `mast-jobs prune` | Delete old jobs and files now (see "Old jobs"). |

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

## Scheduled jobs

A schedule puts a command in the job queue when its cron expression is
due. Every worker (`mast-worker`, and the one that `mastd` hosts)
checks the schedules at the start of every minute, unless
`schedules = false` is in the `[worker]` section of `jobs.conf`. So the
schedules run whenever a worker runs. Manage them with `mast-jobs` on
the MAST server, or as an admin in the web UI under Admin → Schedules,
which has the form of each command and shows the next five runs:

    :::bash
    ./mast-jobs schedule-add -n nightly-domains -c system.list-domains \
        -t "0 2 * * *" -s 'appliances=["prod"]' -s credential=collector
    ./mast-jobs schedule-list
    ./mast-jobs schedule-run -n nightly-domains     # now, once
    ./mast-jobs schedule-disable -n nightly-domains # and -enable
    ./mast-jobs schedule-update -n nightly-domains -t "30 1 * * *"
    ./mast-jobs schedule-remove -n nightly-domains

* The times are in UTC: five fields (minute, hour, day of the month,
month, day of the week), or `@daily`, `@hourly` and the like.
* A schedule holds no password. A command that talks to appliances uses
a stored credential (`-s credential=NAME`, see "Stored credentials"),
which is checked when the schedule is saved and again for each job.
* The parameters are those of the command: `-p` takes them as a JSON
object, and `-s NAME=VALUE` sets one (a VALUE that is JSON, such as
`["prod"]`, is read as JSON).
* Its jobs run as `schedule:NAME`, with the role of the admin who made
the schedule. The Jobs page can show only the jobs of one schedule.
* If the job of the last run still waits or runs when the schedule is
due again, that run is skipped, and `schedule-list` says why.
* If the admin who made it is removed, disabled or no longer an
operator or an admin, the schedule is paused, with the reason. Enable
it again once that is fixed, or make it again as another admin.
* If no worker was running, the runs that were missed are not made up,
except that after a short gap (up to an hour) a schedule that was due
runs once. Several workers on one database do not queue a run twice.

## Old jobs

The workers that check the schedules also delete old things each day
at 03:30 UTC, as `[retention]` of `jobs.conf` says. If several do, the
first one deletes and the others find little left. `mast-jobs prune` does it now; `--dry-run` only counts.

| What | Kept for (days) | Setting |
|---|---|---|
| Finished jobs that people submitted, with their progress, results and files | 90 | `jobs_days` |
| Finished jobs of schedules that succeeded | 7 | `scheduled_jobs_days` |
| Finished jobs of schedules that did not succeed | 30 | `scheduled_failed_days` |
| The uses of stored credentials, and the audit events of the credentials | 400 | `credential_uses_days`, `audit_events_days` |
| Files uploaded in the web UI | 1 | `uploads_days` |
| Log files in `var/log` (by the time they were last written), and the folders that they leave empty | 30 | `logs_days` |
| The status of the appliances: each poll, then summed up by hour, then by day | 2, 31, 400 | `status_samples_days`, `status_hourly_days`, `status_daily_days` |
| The snapshots of the crypto inventory (the newest of each appliance stays) | 400 | `crypto_snapshots_days` |

A child job goes with its parent, and a parent stays while one of its
children has not finished. `0` keeps that kind of thing for ever, and
an empty `schedule` turns the daily run off. The deletes go in batches
of 1000 rows, so that the queue is never held up for long.

## The status of the appliances

`mast-status collect` asks the appliances for their status and keeps it
for the Appliances page: whether they answer, the firmware, the CPU,
the memory, the load, the free space, the domains and the objects that
are down. It keeps no configuration. With no `-a`, it polls every
appliance whose status is collected (`mast-appliances update -n NAME
--poll yes`, or "Collect status" in Admin → Appliances).

    :::bash
    ./mast-status collect --credential collector     # now, once
    ./mast-status collect -a dp1 -c user:password

Poll on a schedule with a stored credential of a user that may only
read (see "A DataPower user that may only read"). An admin starts it on
the Appliances page, which makes the schedule `status-poll` (every 5
minutes by default), or on the command line:

    :::bash
    ./mast-jobs schedule-add -n status-poll -c status.collect \
        -t "*/5 * * * *" -s credential=collector

* One job polls all its appliances, 8 at a time (`--workers`). The
requests to one appliance are one after the other, with a short pause
(`--request-delay`, 0.1 s), because DataPower answers a quick series of
requests with "Authentication failure".
* An appliance that does not answer is marked so, with the reason, and
keeps the values of its last good poll. The job is then `partial`.
* An object is down when it is enabled but not up; the objects that an
admin disabled do not count.
* DataPower measures the CPU only while **Statistics** is enabled in
the `default` domain, and it is disabled out of the box. Without it the
CPU is empty and the page says "Statistics off: no CPU". Enable the
`Statistics` object of the `default` domain on the appliance; the CPU
shows from the next poll. The domains other than `default` are
asked for their objects every 15 minutes (`--object-status-minutes`).
* The jobs of `mast-status collect` are passive: they neither wait for
nor hold up other jobs. Each worker also has a slot for passive jobs
only (`passive_slots` in the `[worker]` section of `jobs.conf`), so a
long backup or deployment does not stop the status from updating.

## The crypto inventory

`mast-crypto inventory` reads what cryptography the appliances use and
keeps it as a snapshot of each appliance in the job database: the
certificates (subject, issuer, validity, key algorithm and size,
signature algorithm, alternative names), the keys, the identification
and validation credentials, the crypto profiles, the TLS profiles
(protocols, ciphers, KEM algorithms), the password aliases (their names
only), the files of `cert:`, `pubcert:` and `sharedcert:`, and which
object uses which, up to the listeners and the services. No password,
key or file content is kept; a key with a plaintext password in its
configuration is marked so. With no `-a`, every registered appliance.

    :::bash
    ./mast-crypto inventory --credential collector
    ./mast-crypto inventory -a prod -d app1 -d app2 --credential collector
    ./mast-jobs schedule-add -n crypto-inventory -c crypto.inventory \
        -t "0 2 * * *" -s credential=collector

* Each appliance is a child job of the inventory: the Jobs page shows
them under it, each with its progress and its result. The children run
in the same worker as their parent, a few at a time (`--workers`).
* For each domain, MAST reads the running and the saved configuration
once each, and each certificate with `do-view-certificate-details`.
On firmware older than 7.5 (whose SOMA spec does not have that
request), it exports each certificate to a temporary file with a name
of the job, reads it and deletes it, also when something fails.
* A user that may only read (see "A DataPower user that may only read")
can read everything that the inventory needs.

`mast-crypto export` writes the newest snapshot of each appliance (or
the one of `--snapshot ID`) to a file:

    :::bash
    ./mast-crypto export -f cbom      # CycloneDX 1.6 JSON
    ./mast-crypto export -f xlsx      # a sheet each: certificates, keys, ...
    ./mast-crypto export -f csv -a prod-dp1

The CBOM (cryptographic bill of materials) lists each certificate, key,
credential, password alias and TLS profile as a `cryptographic-asset`,
the algorithms of the certificates, each listener or service that uses
them as an `application`, and the references as `dependencies`. The
spreadsheet also has a sheet of the findings (see below). **Export**
on the Crypto page downloads the same files, made from the newest
snapshots of every appliance, for any role.

## The posture rules

Each snapshot that `mast-crypto inventory` keeps is checked by the
posture rules at once, and their findings are kept with it. The
**Crypto** page shows them, and `mast-crypto rules` lists the rules:

| Rule | Severity | Finds |
|---|---|---|
| `cert-expired` | critical | A certificate that has expired. |
| `cert-expiring` | high, medium, low | A certificate that expires within 30, 60 or 90 days. |
| `tls-old-protocol` | high (critical for SSL) | A TLS profile with SSL 3.0, TLS 1.0 or TLS 1.1 on; a crypto profile that does not disable them. |
| `tls13-off` | low | A TLS profile with TLS 1.3 off, on a firmware that has it. |
| `weak-rsa-key` | high | A certificate with an RSA key under 2048 bits. |
| `weak-signature` | high | A certificate signed with SHA-1 or MD5 (not a self-signed one: nobody checks the signature of a trust anchor). |
| `self-signed-production` | medium | A self-signed certificate that an identification credential presents, on an appliance of an environment marked production. |
| `plaintext-password` | high | A key or certificate whose password is in its configuration as plain text. |
| `plaintext-password-unused` | medium | A key or certificate that uses a password alias but still has a plaintext password in its configuration: DataPower ignores it, but keeps it and get-config returns it. |
| `unused-file` | low | A file of `cert:` or `sharedcert:` that no object of the running configuration names. |
| `not-saved` | low | A domain whose crypto objects differ between the running and the saved configuration. |
| `disabled-in-use` | medium | A disabled object that an enabled object still refers to. |
| `mast-tls-none` | high | A registered appliance with `tls_mode none`. |
| `mast-collect-can-write` | high | A stored credential for collecting whose user could change the appliances at its last test. |
| `mast-collect-untested` | low | A stored credential for collecting that was never tested (or whose last test failed): until a test, MAST cannot tell whether its user can change the appliances. |
| `mast-credential-unused` | low | A stored credential that no job used for 180 days. |

The rules that start with `mast-` check how MAST is set up, not a
snapshot, so they are checked each time the page asks. The thresholds
are in `etc/default/crypto.conf`; put changes in
`etc/local/crypto.conf`, where `[rules] disabled` turns rules off. Run
the rules again on the newest snapshots after a change:

    :::bash
    ./mast-crypto posture                 # the findings, and the self-checks
    ./mast-crypto posture -a prod-dp1
    ./mast-crypto rules

The **posture score** of an appliance is 100 minus the weight of each
finding that no exception covers (critical 40, high 15, medium 5, low 1,
in `[score]`), and never below 0. The Appliances page shows it, with the
certificate that expires first.

An **exception** accepts the findings of one rule on some objects until
a date, for a reason. Its findings stay on the Crypto page, marked with
the exception, and no longer lower the score. When it expires, they
count again. Only an admin can add or remove one (the action
`manage-exception`), on the Crypto page or with:

    :::bash
    ./mast-crypto exception-add -r unused-file -p 'cert:///old-*' \
        -R "Kept until the rollback window closes" -e 2027-01-31 -a prod-dp1
    ./mast-crypto exceptions
    ./mast-crypto exception-remove -i 3

The object is a name or a shell pattern (`old-*`); the appliance and
the domain are optional (every one by default). An exception lasts at
most 365 days (`[exceptions] max_days`).

## Post-quantum readiness

The `pqc` pack of rules checks whether the TLS of the appliances can
use hybrid post-quantum key exchange (`X25519MLKEM768`,
`SecP256r1MLKEM768`, `SecP384r1MLKEM1024`). Key exchange is the urgent
part: traffic recorded today can be decrypted later, when a quantum
computer can break the classical exchange.

What a profile negotiates depends on the firmware, its `KEMAlgs` and
its protocols, not on `KEMAlgs` alone. MAST uses what tests of every
level from 10.5 to 11.0.0.3 found:

| Firmware | With no `KEMAlgs` | `KEMAlgs` |
|---|---|---|
| 10.5 and older, 10.6.0.x before 10.6.0.11 | classical only | refused |
| 10.6.0.11 | negotiates `X25519MLKEM768` | refused |
| 10.6.2, 10.6.3 (preview) | classical only | accepted, with preview names that standard clients cannot use |
| 10.6.4 and newer | negotiates `X25519MLKEM768` | accepted |

From 10.6.2 to 11.0.0.1, a profile with `KEMAlgs` and TLS 1.2 (or
older) on **goes down**, although the change says OK: every service
that uses it refuses connections. From 11.0.0.2 it is allowed, and TLS
1.2 then works only if `KEMAlgs` has `classical`.

| Rule | Severity | Finds |
|---|---|---|
| `pqc-no-hybrid` | high | A server or client profile with TLS 1.3 on that cannot negotiate a hybrid group. |
| `pqc-tls13-off` | high | A server or client profile with TLS 1.3 off: no post-quantum key exchange is possible. |
| `pqc-only` | medium | A profile whose `KEMAlgs` has no `classical`: it refuses every peer without a PQC group, and all of TLS 1.2. |
| `pqc-profile-down` | high | A TLS profile that is down (from its status; or, on an older snapshot, one that the firmware would take down). |
| `pqc-preview-names` | medium | Preview names (`mlkem768`, `x448_mlkem768`...) in `KEMAlgs`. |
| `pqc-firmware` | medium, low | Firmware that cannot choose its PQC groups. |
| `pqc-pure-mlkem` | info | `MLKEM768` (or another ML-KEM) without a hybrid group. |
| `pqc-probe-mismatch` | medium | A port where the probe saw other groups than the configuration predicts. The probe is the truth. |
| `pqc-classical-certificate` | info, rising | A certificate with an RSA or EC key, by its NIST IR 8547 date: RSA under 3072 bits is deprecated after 2030, and every RSA and EC key is disallowed after 2035. The severity rises as the year comes nearer (`[pqc]` in `crypto.conf`). |
| `pqc-legacy-profile` | medium | An SSL proxy profile: its crypto profiles cannot use PQC. |

The `cnsa` pack (CNSA 2.0: ML-KEM-1024 and ML-DSA-87, for the systems
that must follow it) is off. To turn it on, put an empty list in
`etc/local/crypto.conf`:

    :::ini
    [packs]
    disabled:

The **TLS probe** connects to the ports of the appliances as a peer
would, and records what they accept: the protocol, cipher and group
that a default client negotiates, the certificate chain, and whether
each of TLS 1.0 to 1.3, each hybrid group, `MLKEM768` and the classical
groups alone is accepted. It needs no credential and logs in to
nothing.

    :::bash
    ./mast-crypto probe                    # every registered appliance
    ./mast-crypto probe -a prod -t gw.example.com:443

* The ports are those of the listeners of the newest inventory (the
front side handlers that use a server profile, the XML management and
the WebGUI), and the XML management and WebGUI ports of the registry.
For those two the port of the registry wins, so that an appliance
behind NAT or a port mapping is reached.
* The group checks need Python 3.15 (the Python that MAST ships with)
and OpenSSL 3.5. On an older Python, the probe still checks the
protocols and the certificates, and says that the groups were not
checked.
* After probing an appliance, the rules of its newest snapshot run
again. A probe counts for a snapshot if it is at most 8 days older
(`[pqc] probe_max_age_days`).
* The **TLS probe** tab of the Crypto page shows the newest probe of
each appliance; an operator can run it, and an admin can make the
schedule `crypto-probe` (Sundays at 03:00). The **PQC readiness** card
of the Overview shows each appliance at three levels: the firmware
(can it?), the profiles (are they configured?) and the probe (do the
peers get it?).
* Old probe results go with the snapshots (`crypto_snapshots_days` in
`jobs.conf`); the newest result of each port stays.

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

What this protects against, and what it does not, is in "What MAST
protects" below.

## A DataPower user that may only read

A `collect` credential should be for a DataPower user that cannot change
anything, so that a mistake or a stolen credential cannot change an
appliance either. On DataPower, that is a user group whose only access
policy is `*/*/*?Access=r` (read everything, in every domain), and a
user in that group. It needs no other policy: with only this one, every
command that only reads works, including the status of the appliance,
the configuration, the file stores and the content of files, and
DataPower answers every change with "Authorization failure". This was
tested on DataPower 11.0.0.3.

With MAST, as an admin, with a credential that may change the
appliances (`--credential admin-cred` here, or `-c`):

    :::bash
    ./mast-accounts add-group -a prod --credential admin-cred \
        -N mast-read -A '*/*/*?Access=r' -s
    ./mast-accounts add-user -a prod --credential admin-cred \
        -u mastread -p 'A-long-password' -g mast-read -S -s
    ./mast-credentials add -n collector -u mastread -p collect -b prod
    ./mast-credentials test -n collector -a prod-dp1

`-S` spares the user the change of password at the first login, which
DataPower otherwise asks for before it gives the user any data. `-s`
saves the configuration. `test` should say `works: yes` and
`can change: no`. Give the same password to `mast-credentials add` when
it asks.

# The web GUI

The web GUI listens on `127.0.0.1:5000` by default, so only the MAST
server can reach it. Set `host` and `port` in the `[server]` section
of `etc/local/server.conf` to change that.

Without TLS (`secure = false`), the host must be `127.0.0.1` or `::1`:
`mast-web` and `mastd` refuse to serve the web GUI without TLS on an
address that other hosts can reach, and say why.

The web UI is at `/`. It has these pages, each for a role and up (the
classic GUI of earlier releases is gone; see the release notes for what
replaced each of its tabs):

* **Appliances** (viewer), the first page after the login: one row for
each appliance, the ones with a problem first. It shows whether the
appliance answered and when, its firmware, its CPU, memory and
temporary space, its domains and the objects that are down, and
warnings: a certificate or a host key that changed, a credential of the
poll that could change the appliance, a status that is old. A row opens
to show the domains, the objects that are down, the charts of the last
24 hours, and links to its jobs and to run a command on it. The page
refreshes every 30 seconds. With no schedule that polls, it says how to
start one (see "The status of the appliances"). Its last two columns,
the certificate that expires first and the posture score, come from the
crypto inventory and link to the Crypto page.
* **Crypto** (viewer): the crypto inventory and its posture (see "The
posture rules"), in four views. **Overview**: the score and the
findings of each appliance, the certificate that expires first, the
date of its last snapshot, and a chart of the certificates that expire
in the next 12 months. An operator can run the inventory now with a
stored credential for collecting (it opens the job), and an admin can
make it run every day. **Inventory**: the certificates, keys,
credentials, TLS profiles, password aliases and files of the newest
snapshots, to filter. An object opens to show its configuration
(without secrets), its certificate, what it uses, what uses it, its
findings, and for a certificate what breaks if it expires.
**Findings**: grouped by rule, with filters, and the exceptions; an
admin adds an exception from a finding. **History**: the snapshots of
an appliance, and what changed between two of them (objects added,
removed and changed, findings opened and closed).
* **Jobs** (viewer): every job, newest first, with filters (status,
mine, command, appliance), and the workers. A job shows its
parameters (secrets as `********`), its progress as it runs, its
result as a table that can be sorted and filtered, its child jobs,
and its downloads. An operator can cancel their own jobs, an admin
any job.
* **Commands** (operator): every command, with a form made from its
options. The target bar at the top holds the appliances and the
credentials for the next command. A list of what is configured on the
appliances (the domains, for example) is asked from them with those
credentials; with a stored credential, that is a short job of
`system.list-objects`, which the Jobs page shows like any other. The browser keeps only the names
of the appliances (in the tab, until it is closed); a password stays
in the page and is sent only when you run a command. Running a
command opens its job. **Test** checks the login to each appliance
(see "Testing the login to the appliances").
* **Terminal** (operator): the DataPower CLI of the appliances of the
target bar, side by side (see "The Terminal page").
* **Admin → Users** (admin): add a user with a temporary password,
change a role, disable, enable, set a new temporary password, and
delete. An admin cannot change their own role, disable or delete
themselves there; `mast-users` can.
* **Admin → Appliances** (admin): the appliances and the environments
(see "Appliances and environments"), and the **Pin** button, which
shows the certificate and the SSH host key of an appliance before it
pins them.
* **Admin → Credentials** (admin): the stored credentials, their test,
their uses and the audit of their changes. No page shows a password.
* **Admin → Schedules** (admin): the schedules, with the form of their
command, the next runs and **Run now** (see "Scheduled jobs").

The web UI has a JSON API under `/api/v1` that its pages use. It needs
the same login and the CSRF token, so it is not yet meant for scripts.

## A short tour

1. Log in. Until there is a user, the page says how to add the first
admin on the MAST server.
2. As an admin, add or import the appliances (Admin → Appliances), pin
each one, turn on "Collect status", and store a credential for
collecting (Admin → Credentials). Test it there. Then start the polling
on the **Appliances** page, which shows the status of each appliance
from then on.
3. As an operator, open **Commands**. Choose the appliances or an
environment in the target bar at the top, and a stored credential (or
type a user name and a password). Find a command by name or by its
plugin; its form shows its options. **Run** opens its job.
4. **Jobs** shows the job as it runs, then its result as a table that
you can sort and filter, and its downloads. The filters find the jobs
of an appliance, a command, a schedule or your own. **Edit and run
again** opens the form of the command with the values of the job, to
change one thing and run it again; type the passwords and choose the
files again.
5. As an admin, turn a command that you run often into a schedule
(Admin → Schedules). Its jobs run while a worker runs, without
anybody logged in.
6. As an operator, open **Terminal** for the DataPower CLI of the
appliances of the target bar, side by side (see "The Terminal page").

## The Terminal page

The **Terminal** page opens an SSH session to the DataPower CLI of each
appliance of the target bar, in one pane each (a grid on a wide screen,
tabs on a phone). A line goes to every pane that is ticked, and the
answers come side by side. Up and Down go through the lines sent;
**Ctrl-C** (or the button) stops a line that is still running. Each
pane has **Download transcript** and, when it lost its connection,
**Reconnect**. A reload of the page attaches to its session again.

* **Who:** a terminal can change anything, so it is for operators and
admins only; a viewer does not see the page. An operator types a user
name and a password in the target bar. An admin may also choose a
stored credential of purpose `change`, bound to every appliance and
only on appliances whose SSH host key is pinned (Admin → Appliances).
* **Each session is a job** (`ssh.terminal`) that the web server runs
itself: the Jobs page shows each line (`> line`) and each answer, and
the whole transcript when the session ends. **Cancel** there closes the
session. The jobs are passive: a terminal does not hold up a
deployment on the same appliance, and what is typed in it runs
alongside the jobs.
* **Passwords:** the password of the login, and a line typed while an
appliance asks for a password (the input becomes a password field),
are `********` in the job, the transcripts and the logs.
* **Limits** (`[ssh]` in `ssh.conf`): a session closes after 15
idle minutes (`idle_minutes`) and after 8 hours (`max_hours`); a user
has at most 3 open (`max_sessions_per_user`), each of at most 20
appliances (`max_appliances_per_session`); one line runs at most 120
seconds (`command_timeout`). It also closes when its user logs out or
gets a new password, and when the web server stops.
* The sessions live in the web server process: run one web server for
each MAST home (`mast-web`, or `mastd`).
* The output is shown as text: ANSI colours are removed, not rendered.

On the command line, `mast-ssh` does the same in one terminal. With a
stored credential (`mast-ssh -a dp1 --credential NAME`: purpose
`change`, pinned SSH host keys), its session is recorded as an
`ssh.terminal` job too, with each line and each use of the credential.

    :::bash
    ./mast-ssh -a dp1 -a dp2 --credential changer

## Testing the login to the appliances

**Test** in the target bar (on the Commands and Terminal pages) asks
whether MAST can log in to each appliance of the bar with the typed or
the stored credential: to its XML management interface (it reads the
firmware version) and to its SSH (it logs in and out). It changes
nothing. Each appliance shows `SOMA ok` or `failed` and `SSH ok` or
`failed`; hover for the firmware or the reason. It runs a passive job
of `mast-system test-connectivity`, so it is on the Jobs page; with a
stored credential, SSH needs a pinned host key. On the command line:

    :::bash
    ./mast-system test-connectivity -a dp1 -a dp2 --credential collector

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
| `admin` | Also cancel any job, manage the users, the appliances and the stored credentials in the web UI (Admin), add and remove the exceptions of the posture rules, and use credentials for changing. Operators may use the stored credentials for collecting. |

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
scripts and style sheets that it serves itself, and nobody can put it
in a frame. A typed password stays in the memory of the page and is
sent only in the body of the request that needs it; it is never kept
in the browser. The browser keeps, for its tab only, the names of the
appliances of the target bar and of the stored credential chosen
there, and the server checks again for each job that the user may use
that credential on those appliances. A script that posts to the web GUI must first load
the page and send the token back, see the release notes.

# Credentials

A command gets the credentials for the appliances in one of two ways:
the name of a stored credential (`--credential`, see "Stored
credentials"), whose password never leaves the MAST server, or a user
name and a password on its command line (`-c`) or in the form of the
web GUI.

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

# What MAST protects

MAST protects against:

* A copy of the database, or of a backup of it, without the key file:
it does not give the stored passwords.
* A user of the web UI who is not an admin and wants a password of an
appliance: no page and no answer of the API shows one, and an operator
can use a stored credential only for commands that only read, on the
appliances that it is bound to.
* A user who points a stored credential at a host of their own: it is
sent only to the registered appliances that it is bound to.
* Someone in between MAST and an appliance: a stored credential is
sent only after the certificate (or the SSH host key) is checked, and
a pinned one is always checked.
* A stolen session cookie, after a logout, a new password or a disable.
* A password in a log, a job, its result, its files or an answer of the
API: a test runs a job with a stored credential in every way and
searches each of them for the password.

MAST does not protect against:

* Someone who can read the files of the account that runs MAST. They
have the database and the key files, and so the passwords. Keep the
account and the server for MAST alone.
* Someone who can read the memory of a worker while a job runs.

An external key store (Vault, the key service of a cloud, the key ring
of the system) would close part of the first gap. MAST is ready for one
(`[credentials] key_provider`), but only `file` exists so far.

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
