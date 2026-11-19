# MAST for IBM DataPower

McIndi's Automated Solutions Tool (MAST) manages fleets of IBM DataPower appliances from one place: backups, deployments, configuration promotion, user and network changes, and certificate audits, across as many appliances and environments as you have.

MAST has run in large financial services and healthcare DataPower estates for over a decade, including a commercial license held by a Big Four firm from 2015 to 2026. It is open source under the GPLv3, with commercial support and licensing available from [McIndi Solutions](https://www.mcindi.com/software/mast/).

> **Coming next: crypto inventory.** A `mast crypto inventory` command that maps the cryptography across your DataPower domains, not only the certificates. It lists every certificate and key (expiry, issuer, key type, key size), the identification and validation credentials that group them, and the TLS profiles that use them, with their protocol versions and cipher suites. Legacy crypto profiles and unreferenced files are flagged. The result shows what expires, where it is used and what each endpoint negotiates, ready for public TLS certificates dropping to 100 days on March 15, 2027 and for post-quantum readiness work. Follow this repo for the release.

## What MAST does

**Operations**
- Backups and checkpoints
- Exports, imports, and deployments
- Adding and removing domains
- Configuration change tracking

**Administration**
- Adding and removing users, forcing password changes
- Network changes: secondary IP addresses, host aliases, DNS static hosts, routing table
- TCP connection tests

**Security**
- Certificate audits across domains and appliances

**Tooling**
- A multi-appliance SSH client (`mast-ssh`)
- Automated PMR (IBM support case) information gathering
- A web UI

## Supported DataPower versions

MAST ships a SOMA spec file for each of these DataPower versions. Each one was tested against a running appliance in a local lab (list users, read the file store, make a normal backup).

| Version | Release type | Spec file |
|---|---|---|
| 10.5.0.23 | LTS | `etc/v10.5.0.23-C.xml` |
| 10.6.0.11 | LTS | `etc/v10.6.0.11-C.xml` |
| 10.6.6.0 | CD | `etc/v10.6.6.0-C.xml` |
| 11.0.0.3 | LTS | `etc/v11.0.0.3-C.xml` |

The default is `etc/v11.0.0.3-C.xml`. It worked with all four versions in the lab. To use a different file for one appliance, set `soma_spec_file` in that appliance's section of `etc/local/appliances.conf`.

## Quick start

### 1. Build the installer

MAST builds a self-contained distribution with its own Python runtime. The build machine needs Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). The machine where MAST runs needs no Python and no internet access: the zip has its own Python and every library, so it also works on a jump server. Only `git-deploy` needs a program from the machine: `git` must be on the `PATH`.

```shell
./mast-setup                    # Linux and macOS
python mast-setup               # Windows
```

The installer is written to `dist/`. To review its contents, look in `build/assemble`. `./mast-setup --help` lists the options.

The dependencies come from `uv.lock`, and the Python runtime is the python-build-standalone release in `[tool.mast-setup]` of `pyproject.toml`, so two builds of the same commit ship the same files. Downloads are kept in `build-cache/`. The Linux installer runs on glibc 2.28 and newer (RHEL 8, Debian 10, Ubuntu 20.04).

You do not have to build a release yourself. When a tag that starts with `v` is pushed (for example `v3.15.2`), GitHub Actions runs the tests, builds the installers for Windows, Linux and macOS, and publishes them as a GitHub release. The tag must match the version in `pyproject.toml`.

