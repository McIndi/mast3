# Assessment of MAST crypto

An assessment of everything in this repository that touches DataPower
crypto (certificates, keys, credentials, TLS profiles, password aliases,
secure backup), plus the crypto MAST uses for itself. Sections 1 to 5
were compiled by reading the code; nothing there was run against an
appliance. Section 6 is different: it records tests of post-quantum
cryptography (PQC) on DataPower containers.

This document is not the crypto inventory feature, and it does not list
the crypto objects on an appliance. Several MAST scripts do that. This
document describes those scripts.

Its purpose is to keep what the scripts know. They hold years of
experience with DataPower crypto objects and with the behaviour of the
management interface. The crypto inventory function will be designed
again from the start, and this knowledge is the input to that design.

Paths are relative to the repository root. `contrib/` means
`files/mast_home/contrib/`, which is installed to `$MAST_HOME/contrib`.

## 1. Summary

| # | Script / command | Location | Touches appliance? | What it does |
|---|---|---|---|---|
| 1 | `mast-crypto cert-audit` | `mast/datapower/crypto/__init__.py:43` | read-only | Spreadsheet of every enabled `CryptoCertificate`: subject, SANs, issuer, validity, expiry |
| 2 | `mast-crypto cert-file-audit` | `mast/datapower/crypto/__init__.py:287` | read-only | Lists every file in `cert:`, `pubcert:`, `sharedcert:` |
| 3 | `mast-crypto export-certs` | `mast/datapower/crypto/__init__.py:414` | writes + deletes a temp file | Downloads every enabled certificate as PEM |
| 4 | `contrib/thorough-cert-audit.py` | contrib | writes + deletes temp files | Joins cert files → certs → keys → credentials → crypto profiles into one sheet |
| 5 | `contrib/cert-recurse.py` | contrib | writes + deletes temp files | For certs expiring within N days, walks up to every object that depends on them |
| 6 | `contrib/CryptoProfile.py` | contrib | **modifies config** | Disables SSLv3 / TLS 1.0 / TLS 1.1 on every `CryptoProfile` |
| 7 | `contrib/find_passwords.py` | contrib | offline | Finds plaintext `<Password>` values in exported config XML |
| 8 | `contrib/gen-cert.py` | contrib | offline | Self-signed cert + key for MAST's own web GUI |
| 9 | `contrib/extract-service.py` | contrib | read-only | Service extract; crypto parts: `crypto-details.csv`, `referenced_cert_files.txt` |
| 10 | `mast-backups secure-backup` | `mast/datapower/backups/backups.py:342` | **writes, may quiesce** | Encrypted whole-appliance backup, downloaded and checksum-verified |
| 11 | `mast-backups restore-secure-backup` | `mast/datapower/backups/backups.py:551` | **destructive** | `SecureRestore` (or validate-only) |
| 12 | `mast-deployment add-password-map-alias` | `mast/datapower/deployment/deployment.py:1095` | **modifies config** | `AddPasswordMap` + save |
| 13 | `mast-deployment del-password-map-alias` | `mast/datapower/deployment/deployment.py:1205` | **modifies config** | `DeletePasswordMap` + save |
| 14 | `mast-deployment git-deploy` | `mast/datapower/deployment/git_deploy.py` | **modifies config** | Uploads cert files and password aliases from a repo layout |
| 15 | `mast-deployment deploy` | `mast/datapower/deployment/deployment.py:446` | **modifies config** | Optional secure backup step before deploying |
| 16 | `mast-system xor` | `mast/datapower/system/system.py:214` | offline | Obfuscates `user:password` strings |

Generic tools that are routinely pointed at crypto objects but contain no
crypto logic of their own: `contrib/getconfig.py` (used by #4, has
`--obfuscate-password`), `contrib/modify-config.py` (its help uses
`SSLOptions/Disable-SSLv2=on` on a `CryptoProfile` as the example),
`contrib/file-sizes.py` (includes the three cert directories),
`contrib/find-refs.py`.

