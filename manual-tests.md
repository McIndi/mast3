These tests assume that you have two DataPowers, one at 192.168.0.10
and one at 192.168.0.20. 

# To do a fresh build

#   The build needs uv (https://docs.astral.sh/uv/) and Python 3.11 or newer
python mast-setup

# Installation file will be in dist directory
# For testing or review, the contents of the installation file can be found in build/assemble
cd build\assemble

cd ..\..\ && python mast-setup && cd build\assemble

# Add the appliances and the environment
mast-appliances add -n dp1 -A 192.168.0.10
mast-appliances add -n dp2 -A 192.168.0.20
mast-appliances env-add -n lab -m dp1 -m dp2

# Setup creds (choose one)

set MAST_CREDS=username:password
set XOR_MAST_CREDS=<from mast-system xor>

# Pin the certificate and the SSH host key of dp1 (shows them and asks;
# compare them with the appliance's console). A self-signed appliance
# needs this before a stored credential works with it.
mast-appliances pin -n dp1 --both
mast-appliances list                          # dp1: pin, pinned

# Or store a credential (asks for the password), test it, and use it.
# The appliance's certificate must be pinned, or one that the MAST host trusts.
mast-credentials add -n lab-read -u <a user that may only read> -p collect -b lab
mast-credentials test -n lab-read -a dp1      # works; can change: no
mast-system list-domains -a lab --credential lab-read
mast-system save -a lab --credential lab-read # refused: only reads
mast-credentials uses -n lab-read
# Pin a wrong fingerprint: the job fails with "The certificate of dp1
# changed" and sends nothing; list shows CHANGED. Then pin again.
mast-appliances pin -n dp1 -f 0000000000000000000000000000000000000000000000000000000000000000
mast-system list-domains -a dp1 --credential lab-read
mast-appliances list
mast-appliances pin -n dp1

# Try ssh (choose one)
mast-ssh -a lab -c %MAST_CREDS% -d default
mast-ssh -a lab -c %XOR_MAST_CREDS% -d default

# Issue a couple of commands:
show mem
show cpu
show web-mgmt
...

# Create a directory
mast-system create-dir -a lab -c %MAST_CREDS% -n -D default -d local:///20231113/

# Set a file
mast-system set-file -a lab -c %MAST_CREDS% -n -D default -f ..\..\README.md -d local:///20231113/

# List the directory
mast-system get-filestore -a lab -c %MAST_CREDS% -n -D default -l local: -o tmp
    
    # Examine the output
    grep -R 20231113 tmp/.
    grep -R README tmp/.

# Remove the directory
mast contrib\dprm.py -a lab -c %MAST_CREDS% -n -d default -p local:/20231113/README.md --dry-run
mast contrib\dprm.py -a lab -c %MAST_CREDS% -n -d default -p local:/20231113 --dry-run

mast contrib\dprm.py -a lab -c %MAST_CREDS% -n -d default -p local:/20231113/README.md
mast contrib\dprm.py -a lab -c %MAST_CREDS% -n -d default -p local:/20231113

# Take a normal backup
mast-backups.bat normal-backup -a lab -c %XOR_MAST_CREDS% -n -D all-domains -C "Daily Backup 20231113" -o tmp
mast-backups.bat normal-backup -a lab -c %MAST_CREDS% -n -D all-domains -C "Daily Backup 20231113" -o tmp

# Restore a normal backup
mast-backups restore-normal-backup -a lab -c %MAST_CREDS% -n -f tmp\dp1\NormalBackup\20231113103737\20231113103737-dp1-all-domains.zip -D default --timeout 1200

# Generate docs
mast contrib/gendocs.py

# List users
mast-accounts list-users -n -a lab -c %MAST_CREDS%

# Add a group defined user
mast-accounts add-user -n -a lab -c %MAST_CREDS% -u new_user -p pa55word -g developer -s

# Add a priviledged user
mast-accounts add-user -n -a lab -c %MAST_CREDS% -u priv_user -p pa55word -P -s

