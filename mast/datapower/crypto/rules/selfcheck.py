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
_module_: `mast.datapower.crypto.rules.selfcheck`

The `mast` pack: checks of how MAST itself is set up, from the registry
and the stored credentials. They are computed each time they are shown.
"""
from datetime import timedelta, timezone

from . import Finding, check


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@check("mast-tls-none", "high", "Appliance certificate not checked")
def tls_none(setup):
    """A registered appliance with `tls_mode none`: MAST does not check
    the certificate of its management interface, so anyone in between
    could read the credentials that MAST sends."""
    for appliance in setup.appliances:
        if appliance.tls_mode == "none":
            yield Finding(
                subject=appliance.name, appliance=appliance.name,
                message="MAST does not check the TLS certificate of {} "
                        "(tls_mode none).".format(appliance.name),
                remediation="Pin its certificate (Admin → Appliances → Pin) "
                            "or give a CA bundle that validates it.")


@check("mast-collect-can-write", "high", "Collect credential can write")
def collect_can_write(setup):
    """A stored credential for collecting whose DataPower user could
    change the appliances at its last test. A collect credential should
    only read."""
    for credential in setup.credentials:
        if credential.purpose == "collect" and credential.can_write:
            yield Finding(
                subject=credential.name, name=credential.name,
                message="The stored credential {} (user {}) is for "
                        "collecting, but it could change the appliances at "
                        "its last test.".format(credential.name,
                                                credential.username),
                remediation="Give it a DataPower user that may only read "
                            "(an access policy of */*/*?Access=r), then test "
                            "it again.")


@check("mast-collect-untested", "low", "Collect credential not tested")
def collect_untested(setup):
    """A stored credential for collecting that was never tested, or
    whose last test could not tell whether its user can change the
    appliances. Until a test says so, mast-collect-can-write cannot see
    a collect credential with an admin user."""
    for credential in setup.credentials:
        if credential.purpose != "collect" or credential.can_write is not None:
            continue
        yield Finding(
            subject=credential.name, name=credential.name,
            message="The stored credential {} (user {}) is for collecting, "
                    "{}, so MAST does not know whether its user can change "
                    "the appliances.".format(
                        credential.name, credential.username,
                        "and its last test did not work"
                        if credential.last_tested_at else
                        "and it was never tested"),
            remediation="Test it (Admin → Credentials → Test, or "
                        "mast-credentials test -n {} -a APPLIANCE).".format(
                            credential.name))


@check("mast-credential-unused", "low", "Credential not used")
def credential_unused(setup):
    """A stored credential that no job used for `[credentials]
    unused_days` (180), or never in that long since it was made."""
    days = setup.settings.get("credentials", "unused_days")
    cutoff = setup.now - timedelta(days=days)
    for credential in setup.credentials:
        last = _aware(credential.last_used_at)
        if (last or _aware(credential.created_at)) > cutoff:
            continue
        yield Finding(
            subject=credential.name, name=credential.name,
            message="No job used the stored credential {} {}.".format(
                credential.name,
                "since {}".format(last.strftime("%Y-%m-%d")) if last
                else "since it was made"),
            remediation="Delete it if it is no longer needed: an unused "
                        "password is one more to guard.")