## 2. The management-interface techniques

Everything goes over the XML Management Interface (SOMA), namespace
`http://www.datapower.com/schemas/management`. The library is
`mast/datapower/datapower/DataPower.py`. No script uses the REST
management interface for crypto.

### 2.1 Listing certificate objects

```python
config = appliance.get_config("CryptoCertificate", domain=domain, persisted=False)
certs = config.xml.findall(datapower.CONFIG_XPATH)
certs = [c for c in certs if c.find("mAdminState").text == "enabled"]
```

- `persisted=False` reads the running config, not the saved one.
  `cert-audit` and `CryptoProfile.py` do this; `export-certs` and the
  contrib audits use the default.
- Disabled objects are skipped. The stated reason is fewer requests.
- Useful children: `Filename` (for example `cert:///foo.pem`), `Alias`
  (password alias), `Password` (legacy plaintext), `mAdminState`.
- `CryptoProfile.py` also skips objects with attribute `external="true"`
  (objects that are visible from another domain).

### 2.2 Reading certificate contents — two methods

**Method A: `do-view-certificate-details`** (`DataPower.get_certificate_details`,
`DataPower.py:1913`). Used by `cert-audit` and `extract-service.py`.

```xml
<man:request domain="DOMAIN">
  <man:do-view-certificate-details>
    <man:certificate-object>NAME</man:certificate-object>
  </man:do-view-certificate-details>
</man:request>
```

The response holds `CryptoCertificate/CertificateDetails` with `Subject`,
`Issuer`, `SerialNumber`, `SignatureAlgorithm`, `NotBefore`, `NotAfter`,
and `Extensions/Extension[@name='subjectAltName']/item`. One request, no
temp file, the appliance does the parsing.

This request is not in the bundled templates for firmware 4.0, 5.0, 6.0
or 7.0 (`etc/v4000-xi52.xml` … `v7000-xi52.xml`). It first appears in
`v7500-xi52.xml`. On older firmware only Method B works.

**Method B: `CryptoExport` → `get-file` → `DeleteFile`.** Used by
`export-certs`, `thorough-cert-audit.py`, `cert-recurse.py`.

```python
appliance.CryptoExport(domain=domain, ObjectType="cert",
                       ObjectName=name, OutputFilename=name)
raw = appliance.getfile(domain, "temporary:///{}".format(name))
appliance.DeleteFile(domain=domain, File="temporary:///{}".format(name))
```

- `CryptoExport` writes to `temporary:///` in the object's domain.
- The exported file is XML, not PEM. The base64 DER is in a
  `<certificate>` element with no line breaks.
- To get PEM: wrap that text at 64 characters and add the
  `-----BEGIN CERTIFICATE-----` / `-----END CERTIFICATE-----` lines
  (`insert_newlines` in `mast/datapower/crypto/utils.py`).
- The scripts then parse the PEM locally with pyOpenSSL.
- Always delete the temp file. The scripts treat a failed fetch as
  "skip this cert" and continue.
- Only `ObjectType="cert"` is ever used. No script exports a key.

**Throttling.** `cert-audit` and `export-certs` sleep `--delay` (default
0.5 s) between certificates. The help text says to raise it if you see
intermittent `AuthenticationFailure` responses. Treat that as a known
behaviour of the appliance under rapid requests.

### 2.3 Listing certificate files

```python
filestore = appliance.get_filestore(domain="default", location="cert:")
```

- Locations are `cert:`, `pubcert:`, `sharedcert:`.
- All three are read from the `default` domain. From there `cert:`
  contains one subdirectory per application domain, which is how
  `thorough-cert-audit.py` infers the domain of a file (second path
  segment).
- You get name, size and modified time only. File contents in `cert:`
  cannot be read back; that is why Method B exists.
- Filenames need normalising before you can join them to object config:
  `re.sub(r":[/]*", ":///", filename)`.