# Force change password
mast-accounts force-change-password -n -a lab -c %XOR_MAST_CREDS% -U new_user

# List domains
mast-system list-domains -n -a lab -c %MAST_CREDS%

# Add domain
mast-system add-domain -n -a lab -c %MAST_CREDS% -d new_domain -s

# Import configuration
mast-developer import -n -a lab -c %MAST_CREDS% -D new_domain -f ~/Downloads/export.zip

# Save a domain
mast-system save -n -a lab -c %MAST_CREDS% -D new_domain

# Schedules (a worker running: mast-worker, or the one in mastd)
mast-jobs schedule-add -n lab-domains -c system.list-domains -t "*/2 * * * *" -s 'appliances=["lab"]' -s credential=lab-read
mast-jobs schedule-list                       # next run in two minutes
# Wait: a job by schedule:lab-domains every two minutes (mast-jobs list)
mast-jobs schedule-run -n lab-domains         # now; again at once: skipped
mast-jobs schedule-remove -n lab-domains -y
# In the web UI: Admin -> Schedules, add one with the form, Run now,
# then Jobs (filtered by the schedule)
mast-jobs prune --dry-run

# Phase A: from a fresh home to a stored credential

A fresh home, one DataPower (the container of the integration tests is
enough), the web UI in a browser. On Windows, use the `.bat` launchers.

    # 1. A fresh home: unzip, make the certificate, add the admin (asks
    #    for a password), start the web UI and a worker
    ./mast contrib/gen-cert.py
    ./mast-users add -u alice -r admin
    ./mast-web          # and, in a second terminal: ./mast-worker
    # Open https://127.0.0.1:5000/: log in as alice, choose a new password.
    # /classic/ is 404, and no page links to it.

    # 2. The appliances: Admin -> Appliances, add dp1 (or
    #    ./mast-appliances import from the files of MAST 3.15.2), then Pin.
    #    The dialog shows the certificate and the SSH host key: compare the
    #    SHA-256 with what the appliance itself shows, and pin.
    ./mast-appliances list                       # TLS pin, SSH key pinned

    # 3. A collect credential: make the DataPower user that may only read
    #    (Operator's Guide, "A DataPower user that may only read"), then
    #    Admin -> Credentials: add "collector", purpose collect, bound to
    #    dp1, and Test: works yes, can change no.

    # 4. Admin -> Users: add olga as an operator. In another browser (or a
    #    private window) log in as olga. Commands -> system.get-status:
    #    dp1 and the credential collector in the target bar, a provider,
    #    Run. The Jobs page shows the job live, then its result.

    # 5. The use is recorded, without the password:
    ./mast-credentials uses -n collector          # the job, olga, collect
    # Admin -> Credentials -> collector: the same uses, and the audit.

    # 6. A new key: every credential is encrypted again, and works.
    ./mast-credentials rotate-key -y
    # As olga, run get-status again: it works.

    # 7. As olga, Commands -> accounts.add-group with the credential
    #    collector: refused ("for collecting ... can change the
    #    appliances"), and no job runs. The same with -c and the password
    #    of the read-only user: DataPower answers "Authorization failure".

# Phase R: the classic GUI is gone

    # /classic/, /classic/plugins.js and /test/connectivity/dp1: 404.
    # The menu has no "Classic GUI".
    # Commands and Terminal: the target bar has Test. With a stored
    # credential, and with a typed user name and password: each appliance
    # shows SOMA ok/failed and SSH ok/failed; hover for the reason. A
    # wrong password: SOMA failed and SSH failed, with the reason. An
    # unpinned SSH host key with a stored credential: SSH failed, "not
    # pinned". The link opens the job system.test-connectivity.
    # Change the password or the credential: the results go.
    # One command of each plugin from the Commands page, on the container:
    # accounts.list-users, backups.list-checkpoints, crypto.cert-file-audit,
    # deployment.get-file, developer.list-probes, network.list-host-aliases,
    # system.get-status; status: the Appliances page; ssh: the Terminal page.

