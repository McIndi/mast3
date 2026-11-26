# -*- coding: utf-8 -*-
# This file is part of McIndi's Automated Solutions Tool (MAST).
#
# MAST is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3
# as published by the Free Software Foundation.
#
# MAST is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MAST.  If not, see <https://www.gnu.org/licenses/>.
#
# Copyright 2015-2026, McIndi Solutions, All rights reserved.
"""
_module_: `mast.v3`

The names of MAST 3.x, and what they are in 4.0. The import of a 3.x
installation (design step 13) and the migration notes use this.

* `COMMANDS`: each command id of 3.x and the id of the same command in
4.0. Thirteen 3.x commands were the same SOMA action in two namespaces
(`accounts.flush-aaa-cache` and `system.flush-aaa-cache`, for example):
both map to the one 4.0 command.
* `PARAMETERS`: the parameters whose name changed, by 3.x command id.
* `LAUNCHERS`: each launcher of 3.x and the root of `mast` that has its
commands. Some commands also changed their name; `COMMANDS` has those.
"""

__all__ = ["COMMANDS", "PARAMETERS", "LAUNCHERS", "command"]

COMMANDS = {
    "accounts.add-group": "datapower.add-group",
    "accounts.add-rbm-fallback": "datapower.add-rbm-fallback",
    "accounts.add-user": "datapower.add-user",
    "accounts.change-password": "datapower.change-password",
    "accounts.del-group": "datapower.del-group",
    "accounts.del-rbm-fallback": "datapower.del-rbm-fallback",
    "accounts.del-user": "datapower.del-user",
    "accounts.flush-aaa-cache": "datapower.flush-aaa-cache",
    "accounts.flush-ldap-pool-cache": "datapower.flush-ldap-pool-cache",
    "accounts.flush-rbm-cache": "datapower.flush-rbm-cache",
    "accounts.force-change-password": "datapower.force-change-password",
    "accounts.list-groups": "datapower.list-groups",
    "accounts.list-rbm-fallback": "datapower.list-rbm-fallback",
    "accounts.list-users": "datapower.list-users",
    "backups.list-checkpoints": "datapower.list-checkpoints",
    "backups.normal-backup": "datapower.normal-backup",
    "backups.remove-checkpoint": "datapower.remove-checkpoint",
    "backups.restore-normal-backup": "datapower.restore-normal-backup",
    "backups.restore-secure-backup": "datapower.restore-secure-backup",
    "backups.rollback-checkpoint": "datapower.rollback-checkpoint",
    "backups.secure-backup": "datapower.secure-backup",
    "backups.set-checkpoint": "datapower.set-checkpoint",
    "crypto.cert-audit": "datapower.cert-audit",
    "crypto.cert-file-audit": "datapower.cert-file-audit",
    "crypto.exception-add": "core.crypto-exception-add",
    "crypto.exception-remove": "core.crypto-exception-remove",
    "crypto.exceptions": "core.crypto-exceptions",
    "crypto.export": "core.crypto-export",
    "crypto.export-certs": "datapower.export-certs",
    "crypto.inventory": "core.crypto-inventory",
    "crypto.inventory-appliance": "datapower.collect-crypto",
    "crypto.posture": "core.crypto-posture",
    "crypto.probe": "core.crypto-probe",
    "crypto.rules": "core.crypto-rules",
    "deployment.add-password-map-alias": "datapower.add-password-map-alias",
    "deployment.clean-up": "datapower.clean-up",
    "deployment.del-file": "datapower.del-file",
    "deployment.del-password-map-alias": "datapower.del-password-map-alias",
    "deployment.deploy": "datapower.deploy",
    "deployment.get-file": "datapower.get-file",
    "deployment.git-deploy": "datapower.git-deploy",
    "deployment.postdeploy": "datapower.postdeploy",
    "deployment.predeploy": "datapower.predeploy",
    "deployment.set-file": "datapower.set-file",
    "developer.export": "datapower.export",
    "developer.flush-document-cache": "datapower.flush-document-cache",
    "developer.flush-stylesheet-cache": "datapower.flush-stylesheet-cache",
    "developer.import": "datapower.import",
    "developer.list-probes": "datapower.list-probes",
    "network.add-host-alias": "datapower.add-host-alias",
    "network.add-secondary-address": "datapower.add-secondary-address",
    "network.add-static-host": "datapower.add-static-host",
    "network.add-static-route": "datapower.add-static-route",
    "network.check-connectivity": "datapower.check-connectivity",
    "network.del-host-alias": "datapower.del-host-alias",
    "network.del-secondary-address": "datapower.del-secondary-address",
    "network.del-static-host": "datapower.del-static-host",
    "network.del-static-route": "datapower.del-static-route",
    "network.display-ethernet-interface": "datapower.display-ethernet-interface",
    "network.display-routing-table": "datapower.display-routing-table",
    "network.flush-arp-cache": "datapower.flush-arp-cache",
    "network.flush-dns-cache": "datapower.flush-dns-cache",
    "network.flush-nd-cache": "datapower.flush-nd-cache",
    "network.flush-pdp-cache": "datapower.flush-pdp-cache",
    "network.list-host-aliases": "datapower.list-host-aliases",
    "network.list-secondary-addresses": "datapower.list-secondary-addresses",
    "network.list-static-hosts": "datapower.list-static-hosts",
    "network.list-static-routes": "datapower.list-static-routes",
    "network.tcp-connection-test": "datapower.tcp-connection-test",
    "ssh.terminal": "datapower.terminal",
    "status.collect": "datapower.collect-status",
    "system.add-domain": "datapower.add-domain",
    "system.check-credentials": "datapower.check-credentials",
    "system.clean-up": "datapower.clean-up",
    "system.copy-directory": "datapower.copy-directory",
    "system.copy-file": "datapower.copy-file",
    "system.create-dir": "datapower.create-dir",
    "system.del-domain": "datapower.del-domain",
    "system.del-file": "datapower.del-file",
    "system.disable-domain": "datapower.disable-domain",
    "system.enable-domain": "datapower.enable-domain",
    "system.fetch-logs": "datapower.fetch-logs",
    "system.firmware-upgrade": "datapower.firmware-upgrade",
    "system.flush-aaa-cache": "datapower.flush-aaa-cache",
    "system.flush-arp-cache": "datapower.flush-arp-cache",
    "system.flush-dns-cache": "datapower.flush-dns-cache",
    "system.flush-document-cache": "datapower.flush-document-cache",
    "system.flush-ldap-pool-cache": "datapower.flush-ldap-pool-cache",
    "system.flush-nd-cache": "datapower.flush-nd-cache",
    "system.flush-nss-cache": "datapower.flush-nss-cache",
    "system.flush-pdp-cache": "datapower.flush-pdp-cache",
    "system.flush-rbm-cache": "datapower.flush-rbm-cache",
    "system.flush-stylesheet-cache": "datapower.flush-stylesheet-cache",
    "system.generate-error-report": "datapower.generate-error-report",
    "system.get-config": "datapower.get-config",
    "system.get-encrypted-filesystem": "datapower.get-encrypted-filesystem",
    "system.get-error-reports": "datapower.get-error-reports",
    "system.get-file": "datapower.get-file",
    "system.get-filestore": "datapower.get-filestore",
    "system.get-pmr-info": "datapower.get-pmr-info",
    "system.get-status": "datapower.get-status",
    "system.get-temporary-filesystem": "datapower.get-temporary-filesystem",
    "system.list-domains": "datapower.list-domains",
    "system.list-objects": "datapower.list-objects",
    "system.object-audit": "datapower.object-audit",
    "system.quiesce-appliance": "datapower.quiesce-appliance",
    "system.quiesce-domain": "datapower.quiesce-domain",
    "system.quiesce-service": "datapower.quiesce-service",
    "system.reboot-appliance": "datapower.reboot-appliance",
    "system.reload-appliance": "datapower.reload-appliance",
    "system.restart-domain": "datapower.restart-domain",
    "system.save": "datapower.save",
    "system.set-file": "datapower.set-file",
    "system.shutdown-appliance": "datapower.shutdown-appliance",
    "system.test-connectivity": "datapower.test-connectivity",
    "system.unquiesce-appliance": "datapower.unquiesce-appliance",
    "system.unquiesce-domain": "datapower.unquiesce-domain",
    "system.unquiesce-service": "datapower.unquiesce-service",
    "system.xor": "datapower.xor",
}

