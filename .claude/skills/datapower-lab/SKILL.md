---
name: "datapower-lab"
description: "Use when you need DataPower appliances to test MAST against: stand up, rebuild or tear down the lab of free developers-limited containers, add test crypto objects, or run the inventory and probe end to end."
---

# DataPower test lab

The lab is IBM's free developers-limited DataPower container: `icr.io/cpopen/datapower/datapower-limited:<tag>`. It is for development and testing only. Never point MAST lab work at a client appliance or put client data in the lab.

The lab is disposable: rebuild it from this file, the scripts in `tools/lab/` and a MAST build. It needs a container host (podman or Docker, about 4 GB RAM per appliance). A cloud session container usually cannot run nested containers. If `podman info` and `docker info` both fail, say so and stop.

## Versions

| Tag | Line | Why test it |
|---|---|---|
| 11.0.0.3 | 11.0 LTS | Default target |
| 10.6.0.11 | 10.6 LTS | Most likely in production estates |
| 10.6.6.0 | 10.6 CD (out of service) | First release with ML-DSA |
| 10.5.0.23 | 10.5 LTS (out of service) | Older estates that need an upgrade |

MAST must give the same results on every row. A difference in the management API between versions is a test case, not a surprise.

## MAST build

Last full lab pass: release v3.15.2.1. Build this checkout (`./mast-setup`, or `python mast-setup` on Windows) or unzip a release. MAST_HOME is the unzipped folder. After a full pass with a newer release, update the tag above.

## Two-appliance lab

1. Create each container with loopback-only ports, copy the startup config in, then start it:
   - `podman create --name mast-dp1 -e DATAPOWER_ACCEPT_LICENSE=true -p 127.0.0.1:5551:5550 -p 127.0.0.1:9091:9090 -p 127.0.0.1:2201:2222 -p 127.0.0.1:8443-8444:8443-8444 icr.io/cpopen/datapower/datapower-limited:11.0.0.3`
   - Write `STARTUP_CONFIG` from `mast/testsuite/integration/container.py` to `auto-startup.cfg`, then `podman cp auto-startup.cfg mast-dp1:/opt/ibm/datapower/drouter/config/` and `podman start mast-dp1`.
   - dp2: tag 10.6.0.11, name `mast-dp2`, ports 5552, 9092, 2202 and 8453-8454.
2. Wait for the XML management port, not a fixed time (it usually takes two to four minutes): repeat `curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:5551/service/mgmt/current` until it prints something other than `000`. After 10 minutes, stop and show `podman logs --tail 50 mast-dp1`.
3. The containers use IBM's published default login. Never commit a changed password.
4. Register the appliances: `mast-appliances add` (no `--tls-mode`), then `mast-appliances pin -n <name> --both -y`, then `mast-appliances update -n <name> --tls-mode pin` (pin mode is refused before a pin exists). Group them with `mast-appliances env-add -n lab -m dp1 -m dp2`.
5. mast-web needs a certificate first: run `mast contrib/gen-cert.py` in MAST_HOME.

## Crypto test data

Copy `tools/lab/` to `MAST_HOME/lab-scripts/`. From MAST_HOME, for each appliance:

- `mast lab-scripts/labcrypto.py <xml-port> <tls-port>` adds CA, server, EC, expiring, expired and 1024-bit SHA-1 certs, keys, idcreds, a valcred, TLS profiles and a legacy SSL proxy profile. The `labsecret` password in it is test data on purpose.
- `mast lab-scripts/labfw.py <xml-port> <tls-port>` replaces the HTTPS handlers with two XML firewalls, because a handler needs a service to listen.
- Ports: dp1 `5551 8443`, dp2 `5552 8453`.

Then run `mast-crypto inventory -a lab -c <user>:<password>` and `mast-crypto probe -a lab`. Expect about 16 to 18 findings per appliance. The legacy ports fail the probe, because its client does not offer the old CBC suites.

## Gotchas

- The SHA-1 cert comes from OpenSSL, because the bundled `cryptography` refuses SHA-1.
- SOMA: a server or client profile needs `<Ciphers>`, and the handler's profile element is `<SSLServer>`.
- `mast-users add` reads the password with getpass, so it cannot be piped; a person runs it. MAST refuses a password under 12 characters or equal to the user name.
- CPU status is empty until Statistics is enabled in the default domain.
- `--poll yes` on `mast-appliances update` polls only after an admin sets up the `status-poll` schedule with a stored credential.
- On Windows, older launchers break when `NoDefaultCurrentDirectoryInExePath=1` is set; current ones work with it.

## Tear down

`podman rm -f mast-dp1 mast-dp2`. Nothing needs saving. Put new gotchas in this file.
