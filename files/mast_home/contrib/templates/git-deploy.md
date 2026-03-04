# **git_deploy.py — Deployment Process and Git Integration Overview**

`git_deploy.py` is a core deployment utility in the **MAST (McIndi’s Automated Solutions Tool)** framework, used to automate **IBM DataPower appliance deployments** directly from a **Git repository**.
It ensures that each deployment is **repeatable, auditable, and safe**, using Git to pin exact configuration states and a predictable repository layout to drive what is deployed.

---

## **Purpose and Capabilities**

### **In Brief**

* Automates deployment of DataPower configurations from a versioned Git repository.
* Clones or updates repositories, checks out specific commits or branches, and copies that exact tree for auditing.
* Plans and executes safe deployments with backups, checkpoints, file uploads, configuration imports, and service management.
* Supports multiple appliances and environments, with optional GUI (web mode).
* Provides full traceability through logging and artifact preservation.

---

## **Repository Structure and Conventions**

The Git repository defines *what* will be deployed.
Folder names are **case-sensitive** and strictly interpreted by the script.

### **Common (top-level of the repo)**

| Folder                        | Purpose                                                     |
| ----------------------------- | ----------------------------------------------------------- |
| `config/`                     | Common `.xcfg` configuration files to import.               |
| `local/`                      | Files recursively uploaded to `local://…` on the appliance. |
| `cert/`                       | Files uploaded to `cert:///`.                               |
| `sharedcert/`                 | Files uploaded to `sharedcert:///`.                         |
| `pubcert/`                    | Files uploaded to `pubcert:///`.                            |
| `password/alias-password.map` | Optional password-map alias file.                           |

### **Per-Environment (folder named exactly as the environment)**

Each environment overrides or supplements the common content.

| Folder                              | Purpose                                                                         |
| ----------------------------------- | ------------------------------------------------------------------------------- |
| `<ENV>/config/`                     | Environment-specific `.xcfg` imports.                                           |
| `<ENV>/local/`                      | Environment-specific local uploads (override common if same target path).       |
| `<ENV>/cert/`                       | Environment-specific cert uploads.                                              |
| `<ENV>/sharedcert/`                 | Environment-specific sharedcert uploads.                                        |
| `<ENV>/pubcert/`                    | Environment-specific pubcert uploads.                                           |
| `<ENV>/password/alias-password.map` | Environment-specific password aliases.                                          |
| `<ENV>/DeploymentPolicy/`           | (Capital D, capital P) Directory containing one `.xcfg` deployment policy file. |

> **Important:** Directory discovery and uploads are built from these **exact paths**.
> The environment folder name must exactly match the environment identifier used at runtime.

---

## **How `git_deploy.py` Uses Git**

1. **Clone / Pull & Pin**

   * If the local clone already exists at `repo_dir`, the script runs `git pull`; otherwise, it runs `git clone`.
   * If a specific branch, tag, or commit is provided, it runs `git checkout <ref>`.
   * The checked-out repository is copied into `out_dir/<repo-name>` as an **audit snapshot** for traceability.

2. **Plan Build**

   * The script analyzes the repository tree to build a *plan* of actions.
   * It gathers uploads from **common first**, then **environment** folders:

     * `pubcert/` → `pubcert:///…`
     * `cert/` → `cert:///…`
     * `sharedcert/` → `sharedcert:///…`
     * `local/` (recursive) → `local://…`
   * Later entries override earlier ones (i.e., **environment overrides common** on duplicate target paths).
     This is a “last-writer-wins” merge model.

3. **Configuration Imports**

   * Imports all `.xcfg` configuration files found in both `config/` directories (common and environment).

4. **Password Map Aliases**

   * Reads `password/alias-password.map` files from both common and environment directories, adding or updating aliases on the target appliance.

5. **Deployment Policy Merge**

   * Expects exactly one environment policy file under `<ENV>/DeploymentPolicy/`.
   * If present, merges it with a **common deployment policy** (configured in `service-config.conf`).

     * Inserts `AcceptedConfig`, `FilteredConfig`, and `ModifiedConfig` sections from the common policy into the environment policy.
   * The merged file, `merged_deployment_policy.xcfg`, is written to the `out_dir`.
   * If the environment policy directory is missing and “no policy” is not allowed, the script halts with an error.

---

## **Deployment Workflow**

### **1. Preparation**

* Parse `service-config.conf` for domains, environments and options.
* Initialize logging and output directories.
* Clone or update the Git repository and checkout the desired commit.
* Copy the checked-out tree into the audit directory.

### **2. Planning**

* The `Plan` class builds a sequence of `Action` objects representing discrete deployment steps.
* Scans the repository for uploads, configuration imports, service definitions, and password aliases.
* Merges deployment policies as described above.
* Prepares a complete ordered list of actions (uploads, imports, backups, checkpoints, etc.).

### **3. Execution (Per Appliance)**

Each appliance executes the plan in this general order:

**Pre-Deployment**

* Validate domain existence and check for unsaved changes (unless ignored).
* Optionally gather pre-deployment object status.
* Quiesce services (if configured).
* Create checkpoints (rotating oldest if needed).
* Perform appliance backups.

**Core Deployment**

* Add password map aliases.
* Create directories for local uploads (if not present).
* Upload files (`cert/`, `sharedcert/`, `pubcert/`, `local/`).
* Import merged deployment policy (if applicable).
* Import `.xcfg` configurations (with or without deployment policy).

**Post-Deployment**

* Unquiesce services (if previously quiesced).
* Save configuration (if enabled).
* Optionally capture post-deployment status.

### **4. Cleanup & Output**

* All API requests/responses, backups, and configurations are saved to the output directory.
* Logs and summary tables are produced.
* In web/GUI mode, live progress is displayed via Tkinter queues.

---

## **Performance and Best Practices**

* **Repository Layout:**
  Keep `local/`, `cert/`, and similar directories clean; avoid unnecessary nesting or large binary files.
* **Git Hygiene:**
  Large binaries can slow clone/pull operations and audit copies
* **Environment Overrides:**
  Place default configurations in top-level folders and use per-environment directories only for differences.
* **Audit Consistency:**
  Each deployment copies the full Git state to the audit folder for reproducibility and rollback.

---

## **Safety and Audit Features**

* **Backups and Checkpoints:** Optional pre-deployment safety nets.
* **Traceability:** Every deployment is tied to a specific Git commit and produces a corresponding on-disk snapshot.
* **Dry Run Mode:** Generate and review the deployment plan without executing it.
* **Error Handling:** Stops safely on issues like missing policies, corrupt backups, or invalid configs.
* **Deployment:** Multi-appliance support with isolated logs and artifacts per target.

---

## **Summary**

`git_deploy.py` unifies **version control**, **configuration management**, and **deployment automation** into a single reliable pipeline.
By leveraging Git to define exact states and structuring repositories around predictable folder conventions, it guarantees **auditable**, **idempotent**, and **safe** deployments across environments and appliances.
