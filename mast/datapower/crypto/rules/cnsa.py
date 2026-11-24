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
_module_: `mast.datapower.crypto.rules.cnsa`

The CNSA 2.0 pack, for the systems that must follow the NSA's
Commercial National Security Algorithm Suite 2.0. It is off unless
`crypto.conf` turns it on (`[packs] disabled` without `cnsa`).

CNSA 2.0 asks for ML-KEM-1024 for key establishment and ML-DSA-87 for
signatures. For web servers and cloud services it is preferred from
`[cnsa] prefer_year` (2025) and the only choice from `exclusive_year`
(2033).
"""
from . import Finding, rule
from .pqc import PROFILES, _groups

_KEX = ("MLKEM1024", "SecP384r1MLKEM1024")


def _severity(view):
    year = view.now.year
    if year >= view.settings.get("cnsa", "exclusive_year"):
        return "high"
    if year >= view.settings.get("cnsa", "prefer_year"):
        return "medium"
    return "low"


@rule("cnsa-key-exchange", "medium", "No CNSA 2.0 key exchange", pack="cnsa")
def key_exchange(view):
    """A server or client profile that cannot negotiate ML-KEM-1024
    (MLKEM1024, or the hybrid SecP384r1MLKEM1024). Medium from [cnsa]
    prefer_year, high from exclusive_year."""
    for obj in view.crypto(*PROFILES):
        groups = _groups(view, obj)
        if groups.down or any(g in groups.tls13 for g in _KEX):
            continue
        yield Finding(
            obj=obj, severity=_severity(view),
            message="{} cannot negotiate ML-KEM-1024 ({}).".format(
                obj.name, groups.reason),
            remediation="On 10.6.4 or newer, put SecP384r1MLKEM1024 (or "
                        "MLKEM1024) in the KEMAlgs of {}, with TLS 1.3 "
                        "only.".format(obj.name))


@rule("cnsa-signature", "info", "No CNSA 2.0 signature", pack="cnsa")
def signature(view):
    """A certificate that an identification credential presents, whose
    key is not ML-DSA-87. Info until [cnsa] exclusive_year: DataPower
    cannot authenticate TLS with ML-DSA yet (11.0.0.3)."""
    exclusive = view.settings.get("cnsa", "exclusive_year")
    for obj in view.crypto("CryptoCertificate"):
        cert = view.certs.get(obj.id)
        if cert is None or "ML-DSA-87" in (cert.key_alg or "").upper():
            continue
        if not any(u.class_ == "CryptoIdentCred"
                   for u in view.users(obj, via="Certificate")):
            continue
        yield Finding(
            obj=obj,
            severity="high" if view.now.year >= exclusive else "info",
            message="{} has an {} key, not ML-DSA-87.".format(
                cert.subject or obj.name,
                "{} {}".format(cert.key_alg or "", cert.key_bits or "")
                .strip() or "unknown"),
            remediation="Replace it with an ML-DSA-87 certificate when the "
                        "firmware can use one for TLS.")