### 2.4 The crypto object dependency chain

`thorough-cert-audit.py` records the join keys:

```
file (cert:///x)  ──Filename──▶  CryptoCertificate ─┐
                  ──Filename──▶  CryptoKey          ├─▶ CryptoIdentCred (Key + Certificate)
                  ──Filename──▶  CryptoSSKey        │
                    CryptoCertificate ──────────────┴─▶ CryptoValCred  (Certificate)
                    CryptoIdentCred + CryptoValCred ──▶ CryptoProfile  (IdentCredential + ValCredential)
```

It joins on `(appliance, domain, …)` with left joins from the file list,
so an unreferenced file shows as a row with empty object columns.

It stops at `CryptoProfile`. It does not follow `SSLProxyProfile`,
`SSLServerProfile`, `SSLClientProfile` or `SSLSNIServerProfile`, and it
joins a `CryptoValCred` on a single `Certificate` column.

`cert-recurse.py` handles the general case instead. It pulls the whole
domain config once and, for a given object, finds every object that
references it by class and name, then repeats on each result:

```python
config.xpath(".//*[local-name()='config']/*[.//*/@class='{}' and .//*/text()='{}']"
             .format(klass, name))
```

This is the technique to keep for "what breaks when this cert expires".

### 2.5 Changing TLS protocol versions

`CryptoProfile.py` sends one `modify-config` per profile:

```xml
<man:modify-config>
  <CryptoProfile name="NAME">
    <SSLOptions>
      <Disable-SSLv3>on</Disable-SSLv3>
      <Disable-TLSv1>on</Disable-TLSv1>
      <Disable-TLSv1d1>on</Disable-TLSv1d1>
      <Disable-TLSv1d2>off</Disable-TLSv1d2>
    </SSLOptions>
  </CryptoProfile>
</man:modify-config>
```

- The values are hard-coded. `--exclude` takes glob patterns,
  `--dry-run` prints the request, `--save-config` persists per domain.
- It does not set `Disable-SSLv2` or any TLS 1.3 option.
- It only covers the legacy `CryptoProfile` class, not
  `SSLServerProfile` / `SSLClientProfile`.

### 2.6 Password map aliases

- Add: `appliance.AddPasswordMap(domain=…, AliasName=…, Password=…)`.
- Delete: `appliance.DeletePasswordMap(domain=…, AliasName=…)`.
- Both commands save the domain config afterwards by default.
- A `CryptoCertificate` or `CryptoKey` refers to an alias through its
  `Alias` child. `cert-audit` reports it in the `password-alias` column.
- `git-deploy` reads `password/alias-password.map` (common, then
  `<ENV>/password/alias-password.map`). One alias per line, either
  `name:password` in plain text or the same string xor-encoded
  (`mast-system xor`). It tries xor first and falls back to plain text.

### 2.7 Uploading crypto files (git-deploy)

Repo directory → appliance target (`git_deploy.py:559`, `:800`):

| Repo dir | Uploaded to | Domain used for the upload |
|---|---|---|
| `cert/`, `<ENV>/cert/` | `cert:///file` | the application domain |
| `pubcert/`, `<ENV>/pubcert/` | `pubcert:///file` | `default` |
| `sharedcert/`, `<ENV>/sharedcert/` | `sharedcert:///file` | `default` |

Environment files override common files of the same name. Files with
`EMPTY` in the name are ignored. The upload itself is a plain `set-file`.
User documentation is in `contrib/templates/git-deploy.md`.

### 2.8 Secure backup and restore

`secure-backup` sequence:

1. Optional `QuiesceDP`, then sleep for the quiesce timeout.
2. `CreateDir` of `<destination>/<timestamp>` in `default`.
3. `SecureBackup` with `cert` (the name of a `CryptoCertificate` object
   whose public key encrypts the backup), `destination`, `include-iscsi`,
   `include-raid` (`on`/`off`).
