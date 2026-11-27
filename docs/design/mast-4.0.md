# Design: MAST 4.0

| | |
|---|---|
| Status | Proposed |
| Date | 2026-11-26 |
| Target release | 4.0.0 |
| Branch | `main` (4.x). The 3.x releases continue on `version_3`. |
| Scope | The core split, one CLI, path-based permissions, the DataPower and Linux plugins, the schema lifecycle, distribution, and the import from 3.x. |

## Summary

MAST 4.0 splits MAST into a core and plugins, in one repository:

| Distribution | Import | Responsibility |
|---|---|---|
| `mcindi-mast` | `mast` | Identity, permissions, credentials, targets, fleets, execution, scheduling, the crypto framework, the web UI, the API, setup, and the SDK |
| `mcindi-mast-datapower` | `mast_datapower` | DataPower transport, commands, collectors, rules, status, and the terminal |
| `mcindi-mast-linux` | `mast_linux` | Linux status and public-certificate collection over SSH |

All parts are delivered together in 4.0. The steps in "Plan" are milestones in that release, not separate releases.

4.0 is a breaking release. It has no compatibility aliases, no legacy console scripts, and does not keep the 3.x command IDs. An existing installation moves to 4.0 with a one-time import (step 13). The 3.x installation is not changed.

## Release lines

| Branch | Line | Tags | Gets |
|---|---|---|---|
| `main` | 4.x | `v4.*` and later | All new work, including this design |
| `version_3` | 3.x | `v3.*` | Fixes, and 3.x work that is already in progress |

A fix to 3.x goes to `version_3` first and is then merged into `main`. When the DataPower code has moved into `mast_datapower` (step 9), these merges will conflict, so fixes are then ported by hand.

`release.yml` refuses a tag that is not on the branch of its line. Only the highest release is "Latest" on GitHub, so a 3.x fix after 4.0 does not replace 4.x as Latest.

## Decisions

| # | Decision |
|---|---|
| D1 | There are three distributions: `mcindi-mast`, `mcindi-mast-datapower` and `mcindi-mast-linux`. Core imports neither plugin. |
| D2 | From 4.0, the MAST version and the plugin versions do not follow the bundled Python. Core starts at `4.0.0`, and the plugin API at `1`. |
| D3 | There are no compatibility aliases, no legacy console scripts, and the 3.x command IDs are not kept. The 3.x import (step 13) replaces them. |
| D4 | There is one console command, `mast`. Each plugin command is `mast PROVIDER COMMAND`. |
| D5 | A plugin command has one identity everywhere: the ID `PROVIDER.COMMAND`, the permission path `mast/PROVIDER/COMMAND`, and the CLI invocation `mast PROVIDER COMMAND`. |
| D6 | The DataPower namespaces (`system`, `backups`, ...) are flattened into `datapower`. Categories stay as catalog and UI metadata only. |
| D7 | A plugin adds commands only beneath its own provider root. The `extends` mechanism of the earlier draft is removed. |
| D8 | Path-based permission sets and groups replace roles. There is no `User.role` and no `Page.min_role`. |
| D9 | Each package has its own Alembic migration context and version table. 4.0 starts with fresh baselines. |
| D10 | The crypto framework is in core. Product interpretation (for example, DataPower firmware defaults and `KEMAlgs`) is in the collectors. |
| D11 | `git-deploy` is removed. The supported deployment commands stay. |
| D12 | The documented PyPI install is `python -m pip install mcindi-mast-datapower`. The Linux plugin is published separately. |
| D13 | The zip installers and the GHCR images bundle core and DataPower, from the same tested lockfile. |
| D14 | Duplicate commands of 3.x are merged: one 4.0 command for each pair, and both 3.x ids map to it (Q1). |
| D15 | Core commands have permission paths `mast/core/NAME`. Core resources have reserved roots of their own: `mast/credentials/NAME`, `mast/targets/NAME`, `mast/fleets/NAME`. No plugin may use these names as its provider (Q3). |
| D16 | Prefix wildcards stay as designed. Their safety rests on the correct `effect` of every command; there is no separate flag for sensitive reads (Q2). |
| D17 | In the installer, `mast` is the command and the interpreter is `mast-python`. Core has program roots for its processes: `mast web`, `mast worker`, `mast daemon`, `mast version`, `mast selftest`. |
| D18 | A target has one base kind and any number of extensions. An extension is a kind that extends another kind, of the same plugin or another one (`django` extends `linux`, `apic-gateway` extends `datapower`), and extensions can extend extensions. An extension inherits the attributes, endpoints, credential types, trust and capabilities of the kind that it extends, and adds its own. It may change how an inherited capability works, but never removes or redefines an inherited attribute. |
| D19 | A command that accepts a kind accepts every kind that extends it, and a target qualifies through its base kind or any of its extensions. A plugin's commands may accept the kinds of other plugins. A command that accepts only kinds that no installed plugin gives is unavailable, with the plugin to install, and the rest of its plugin works. |
| D20 | Kinds declare capabilities (`collect-crypto`, `test-credentials`, ...). A core command never names a plugin kind: it asks for capabilities (`needs`) and accepts every installed kind that has them, so a plugin installed later is covered with no change to core. |
| D21 | One kind has one definition: two plugins that give the same kind are both left out. A plugin whose kind extends a kind that no installed plugin gives is left out until that plugin is installed. |
| D22 | A plugin defines groups with pre-baked permission sets over its own commands (`mast.plugins.Group`). MAST makes them without members when it first sees them, and an admin assigns users after setup or after installing the plugin. A newer plugin version updates a set that no admin changed; a group that an admin removed is not made again. |