# Phase D: the Terminal page

Two appliances: two containers, or one container registered twice
(`mast-appliances add -n dp-a ...` and `-n dp-b` with the same address
and ports), with SSH on. Pin both (`mast-appliances pin -n NAME --both`).

    # As a viewer: no Terminal in the menu; /terminal is 403.
    # As an operator: both in the target bar, user name and password,
    # Open session: two panes, connected. "show clock" goes to both.
    # Untick one: "show version" goes to the other only.
    # "ping 10.255.255.1": both panes show "running", then the answers.
    # A long command, then Ctrl-C: it stops.
    # "configure terminal" then "user-password": the input becomes a
    # password field; the job shows the line as ********.
    # Up and Down: the lines sent (not the password).
    # Reload the page: it attaches to the session, with its output.
    # Download transcript: no password in it.
    # Jobs page: the ssh.terminal job, running, with "> show clock" and
    # each answer. Disconnect: the job succeeded, with the transcript.
    # Open three sessions (three tabs); the fourth is refused.
    # Leave one idle for 15 minutes: it closes (its job says why).
    # As an admin, Cancel on the Jobs page closes a session.
    # As an admin, choose a stored credential of purpose change: it
    # opens only when both host keys are pinned; a collect credential
    # is refused. An operator does not see the stored credentials there.
    # mast-ssh -a dp-a --credential NAME: a job ssh.terminal with each
    # line; the credential's Uses list it.
    # Phone width, dark theme: tabs, one pane at a time.

# Phase C: the crypto inventory

The home of the Phase A test, with the credential collector. A domain
with crypto objects helps: `uv run python -c "from mast.datapower.datapower
import DataPower; from mast.testsuite.integration import crypto_lab;
crypto_lab.make_lab(DataPower('127.0.0.1', 'admin:PASSWORD', port=PORT,
check_hostname=False), 'cryptolab')"` makes one on a test appliance.

    ./mast-crypto inventory --credential collector
    # The Jobs page: the inventory, and a child job for each appliance
    # under it, each with its table (objects, certificates, files).
    ./mast-crypto export -f xlsx                  # open it: six sheets
    ./mast-crypto export -f cbom                  # a CycloneDX 1.6 JSON
    # In the files under tmp/: no password (grep for the passwords of
    # the appliance's keys and aliases finds nothing).
    # Cancel an inventory of several appliances: its children are
    # cancelled too.

The posture rules and the Crypto page (C-2), on the lab domain:

    ./mast-crypto posture
    # cert-expired on lab-old-cert, plaintext-password on lab-old-key,
    # unused-file on lab-unused.crt, not-saved on cryptolab until the
    # domain is saved. Save it, run the inventory again: not-saved goes.
    ./mast-crypto exception-add -r unused-file -p lab-unused.crt \
        -R "Kept for the manual test" -e 7d
    # As a viewer, /crypto: the score of each appliance, the expiry
    # chart, the self-checks. No "Run inventory now", no "Add exception".
    # As an operator: Run inventory now opens the job; the Overview
    # shows the new snapshot when it is done.
    # As an admin: Run it every day makes the schedule crypto-inventory.
    # Inventory: filter "lab", open lab-server-cert: what breaks shows
    # lab-idcred, lab-server-profile, lab-https.
    # Findings: the exception shows on lab-unused.crt (tick "Show the
    # accepted ones"); Add exception on another finding, then Remove it.
    # History: compare the two newest snapshots of the appliance.
    # The Appliances page: the "Nearest expiry" and "Crypto posture"
    # columns link to the Crypto page.
    # Firefox and Chromium, light and dark theme, a phone width.

