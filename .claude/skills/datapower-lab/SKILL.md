---
name: "datapower-lab"
description: "Use when you need DataPower appliances to test MAST 4.0 against: build, start, stop or destroy the two-appliance lab of free developers-limited containers, or run the crypto inventory and probe against it."
---

# DataPower test lab (MAST 4.0)

The lab is two of IBM's free developers-limited DataPower containers, registered in a development home of this checkout. It is for development and testing only. Never point lab work at a client appliance or put client data in the lab.

Everything is in `scripts/lab.sh` next to this file. Run it, do not retype its steps. It is the one place the appliances and their credentials are set; change them there.

## Before you start

- Needs podman, bash, curl and uv, and about 4 GB RAM per appliance. A cloud session container usually cannot run nested containers. If `podman info` fails, say so and stop.
- `MAST_REPO` defaults to this checkout. `MAST_LAB_HOME` (the lab's MAST home) defaults to `~/mast-lab`, outside the repo.

## Use it

1. `.claude/skills/datapower-lab/scripts/lab.sh build`: makes the development home (`mast-setup --development-mode`, so it runs whatever is checked out), creates and starts `mast-lab-dp1` (11.0.0.3) and `mast-lab-dp2` (10.6.4.0) on loopback-only ports, waits until each XML management port answers (up to 5 minutes), then:
   - registers them (`mast targets add -k datapower`) and pins their certificates (`mast targets pin --yes`),
   - makes the fleet `lab` (`mast fleets add -k datapower`),
   - stores two credentials: `lab-admin` (may change the appliances) and `lab-read` (a DataPower user that may only read), and tests `lab-read`.
2. `lab.sh up` starts the appliances and mastd (web UI on http://127.0.0.1:5088). Add a web user with `mast users`: it reads the password interactively, so a person runs it.
3. `lab.sh status`, `lab.sh down` (nothing is lost), `lab.sh destroy` (removes the containers and the home).

If an appliance does not answer, `lab.sh` stops and names the `podman logs` command to run.

## Crypto inventory and probe

From the lab home:

- `./mast crypto inventory -a lab --credential lab-read`
- `./mast crypto probe -a lab` (needs no credential)

The appliances start with only their default crypto objects. There is no 4.0 script yet that loads test crypto objects (weak, expiring and expired certificates, legacy TLS profiles). If a test needs them, write it as a step after `build` in `lab.sh`, not as a separate script.

## Ports

| Appliance | Image tag | SOMA | WebGUI | SSH |
|---|---|---|---|---|
| dp1 | 11.0.0.3 (11.0 LTS) | 15550 | 19090 | 12222 |
| dp2 | 10.6.4.0 (10.6 LTS) | 25550 | 29090 | 22222 |

Other tags worth testing when a change touches the management API: 10.6.6.0 (first release with ML-DSA) and 10.5.0.23 (out of service, still in older estates). Swap one into the `APPLIANCES` array; MAST should give the same results on each.

## Gotchas

- `mast users` reads the password with getpass, so it cannot be piped. MAST refuses a password under 12 characters or equal to the user name.
- CPU status is empty until Statistics is enabled in the default domain.
- Status polling only happens once an admin sets up the `status-poll` schedule with a stored credential.

Put new gotchas in this file.