## Deferred

* Layer7. It is the next commercial integration, after a test environment is available.
* MCP, change plans, general workflows, and arbitrary remote execution.
* A Git-backed DataPower deployment primitive that replaces `git-deploy`.

## Plan

### 1. Package boundaries

Create the three distributions in one repository (see "Summary"). Each plugin declares the range of core that it requires.

Remove compatibility aliases, legacy console scripts, and the preservation of 3.x command IDs from the implementation.

**Done when:** each distribution builds alone, each plugin declares its core dependency, and core imports neither plugin.

### 2. One CLI and one command registry

Install one console entry point, `mast`:

```text
mast datapower get-status
mast datapower normal-backup
mast datapower cert-audit
mast linux get-status
mast linux check-credentials
mast linux collect-crypto

mast crypto inventory
mast targets ...
mast fleets ...
mast credentials ...
mast jobs ...
mast schedules ...
mast users ...
mast groups ...
mast permissions ...
mast plugins ...
mast setup ...
```

One mapping connects command identity, permission path and invocation:

| Property | Example |
|---|---|
| Provider | `datapower` |
| Command name | `get-status` |
| Canonical command ID | `datapower.get-status` |
| Permission path | `mast/datapower/get-status` |
| CLI invocation | `mast datapower get-status` |

* A command name is unique within its provider. The 13 names that collide when the namespaces are flattened were duplicates of one SOMA action, and are merged (D14).
* Categories (system, backups, deployment, crypto, ...) are catalog and UI metadata. They do not add CLI levels or permission-path segments.
* Core commands have explicit canonical names, such as `core.add-target` and `core.cancel-own-job`.
* The CLI parsers, the help, the web forms and the JSON catalog are generated from the same command specifications.
* Jobs and schedules store canonical IDs.
* Core reserves its root commands. Generic crypto operations dispatch through collector interfaces.

**Done when:** the CLI, the web UI and the API resolve the same specifications, every plugin invocation is `mast PROVIDER COMMAND`, and a duplicate ID or CLI route always fails registration the same way.

### 3. Plugin API 1

Each distribution has one `mast_plugin` entry point.

A plugin ships a manifest with its provider identity, version, supported core versions, plugin API version, target kinds, command ownership and schema versions. Core reads the manifest without importing the plugin's runtime code.

Plugin API 1 defines interfaces for:

* Commands and typed parameters.
* Target attributes and connection environments.
* Credential types and credential testing.
* Named connection endpoints and trust verification.
* Choice providers.
* Crypto collectors and rule packs.
* Optional status summaries and web pages.
* Models, migrations, retention, and packaged home files.

Every executable command declares:

* `effect="read"` or `effect="change"`. `effect` is separate from `exclusive`.
* The target kinds or capabilities that it accepts.
* Its target parameters and secret parameters.
* Its result structure.
* Its scheduling locks.

Core registers a plugin only after the plugin is fully validated. A plugin that fails registers no commands or pages. `mast plugins list` and the web UI show the failure.

**Done when:** missing, incompatible, conflicting and broken plugins give diagnostics that say what to do, and core stays available.

**Status.** The manifest (`mast.plugins.Manifest`) has the provider, the version, the plugin API, the supported core versions (a version specifier), the commands, the kinds (`mast.plugins.Kind`: a name, what it extends, its capabilities) and the pages. Every command declares `effect`, the kinds that it accepts (`targets`) or the capabilities that it needs (`needs`), its target and secret parameters (`Option(target=True)`, `Option(secret=True)`) and its locks (`locks`, which was `resources`). Registration checks every command first, and a plugin with one fault is left out with its commands and pages. A failure is `broken`, `incompatible`, `conflict`, `invalid` or `missing`, with a fix, in `mast plugins list` and Admin → Plugins. `mast plugins kinds` shows every kind, what it extends and its capabilities (D18 to D21). The interfaces for target attributes, credential types, endpoints and trust come with step 4, schema versions with step 7, and crypto collectors with step 8. The result structure of a command is not declared yet: commands return `Text` or `Table`. The purpose `collect` of a stored credential now follows `effect`, not `exclusive`.

### 4. Targets, fleets, credentials and trust

The appliance-specific core model is replaced:

* A **target** has an immutable ID, a name, a base kind, any number of extensions (D18), an address, validated attributes, an attribute-schema version and a configuration revision. Each extension adds its own attributes, and an extension that a host has more than once (two Django sites on one host) holds one entry for each.
* A **fleet** holds targets of one base kind, or of one extension and its descendants. It has a description and a production flag.
* A **credential** has a credential type, an encrypted payload, a purpose, and target or fleet bindings.
* A **trust record** is attached to a named endpoint.

Transport settings such as `soma_port`, `ssh_port` and `web_port` are plugin attributes. A plugin describes its endpoints. Core does certificate and host-key verification, the pin preview, the confirmation and the audit records.

* Credential types are username/password and SSH private key. Secret material is stored only in the credential store.
* A target's base kind cannot change. Adding or removing an extension is a change to the target, and an extension that adds an endpoint needs a trust review for it. A change to an endpoint's address, port or identity needs an explicit trust review.
* Plugin commands run only against registered targets. Core has a built-in `endpoint` kind for TCP connectivity checks and TLS probes, so core works with no plugin.
* A fleet is resolved to target IDs before submission. The job records those IDs and the configuration revisions of the targets. A change to fleet membership never adds targets to a queued job.

**Done when:** a wrong-kind selection, a mixed-kind fleet, an unregistered plugin target and a changed endpoint are all handled before credentials are sent.

**Status (4a).** Targets and fleets replace appliances and environments (`mast.registry`, migration `4001`): a target has an immutable id, a name, a base kind, extensions with entries, an address, attributes checked against its kind (`mast.plugins.Attribute`), the schema version of its kind and a revision that every change raises. Each endpoint of its kinds (`mast.plugins.Endpoint`) has a trust record; the certificate and host-key checks, the pin preview and the review moved from the DataPower plugin into core (`mast.trust`). A change of the address, or of the port of a pinned endpoint, marks the pin for review, and nothing is sent to it until an admin reviews or replaces it (pins only: a CA check is itself a check of the new address). A fleet has a kind, and its members must be of it. Commands run only on registered targets of a kind that they accept; the queue refuses anything else before a job exists. The DataPower ports default to the standard ports, so that core can preview a pin without DataPower code. The target ids and revisions of each job are 4c, below.

