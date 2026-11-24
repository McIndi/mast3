# PQC on DataPower: the raw results of the container tests

Tests of 2026-11-21 on `icr.io/cpopen/datapower/datapower-limited`
images, 10.5.0.23 to 11.0.0.3, probed with Python 3.15.2 (OpenSSL
3.5.9) offering one group at a time.

* `result-<level>.txt`: DataPower as the TLS server. An
`SSLServerProfile` used by `web-mgmt`, with each `KEMAlgs` variant
(set through SOMA set-config), with TLS 1.2 + 1.3 and with TLS 1.3
only. `stored:` is what get-config returned after the change.
* `outbound-<level>.txt`: DataPower as the TLS client. An
`SSLClientProfile` behind an XML manager's user agent, `FetchFile`
against a Python server that offers `X25519MLKEM768`, `X25519` or the
OpenSSL default.

`test_mast_datapower_crypto_pqc` checks
`mast.datapower.crypto.pqc.effective_groups` against every row.