4. Poll until `<destination>/backupmanifest.xml` exists. That file is the
   completion signal.
5. `copy_directory` to `<out_dir>/<hostname>/SecureBackup/<timestamp>`.
6. `verify_local_backup` (`DataPower.py:3749`): compare each file's
   checksum in `backupmanifest.xml` with a local SHA-1 or SHA-512.
7. `RemoveDir` on the appliance only if verification passed.
8. Optional `UnquiesceDP`.

`restore-secure-backup` calls `SecureRestore` with `cred` (the object
that holds the matching private key), `source`, and `validate`
(`on` = check only). The backup must already be on the appliance.

`mast-deployment deploy --do-secure-backup --CryptoCertificate NAME`
runs the same backup before a deployment and fails if the certificate
name is missing.

### 2.9 How action parameters are known

`etc/v*.xml` holds one SOMA request template per firmware level (37
files, 4.0 through 11.0.0.3). `DataPower._add_dynamic_methods`
(`DataPower.py:1521`) turns every `do-action` child into a method, and
`do_action` rejects any keyword that is not a valid child. These files
are therefore the reference for what each crypto action accepts.

## 3. Crypto actions available but not used by any script

From `etc/v11.0.0.3-C.xml`. `CryptoExport`, `CryptoImport`, `Keygen`,
`SecureBackup` and `AddPasswordMap` are in all 37 templates.

| Action | Parameters |
|---|---|
| `Keygen` | `LDAPOrder, C, ST, L, O, OU, OU1–3, CN, KeyType, KeyLength, Digest, ECDSACurve, FileName, Days, Password, PasswordAlias, ExportKey, GenSSCert, ExportSSCert, GenObject, ObjectName, HSM, UsingKey, ConfigFile` |
| `CryptoImport` | `ObjectType, ObjectName, InputFilename, ImportPassword, ImportPasswordAlias` |
| `CryptoExport` (used, cert only) | `ObjectType, ObjectName, OutputFilename, Mechanism` |
| `ConvertKey`, `ConvertCertificate` | `ObjectName, OutputFilename, Format` |
| `ValCredAddCertsFromDir` | (no children in the template) |
| `TestPasswordMap` | `AliasName, Type, FileURL` |
| `NoPasswordMap` | — |
| `CryptoModeSet` | `Mode` |
| `CryptoHwDisable` | `Amount` |
| `DeviceCertificate` | `CN, SSCert` |
| `HSMCloneKWK` | `InputFilename, OutputFilename` |
| `HSMSetRole` | `Role` |
| `DeleteHSMKey` | `KeyHandle, KeyType` |
| `CreateLunaClientCert`, `ImportLunaClientCert`, `LunaHASync`, `LunaHARecover`, `LunaOTTUpdate` | see template |

Related lists that the web GUI offers (`mast/plugin_utils/plugin_functions.py`,
mirrored in `etc/ObjectClasses.txt` and `etc/statusProviders.txt`):

- Object classes: `CryptoCertificate`, `CryptoFWCred`, `CryptoIdentCred`,
  `CryptoKerberosKDC`, `CryptoKerberosKeytab`, `CryptoKey`,
  `CryptoProfile`, `CryptoSSKey`, `CryptoValCred`, `SSLClientProfile`,
  `SSLProxyProfile`, `SSLServerProfile`, `DocumentCryptoMap`,
  `PasswordAlias`, `SecureBackupMode`, `Luna*`.
- Status providers: `CryptoEngineStatus`, `CryptoEngineStatus2`,
  `CryptoHwDisableStatus`, `CryptoModeStatus`, `HSMKeyStatus`,
  `LunaLatency`, `SSLProxyServiceSummary`.

MAST has no workflow for key generation, CSR creation or certificate
issuance. That is by design, not a gap: see section 3.1.

### 3.1 Intended division of labour

Stated by the maintainer, not derived from the code.

- **Issuance happens elsewhere.** DataPower is not a good CA. A vendor
  or an internal CA team issues certificates. `Keygen` is not part of
  the workflow.