**Status (4b).** A stored credential has a type, `password` or `ssh-key` (a private key and its passphrase, checked when stored; migration `4002`). A kind declares the types that its targets take (`Kind.credential_types`) and the command that tests a credential on one of them (`Kind.credential_test`); core names no plugin command. A credential is bound to targets and fleets only of kinds that take its type, and used only on those. A target can also be set to take fewer types than its kinds (`credential_types`: how it is configured now, such as a Linux host that took a password to install an SSH key and then takes only keys); a credential of another type is refused on it when it is used, while its bindings stay. The setting is configuration: changing it raises the revision of the target. The plugins of 4.0 take passwords, so an SSH key is not given to any command yet: the Linux plugin (step 10) adds the way a command receives one. `mast targets import` and `export` of the 3.x files stay until the 3.x import (step 13).

**Status (4c).** A job records the id and the revision of each of its targets (`job_targets.target_id` and `revision`; migration `4003`), and the fleets of its target parameters are replaced by their targets when it is queued, so a change to a fleet adds no target to it. Before the runner opens a stored credential or calls the command, it checks each recorded target: a target that was removed or whose revision changed fails the job (`mast.jobs.queue.changed_targets`). A change of the notes alone does not raise a revision. With 4a and 4b, the "Done when" of this step holds: a wrong kind and an unregistered target are refused at submission, a mixed-kind fleet cannot be made, and a changed endpoint (a new address or port of a pin) or a changed target gets no credential. `mast targets import` and `export` of the 3.x files stay until the 3.x import (step 13).

### 5. Path-based permission sets and groups

| Capability | Meaning |
|---|---|
| `r` | Discover commands, and read authorized resources or stored results |
| `w` | Modify a resource, or permit a command's change effects |
| `x` | Execute a command or perform a protected action |

* Running a command needs `x`. A command whose effect is `change` needs `w` and `x`. Reading its output needs `r`.
* A path is exact, or ends in one `*` prefix wildcard:

  ```text
  mast/datapower/get-status
  mast/datapower/list-*
  mast/datapower/*
  mast/credentials/dp-reader
  ```

* `mast/datapower/get-status` is `mast datapower get-status`. `mast/datapower/list-*` applies to the matching commands directly beneath `mast datapower`.
* A prefix grants every command that matches it, whatever the command does: `mast/datapower/get-*` also matches `get-file`, which reads any file of an appliance. The `effect` of each command must be right (D16).
* Malformed paths, embedded wildcards and unknown capabilities are rejected.
* The default is deny. Matching grants from all permission sets are combined. An explicit denial that applies wins for the capability that it denies.
* A target-related rule has an explicit scope: all targets, given targets, or given fleets. Names are resolved to immutable IDs when the policy is saved. A rule with no scope grants no target access.

Example:

```json
{
  "name": "datapower-lab-reader",
  "rules": [
    {
      "path": "mast/datapower/list-*",
      "allow": ["r", "x"],
      "scope": {"fleets": ["dp-lab"]}
    },
    {
      "path": "mast/credentials/dp-reader",
      "allow": ["x"],
      "scope": {"fleets": ["dp-lab"]}
    }
  ]
}
```

Users and service accounts belong to groups. Groups reference named permission sets. Role ranking and the single `User.role` field are removed.

Core seeds three ordinary groups, which can be edited:

| Group | Default core permissions |
|---|---|
| `viewer` | Read the non-secret core operational views |
| `operator` | Viewer, plus job submission and the cancellation of their own jobs |
| `admin` | Manage core identities, permissions, targets, credentials, schedules and jobs |

Plugin commands are granted only through explicit permission sets. Installing a plugin does not grant its commands to any existing group. A plugin defines its own groups with pre-baked permission sets (D22), which start without members.

For a credential, `r` shows its metadata, `w` manages the record, and `x` permits its use. No operation returns a decrypted secret. The purpose and the target bindings of a credential still restrict its use.