Every package in `uv.lock` is checked for known vulnerabilities with [pip-audit](https://pypi.org/project/pip-audit/) on each pull request, on each push to `main`, each week, and before a release (`.github/workflows/audit.yml`). A known vulnerability fails the check and stops the release. Each release has the report of the check (`MAST-<version>_pip-audit.json`) and a software bill of materials of those packages in CycloneDX format (`MAST-<version>_sbom.cdx.json`), next to the installers and in `SHA256SUMS`. The packages of every platform are in both files, so `pywin32` is listed although only the Windows installer has it.

### 2. Install

Unzip the archive from `dist/` wherever you want MAST to live. That folder is `MAST_HOME`. Run the launchers in it (`mast-system`, `mast-backups` and so on, or `mast-system.bat` on Windows). No separate Python install is needed.

The console scripts in `MAST_HOME/python/<version>/bin` (`Scripts` on Windows) also work, from any directory and with no `MAST_HOME` set, for example through a link in `/usr/local/bin`. On macOS they need macOS 13 or newer.

### 3. Describe your appliances

Put these files in `MAST_HOME/etc/local/`. `etc/default/` has the same files with every setting explained.

`hosts.conf`:

```ini
[hosts]
dp1: 192.168.0.10
dp2: 192.168.0.20
```

`environments.conf`:

```ini
[lab]
appliances: dp1 dp2
```

### 4. Run a command against the whole environment

On Linux and macOS:

```shell
MAST_CREDS=username:password
./mast-ssh -a lab -c "$MAST_CREDS" -d default
```

On Windows:

```bat
set MAST_CREDS=username:password
mast-ssh -a lab -c %MAST_CREDS% -d default
```

`-a` takes an environment name or a list of appliances, so the same command runs against one box or the whole fleet.

## The job queue

Each command of `mast-accounts`, `mast-backups`, `mast-crypto`, `mast-deployment`, `mast-developer`, `mast-network` and `mast-system`, and each form that you submit in the web UI, is recorded as a job in a database, with its progress and its result. `mast-ssh`, the scripts in `contrib` and `mast-system xor` are not jobs. Jobs for one appliance run one at a time, in the order in which they were submitted. Commands that only read (`list-users`, `get-status` and so on) can run at the same time as each other.

You do not have to set anything up. If no worker is running, the command does its own job and you see the same output as before.

To have jobs done by a worker process, start one or more workers. `mastd` also hosts one, unless `mastd = false` is in the `[worker]` section of `etc/local/jobs.conf`.

```shell
mast-worker             # run jobs until Ctrl-C
mast-worker -c 4        # up to four jobs at the same time
mast-worker -d          # stop when no job waits in the queue
```

To stop a worker, press Ctrl-C or send it SIGTERM. It takes no more jobs and stops when its running jobs end. A second Ctrl-C or SIGTERM stops it at once: its running jobs become `lost`, and its exit status is 1.

With a worker running, a command waits for the worker to do its job and shows the progress as before. With `--local` the command does its own job, also when a worker is running. With `--detach` it returns at once and leaves the job to the worker:

```shell
mast-backups normal-backup -a lab -c admin:password -D default --detach
mast-jobs list          # the newest jobs
mast-jobs show -j 3f2a  # one job: progress and result (the start of the id is enough)
mast-jobs follow -j 3f2a
mast-jobs cancel -j 3f2a
mast-jobs workers
```

The web UI works the same way. A form that you submit becomes a job, the page shows the progress of the job while it runs, and then its output. Each running job has a Cancel link. A worker does the job if one is running. If none is, the web server does it, also when the last worker stops before it takes the job. You can close the browser: the job goes on. If the web server (`mast-web` or `mastd`) stops before the job starts, the job is cancelled.

Most commands do not stop when one appliance refuses an action: they continue with the other appliances and show what happened on each one. The status of the job tells you how that went.

| Status | Meaning | Exit status of the command |
|---|---|---|
| `succeeded` | Every action worked. | 0 |
| `partial` | Some actions failed and some worked, for example on one of three appliances. | 1 |
| `failed` | Every action failed, or the command stopped with an error. | 1 |
| `cancelled` | Somebody cancelled the job. | 130 if you pressed Ctrl-C, otherwise 1 |
| `lost` | The worker stopped or lost contact with the database while the job ran. | 1 |

**Change from earlier versions:** a command now exits with status 1 when an appliance refuses an action. Before, the exit status was 0 unless the command stopped with an error. Check the scripts that call MAST and test `$?`. Commands whose result is a download or a table of their own (`fetch-logs`, `check-connectivity` and others) do not report a failure this way yet, and neither do the steps of `git-deploy`.

Ctrl-C in a command cancels its job. A job that is running stops at its next step, before its next request to an appliance, and what it did before that is not undone. A job that runs in the same program stops at once after Ctrl-C. A job that is cancelled with `mast-jobs cancel` or the Cancel link of the web GUI stops within about a second (`cancel_poll_seconds` in the `[worker]` section of `jobs.conf`). A request to an appliance that has started is completed first.

If a worker cannot reach the database for longer than its lease (`lease_seconds`, 60 by default), its job is marked `lost` and the next job for the same appliances can start. The worker stops a lost job at its next step. A lost job is never run again, because nobody knows how much of it was done. Look at the appliance before you run the command again.

Only one `git-deploy` runs at a time, because every deployment uses the same directory of checkouts.

The database is SQLite in `var/jobs/` by default. To use PostgreSQL, set `url` in `etc/local/jobs.conf`. That file also sets the lease, the number of jobs that a worker runs at a time (`concurrency`) and how often an idle worker looks for a job (`poll_seconds`); `etc/default/jobs.conf` explains each setting. Workers and commands must share one `MAST_HOME`, because jobs read and write files there.

The credentials of a waiting job, and the other secret parameters such as a new password, are stored encrypted with the key in `etc/local/jobs.key`, which MAST makes the first time it needs it. Every process that uses the database must have the same key, so keep it with the database when you back up or move MAST. They are removed when the job ends. The values are also removed from the progress, the result and the request history of the job before these are stored. The `xor` utility is not a job, and nothing about it is stored.

## Development

From a checkout there are two environments. They are separate, and you can use both.

**`uv sync` for tests and editors.** It makes `.venv` with the versions in `uv.lock`. The tests make their own MAST home (see `conftest.py`).

```shell
uv sync
uv run pytest mast/testsuite/unit mast/testsuite/regression tools
```

**`./mast-setup --development-mode` to run MAST as users do.** It makes `.dev/mast_home`: the layout, launchers and Python runtime of the installer, with the code of the checkout.

```shell
./mast-setup --development-mode
.dev/mast_home/mast-system list-domains -a lab -c ...
```

- MAST is installed editable, so a change to the code is live.
- The files of `files/mast_home` that git tracks (`etc/default`, the SOMA specs, the web templates and static files) are links to the checkout, so a change to them is live too.
- What MAST writes (`etc/local`, `var/log`, `tmp` and so on) stays in `.dev/mast_home` and never shows in `git status`. Put your `hosts.conf` and other local configuration in `.dev/mast_home/etc/local`.
- Run the command again after you pull: it links new files, removes stale links, and updates the libraries when `uv.lock` changed. `--recreate` starts from an empty home.
- On Windows, links need developer mode. Without it the files are copied, and you run the command again after you change them.

From a checkout, run MAST through `.dev/mast_home` made by `./mast-setup --development-mode`. Do not make another MAST_HOME by hand, except in tests (`conftest.py` does that).

**MAST runs with its home as the current directory.** The launchers set `MAST_HOME` and change to that folder before they start Python, and some paths in the configuration (`soma_spec_file`, `out_dir`, the web folders) are relative to it. Without `MAST_HOME`, MAST looks for its home in the current directory and its parents, and then in the folders that contain its Python interpreter (`mast/home.py`). The interpreter of an installer or of a development home is in `MAST_HOME/python`, so its console scripts work from any directory.

## Running the tests

The tests run with [pytest](https://docs.pytest.org) from a source checkout. Install [uv](https://docs.astral.sh/uv/) first. `uv sync` makes `.venv` with the versions in `uv.lock`, on every platform.

```shell
uv sync
uv run pytest mast/testsuite/unit mast/testsuite/regression tools
```

MAST supports Python 3.13 and newer. CI runs the unit tests on 3.13, 3.14 and 3.15, and the other tests on 3.15, the Python of the installer. To test on another version, give it to uv, for example `uv run --python 3.13 pytest mast/testsuite/unit`.

The job queue tests run against SQLite. To run them against PostgreSQL too, set `MAST_TEST_JOBS_DB_URL` to an empty database that the tests may erase, for example `postgresql+psycopg://mast:mast@localhost/mast_test`. CI does this.

### Integration tests

The integration tests start a real IBM DataPower Gateway in a container, with podman or docker, and run MAST against it. IBM publishes "limited" images for non-production use. The image does not start until you accept the IBM license, so you must set a variable to run these tests:

```shell
export MAST_TEST_DATAPOWER_ACCEPT_LICENSE=true
uv run pytest mast/testsuite/integration
```

Without that variable, or without a container runtime, the integration tests are skipped.

| Variable | Effect |
|---|---|
| `MAST_TEST_DATAPOWER_ACCEPT_LICENSE` | Must be `true` for the tests to run. |
| `MAST_TEST_DATAPOWER_IMAGE` | The image to test against. The default is `icr.io/cpopen/datapower/datapower-limited:11.0.0.3`. |
| `MAST_TEST_DATAPOWER_KEEP` | Set to `true` to keep the container (`mast-test-keep`) and use it again in the next run. |
| `MAST_TEST_DATAPOWER_TIMEOUT` | Seconds to wait for the gateway to start. The default is 300. |
| `MAST_TEST_CONTAINER_RUNTIME` | `podman` or `docker`. The default is the first one found. |
| `MAST_TEST_HOME` | A MAST home: a development home (`.dev/mast_home`, made by `./mast-setup --development-mode`) or an unzipped installer. The command line tests then run the launchers of a copy of that home (`mast-system` and so on), as users do. Without it they run `python -m` with the interpreter of the tests, and the tests of the installed product are skipped. |

### Tests of the installed product

`mast/testsuite/integration/test_installed.py` runs a MAST home as users do: through its launchers, from another directory, in a folder whose name has a space. It checks the launchers, the exit statuses, cancelled jobs (Ctrl-C, `mast-jobs cancel`, a job that waits) and workers that stop (SIGTERM once and twice, `--drain`), and that the web GUI answers. It needs no DataPower: the appliances are local servers that answer like SOMA after a delay. It runs on Linux and macOS, and only when `MAST_TEST_HOME` is set:

```shell
./mast-setup
mkdir /tmp/mast && unzip -q dist/*.zip -d /tmp/mast
MAST_TEST_HOME=/tmp/mast uv run pytest mast/testsuite/integration/test_installed.py
```

CI runs these tests against the installer on each pull request, and on Linux and macOS for each release. Use `MAST_TEST_HOME=.dev/mast_home` to test the code of the checkout.

A container cannot test static hosts, secondary addresses, static routes or firmware upgrades, because DataPower locks that configuration when it runs in a container.

## Commercial support

McIndi offers commercial support, commercial licensing, and DataPower engineering, including certificate-lifetime and post-quantum readiness engagements. See [mcindi.com](https://www.mcindi.com/) or email sales@mcindi.com.

## Links

- [Issue tracker](https://github.com/McIndi/mast3/issues)
- [Pull requests](https://github.com/McIndi/mast3/pulls)
- [MAST on mcindi.com](https://www.mcindi.com/software/mast/)

## License

GPLv3. See [LICENSE](LICENSE).
