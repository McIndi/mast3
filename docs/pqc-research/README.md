# PQC tests on DataPower containers

The scripts and raw results behind section 6 of
`docs/assessment-of-mast-crypto.md`. They were run on 2026-11-21 with
podman and the `icr.io/cpopen/datapower/datapower-limited` images.

These are research scripts, not tests of MAST. They don't use the
MAST code. They talk to the XML management interface with `curl`.

## Requirements

- **podman.**
- **The IBM licence:** the scripts set `DATAPOWER_ACCEPT_LICENSE=true`
  on the container. Run them only if you accept the IBM DataPower
  licence.
- **Python 3.15** for `ssl.SSLContext.set_groups()` and
  `SSLSocket.group()`, with OpenSSL 3.5 or later for ML-KEM. The
  scripts use `.dev/mast_home/python/3.15.2/bin/python3` of the
  repository (`./mast-setup --development-mode`). Set `PY` to use
  another interpreter.
- **Login:** the default credentials of the limited image,
  `admin`/`admin`.

## Run

1. Make a throw-away key and certificate in this directory. They are
   not in the repository.

       openssl req -x509 -newkey rsa:2048 -nodes -keyout pqc.key -out pqc.crt -days 30 -subj /CN=pqc-test

2. Test one or more firmware levels:

       bash run_all.sh 10.6.4.0 11.0.0.3

   For each tag, the script takes these steps:
   1. It starts the container `pqc-<tag>`.
   2. **Inbound** (`matrix.sh`): `web-mgmt` uses the
      `SSLServerProfile` `pqc-server`. Each `KEMAlgs` value is set
      with `set-config`, first with TLS 1.2+1.3 on and then with
      TLS 1.3 only. `probe.py` then connects with one group at a
      time. The output is `result-<tag>.txt`. This step is skipped
      when the result file is already there.
   3. **Outbound** (`outbound.sh`): DataPower fetches a URL with
      `FetchFile` from `server.py` on the host
      (`host.containers.internal:18443`). The fetch goes through an
      `XMLManager` and an `HTTPUserAgent` that use the
      `SSLClientProfile` `pqc-client`. The server offers
      `X25519MLKEM768` only, `X25519` only, or the OpenSSL default,
      and it logs the group of each connection. The output is
      `outbound-<tag>.txt`.
   4. It removes the container.

   One firmware level takes about five minutes. Run one `run_all.sh`
   at a time: the outbound test uses a fixed port.

3. Summaries:

       python3 compact.py     # one line per firmware
       python3 summarize.py   # every row

## The files

| File | Purpose |
|---|---|
| `start_dp.sh` | start a container with `web-mgmt` and `xml-mgmt` enabled |
| `somareq.sh`, `soma.sh` | send a SOMA request |
| `setkem.sh` | set `KEMAlgs` (and the protocols from `PROTOS`) on `pqc-server` |
| `probe.py` | the TLS client of the inbound test |
| `server.py` | the TLS server of the outbound test |
| `matrix.sh`, `outbound.sh`, `run_all.sh` | the tests |
| `summarize.py`, `compact.py` | summaries |
| `result-*.txt`, `outbound-*.txt` | the raw results |
| `mldsa/NOTES.md` | the ML-DSA certificate test (11.0.0.3, by hand) |

The 11.0.0.3 inbound results were partly made by hand before
`matrix.sh` existed. `result-11.0.0.3.txt` is a later run of the
script.