**Done when:** permission sets and memberships can be managed through the CLI and the API, and administrators can see effective permissions and the rules that match.

**Status.** `mast.permissions` (migration `4004`): permission sets of rules, groups (`user_groups`), the sets of a group and its members. A path is `mast/ROOT/NAME`; a `*` may only end it and matches within the last segment, so `mast/datapower/*` is every command of DataPower and never more. Scopes are kept as ids (a renamed target stays in scope). A denial without a scope applies to every target; an allow without a scope grants no target. Core actions that are not commands have a path of their own (`mast/core/cancel-own-job`). The sets and groups `viewer`, `operator` and `admin` are seeded as the table above says, and no seeded set names a plugin. The DataPower plugin defines the groups `datapower-reader` (the commands whose effect is `read`) and `datapower-operator` (every DataPower command), on all targets (D22). `mast permissions` (sets, rules, `check`, `effective`), `mast groups` (sets, members) and the admin API manage them and explain decisions. Until step 6, the role of a user decides what they may do and each user is kept in the groups of their role; step 6 enforces the rules, removes `User.role`, and adds service accounts with tokens. A page of the web UI for permissions is part of step 11.

### 6. Enforcement

* Authenticated MAST identities replace the automatic local OS-admin identity. `mast login` gives revocable tokens for the CLI. A dedicated local setup operation makes the first administrator.
* The same authorization service is used by the CLI, the web UI, the API, workers, schedules, choice lookups, collectors and terminal sessions.
* At submission, the command, every resolved target and each credential use are authorized. If one selected target is not authorized, the whole submission is rejected.
* Before execution and before credential decryption, the identity status, current permissions, credential bindings, target kind and target configuration are checked again. The request guard checks authorization changes, as it checks cancellation and lease ownership. After a revocation, no further requests are sent.
* Each job stores the canonical permission path, the provider version, the submitter, the execution identity and the target IDs. Access checks on old jobs work without importing the original plugin.
* Job lists, counts, events, downloads, inventory and exports are filtered the same way. In 4.0, the detailed output of a multi-target job is shown only to a caller who may read it for every recorded target. Ownership alone does not give read access.
* A schedule has an explicit `run_as` identity. Scheduling work as another identity needs a delegation permission, and that identity's current rights are checked each time the schedule fires. The creator, the editor, the execution identity and the schedule ID are recorded.
* A generic inventory operation authorizes the provider collectors as well as the generic command.

**Done when:** neither direct calls nor generic dispatch can get around authorization, and the revocation tests pass for queued and running jobs.

### 7. Schema lifecycles

4.0 starts with fresh migration baselines. Core owns its tables, and each plugin owns its tables. Each has its own Alembic migration context and version table:

```text
mast_schema_core
mast_schema_datapower
mast_schema_linux
```

* A plugin declares the core schema versions that it needs in its metadata. Core is upgraded first, then the compatible installed plugins. Dependencies between packages are not in Alembic's revision graph.
* Migrations run explicitly, through setup or the container's initialization role. Runtime processes check schema compatibility when they start.
* A plugin migration never changes core tables. It may add foreign keys to documented core tables. Migration generation is limited to the tables of the owning package.
* When a plugin is removed, its tables and revision records stay. A separate, explicit purge operation removes them. A missing plugin's migrations do not stop core from reading its own schema.
* The supported 4.x upgrades and downgrades are tested on SQLite and PostgreSQL. An incompatible schema version is rejected with clear recovery instructions.

**Done when:** core starts with a plugin absent whose tables remain, and reinstalling the plugin restores access with no data reconstruction.

### 8. The crypto framework in core

Snapshot storage, object relationships, certificates, findings, exceptions, posture, probing and the CBOM export move into core.

