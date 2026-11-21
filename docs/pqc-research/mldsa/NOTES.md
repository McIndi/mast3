# ML-DSA certificate test, DataPower 11.0.0.3 (2026-11-21)

Key and certificate: `openssl req -x509 -newkey ML-DSA-65 -nodes -keyout mldsa.key -out mldsa.crt -days 30 -subj /CN=mldsa-test` (OpenSSL 4.0.3).

- **Objects:** `CryptoCertificate` and `CryptoKey` with the ML-DSA-65 files are accepted, and are `up` in `ObjectStatus`. So is a `CryptoIdentCred` built from them.
- **Certificate details:** `do-view-certificate-details` returns `SignatureAlgorithm ML-DSA-65`, `SubjectPublicKeyAlgorithm ML-DSA-65` and `SubjectPublicKeyBitLength 15616`. The cheap method gives the key algorithm and size.
- **TLS:** an `SSLServerProfile` with that idcred is `up`, but every TLS 1.3 handshake fails. The log says `TLS library error: error:0A000076:SSL routines::no suitable signature algorithm`.
- **`SigAlgs`:** accepts `ed25519` and `rsa_pss_rsae_sha256`, but refuses `mldsa65`, `MLDSA65`, `ML-DSA-65`, `mldsa44` and `mldsa87` with a SOAP fault.
- **Conclusion:** on 11.0.0.3, ML-DSA certificates can be stored and inspected, but not used for TLS authentication. Post-quantum signatures exist only as XSLT functions (`dp:sign-pqc` and `dp:verify-pqc`: ML-DSA-44/65/87 and SLH-DSA, from the 11.0.0 docs).
