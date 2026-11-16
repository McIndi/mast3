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

MAST builds a self-contained distribution with its own Python runtime.

```shell
python -m venv .venv
.venv\Scripts\activate          # Windows (use: source .venv/bin/activate on Linux/macOS)
python -m pip install requests
python build.py
```

The installer is written to `dist/`. To review its contents, look in `build/assemble`.

You do not have to build a release yourself. When a tag that starts with `v` is pushed (for example `v3.14.10`), GitHub Actions runs the tests, builds the installers for Windows, Linux and macOS, and publishes them as a GitHub release. The tag must match the version in `pyproject.toml`.

### 2. Install

Unzip the archive from `dist/` wherever you want MAST to live, then run `mast` from that folder. No separate Python install is needed.

### 3. Describe your appliances

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

```shell
set MAST_CREDS=username:password
mast-ssh -a lab -c %MAST_CREDS% -d default
```

`-a` takes an environment name or a list of appliances, so the same command runs against one box or the whole fleet.

## The job queue

Each command that you run is recorded as a job in a database, with its progress and its result. Jobs for one appliance run one at a time, in the order in which they were submitted. Commands that only read (`list-users`, `get-status` and so on) can run at the same time as each other.

You do not have to set anything up. If no worker is running, the command does its own job and you see the same output as before.

To have jobs done by a worker process, start one or more workers. `mastd` also hosts one.

```shell
mast-worker             # run jobs until Ctrl-C
mast-worker -c 4        # up to four jobs at the same time
```

With a worker running, a command can return at once and leave the job to the worker:

```shell
mast-backups normal-backup -a lab -c admin:password -D default --detach
mast-jobs list          # the newest jobs
mast-jobs show -j 3f2a  # one job: progress and result (the start of the id is enough)
mast-jobs follow -j 3f2a
mast-jobs cancel -j 3f2a
mast-jobs workers
```

The web UI works the same way. A form that you submit becomes a job, the page shows the progress of the job while it runs, and then its output. Each running job has a Cancel link.

Most commands do not stop when one appliance refuses an action: they continue with the other appliances and show what happened on each one. The status of the job tells you how that went.

| Status | Meaning | Exit status of the command |
|---|---|---|
| `succeeded` | Every action worked. | 0 |
| `partial` | Some actions failed and some worked, for example on one of three appliances. | 1 |
| `failed` | Every action failed, or the command stopped with an error. | 1 |
| `cancelled` | Somebody cancelled the job. | 130 if you pressed Ctrl-C, otherwise 1 |
| `lost` | The worker stopped or lost contact with the database while the job ran. | 1 |

**Change from earlier versions:** a command now exits with status 1 when an appliance refuses an action. Before, the exit status was 0 unless the command stopped with an error. Check the scripts that call MAST and test `$?`. Commands whose result is a download or a table of their own (`fetch-logs`, `check-connectivity` and others) do not report a failure this way yet, and neither do the steps of `git-deploy`.

Ctrl-C in a command cancels its job. A job that is running stops at its next step, and what it did before that is not undone.

If a worker cannot reach the database for longer than its lease (60 seconds by default), its job is marked `lost` and the next job for the same appliances can start. The worker stops a lost job at its next step. A lost job is never run again, because nobody knows how much of it was done. Look at the appliance before you run the command again.

Only one `git-deploy` runs at a time, because every deployment uses the same directory of checkouts.

The database is SQLite in `var/jobs/` by default. To use PostgreSQL, set `url` in `etc/local/jobs.conf` (see `etc/default/jobs.conf`). Workers and commands must share one `MAST_HOME`, because jobs read and write files there.

The credentials of a waiting job, and the other secret parameters such as a new password, are stored encrypted with the key in `etc/local/jobs.key`. They are removed when the job ends. The values are also removed from the progress, the result and the request history of the job before these are stored. The `xor` utility is not a job, and nothing about it is stored.

## Running the tests

The tests run with [pytest](https://docs.pytest.org) from a source checkout. This example is for Linux; on Windows or macOS, use the requirements file for that platform.

```shell
python -m venv .venv
.venv/bin/pip install -r linux-requirements.txt pytest
.venv/bin/pip install .
.venv/bin/python -m pytest mast/testsuite/unit mast/testsuite/regression
```

### Integration tests

The integration tests start a real IBM DataPower Gateway in a container, with podman or docker, and run MAST against it. IBM publishes "limited" images for non-production use. The image does not start until you accept the IBM license, so you must set a variable to run these tests:

```shell
export MAST_TEST_DATAPOWER_ACCEPT_LICENSE=true
.venv/bin/python -m pytest mast/testsuite/integration
```

Without that variable, or without a container runtime, the integration tests are skipped.

| Variable | Effect |
|---|---|
| `MAST_TEST_DATAPOWER_ACCEPT_LICENSE` | Must be `true` for the tests to run. |
| `MAST_TEST_DATAPOWER_IMAGE` | The image to test against. The default is `icr.io/cpopen/datapower/datapower-limited:11.0.0.3`. |
| `MAST_TEST_DATAPOWER_KEEP` | Set to `true` to keep the container (`mast-test-keep`) and use it again in the next run. |
| `MAST_TEST_CONTAINER_RUNTIME` | `podman` or `docker`. The default is the first one found. |

A container cannot test static hosts, secondary addresses, static routes or firmware upgrades, because DataPower locks that configuration when it runs in a container.

## Commercial support

McIndi offers commercial support, commercial licensing, and DataPower engineering, including certificate-lifetime and post-quantum readiness engagements. See [mcindi.com](https://www.mcindi.com/) or email sales@mcindi.com.

## Links

- [Issue tracker](https://github.com/McIndi/mast3/issues)
- [Pull requests](https://github.com/McIndi/mast3/pulls)
- [MAST on mcindi.com](https://www.mcindi.com/software/mast/)

## License

GPLv3. See [LICENSE](LICENSE).