* Normalized records describe object roles, certificate and key metadata, relationships, TLS versions, cipher suites, key-exchange groups, listeners and collection problems. Native class names and sanitized native attributes are kept separately.
* Configured facts and observed facts are kept apart. Unknown, unsupported, unavailable and incomplete information is shown explicitly.
* Product and firmware interpretation stays in the collectors. DataPower firmware defaults and the behavior of `KEMAlgs` are in the DataPower plugin.
* A snapshot writer validates collector output and records completeness, errors, timestamps, the collector version and the source target. A failed or incomplete collection never appears as a clean posture assessment.
* Rules have provider-qualified identities. Generic rules run on normalized facts. Product rules run only on the target kinds that they apply to.
* `mast crypto inventory` is a parent operation with one collection job per target. Starting a collection directly, through the inventory or through a schedule needs the same provider permissions.

**Done when:** the same core rules and exporter process the DataPower and Linux fixtures, with no product-specific branches in core.

### 9. Extract DataPower and remove `git-deploy`

* Move the DataPower transports, environments, commands, helpers, collectors, native rules, status tables and terminal into `mast_datapower`.
* Mount its commands directly beneath `mast datapower`. Update the command registration, help, documentation, contributed scripts, tests and generated forms to the flat names. Keep the functional categories as metadata.
* Replace the core imports of DataPower and the DataPower assumptions in command discovery, widgets, pinning, credential testing, retention, migrations, configuration and logging.
* Remove `git-deploy`: its command registration, implementation, dedicated locks, configuration, tests, current documentation and catalog entries.
* Remove external Git from the runtime image requirements. Remove a Python dependency only after its remaining users are checked. The tracking scripts in `contrib` (`track-autoconfig.py`, `track-getconfig.py`) use `dulwich` without `git-deploy`.
* Route the supported DataPower operations through the 4.0 registry and execution service. An unrestricted terminal session is a change operation that needs `w` and `x`.
* Every target operation uses the request guard and reports an explicit outcome for each target. Correct the existing commands that exit with success when targets fail.

**Done when:** DataPower works through its package and the flat CLI, core has no DataPower imports, and `git-deploy` is absent from every executable interface.

### 10. The Linux reference plugin

`mast_linux` gives Linux status and public-certificate inventory over SSH:

| Command ID | Permission path | Invocation |
|---|---|---|
| `linux.get-status` | `mast/linux/get-status` | `mast linux get-status` |
| `linux.check-credentials` | `mast/linux/check-credentials` | `mast linux check-credentials` |
| `linux.collect-crypto` | `mast/linux/collect-crypto` | `mast linux collect-crypto` |

* The collector is connected to `mast crypto inventory`.
* Status gives the OS identity, kernel version, uptime, load averages and memory, from fixed operations and known files. `/etc/os-release` is parsed as data.
* The target attributes are the SSH port, timeouts, named certificate sources, permitted certificate roots, and explicit TLS endpoints (host, port and SNI).
* Public certificates are extracted remotely through fixed operations. Raw files that can contain private keys are not transferred. Certificates seen on endpoints are matched to collected certificates by fingerprint.
* The effective TLS configuration and the service that owns an endpoint are reported as unknown unless they are directly established.
* Out of scope for 4.0: arbitrary shell commands, sudo, package management, service control, general file browsing, private-key collection and certificate deployment.
* Fixtures: non-privileged OpenSSH and NGINX containers with valid and expired certificates, weak keys, malformed and unreadable sources, wrong credentials, host-key mismatches, missing tools and partial collection.

**Done when:** a Linux-only installation uses the production plugin interfaces and the crypto framework, with fixtures that are all open source.

### 11. The permission-aware web UI

* Core pages: Targets, Jobs, Commands, Crypto, and administration.
* Targets shows each target's kind, fleets, last collection, freshness and errors. A plugin can add a status summary, so basic visibility needs no plugin-specific page.
* Capability checks replace role comparisons and `Page.min_role`. Navigation and forms are filtered by effective permissions, and the server enforces every operation.
* Each plugin command shows its flat CLI invocation and its canonical permission path. Categories organize the commands on the page and do not change either identity.
* Administration covers users, service accounts, groups, permission sets, credentials, schedules and plugin health. It shows why a user can or cannot do an operation.
* The detailed DataPower status and terminal pages stay in the DataPower plugin.