- **Crypto configuration belongs in version control.** Deploy the
  `CryptoKey`, `CryptoCertificate`, credential and profile objects with
  `git-deploy` or with `mast-developer export` / `import`
  (`mast/datapower/developer/developer.py:193`, `:312`).
- **Private keys and secrets are handled separately.** DataPower helps
  here: an export contains the configuration of a `CryptoKey` (its
  `Filename`, its password alias) but never the key file itself. The
  object config and the key material therefore travel by different
  routes without extra effort.
- **Key files are uploaded with `set-file`.** It is available on the
  CLI as `mast-deployment set-file` (`deployment.py:67`) and
  `mast-system set-file` (`system.py:2770`), and in the web GUI.

Two places where the tooling will let you break this rule:

1. `git-deploy` uploads everything in `cert/` and `<ENV>/cert/` to
   `cert:///`. Nothing separates certificates from private keys, so a
   key placed there ends up in the repository. Keep only public
   material in those directories.
2. `password/alias-password.map` holds the alias passwords. The xor
   encoding is obfuscation only (section 4), so a map file in the
   repository is a secret in the repository.

## 4. Crypto MAST uses for itself

| Item | Location | Notes |
|---|---|---|
| TLS to appliances | `DataPower.py:1464`, `:4013`; `WSClientLib.py:370` | `--no-check-hostname` turns off hostname checking **and** all certificate verification (`ssl.CERT_NONE`) |
| Web GUI TLS | `mast/datapower/web/gui.py`, `etc/default/server.conf:105-108` | `secure = true`, key `etc/crypto/private/mast.key`, cert `etc/crypto/cert/mast.crt`. Both directories ship empty |
| GUI cert generation | `contrib/gen-cert.py` | RSA 4096, SHA-512, 365 days, serial 0, writes to the two paths above |
| Flask session key | `etc/default/server.conf` | Ships as `INSECURE_SECRET_KEY_CHANGE_ME` |
| Credential obfuscation | `mast/xor/__init__.py`, `etc/default/xor.conf` | XOR with a repeating key, then base64. Default key is `_`. This is obfuscation, not encryption |
| File hashes | `mast/hashes/__init__.py` | hashlib wrappers; used to verify secure backups |
| Crypto plugin registration | `pyproject.toml:49`, `files/invocation_scripts/{linux,darwin,windows}/mast-crypto*` | `mast-crypto` runs `mast -m mast.datapower.crypto` |

## 5. Defects and hazards found while reading

Each is from reading the code, not from running it. There is no plan
to correct these scripts one at a time. The list is here so that the new
design does not repeat the defects, and so that nobody relies on a
script that does not work.

1. `cert-audit` (`crypto/__init__.py:164`): the handler for a cert with
   no `Filename` uses `name` before it is assigned. It raises `NameError`
   on the first cert, or records the previous cert's name later.
2. `thorough-cert-audit.py`: calls `wb.get_active_sheet()`, which
   current openpyxl no longer has; joins `get_components()` bytes with a
   `str` separator; `final_df` is undefined if the first merge is
   skipped. It also imports `getconfig` as a sibling module, so it needs
   `contrib/` on the import path. It looks unported from Python 2.
3. `cert-recurse.py`: calls `sys.exit` without importing `sys`. The
   export-failure branch overwrites the whole `CryptoCertificate` node
   for the domain. Its default `out_file` is `tmp/cert-audit.json`.
4. `find_passwords.py` and the crypto section of `extract-service.py`:
   write `str` to files opened with `"wb"`, which fails on Python 3.
5. `find_passwords.py`, `extract-service.py` (`crypto-details.csv`) and
   `thorough-cert-audit.py` (unless `--obfuscate-passwords`) write
   plaintext key passwords to disk under `tmp/` or the output directory.
6. `export-certs` and both contrib audits name the temp file after the
   object. Two runs against the same domain at once would collide.

