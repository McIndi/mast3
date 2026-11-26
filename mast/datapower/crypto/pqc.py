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
_module_: `mast.datapower.crypto.pqc`

What a DataPower TLS profile can negotiate for key exchange, from its
firmware, its `KEMAlgs` and its `Protocols`. IBM's documentation is not
enough for this: the rules here come from tests on `datapower-limited`
containers of every level from 10.5.0.23 to 11.0.0.3 (2026-11-21; the
raw results are in `mast/testsuite/data/pqc-research/`, and the tests
of this module check every one of them).

What the tests found:

* **10.5 and older, and 10.6.0.x before 10.6.0.11:** classical groups
only. `KEMAlgs` is refused.
* **10.6.0.11:** the default (no `KEMAlgs`) negotiates `X25519MLKEM768`
with a client that offers it, and classical groups with the others.
`KEMAlgs` is refused, so this cannot be changed.
* **10.6.2 and 10.6.3 (technology preview):** the default is classical
only. `KEMAlgs` is accepted, with `X25519MLKEM768`, `classical` and the
preview names (`mlkem768`, `x448_mlkem768`, `p384_mlkem1024`...) that no
standard client can negotiate.
* **10.6.4 and newer:** the default negotiates `X25519MLKEM768`. `KEMAlgs`
takes the standard names (`X25519MLKEM768`, `SecP256r1MLKEM768`,
`SecP384r1MLKEM1024`, `MLKEM768`...) and `classical`.
* **`KEMAlgs` and TLS 1.2:** from 10.6.2 to 11.0.0.1, a profile with
`KEMAlgs` and TLS 1.2 (or older) on goes down ("KEM algorithm selection
is only allowed in TLSv1.3"), although set-config says OK. From 11.0.0.2
it is allowed: TLS 1.2 uses the classical groups if `KEMAlgs` has
`classical`, and is refused if it does not.
* A name that the firmware refuses is not stored: the profile keeps
what it had. So what get-config returns is what counts.
* 11.0.0.3 returns `classical` as the list of the classical groups
(`brainpoolP512` ... `x25519`); 10.6.x returns the word.

A probe of the port (`mast crypto probe`) is the final word.
"""
import re
from dataclasses import dataclass

__all__ = ["HYBRID", "PURE", "CLASSICAL", "Groups", "effective_groups",
           "kem_algs_of", "normalize", "firmware_support", "is_preview",
           "LEVELS"]

# The hybrid groups: a classical exchange and ML-KEM together
HYBRID = ("X25519MLKEM768", "SecP256r1MLKEM768", "SecP384r1MLKEM1024")
# ML-KEM alone
PURE = ("MLKEM512", "MLKEM768", "MLKEM1024")
# The word for every classical group
CLASSICAL = "classical"
# The classical groups, as 11.0.0.3 spells them when it expands
# `classical` (and as OpenSSL names them)
_CLASSICAL_NAMES = re.compile(
    r"^(brainpool\w*|ffdhe\d+|x25519|x448|p-?\d+|secp\d+[rk]1|"
    r"prime\d+v\d|sect\d+\w*)$", re.I)
# The preview names of 10.6.2 and 10.6.3: lower case, with mlkem in them
_PREVIEW = re.compile(r"^[a-z0-9_]*mlkem\d+[a-z0-9_]*$")

_OLDER = ("TLSv1d2", "TLSv1d1", "TLSv1d0", "SSLv3")

# The firmware levels (`firmware_version` tuples)
_CONTROL = (10, 6, 2, 0)
_STANDARD = (10, 6, 4, 0)
_LTS_DEFAULT = (10, 6, 0, 11)
_TLS12_ALLOWED = (11, 0, 0, 2)

# What `firmware_support` says, from the best
LEVELS = ("control", "preview", "default-only", "none", "unknown")


def is_preview(name):
    """A preview name of 10.6.2/10.6.3 (`mlkem768`, `x448_mlkem768`)."""
    return bool(_PREVIEW.match(name or ""))


def normalize(names):
    """The names of `KEMAlgs` as a tuple in their order, with every
    classical group as one `classical`."""
    found = []
    for name in names or ():
        name = (name or "").strip()
        if not name:
            continue
        if name.lower() == CLASSICAL or _CLASSICAL_NAMES.match(name):
            name = CLASSICAL
        if name not in found:
            found.append(name)
    return tuple(found)


def kem_algs_of(attrs):
    """The `KEMAlgs` of the `attrs` of a profile, normalized."""
    value = (attrs or {}).get("KEMAlgs")
    if value is None:
        return ()
    if not isinstance(value, list):
        value = [value]
    return normalize(v if isinstance(v, str) else "" for v in value)


def _version(firmware):
    from .inventory import firmware_version
    if isinstance(firmware, tuple):
        return firmware
    return firmware_version(firmware)


def firmware_support(firmware):
    """
    _function_: `mast.datapower.crypto.pqc.firmware_support(firmware)`

    What the firmware can do about PQC key exchange (one of `LEVELS`):

    * `control`: 10.6.4 and newer; `KEMAlgs` with the standard names,
    and the default negotiates `X25519MLKEM768`.
    * `preview`: 10.6.2 and 10.6.3; `KEMAlgs` with the preview names and
    `X25519MLKEM768`, and the default is classical.
    * `default-only`: 10.6.0.11; the default negotiates `X25519MLKEM768`
    and cannot be changed.
    * `none`: older; classical only.
    * `unknown`: no version.
    """
    version = _version(firmware)
    if version is None:
        return "unknown"
    if version >= _STANDARD:
        return "control"
    if version >= _CONTROL:
        return "preview"
    if _LTS_DEFAULT <= version < (10, 6, 1, 0):
        return "default-only"
    return "none"


@dataclass(frozen=True)
class Groups(object):
    """
    _class_: `mast.datapower.crypto.pqc.Groups`

    What a profile can negotiate:

    * `tls13`: the groups of TLS 1.3, `classical` for every classical
    group (empty if TLS 1.3 is off or the profile is down).
    * `tls12`: TLS 1.2 (or older) works, with classical groups.
    * `down`: the firmware takes the profile down for this
    configuration.
    * `configured`: `KEMAlgs` is set and the firmware uses it.
    * `reason`: why, in words.
    """
    tls13: frozenset
    tls12: bool
    down: bool
    configured: bool
    reason: str

    @property
    def hybrid(self):
        """The hybrid groups it negotiates, in the order of `HYBRID`."""
        return tuple(g for g in HYBRID if g in self.tls13)

    @property
    def classical(self):
        """A classical client can connect (TLS 1.3 or older)."""
        return CLASSICAL in self.tls13 or self.tls12


def effective_groups(firmware, kem_algs, protocols):
    """
    _function_: `mast.datapower.crypto.pqc.effective_groups(firmware,
    kem_algs, protocols)`

    The `Groups` of a profile: `firmware` (a name such as
    `IDG.10.6.4.0`, or a `firmware_version` tuple), `kem_algs` (its
    `KEMAlgs`, as get-config returns them; any order, either spelling
    of `classical`) and `protocols` (its `Protocols`: `{"TLSv1d3": "on",
    ...}`).
    """
    support = firmware_support(firmware)
    version = _version(firmware)
    kem = normalize(kem_algs)
    protocols = protocols if isinstance(protocols, dict) else {}
    tls13 = protocols.get("TLSv1d3") == "on"
    older = any(protocols.get(key) == "on" for key in _OLDER)
    configured = bool(kem) and support in ("control", "preview")
    if configured and older and version < _TLS12_ALLOWED:
        return Groups(frozenset(), False, True, True,
                      "KEMAlgs is set and TLS 1.2 or older is on: this "
                      "firmware takes the profile down")
    # From 11.0.0.2, TLS 1.2 takes the classical groups of KEMAlgs: a
    # list without classical refuses all of TLS 1.2
    tls12 = older and (not configured or CLASSICAL in kem)
    if not tls13:
        return Groups(frozenset(), tls12, False, configured,
                      "TLS 1.3 is off")
    if configured:
        return Groups(frozenset(kem), tls12, False, True,
                      "KEMAlgs: " + ", ".join(kem))
    if support in ("control", "default-only"):
        return Groups(frozenset(("X25519MLKEM768", CLASSICAL)), older, False,
                      False, "the default of this firmware")
    return Groups(frozenset((CLASSICAL,)), older, False, False,
                  "the default of this firmware is classical only"
                  if support == "preview" else
                  "this firmware has classical key exchange only")
