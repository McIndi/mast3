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

MAST builds a self-contained distribution with its own Python runtime. The build machine needs Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). The machine where MAST runs needs nothing: the zip has its own Python and every library, so it also works on a jump server with no internet access.

```shell
./mast-setup                    # Linux and macOS
python mast-setup               # Windows
```

The installer is written to `dist/`. To review its contents, look in `build/assemble`. `./mast-setup --help` lists the options.

The dependencies come from `uv.lock`, and the Python runtime is the python-build-standalone release in `[tool.mast-setup]` of `pyproject.toml`, so two builds of the same commit ship the same files. Downloads are kept in `build-cache/`. The Linux installer runs on glibc 2.28 and newer (RHEL 8, Debian 10, Ubuntu 20.04).

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

**MAST runs with its home as the current directory.** The launchers set `MAST_HOME` and change to that folder before they start Python, and some paths in the configuration (`soma_spec_file`, `out_dir`, the web folders) are relative to it. Without `MAST_HOME`, MAST looks for its home in the current directory and its parents (`mast/home.py`).

## Running the tests

The tests run with [pytest](https://docs.pytest.org) from a source checkout. Install [uv](https://docs.astral.sh/uv/) first. `uv sync` makes `.venv` with the versions in `uv.lock`, on every platform.

```shell
uv sync
uv run pytest mast/testsuite/unit mast/testsuite/regression
```

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
| `MAST_TEST_CONTAINER_RUNTIME` | `podman` or `docker`. The default is the first one found. |
| `MAST_TEST_HOME` | A MAST home: a development home (`.dev/mast_home`, made by `./mast-setup --development-mode`) or an unzipped installer. The command line tests then run the launchers of a copy of that home (`mast-system` and so on), as users do. Without it they run `python -m` with the interpreter of the tests. |

A container cannot test static hosts, secondary addresses, static routes or firmware upgrades, because DataPower locks that configuration when it runs in a container.

## Commercial support

McIndi offers commercial support, commercial licensing, and DataPower engineering, including certificate-lifetime and post-quantum readiness engagements. See [mcindi.com](https://www.mcindi.com/) or email sales@mcindi.com.

## Links

- [Issue tracker](https://github.com/McIndi/mast3/issues)
- [Pull requests](https://github.com/McIndi/mast3/pulls)
- [MAST on mcindi.com](https://www.mcindi.com/software/mast/)

## License

GPLv3. See [LICENSE](LICENSE).