## 6. Post-quantum readiness of the DataPower firmware (tested)

Tested on 2026-11-21 with the `icr.io/cpopen/datapower/datapower-limited`
images and podman. The scripts and the raw results are in
`docs/pqc-research/`. Its README says how to run them again.

### 6.1 What IBM documents

- The `kem-alg` command (CLI) is the `KEMAlgs` property (SOMA) of
  `SSLServerProfile` and `SSLClientProfile`. It selects the key
  exchange groups of TLS 1.3, including ML-KEM (FIPS 203).
- The IBM documentation says that ML-KEM key exchange was a technology
  preview in 10.6.2, and is fully supported from 10.6.4.
- Documented values: `MLKEM512`, `MLKEM768`, `MLKEM1024`,
  `X25519MLKEM768`, `SecP256r1MLKEM768`, `SecP384r1MLKEM1024` and
  `classical`.
- Several preview names of 10.6.2 were renamed or removed in 10.6.4
  (for example `mlkem768` became `MLKEM768`).
- The documentation says that an empty list means classical groups
  only. The tests show that this is not true from 10.6.4 on (6.3).

### 6.2 How the tests were done

- **Inbound** (DataPower is the TLS server):
  1. Make an `SSLServerProfile` with `set-config`.
  2. Point `web-mgmt` at it.
  3. Connect with Python 3.15.2 (OpenSSL 3.5.9), offering one group
     at a time with `ssl.SSLContext.set_groups()`.
  4. Record the group that was negotiated (`SSLSocket.group()`).
- **Outbound** (DataPower is the TLS client):
  1. Make an `SSLClientProfile`.
  2. Make an `HTTPUserAgent` whose SSL policy maps `https://*` to it,
     and an `XMLManager` that uses that user agent.
  3. Run `FetchFile` against a Python 3.15 server on the host. The
     server offers `X25519MLKEM768` only, `X25519` only, or the
     OpenSSL default.
  4. The server logs the group of each connection.
- **Each `KEMAlgs` value was tested twice:** with TLS 1.2 and 1.3 on,
  and with TLS 1.3 only.

### 6.3 Results

Inbound:

| Firmware | Default (no `KEMAlgs`) negotiates `X25519MLKEM768` | `KEMAlgs` accepted | Values accepted | `KEMAlgs` and TLS 1.2 on |
|---|---|---|---|---|
| 10.5.0.23 | no | no (SOAP fault) | none | not applicable |
| 10.6.0.11 | yes, and it cannot be changed | no (SOAP fault) | none | not applicable |
| 10.6.2.0, 10.6.3.0 | no, classical only | yes | `X25519MLKEM768`, `SecP256r1MLKEM768`, `classical` and the preview names. Not `SecP384r1MLKEM1024` or `MLKEM768` | the profile goes down |
| 10.6.4.0, 10.6.5.0, 10.6.6.0 | yes | yes | `X25519MLKEM768`, `SecP256r1MLKEM768`, `SecP384r1MLKEM1024`, `MLKEM768`, `classical`. Preview names refused | the profile goes down |
| 11.0.0.0, 11.0.0.1 | yes | yes | as 10.6.4 | the profile goes down |
| 11.0.0.2, 11.0.0.3 | yes | yes | as 10.6.4 | allowed. TLS 1.2 uses the classical groups |

Outbound: the same rules apply.

- **The default:** it offers `X25519MLKEM768` first on 10.6.0.11 and
  on 10.6.4.0 and later. On 10.5.0.23, 10.6.2.0 and 10.6.3.0 it is
  classical only, and it cannot connect to a server that accepts only
  PQC.
- **`KEMAlgs` with TLS 1.2 on:** the `SSLClientProfile` goes down on
  10.6.2.0 to 11.0.0.1, and every connection that uses it fails.

Both directions:

- **Without `classical`, the profile accepts only PQC.** A list
  without `classical` refuses every classical peer on TLS 1.3, and
  every peer on TLS 1.2.