**Done when:** an operator of a Linux-only installation has a usable UI, and users see only the records and actions that they are authorized for.

### 12. Home setup and the three channels

Setup moves into core:

```text
mast setup install PATH
mast setup upgrade
mast setup develop
mast setup build
```

* The setup and build commands work before a home exists. A documented bootstrap command uses the repository's Python tooling in a checkout.
* The defaults and other home files are packaged in the wheels. Setup tracks which package owns each file, rejects conflicting contributions, and updates packaged defaults without overwriting local configuration or secrets.
* The runtime home is found from `--home`, then `MAST_HOME`, then the home recorded in the virtual environment. A zip launcher gives its home explicitly.

PyPI, in a virtual environment:

```text
python -m pip install mcindi-mast-datapower
mast setup install /path/to/mast-home
```

The Linux plugin is published separately, as `mcindi-mast-linux`.

* The zip installers and the GHCR images are built from the same tested lockfile, and bundle core and DataPower.
* The image has the roles initialization, web, worker and scheduler. The documented multi-container deployment uses PostgreSQL. The persistent home is initialized or upgraded before the runtime services start.
* `jobs.key`, `credentials.key` and `server.key` are deployment secrets, shared by the processes that need them. The image contains no deployment secrets. It runs as a non-root user and supports a read-only root file system.

**Done when:** a fresh installation and an upgrade work through wheels, zip and containers, including paths with spaces and relocated zip installations.

### 13. The one-time 3.x import

* The import makes a fresh 4.0 home and database. The 3.x installation is not changed.
* `mast setup import-v3 SOURCE --dry-run` shows what would be imported, and a separate apply operation imports it. The supported source is the documented 3.x schema at revision `0011`. Any other schema is rejected (see Q6).
* It imports targets, fleets, production flags, compatible configuration and stored credentials. Credentials are decrypted with the source key and encrypted again with the destination key, with no plaintext intermediate files.
* It gives a complete map from the 3.x launchers and stored command IDs to the 4.0 CLI and IDs:

  | 3.x invocation | 4.0 invocation | 4.0 command ID |
  |---|---|---|
  | `mast-system get-status` | `mast datapower get-status` | `datapower.get-status` |
  | `mast-backups normal-backup` | `mast datapower normal-backup` | `datapower.normal-backup` |
  | `mast-crypto cert-audit` | `mast datapower cert-audit` | `datapower.cert-audit` |

* Supported schedules are imported as disabled drafts that need a 4.0 execution identity and a permission review. Schedules of removed commands, including `git-deploy`, are reported as unsupported.
* Users and group assignments are made through the 4.0 identity model.
* Historical jobs, inventories, findings and exceptions stay in the archived 3.x installation. Pending and running jobs are not imported.
* Operators stop the 3.x workers and schedules before the cutover. To roll back, stop 4.0 and resume the preserved 3.x installation. Changes made in 4.0 are not copied back.

**Done when:** an import rehearsal gives a report that can be reviewed, leaves its source unchanged, and cannot activate imported automation without an explicit action.

### 14. Conformance testing and the 4.0 release

* Test core alone, core with Linux, core with DataPower, and core with both. In the isolated-installation tests, the other plugin packages are physically absent.
* Cover command identity, flat CLI mounting, permission-path mapping, collisions, broken plugins, data kept after uninstall, typed target validation, endpoint trust, credential redaction, cancellation, leases, per-target outcomes and the schema lifecycle.
* Cover permission unions, explicit denials, wildcard boundaries, target scopes, credential use, result filtering, schedule delegation and revocation. Assert that the CLI and the API make the same decisions.
* A small conformance-test plugin exercises the authorization of change commands. The Linux plugin stays within its collection scope.
* Replay fixtures come from synthetic or explicitly sanitized data. A test checks that the fixtures contain no passwords, private keys, tokens or raw sensitive configuration.
* Test the oldest and newest supported dependency versions, and the supported core/plugin version combinations in both directions. All runtime processes of one deployment must have the same core and plugin package set.
* Publish the installation instructions, the CLI migration map, permission-set examples, plugin-author documentation, licensing terms, recovery procedures, SBOMs and artifact attestations.

