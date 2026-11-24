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
_module_: `mast.datapower.crypto.rules.baseline`

The baseline pack: the posture rules that apply to every appliance.
Each rule looks at the objects that are enabled and running, unless it
says otherwise.
"""
from . import Finding, rule

TLS_PROFILES = ("SSLServerProfile", "SSLClientProfile", "SSLSNIServerProfile",
                "SSLProxyProfile")
# The protocols of a TLS profile (`Protocols`), and their names
_OLD = (("SSLv3", "SSL 3.0", "critical"), ("TLSv1d0", "TLS 1.0", None),
        ("TLSv1d1", "TLS 1.1", None))
# The same in the `SSLOptions` of a CryptoProfile (older firmware): a
# protocol is on unless its Disable- flag is on
_OLD_CRYPTO_PROFILE = (("Disable-SSLv2", "SSL 2.0", "critical"),
                       ("Disable-SSLv3", "SSL 3.0", "critical"),
                       ("Disable-TLSv1", "TLS 1.0", None),
                       ("Disable-TLSv1d1", "TLS 1.1", None))
# The locations whose unused files are findings. pubcert: holds the CA
# certificates that come with the firmware, most of which nothing uses.
_UNUSED_LOCATIONS = ("cert:", "sharedcert:")
_KEYS_AND_CERTS = ("CryptoKey", "CryptoCertificate", "CryptoSSKey")
# The files that the firmware makes and uses itself, which no object
# names: the key and the self-signed certificate of the WebGUI when it
# has no TLS profile (seen on 2018.4, 10.0 and 11.0)
_FIRMWARE_FILES = {("default", "cert:///webgui-privkey.pem"),
                   ("default", "cert:///webgui-sscert.pem")}


def _day(value):
    return value.strftime("%Y-%m-%d")


def _days(later, earlier):
    return int((later - earlier).total_seconds() // 86400)


def _certificates(view):
    for obj in view.crypto("CryptoCertificate"):
        cert = view.certs.get(obj.id)
        if cert is not None:
            yield obj, cert


def _aware(value):
    from datetime import timezone
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@rule("cert-expired", "critical", "Certificate expired")
def cert_expired(view):
    """A certificate whose not-after date is before the snapshot."""
    for obj, cert in _certificates(view):
        not_after = _aware(cert.not_after)
        if not_after is None or not_after > view.now:
            continue
        ignored = (obj.attrs or {}).get("IgnoreExpiration") == "on"
        yield Finding(
            obj=obj,
            message="{} expired on {} ({} days before the snapshot); {}.{}"
            .format(cert.subject or obj.name, _day(not_after),
                    _days(view.now, not_after), view.describe_users(obj),
                    " IgnoreExpiration is on, so DataPower still uses it."
                    if ignored else ""),
            remediation="Replace the certificate with a new one and point "
                        "the object at it, or remove the object if nothing "
                        "uses it.")


@rule("cert-expiring", "high", "Certificate expiring soon")
def cert_expiring(view):
    """A certificate that expires within `[expiry] low_days` of the
    snapshot: high within `high_days`, medium within `medium_days`, low
    within `low_days`."""
    levels = [("high", view.settings.get("expiry", "high_days")),
              ("medium", view.settings.get("expiry", "medium_days")),
              ("low", view.settings.get("expiry", "low_days"))]
    for obj, cert in _certificates(view):
        not_after = _aware(cert.not_after)
        if not_after is None or not_after <= view.now:
            continue
        days = _days(not_after, view.now)
        for severity, limit in levels:
            if days < limit:
                yield Finding(
                    obj=obj, severity=severity,
                    message="{} expires on {}, {} days after the snapshot; "
                            "{}.".format(cert.subject or obj.name,
                                         _day(not_after), days,
                                         view.describe_users(obj)),
                    remediation="Get a new certificate and put it in place "
                                "before {}.".format(_day(not_after)))
                break


@rule("tls-old-protocol", "high", "Old SSL or TLS protocol enabled")
def tls_old_protocol(view):
    """A TLS profile with SSL 3.0, TLS 1.0 or TLS 1.1 on, or a crypto
    profile that does not disable SSL 2.0, SSL 3.0, TLS 1.0 or TLS 1.1.
    SSL is critical."""
    for obj in view.crypto(*TLS_PROFILES):
        protocols = (obj.attrs or {}).get("Protocols")
        if not isinstance(protocols, dict):
            continue
        on = [(name, severity) for key, name, severity in _OLD
              if protocols.get(key) == "on"]
        if on:
            yield _old_protocols(obj, on, "Protocols")
    for obj in view.crypto("CryptoProfile"):
        options = (obj.attrs or {}).get("SSLOptions")
        if not isinstance(options, dict):
            continue
        on = [(name, severity) for key, name, severity in _OLD_CRYPTO_PROFILE
              if key in options and options.get(key) != "on"]
        if on:
            yield _old_protocols(obj, on, "SSLOptions")


def _old_protocols(obj, on, where):
    names = [name for name, _ in on]
    return Finding(
        obj=obj, severity="critical" if any(s for _, s in on) else None,
        message="{} {} enabled in {}.".format(
            " and ".join(names), "is" if len(names) == 1 else "are", where),
        remediation="Turn off {} in the {} of {}, after checking that its "
                    "peers can use TLS 1.2 or newer.".format(
                        ", ".join(names), where, obj.name))


@rule("tls13-off", "low", "TLS 1.3 off")
def tls13_off(view):
    """A TLS profile with TLS 1.3 off, on a firmware that has it (its
    configuration has a `TLSv1d3` setting)."""
    for obj in view.crypto(*TLS_PROFILES):
        protocols = (obj.attrs or {}).get("Protocols")
        if isinstance(protocols, dict) and "TLSv1d3" in protocols \
                and protocols["TLSv1d3"] != "on":
            yield Finding(
                obj=obj, message="TLS 1.3 is off in Protocols.",
                remediation="Turn on TLS 1.3 in the Protocols of {}."
                .format(obj.name))


@rule("weak-rsa-key", "high", "RSA key too short")
def weak_rsa_key(view):
    """A certificate with an RSA key of fewer bits than
    `[keys] rsa_min_bits` (2048)."""
    minimum = view.settings.get("keys", "rsa_min_bits")
    for obj, cert in _certificates(view):
        if (cert.key_alg or "").upper() == "RSA" and cert.key_bits \
                and cert.key_bits < minimum:
            yield Finding(
                obj=obj,
                message="{} has an RSA key of {} bits; {}.".format(
                    cert.subject or obj.name, cert.key_bits,
                    view.describe_users(obj)),
                remediation="Replace it with a certificate of a key of at "
                            "least {} bits (or an EC key).".format(minimum))


@rule("weak-signature", "high", "SHA-1 or MD5 signature")
def weak_signature(view):
    """A certificate signed with SHA-1 or MD5. A self-signed
    certificate is left out: nobody checks the signature of a trust
    anchor."""
    for obj, cert in _certificates(view):
        algorithm = (cert.sig_alg or "").lower().replace("-", "")
        if cert.self_signed or not ("sha1" in algorithm or "md5" in algorithm):
            continue
        yield Finding(
            obj=obj,
            message="{} is signed with {}; {}.".format(
                cert.subject or obj.name, cert.sig_alg,
                view.describe_users(obj)),
            remediation="Get a new certificate signed with SHA-256 or "
                        "stronger.")


@rule("self-signed-production", "medium",
      "Self-signed certificate on production")
def self_signed_production(view):
    """A self-signed certificate that an identification credential
    presents, on an appliance in an environment marked production. A
    self-signed CA certificate in a validation credential is normal."""
    if not view.production:
        return
    for obj, cert in _certificates(view):
        if not cert.self_signed:
            continue
        idcreds = [u.name for u in view.users(obj, via="Certificate")
                   if u.class_ == "CryptoIdentCred"]
        if idcreds:
            yield Finding(
                obj=obj,
                message="{} is self-signed, and the identification "
                        "credential {} presents it.".format(
                            cert.subject or obj.name, ", ".join(idcreds)),
                remediation="Use a certificate that a CA signed, which the "
                            "peers can validate.")


@rule("plaintext-password", "high", "Plaintext password in use")
def plaintext_password(view):
    """A key or certificate that uses a password written in its
    configuration (`PasswordAlias` off). get-config shows it to anyone
    who may read the configuration."""
    for obj in view.crypto(*_KEYS_AND_CERTS):
        if obj.has_plaintext_password and \
                (obj.attrs or {}).get("PasswordAlias") != "on":
            yield Finding(
                obj=obj,
                message="The password of {} is in its configuration as "
                        "plain text, and get-config returns it.".format(
                            obj.name),
                remediation="Make a password alias for it, set the object "
                            "to use the alias instead of the password, and "
                            "save the configuration.")


@rule("plaintext-password-unused", "medium", "Plaintext password left over")
def plaintext_password_unused(view):
    """A key or certificate that uses a password alias, but still has a
    password in its configuration. DataPower uses the alias and keeps
    the password, and get-config returns it."""
    for obj in view.crypto(*_KEYS_AND_CERTS):
        attrs = obj.attrs or {}
        if obj.has_plaintext_password and attrs.get("PasswordAlias") == "on":
            alias = attrs.get("Alias")
            alias = alias.get("name") if isinstance(alias, dict) else alias
            yield Finding(
                obj=obj,
                message="{} uses the password alias {}, but a plaintext "
                        "password is still in its configuration, and "
                        "get-config returns it.".format(
                            obj.name, alias or "(none named)"),
                remediation="Set the object again with the alias only, so "
                            "that the password is gone, and save the "
                            "configuration.")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _named_in_attrs(view):
    """The `(domain, file)` that the kept crypto objects name: for a
    snapshot from before MAST recorded the users of each file."""
    from ..inventory import file_name
    used = set()
    for obj in view.objects.values():
        for text in _strings(obj.attrs or {}):
            name = file_name(text)
            if name:
                used.add((obj.domain, name))
    return used


@rule("unused-file", "low", "File that nothing uses")
def unused_file(view):
    """A file in `cert:` or `sharedcert:` that no object of the running
    configuration names, whatever its class. A file of `cert:` belongs
    to its domain; one of `sharedcert:` to every domain. Nothing is said
    about a domain that could not be read. `pubcert:` is left out: it
    holds the CA certificates that come with the firmware. So are the
    files that the firmware makes for the WebGUI (`webgui-privkey.pem`,
    `webgui-sscert.pem` in `default`)."""
    fallback = None
    for item in view.files:
        if item.location not in _UNUSED_LOCATIONS or \
                (item.domain, item.name) in _FIRMWARE_FILES:
            continue
        if item.location == "sharedcert:":
            if view.unread_domains:
                continue
        elif item.domain in view.unread_domains:
            continue
        if item.users is not None:
            if item.users:
                continue
        else:
            if fallback is None:
                fallback = _named_in_attrs(view)
            if item.location == "sharedcert:":
                if any(name == item.name for _, name in fallback):
                    continue
            elif (item.domain, item.name) in fallback:
                continue
        yield Finding(
            subject="{}/{}".format(item.domain, item.name),
            domain=item.domain, name=item.name,
            message="No object {} names {}.".format(
                "of the domain " + item.domain if item.location == "cert:"
                else "of any domain", item.name),
            remediation="Check that nothing outside the configuration uses "
                        "it (a stylesheet can name a file), then delete it, "
                        "keeping a copy elsewhere if it may be needed: a "
                        "forgotten key or certificate is a liability.")


@rule("not-saved", "low", "Running and saved configuration differ")
def not_saved(view):
    """A domain where objects of the inventory differ between the
    running and the saved configuration, or are only in one of them. A
    restart would change them. One finding for each domain, which names
    the objects."""
    by_domain = {}
    for obj in view.objects.values():
        if not (obj.running and obj.saved):
            by_domain.setdefault(obj.domain, []).append(obj)
    for domain, objects in sorted(by_domain.items()):
        names = sorted("{} {}{}".format(
            o.class_, o.name, "" if o.running else " (saved, not running)")
            for o in objects)
        yield Finding(
            subject=domain, domain=domain, name=domain,
            message="{} object{} of the domain {} differ{} from the saved "
                    "configuration: {}.".format(
                        len(names), "" if len(names) == 1 else "s", domain,
                        "s" if len(names) == 1 else "",
                        ", ".join(names[:8]) + (
                            " and {} more".format(len(names) - 8)
                            if len(names) > 8 else "")),
            remediation="Save the configuration of the domain {} if the "
                        "running one is right, or put the objects back as "
                        "they were saved.".format(domain))


@rule("disabled-in-use", "medium", "Disabled object still referenced")
def disabled_in_use(view):
    """A disabled object that an enabled object of the running
    configuration still refers to."""
    for obj in view.objects.values():
        if obj.admin_state != "disabled" or not obj.running:
            continue
        users = sorted({"{} {}".format(u.class_, u.name)
                        for u in view.users(obj)
                        if u.admin_state != "disabled" and u.running})
        if users:
            yield Finding(
                obj=obj,
                message="{} {} is disabled, but {} still refer{} to it."
                .format(obj.class_, obj.name, ", ".join(users[:5]) +
                        (" and {} more".format(len(users) - 5)
                         if len(users) > 5 else ""),
                        "s" if len(users) == 1 else ""),
                remediation="Enable it, or change the objects that refer to "
                            "it to use another one.")