PQC rules and the TLS probe (C-3). On a 10.6.4 to 11.0.0.1 test
appliance, set an SSL server profile's KEMAlgs to `X25519MLKEM768
classical` with TLS 1.2 and 1.3 on (set-config says OK; the profile
goes down), and another to `classical` with TLS 1.3 only:

    ./mast-crypto inventory --credential collector
    ./mast-crypto posture -a NAME
    # pqc-profile-down on the first, with "KEM algorithm selection is
    # only allowed in TLSv1.3"; pqc-no-hybrid on the second;
    # pqc-firmware on a 10.5 or 10.0 appliance; pqc-classical-certificate
    # (info) on the certificates.
    ./mast-crypto probe
    # With Python 3.15: the XML management of 10.6.4+ accepts
    # X25519MLKEM768; that of 10.0 or 2018.4 does not. On 2018.4: TLS 1.2
    # only. An appliance behind a port mapping is probed on the ports of
    # the registry.
    # /crypto: the PQC readiness card (firmware, profiles, peers); the
    # TLS probe tab, where a viewer has no button, an operator has
    # "Probe now" and an admin also "Probe every Sunday at 03:00".
    # Inventory: open the down profile: Status down with the error, and
    # "Key exchange" says the firmware takes it down.
    # Findings, pack CNSA 2.0: nothing until [packs] disabled: is empty
    # in etc/local/crypto.conf, then cnsa-key-exchange on each profile.

# Phase B: the status of the appliances

The home of the Phase A test, with the credential collector and a
worker running.

    # 1. Collect status for dp1 (Admin -> Appliances: Collect status, or)
    ./mast-appliances update -n dp1 --poll yes
    ./mast-status collect --credential collector  # a table: dp1 reachable

    # 2. Log in: the first page is Appliances. dp1 shows firmware, CPU,
    #    memory, temporary space, domains and objects down. As an admin,
    #    with no schedule yet, the page offers "Start polling": choose
    #    collector and every minute. A minute later "Seen just now".
    ./mast-jobs schedule-list                     # status-poll, */1

    # 3. Open the row of dp1: the domains, the objects that are down
    #    (enabled but not up), three charts, "Jobs for this appliance"
    #    (the Jobs page filtered) and "Run a command" (dp1 in the bar).

    # 4. A long job does not stop the polls: start a backup, and see a
    #    status.collect job start and end while it runs (its worker ends
    #    in /p1, the passive slot).
    ./mast-backups normal-backup -a dp1 --credential admin-cred -D all-domains --detach
    ./mast-jobs list -n 5

    # 5. Stop the appliance (or point dp1 at a wrong address): the row
    #    goes first, red, "Not reachable, last seen ..." with the reason
    #    on hover, and the job of the poll is partial. Pin a wrong
    #    fingerprint: "Certificate or host key changed".

    # 6. As a viewer: the same page, without the name of the credential
    #    and without "Start polling" or "Run a command".

    # 7. With Statistics disabled in default, the CPU is empty and the
    #    row says "Statistics off: no CPU"; enable it, and the next poll
    #    has the CPU.

    # 8. Dark theme and phone width: the bars and charts are readable.

    ./mast-jobs prune --dry-run                   # status samples, hourly, daily

# The job queue

These use the `lab` environment and `%MAST_CREDS%` from above. Run them
once with the default SQLite database, and once with PostgreSQL: put
`[database]` and `url = postgresql+psycopg://user:password@host/database`
in `etc\local\jobs.conf`.

    # No worker: the command does its own job, and the job is recorded
    mast-jobs workers
    mast-system list-domains -n -a lab -c %MAST_CREDS%
    mast-jobs list
    mast-jobs show -j <start of the id>
    mast-jobs show -j <start of the id> -H      # with the request history

    # Exit status 1 when an appliance cannot be reached: add an
    # unreachable appliance (mast-appliances add -n dp3 -A 192.0.2.1) to
    # the lab environment (env-set-members -n lab -m dp1 -m dp2 -m dp3).
    # The command stops with the error.
    mast-system save -n -a lab -c %MAST_CREDS% -t 5
    echo %ERRORLEVEL%                            # 1
    mast-jobs list -n 1                          # the job is failed

    # With a worker. In a second terminal:
    mast-worker
    mast-jobs workers
    mast-backups normal-backup -n -a lab -c %MAST_CREDS% -D all-domains -C "Queue test" -o tmp --detach
    mast-jobs follow -j <start of the id>

    # --local runs the job here although a worker is alive
    mast-system list-domains -n -a lab -c %MAST_CREDS% --local
    mast-jobs show -j <start of the id>          # its worker is not the mast-worker

    # Cancel: start a long command, then
    #   - press Ctrl-C once: the job is cancelled at its next step, exit status 130
    #   - or, from another terminal: mast-jobs cancel -j <start of the id>
    #   - or, in the web GUI: the Cancel link of the running job

    # A worker that is stopped twice: start a long command with --detach,
    # press Ctrl-C twice in the worker's terminal. The job becomes lost.
    mast-jobs show -j <start of the id>

    # Web GUI: stop mast-worker, submit a form, and see that the web server
    # does the job. Start mast-worker, submit a form, and see that the
    # worker does it (mast-jobs show).

