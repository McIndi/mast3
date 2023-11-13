These tests assume that you have two DataPowers, one at 192.168.0.10
and one at 192.168.0.20. 

# Add to hosts.conf
[hosts]
dp1: 192.168.0.10
dp2: 192.168.0.20

# Add to environments.conf
[lab]
appliances: dp1 dp2


# test that mast-ssh works on the command line

set MAST_CREDS=username:password
set XOR_MAST_CREDS=<from mast-system xor>

# Try ssh
mast-ssh -a lab -c %MAST_CREDS% -d default
mast-ssh -a lab -c %XOR_MAST_CREDS% -d default

# Issue a couple of commands:
show mem
show cpu
show web-mgmt
...

# Create a directory
mast-system create-dir -a lab -c %MAST_CREDS% -n -D default -d local:///20231113/
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
