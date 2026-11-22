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