# The secret key of the web GUI

    # A fresh install has no etc\local\server.key. Start mast-web: the
    # file is made (on Linux, mode 600). Restart mast-web: the file and
    # its key stay the same.
    # Put secret_key = INSECURE_SECRET_KEY_CHANGE_ME in the [server]
    # section of etc\local\server.conf and start mast-web: var\log\gui.log
    # warns that the key is ignored, and server.key is still used.

# Run the installer with no internet access (Linux, podman)

A jump server has no internet access, and the installer must work there.
This test runs the unzipped installer and a DataPower container on a
podman network that has no route out. A rootless container that uses
`pasta` (such as `mast-test-keep` of the integration tests) cannot join
that network, so start a second DataPower on it. Its management
configuration is the one the integration tests use.

    mkdir unzipped && unzip -q dist/MAST-*_linux.zip -d unzipped
    podman network create --internal mast-offline
    uv run python -c "from mast.testsuite.integration import container as c; open('auto-startup.cfg', 'w').write(c.STARTUP_CONFIG); print(c.CONFIG_DIR)"
    podman create --name mast-offline-dp --network mast-offline \
        --env DATAPOWER_ACCEPT_LICENSE=true icr.io/cpopen/datapower/datapower-limited:11.0.0.3
    podman cp auto-startup.cfg mast-offline-dp:<CONFIG_DIR printed above>/auto-startup.cfg
    podman start mast-offline-dp
    podman inspect mast-offline-dp --format '{{(index .NetworkSettings.Networks "mast-offline").IPAddress}}'
    podman run --rm -it --name mast-offline-client --network mast-offline \
        -v ./unzipped:/opt/mast:Z almalinux:8-minimal bash

DataPower takes a few minutes to start. Inside the client container:

    # The network must really be offline. This must fail:
    /opt/mast/mast -c "import urllib.request; urllib.request.urlopen('https://pypi.org', timeout=10)"

    # Use the address that podman inspect printed
    /opt/mast/mast-appliances add -n dp1 -A <address>
    /opt/mast/mast-appliances env-add -n lab -m dp1
    MAST_CREDS=admin:admin

    /opt/mast/mast-version
    /opt/mast/test-mast --unit
    /opt/mast/mast-system list-domains -a lab -c $MAST_CREDS -n
    /opt/mast/mast-backups normal-backup -a lab -c $MAST_CREDS -n -D default -C "Offline test" -o tmp
    # Listen on the network, without TLS (there is no certificate in a fresh install)
    printf '[server]\nhost = 0.0.0.0\nsecure = false\n' > /opt/mast/etc/local/server.conf
    /opt/mast/mast-web &

From a second container on the same network, the web GUI must answer:

    podman run --rm --network mast-offline almalinux:8-minimal \
        curl -s -o /dev/null -w '%{http_code}\n' http://mast-offline-client:5000/

Clean up:

    podman rm -f mast-offline-dp
    podman network rm mast-offline