Release **4.0.0** after the Linux fixture suite, the DataPower regression suite, the installed-product checks, the migration rehearsal and the distribution checks pass.

## Open questions

These come from a comparison of the plan with the 3.x code at `2427b09`. Q1 to Q4 are decided (D14 to D16, and step 2).

**Q1 (decided: D14). Flat-name collisions.** 3.x has 119 commands. Flattening into `datapower` gives 13 names that occur in two namespaces:

| Name | 3.x namespaces |
|---|---|
| `clean-up`, `del-file`, `get-file`, `set-file` | `deployment`, `system` |
| `flush-aaa-cache`, `flush-ldap-pool-cache`, `flush-rbm-cache` | `accounts`, `system` |
| `flush-arp-cache`, `flush-dns-cache`, `flush-nd-cache`, `flush-pdp-cache` | `network`, `system` |
| `flush-document-cache`, `flush-stylesheet-cache` | `developer`, `system` |

The `flush-*` pairs are separate implementations of the same SOMA action (for example `mast/datapower/accounts/accounts.py` and `mast/datapower/system/system.py`, both `FlushAAACache`). Merge each pair into one command, and map both 3.x IDs to it in the import (step 13)? The file commands must be compared before names are chosen. (`crypto.export` and `developer.export` do not collide, because `crypto` moves to core.) The `datapower` root will also have three similar connectivity commands: `check-connectivity`, `test-connectivity` and `tcp-connection-test`.

**Q2 (decided: D16).** Prefix wildcards over command names.** In the example, `mast/datapower/get-*` with `r` and `x` matches 8 commands: `get-status` and `get-config`, and also `get-file`, `get-filestore`, `get-encrypted-filesystem`, `get-temporary-filesystem`, `get-error-reports` and `get-pmr-info`. `get-pmr-info` generates an error report on the appliance, so its effect is `change`. `get-file` reads any file, including `cert:///`, so with `effect="read"` a "reader" can download key material. The safety of a prefix grant depends on the `effect` of every matching command, and command names become security-relevant. Should the example use a safer path, should sensitive reads need their own grant, or should grants be able to select on `effect` or category? Note that 91 of the 119 commands are `exclusive=True` today, so `exclusive` is no guide to `effect`.

**Q3 (decided: D15).** Permission paths for core. For plugins, the ID, the path and the invocation are one mapping. For core, `core.add-target` is invoked as `mast targets add`, and its permission path is not defined. `mast/core/credentials/dp-reader` puts a resource beneath the same `mast/core/` prefix as core commands, so `mast/core/*` grants all core commands and all credentials. Define a separate scheme for core commands and core resources?

**Q4 (decided: `datapower.collect-crypto`).** The DataPower collection command. Linux has `linux.collect-crypto`. The plan does not name the DataPower command that replaces `crypto.inventory-appliance`. `datapower.collect-crypto`, for symmetry?

**Q5. Unattended local use.** Today every CLI call is an admin: `os_principal()` in `mast/jobs/auth.py`, and `principal_of` treats `os:` and `local` as admin. With `mast login`, cron entries, `contrib` scripts and the Windows service each need a token or a service account. Steps 6 and 12 should say how unattended local use works.

**Q6. The import source revision.** The current 3.x head is `0011`. 3.x work in progress on `version_3` (the next crypto step) plans a migration `0012`. If that ships in 3.x, step 13 must support it.

**Q7. Credential types.** 3.x credentials have only a username, a token and a purpose (`mast/credentials/models.py`). The credential type and SSH private-key credentials are new work in step 4, and the import maps every 3.x credential to username/password.

## Earlier draft

An earlier draft (`core-split.md`, not merged) kept the 3.x launchers, command IDs and `mast.datapower` as an alias, shipped the seams in 3.x releases, and kept migrations 0001–0011 in one chain with plugin branches. This design replaces it. With separate release lines, 4.0 can break compatibility, and the 3.x import replaces the aliases.