PARAMETERS = {
    # network.flush-pdp-cache had XACMLPDP, system.flush-pdp-cache
    # XACML_PDP. The one 4.0 command has XACMLPDP.
    "system.flush-pdp-cache": {"XACML_PDP": "XACMLPDP"},
}

LAUNCHERS = {
    "mast-accounts": "mast datapower",
    "mast-appliances": "mast targets (env-* commands: mast fleets)",
    "mast-backups": "mast datapower",
    "mast-credentials": "mast credentials",
    "mast-crypto": "mast crypto (cert-audit, cert-file-audit, export-certs "
                   "and inventory-appliance: mast datapower)",
    "mast-deployment": "mast datapower",
    "mast-developer": "mast datapower",
    "mast-jobs": "mast jobs (schedule-* commands: mast schedules)",
    "mast-network": "mast datapower",
    "mast-ssh": "mast datapower ssh",
    "mast-status": "mast datapower collect-status",
    "mast-system": "mast datapower",
    "mast-users": "mast users",
    "mast-version": "mast version",
    "mast-web": "mast web",
    "mast-worker": "mast worker",
    "mastd": "mast daemon",
    "test-mast": "mast selftest",
    "mast": "mast-python (the interpreter)",
}


def command(command_id):
    """
    _function_: `mast.v3.command(command_id)`

    Return the 4.0 id of the 3.x command `command_id`, or `None` if 4.0
    has no such command.
    """
    return COMMANDS.get(command_id)