- **With `classical`, PQC is preferred.** A list with `classical`
  negotiates the hybrid with a peer that offers it, and a classical
  group with a peer that does not.

### 6.4 Behaviour that the inventory and any automation must know

1. **An empty `KEMAlgs` does not mean "classical only".** It means
   the firmware default, and on 10.6.0.11 and on 10.6.4.0 and later
   that default includes `X25519MLKEM768`. Readiness depends on the
   firmware level, `KEMAlgs` and the protocols together. Only a
   handshake proves it.
2. **A change can return OK and still break a service.** On 10.6.2.0
   to 11.0.0.1, a `set-config` that sets `KEMAlgs` on a profile that
   also allows TLS 1.2 behaves like this:
   - The response is `OK`, and the error is only in a `log-event` of
     that response: "KEM algorithm selection is only allowed in
     TLSv1.3".
   - The profile is then `down` in `ObjectStatus`, with that text in
     `ErrorCode`.
   - The service that uses the profile stays `up`, but it resets
     every connection. In the test that was `web-mgmt`, so the web
     GUI was lost.

   Read back `ObjectStatus` after every change.
3. **The preview names do not interoperate.** On 10.6.2.0 and
   10.6.3.0, the preview names (`mlkem768`, `x448_mlkem768`,
   `p384_mlkem1024`, ...) are accepted, but a standard client
   (OpenSSL 3.5) cannot negotiate them.
4. **`classical` reads back in two ways.** 11.0.0.3 stores it as the
   list of classical groups (`brainpoolP512` ... `p256`). 10.6.x
   stores the word `classical`.
5. **The SOMA spec files do not prove support.** `etc/v10.6.0.11-C.xml`
   lists `KEMAlgs` and `EllipticCurves`, but 10.6.0.11 refuses both.
   Use the firmware version and the response of the device.
6. **Invalid values fail differently by firmware.** A value that the
   firmware does not know gets a SOAP fault ("Internal Error (from
   client)"), and the configuration does not change.
7. **Making the test objects** needs a few details:
   - An `SSLServerProfile` or `SSLClientProfile` needs at least one
     `Ciphers` entry (error "required property ciphers is missing").
   - An `HTTPUserAgent` `SSLPolicies` entry is accepted only with an
     `SSLProxyProfile` element in it, even an empty one.

### 6.5 ML-DSA certificates (tested on 11.0.0.3 only)

- **Objects:** an ML-DSA-65 key and self-signed certificate (made with
  OpenSSL 4.0.3) load as `CryptoKey`, `CryptoCertificate` and
  `CryptoIdentCred`, and are `up`.
- **Certificate details:** `do-view-certificate-details` returns
  `SignatureAlgorithm`, `SubjectPublicKeyAlgorithm` (`ML-DSA-65`) and
  `SubjectPublicKeyBitLength`. Method A of 2.2 therefore gives the key
  algorithm and size, at least on 11.0.0.3.
- **TLS:** an `SSLServerProfile` with that identification credential
  is `up`, but every handshake fails with "no suitable signature
  algorithm". `SigAlgs` accepts `ed25519` and `rsa_pss_rsae_sha256`,
  and refuses every ML-DSA name that was tried.
- **Signatures in XSLT:** the 11.0.0 documentation describes
  post-quantum signatures as the extension functions `dp:sign-pqc` and
  `dp:verify-pqc` (ML-DSA-44/65/87 and SLH-DSA).

So post-quantum readiness on DataPower is, today, a matter of key
exchange. Post-quantum certificates cannot yet be used for TLS.

### 6.6 Not tested

- The `sr` and `R` builds (for example 10.6.0.11sr1, 11.0.0.3sr1).
- ML-DSA on 10.6.x.
- Real gateways (MPGW, API Gateway) instead of `web-mgmt` and
  `FetchFile`. They use the same profiles, but this was not checked.
