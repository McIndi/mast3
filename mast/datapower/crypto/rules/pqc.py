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
_module_: `mast.datapower.crypto.rules.pqc`

The PQC pack: readiness for post-quantum cryptography.

* **Key exchange** is urgent: traffic recorded today can be decrypted
when a quantum computer can break the classical exchange ("harvest
now, decrypt later"). The rules judge a TLS profile by what it can
negotiate (`mast.datapower.crypto.pqc.effective_groups`, from its
firmware, `KEMAlgs` and `Protocols`), not by `KEMAlgs` alone, and by
what the TLS probe saw.
* **Authentication** (the keys of the certificates) is not urgent: a
signature cannot be forged after the fact. The dates of NIST IR 8547
are in `crypto.conf`.
"""
from datetime import datetime, timezone

from .. import pqc
from . import Finding, rule

# The profiles that have KEMAlgs
PROFILES = ("SSLServerProfile", "SSLClientProfile")
_ALL_PROFILES = PROFILES + ("SSLSNIServerProfile", "SSLProxyProfile")
_UPGRADE = "Upgrade the firmware to 10.6.4 or newer"
# The offers of the probe that are groups of TLS 1.3
_PROBE_GROUPS = pqc.HYBRID + ("MLKEM768",)


def _groups(view, obj):
    attrs = obj.attrs or {}
    return pqc.effective_groups(view.firmware, pqc.kem_algs_of(attrs),
                                attrs.get("Protocols"))


def _profiles(view):
    """The enabled server and client profiles that are up (or whose
    state is not known), with their `Groups`."""
    for obj in view.crypto(*PROFILES):
        if obj.op_state == "down":
            continue
        groups = _groups(view, obj)
        if not groups.down:
            yield obj, groups


def _side(obj):
    return "clients" if obj.class_ == "SSLServerProfile" else "servers"


@rule("pqc-no-hybrid", "high", "No hybrid PQC key exchange", pack="pqc")
def no_hybrid(view):
    """A server or client profile with TLS 1.3 on that can negotiate
    none of X25519MLKEM768, SecP256r1MLKEM768 and SecP384r1MLKEM1024:
    KEMAlgs without them (classical only), or firmware whose default is
    classical (before 10.6.4, apart from 10.6.0.11). Its traffic can be
    recorded now and decrypted later."""
    for obj, groups in _profiles(view):
        if not groups.tls13 or groups.hybrid:
            continue
        support = pqc.firmware_support(view.firmware)
        if groups.configured:
            remediation = ("Put X25519MLKEM768 first in the KEMAlgs of {}, "
                           "then classical, so that the {} that can use "
                           "it do.".format(obj.name, _side(obj)))
            if support == "preview":
                remediation += (" {} first: this firmware has only the "
                                "preview of ML-KEM.".format(_UPGRADE))
        elif support == "preview":
            remediation = ("{}: its default negotiates X25519MLKEM768. Or "
                           "set the KEMAlgs of {} to X25519MLKEM768 then "
                           "classical, with TLS 1.3 only.".format(
                               _UPGRADE, obj.name))
        else:
            remediation = ("{}: its default negotiates X25519MLKEM768, and "
                           "KEMAlgs can choose the groups.".format(_UPGRADE))
        yield Finding(
            obj=obj,
            message="{} cannot negotiate a hybrid PQC group ({}), so its "
                    "key exchange is classical only; {}.".format(
                        obj.name, groups.reason, view.describe_users(obj)),
            remediation=remediation)


@rule("pqc-tls13-off", "high", "TLS 1.3 off: no PQC possible", pack="pqc")
def tls13_off(view):
    """A server or client profile with TLS 1.3 off. Post-quantum key
    exchange exists only in TLS 1.3, so nothing in KEMAlgs can apply."""
    for obj in view.crypto(*PROFILES):
        protocols = (obj.attrs or {}).get("Protocols")
        if isinstance(protocols, dict) and "TLSv1d3" in protocols \
                and protocols["TLSv1d3"] != "on":
            yield Finding(
                obj=obj,
                message="TLS 1.3 is off in {}, so it can never use "
                        "post-quantum key exchange; {}.".format(
                            obj.name, view.describe_users(obj)),
                remediation="Turn on TLS 1.3 in the Protocols of {}, after "
                            "checking that its peers have it.".format(
                                obj.name))


@rule("pqc-only", "medium", "PQC-only key exchange", pack="pqc")
def pqc_only(view):
    """A profile whose KEMAlgs has no classical: it refuses every peer
    that cannot use one of its PQC groups, and all of TLS 1.2."""
    for obj, groups in _profiles(view):
        if not groups.configured or not groups.tls13 or \
                pqc.CLASSICAL in groups.tls13:
            continue
        protocols = (obj.attrs or {}).get("Protocols") or {}
        tls12 = isinstance(protocols, dict) and protocols.get("TLSv1d2") == "on"
        yield Finding(
            obj=obj,
            message="The KEMAlgs of {} ({}) have no classical, so it refuses "
                    "the {} that do not offer one of these groups{}; "
                    "{}.".format(obj.name, ", ".join(sorted(groups.tls13)),
                                 _side(obj),
                                 ", and all of TLS 1.2 although it is on"
                                 if tls12 else "",
                                 view.describe_users(obj)),
            remediation="Add classical at the end of the KEMAlgs of {}, "
                        "unless every peer is known to support the PQC "
                        "groups (then add an exception).".format(obj.name))


@rule("pqc-profile-down", "high", "TLS profile down", pack="pqc")
def profile_down(view):
    """A TLS profile that ObjectStatus shows down, for any reason. One
    reason is KEMAlgs with TLS 1.2 on, on firmware from 10.6.2 to
    11.0.0.1: set-config says OK, and the profile goes down. When the
    state is not known (an older snapshot), a profile that the
    firmware would take down. The services that use it refuse
    connections."""
    for obj in view.crypto(*_ALL_PROFILES):
        if obj.op_state == "down":
            why = obj.status_error or "no reason given"
        elif obj.op_state is None and obj.class_ in PROFILES and \
                _groups(view, obj).down:
            why = "not read; the configuration would take it down " \
                  "on this firmware"
        else:
            continue
        kem = "KEM algorithm selection" in why or "configuration would" in why
        yield Finding(
            obj=obj,
            message="{} {} is down ({}); {}, and they refuse "
                    "connections.".format(obj.class_, obj.name, why,
                                          view.describe_users(obj)),
            remediation=("Turn off TLS 1.2 (and older) in the Protocols of "
                         "{}, or clear its KEMAlgs. From 11.0.0.2, KEMAlgs "
                         "and TLS 1.2 can be on together.".format(obj.name)
                         if kem else
                         "Look at the event log of the domain {} for why, "
                         "and correct the profile.".format(obj.domain)))


@rule("pqc-preview-names", "medium", "Preview ML-KEM names", pack="pqc")
def preview_names(view):
    """A profile whose KEMAlgs has the names of the technology preview
    of 10.6.2 and 10.6.3 (mlkem768, x448_mlkem768, p384_mlkem1024...).
    Standard clients cannot negotiate them."""
    for obj in view.crypto(*PROFILES):
        names = [n for n in pqc.kem_algs_of(obj.attrs) if pqc.is_preview(n)]
        if names:
            yield Finding(
                obj=obj,
                message="The KEMAlgs of {} have the preview names {}, which "
                        "standard TLS libraries (OpenSSL 3.5) cannot "
                        "negotiate.".format(obj.name, ", ".join(names)),
                remediation="{}, and use the standard names "
                            "(X25519MLKEM768, then classical).".format(
                                _UPGRADE))


@rule("pqc-firmware", "medium", "Firmware without PQC control", pack="pqc")
def firmware(view):
    """Firmware that cannot choose its PQC key exchange: 10.5 and older,
    and 10.6.0.x (10.6.0.11 negotiates X25519MLKEM768 by default, but
    cannot be changed; it is low). 10.6.2 and 10.6.3 have only the
    preview (low)."""
    support = pqc.firmware_support(view.firmware)
    if support in ("control", "unknown"):
        return
    texts = {
        "none": ("medium", "has classical key exchange only, and KEMAlgs "
                           "cannot be set"),
        "default-only": ("low", "negotiates X25519MLKEM768 by default, but "
                                "KEMAlgs cannot be set, so it cannot be "
                                "turned on or off by profile"),
        "preview": ("low", "has only the technology preview of ML-KEM: its "
                           "default is classical, and the names are not "
                           "the standard ones"),
    }
    severity, text = texts[support]
    name = view.appliance_name or "the appliance"
    yield Finding(
        subject=name, name=name, severity=severity,
        message="{} runs {}, which {}.".format(name, view.firmware, text),
        remediation="{} to control PQC key exchange.".format(_UPGRADE))


@rule("pqc-pure-mlkem", "info", "ML-KEM without a hybrid", pack="pqc")
def pure_mlkem(view):
    """A profile whose KEMAlgs has ML-KEM alone (MLKEM512, MLKEM768,
    MLKEM1024) and no hybrid group. Few peers offer pure ML-KEM yet, and
    it rests on ML-KEM only."""
    for obj in view.crypto(*PROFILES):
        kem = pqc.kem_algs_of(obj.attrs)
        pure = [n for n in kem if n in pqc.PURE]
        if pure and not any(n in pqc.HYBRID for n in kem):
            yield Finding(
                obj=obj,
                message="The KEMAlgs of {} have {} without a hybrid "
                        "group.".format(obj.name, ", ".join(pure)),
                remediation="Put X25519MLKEM768 before them, so that the "
                            "peers that support the hybrid use it.")


def _profile_of(view, listener):
    """The one server profile that a listener uses, or None."""
    found = [view.objects[i] for i, _ in view.uses.get(listener.id, ())
             if i in view.objects
             and view.objects[i].class_ == "SSLServerProfile"]
    return found[0] if len(found) == 1 else None


def _seen(result, name):
    value = (result.get("offers") or {}).get(name)
    return value if isinstance(value, bool) else None


@rule("pqc-probe-mismatch", "medium", "Probe and configuration disagree",
      pack="pqc")
def probe_mismatch(view):
    """A port whose probe (mast crypto probe) accepted or refused a
    group or TLS 1.3 other than the configuration of its server profile
    predicts. The probe is taken as the truth: the configuration is not
    what runs, or another device answers on the port."""
    for probe in view.probes:
        result = probe["result"]
        target = result.get("target") or {}
        listener = view.by_key.get((target.get("domain"), target.get("class"),
                                    target.get("name")))
        if listener is None or not result.get("ok"):
            continue
        profile = _profile_of(view, listener)
        if profile is None or profile.op_state == "down":
            continue
        groups = _groups(view, profile)
        if groups.down:
            continue
        expected = {"TLSv1.3": bool(groups.tls13),
                    "classical": pqc.CLASSICAL in groups.tls13}
        expected.update({g: g in groups.tls13 for g in _PROBE_GROUPS})
        wrong = []
        for name, predicted in expected.items():
            seen = _seen(result, name)
            if seen is not None and seen != predicted:
                wrong.append("{} {} (the configuration says {})".format(
                    name, "accepted" if seen else "refused",
                    "accepted" if predicted else "refused"))
        if wrong:
            yield Finding(
                obj=listener,
                message="The probe of {}:{} on {} saw {}; its profile is {} "
                        "({}).".format(probe["host"], probe["port"],
                                       probe["probed_at"].strftime("%Y-%m-%d"),
                                       "; ".join(wrong), profile.name,
                                       groups.reason),
                remediation="Check what answers on the port, and whether "
                            "the running configuration of {} is the one "
                            "in the snapshot. The probe shows what peers "
                            "get.".format(profile.name))


def _strength(cert):
    """The NIST IR 8547 group of a certificate's key: 112 (deprecated,
    then disallowed), 128 (disallowed), or None (not RSA or EC)."""
    algorithm = (cert.key_alg or "").upper()
    bits = cert.key_bits or 0
    if algorithm == "RSA":
        return 112 if bits < 3072 else 128
    if algorithm in ("EC", "ECDSA", "ED25519", "ED448") or \
            algorithm.startswith("EC"):
        return 112 if bits and bits < 256 else 128
    return None


def _severity(view, year):
    """The severity of a deadline at the end of `year`, now."""
    lead = view.settings.get("pqc", "lead_years")
    left = year - view.now.year
    if left < 0:
        return "high"
    if left < lead:
        return "medium"
    if left < 2 * lead:
        return "low"
    return "info"


@rule("pqc-classical-certificate", "info",
      "Certificate with a classical key", pack="pqc")
def classical_certificate(view):
    """A certificate with an RSA or elliptic-curve key, by its NIST IR
    8547 date: RSA under 3072 bits is deprecated after [pqc]
    deprecated_year (2030), and every RSA and EC key is disallowed after
    disallowed_year (2035). Info today; the severity rises as the year
    comes nearer ([pqc] lead_years). A certificate that expires before
    its date is info."""
    deprecated = view.settings.get("pqc", "deprecated_year")
    disallowed = view.settings.get("pqc", "disallowed_year")
    for obj in view.crypto("CryptoCertificate"):
        cert = view.certs.get(obj.id)
        if cert is None:
            continue
        strength = _strength(cert)
        if strength is None:
            continue
        year = deprecated if strength == 112 else disallowed
        when = "deprecated" if strength == 112 else "disallowed"
        not_after = cert.not_after
        if not_after is not None and not_after.tzinfo is None:
            not_after = not_after.replace(tzinfo=timezone.utc)
        ends = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        severity = "info" if not_after is not None and not_after < ends \
            else _severity(view, year)
        key = "{} {}".format(cert.key_alg, cert.key_bits or "").strip()
        yield Finding(
            obj=obj, severity=severity,
            message="{} has an {} key, {} by NIST IR 8547 after {}{}.".format(
                cert.subject or obj.name, key, when, year,
                "; it expires before then" if severity == "info"
                and not_after is not None and not_after < ends else ""),
            remediation="Plan its replacement with a post-quantum "
                        "certificate (ML-DSA) when the peers and the "
                        "firmware support one for TLS (DataPower 11.0.0.3 "
                        "loads ML-DSA certificates but cannot use them for "
                        "TLS).")


@rule("pqc-legacy-profile", "medium", "Legacy SSL proxy profile", pack="pqc")
def legacy_profile(view):
    """An SSL proxy profile: it uses crypto profiles, which have no
    KEMAlgs, so it can never use post-quantum key exchange. The ones that
    the firmware makes for itself (system-..., in default) are left
    out."""
    for obj in view.crypto("SSLProxyProfile"):
        if obj.domain == "default" and obj.name.startswith("system-"):
            continue
        yield Finding(
            obj=obj,
            message="{} is an SSL proxy profile, whose crypto profiles "
                    "cannot use post-quantum key exchange; {}.".format(
                        obj.name, view.describe_users(obj)),
            remediation="Replace it with an SSL server profile and an SSL "
                        "client profile, and set the services to use them.")
