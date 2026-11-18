These tests assume that you have two DataPowers, one at 192.168.0.10
and one at 192.168.0.20. 

# To do a fresh build

#   The build needs uv (https://docs.astral.sh/uv/) and Python 3.11 or newer
python mast-setup

# Installation file will be in dist directory
# For testing or review, the contents of the installation file can be found in build/assemble
cd build\assemble

cd ..\..\ && python mast-setup && cd build\assemble

# Add to hosts.conf
[hosts]
dp1: 192.168.0.10
dp2: 192.168.0.20

# Add to environments.conf
[lab]
appliances: dp1 dp2

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
mast-accounts add-user -n -a dev -c %MAST_CREDS% -u priv_user -p pa55word -P -s

# Force change password
mast-accounts force-change-password -n -a lab -c %XOR_MAST_CREDS% -U new_user

# List domains
mast-system list-domains -n -a lab -c %MAST_CREDS%

# Add domain
mast-system add-domain -n -a lab -c %MAST_CREDS% -d new_domain -s

# Import configuration
mast-developer import -a dev -c %MAST_CREDS% -D new_domain -f ~/Downloads/export.zip

# Save a domain
mast-system save -n -a lab -c %MAST_CREDS% -D new_domain

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
    printf '[hosts]\ndp1: <address>\n' > /opt/mast/etc/local/hosts.conf
    printf '[lab]\nappliances: dp1\n' > /opt/mast/etc/local/environments.conf
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
