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
_module_: `mast.datapower.crypto.posture`

The posture of the appliances: the findings of the rules
(`mast.datapower.crypto.rules`) and the exceptions that accept some of
them.

* `evaluate(snapshot_id)` runs the snapshot rules on a snapshot and
stores the findings. `mast-crypto inventory` does it for each snapshot
it keeps; `mast-crypto posture` does it again on demand.
* An exception (`add_exception`) accepts the findings of one rule on
the objects whose name matches a pattern, on one appliance or domain
or on all, until it expires. It needs a reason and an expiry date.
When one is added or removed, the findings of the newest snapshots
are matched again. An exception that has expired no longer applies,
and its findings count again.
* `self_check_findings()` runs the MAST self-checks now.
* `score()` and `summary()` give the posture score of each appliance:
100 minus the weight of each finding that no exception covers
(`[score]` of `crypto.conf`), and never below 0.
"""
import fnmatch
from datetime import date, datetime, time, timedelta, timezone

from pydantic import field_validator
from sqlalchemy import delete, func, select, update

from mast.cli import Option, Params, Table, say
from mast.logging import make_logger

from . import cli
from .models import (CryptoCert, CryptoException, CryptoFinding,
                     CryptoObject, CryptoSnapshot)
from .rules import (RULES, SEVERITIES, Setup, View, self_checks, settings,
                    snapshot_rules)

__all__ = ["evaluate", "latest_snapshots", "findings_of", "self_check_findings",
           "score", "counts", "summary", "add_exception", "remove_exception",
           "list_exceptions", "expiry_of", "reapply", "ExceptionError",
           "MANAGE"]

# The action of mast.auth.roles that adding or removing an exception needs
MANAGE = "manage-exception"


class ExceptionError(ValueError):
    """An exception that cannot be added or removed: the message says
    why."""


def _now():
    return datetime.now(timezone.utc)


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value):
    value = _aware(value)
    return value.isoformat() if isinstance(value, datetime) else value


# Matching
# ========

def _matches(exception, rule_id, appliance, domain, name, subject, now):
    if exception.rule_id != rule_id or _aware(exception.expires_at) <= now:
        return False
    if exception.appliance and exception.appliance != appliance:
        return False
    if exception.domain and exception.domain != domain:
        return False
    pattern = exception.object_pattern or "*"
    return fnmatch.fnmatchcase(name or "", pattern) or \
        fnmatch.fnmatchcase(subject or "", pattern)


def _exception_for(exceptions, rule_id, appliance, domain, name, subject, now):
    for exception in exceptions:
        if _matches(exception, rule_id, appliance, domain, name, subject, now):
            return exception
    return None


def _active_exceptions(session, now):
    return list(session.scalars(
        select(CryptoException).where(CryptoException.expires_at > now)
        .order_by(CryptoException.id)))


# The snapshot rules
# ==================

def evaluate(snapshot_id, now=None, settings_=None):
    """
    _function_: `mast.datapower.crypto.posture.evaluate(snapshot_id,
    now=None, settings_=None)`

    Run the snapshot rules on the snapshot `snapshot_id` and store what
    they find in place of what they found before. A snapshot that
    failed has no findings. A rule that raises is logged and left out,
    so that one broken rule does not hide the others. Returns the
    number of findings.
    """
    from mast.jobs import db
    now = now or _now()
    settings_ = settings_ or settings()
    view = View.load(snapshot_id, settings_)
    if view is None:
        raise ValueError("there is no snapshot {}".format(snapshot_id))
    found = []
    if view.snapshot.ok:
        for item in snapshot_rules(settings_):
            try:
                found += item.run(view)
            except Exception:
                make_logger("mast.crypto").exception(
                    "The posture rule {} failed on snapshot {}".format(
                        item.id, snapshot_id))
    with db.session() as session:
        session.execute(delete(CryptoFinding).where(
            CryptoFinding.snapshot_id == snapshot_id))
        exceptions = _active_exceptions(session, now)
        for finding in found:
            exception = _exception_for(
                exceptions, finding.rule_id, finding.appliance,
                finding.domain, finding.name, finding.subject, now)
            session.add(CryptoFinding(
                snapshot_id=snapshot_id, rule_id=finding.rule_id,
                severity=finding.severity,
                object_id=getattr(finding.obj, "id", None),
                subject=finding.subject[:800], message=finding.message,
                remediation=finding.remediation,
                exception_id=exception.id if exception else None))
    return len(found)


def latest_snapshots(session, appliance_ids=None, ok=True):
    """`{appliance_id: snapshot_id}`: the newest snapshot of each
    appliance (that worked, if `ok`)."""
    query = select(CryptoSnapshot.appliance_id, func.max(CryptoSnapshot.id))
    if ok:
        query = query.where(CryptoSnapshot.ok.is_(True))
    if appliance_ids is not None:
        query = query.where(CryptoSnapshot.appliance_id.in_(appliance_ids))
    return {appliance_id: snapshot_id for appliance_id, snapshot_id in
            session.execute(query.group_by(CryptoSnapshot.appliance_id))}


def reapply(now=None):
    """
    _function_: `mast.datapower.crypto.posture.reapply(now=None)`

    Match the findings of the newest snapshot of each appliance against
    the exceptions again, after one was added or removed. Returns the
    number of findings that an exception covers.
    """
    from mast import registry
    from mast.jobs import db
    now = now or _now()
    names = {a.id: a.name for a in registry.list_appliances()}
    covered = 0
    with db.session() as session:
        exceptions = _active_exceptions(session, now)
        latest = latest_snapshots(session)
        rows = session.execute(
            select(CryptoFinding, CryptoSnapshot.appliance_id,
                   CryptoObject.domain, CryptoObject.name)
            .join(CryptoSnapshot, CryptoSnapshot.id == CryptoFinding.snapshot_id)
            .outerjoin(CryptoObject, CryptoObject.id == CryptoFinding.object_id)
            .where(CryptoFinding.snapshot_id.in_(list(latest.values()) or [0])))
        for finding, appliance_id, domain, name in rows:
            domain, name = _domain_name(finding, domain, name)
            exception = _exception_for(
                exceptions, finding.rule_id, names.get(appliance_id), domain,
                name, finding.subject, now)
            finding.exception_id = exception.id if exception else None
            covered += exception is not None
    return covered


def _domain_name(finding, domain, name):
    """The domain and name that exceptions match: those of the object,
    or for a file the parts of `domain/cert:///name`."""
    if domain is None and "/" in finding.subject:
        domain, name = finding.subject.split("/", 1)
    return domain, name or finding.subject


# Reading the findings
# ====================

def _finding_json(rule_id, severity, subject, message, remediation,
                  exception, now, **more):
    item = RULES.get(rule_id)
    found = {
        "rule_id": rule_id, "title": item.title if item else rule_id,
        "pack": item.pack if item else "", "severity": severity,
        "subject": subject, "message": message, "remediation": remediation,
        "exception": None}
    if exception is not None and _aware(exception.expires_at) > now:
        found["exception"] = _exception_json(exception, now)
    found.update(more)
    return found


def findings_of(snapshot_ids, now=None):
    """
    _function_: `mast.datapower.crypto.posture.findings_of(snapshot_ids,
    now=None)`

    The stored findings of the snapshots `snapshot_ids`, as dicts, with
    the exception that covers each one (if it has not expired since),
    the appliance, and the object.
    """
    from mast import registry
    from mast.jobs import db
    from .rules import _load
    _load()
    now = now or _now()
    names = {a.id: a.name for a in registry.list_appliances()}
    found = []
    with db.session(write=False) as session:
        rows = session.execute(
            select(CryptoFinding, CryptoSnapshot.appliance_id,
                   CryptoObject, CryptoException)
            .join(CryptoSnapshot, CryptoSnapshot.id == CryptoFinding.snapshot_id)
            .outerjoin(CryptoObject, CryptoObject.id == CryptoFinding.object_id)
            .outerjoin(CryptoException,
                       CryptoException.id == CryptoFinding.exception_id)
            .where(CryptoFinding.snapshot_id.in_(list(snapshot_ids) or [0]))
            .order_by(CryptoFinding.id))
        for finding, appliance_id, obj, exception in rows:
            domain, name = _domain_name(
                finding, getattr(obj, "domain", None), getattr(obj, "name", None))
            found.append(_finding_json(
                finding.rule_id, finding.severity, finding.subject,
                finding.message, finding.remediation, exception, now,
                id=finding.id, snapshot_id=finding.snapshot_id,
                appliance=names.get(appliance_id, ""), domain=domain,
                name=name, object_id=finding.object_id,
                object_class=getattr(obj, "class_", None)))
    return found


def self_check_findings(now=None, setup=None):
    """
    _function_: `mast.datapower.crypto.posture.self_check_findings(
    now=None, setup=None)`

    The findings of the MAST self-checks now, as dicts like those of
    `findings_of` (with no `id`): they are not stored.
    """
    from mast.jobs import db
    now = now or _now()
    setup = setup or Setup.load(now)
    found = []
    with db.session(write=False) as session:
        exceptions = _active_exceptions(session, now)
    for item in self_checks(setup.settings):
        try:
            results = item.run(setup)
        except Exception:
            make_logger("mast.crypto").exception(
                "The self-check {} failed".format(item.id))
            continue
        for finding in results:
            exception = _exception_for(
                exceptions, finding.rule_id, finding.appliance, None,
                finding.name, finding.subject, now)
            found.append(_finding_json(
                finding.rule_id, finding.severity, finding.subject,
                finding.message, finding.remediation, exception, now,
                id=None, snapshot_id=None, appliance=finding.appliance or "",
                domain=None, name=finding.name, object_id=None,
                object_class=None))
    return found


def score(findings, settings_=None):
    """
    _function_: `mast.datapower.crypto.posture.score(findings,
    settings_=None)`

    The posture score of these findings (dicts): 100 minus the weight
    of each one that no exception covers, never below 0.
    """
    settings_ = settings_ or settings()
    lost = sum(settings_.weight(f["severity"]) for f in findings
               if not f.get("exception"))
    return max(0, 100 - lost)


def counts(findings):
    """The findings that no exception covers, by severity, and how many
    an exception covers."""
    found = {severity: 0 for severity in SEVERITIES}
    found["excepted"] = 0
    for finding in findings:
        if finding.get("exception"):
            found["excepted"] += 1
        else:
            found[finding["severity"]] = found.get(finding["severity"], 0) + 1
    return found


def summary(now=None):
    """
    _function_: `mast.datapower.crypto.posture.summary(now=None)`

    The posture of each appliance from its newest snapshot that worked,
    for the Appliances page and the Crypto page: `{appliance_id:
    {snapshot_id, collected_at, score, counts, nearest_expiry}}`. The
    self-checks of an appliance count in its score. An appliance with
    no snapshot is not in it.
    """
    from mast import registry
    from mast.jobs import db
    now = now or _now()
    settings_ = settings()
    by_name = {a.name: a.id for a in registry.list_appliances()}
    with db.session(write=False) as session:
        latest = latest_snapshots(session)
        when = {snapshot_id: collected for snapshot_id, collected in
                session.execute(
                    select(CryptoSnapshot.id, CryptoSnapshot.collected_at)
                    .where(CryptoSnapshot.id.in_(
                        list(latest.values()) or [0])))}
        nearest = {}
        for snapshot_id, domain, name, object_id, not_after in session.execute(
                select(CryptoCert.snapshot_id, CryptoObject.domain,
                       CryptoObject.name, CryptoObject.id, CryptoCert.not_after)
                .join(CryptoObject, CryptoObject.id == CryptoCert.object_id)
                .where(CryptoCert.snapshot_id.in_(list(latest.values()) or [0]))
                .where(CryptoObject.admin_state != "disabled")
                .where(CryptoCert.not_after.is_not(None))):
            current = nearest.get(snapshot_id)
            if current is None or _aware(not_after) < current[3]:
                nearest[snapshot_id] = (domain, name, object_id,
                                        _aware(not_after))
    findings = findings_of(latest.values(), now)
    checks = [f for f in self_check_findings(now) if f["appliance"]]
    found = {}
    for appliance_id, snapshot_id in latest.items():
        mine = [f for f in findings if f["snapshot_id"] == snapshot_id]
        mine += [f for f in checks if by_name.get(f["appliance"]) == appliance_id]
        expiry = nearest.get(snapshot_id)
        found[appliance_id] = {
            "snapshot_id": snapshot_id,
            "collected_at": _iso(when.get(snapshot_id)),
            "score": score(mine, settings_), "counts": counts(mine),
            "nearest_expiry": None if expiry is None else {
                "domain": expiry[0], "name": expiry[1],
                "object_id": expiry[2], "not_after": _iso(expiry[3])}}
    return found


# Exceptions
# ==========

def _exception_json(exception, now=None):
    now = now or _now()
    return {
        "id": exception.id, "rule_id": exception.rule_id,
        "appliance": exception.appliance, "domain": exception.domain,
        "object_pattern": exception.object_pattern, "reason": exception.reason,
        "expires_at": _iso(exception.expires_at),
        "expired": _aware(exception.expires_at) <= now,
        "created_by": exception.created_by,
        "created_at": _iso(exception.created_at)}


def expiry_of(text, now=None):
    """
    _function_: `mast.datapower.crypto.posture.expiry_of(text, now=None)`

    The end of an exception from text: a date (`2027-03-31`, the end of
    that day in UTC), a time with a zone, or a number of days (`90d`).
    """
    now = now or _now()
    if isinstance(text, datetime):
        return _aware(text)
    text = str(text or "").strip()
    if not text:
        raise ExceptionError("an exception needs an expiry date")
    if text.endswith("d") and text[:-1].isdigit():
        return now + timedelta(days=int(text[:-1]))
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            return datetime.combine(day, time(23, 59, 59), timezone.utc)
        value = datetime.fromisoformat(text)
    except ValueError:
        raise ExceptionError(
            "the expiry is a date (2027-03-31) or a number of days (90d), "
            "not {!r}".format(text))
    if value.tzinfo is None:
        raise ExceptionError("give the expiry with its time zone, or as a "
                             "date")
    return value


def add_exception(rule_id, object_pattern, reason, expires, principal,
                  appliance=None, domain=None, now=None):
    """
    _function_: `mast.datapower.crypto.posture.add_exception(rule_id,
    object_pattern, reason, expires, principal, appliance=None,
    domain=None, now=None)`

    Accept the findings of the rule `rule_id` on the objects whose name
    matches `object_pattern` (a shell pattern: `*`, `lab-*`), on
    `appliance` and in `domain` if given, until `expires` (see
    `expiry_of`). `principal` needs `manage-exception` (an admin).
    Returns the exception as a dict. Raises `ExceptionError`.
    """
    from mast.jobs import db
    from mast.jobs.auth import authorize
    from .rules import _load
    authorize(principal, MANAGE)
    _load()
    now = now or _now()
    rule_id = str(rule_id or "").strip()
    if rule_id not in RULES:
        raise ExceptionError("there is no rule {!r}; mast-crypto rules lists "
                             "them".format(rule_id))
    object_pattern = str(object_pattern or "").strip()
    if not object_pattern or len(object_pattern) > 255:
        raise ExceptionError("give the name of the object, or a pattern of "
                             "names (up to 255 characters)")
    reason = str(reason or "").strip()
    if len(reason) < 10:
        raise ExceptionError("give the reason for the exception (at least 10 "
                             "characters), for those who read it later")
    expires_at = expiry_of(expires, now)
    longest = settings().get("exceptions", "max_days")
    if expires_at <= now:
        raise ExceptionError("the expiry must be in the future")
    if expires_at > now + timedelta(days=longest, hours=24):
        raise ExceptionError("an exception may last at most {} days "
                             "([exceptions] max_days of crypto.conf)"
                             .format(longest))
    appliance = str(appliance or "").strip() or None
    domain = str(domain or "").strip() or None
    if appliance:
        from mast import registry
        if registry.by_name(appliance) is None:
            raise ExceptionError("there is no appliance {}".format(appliance))
    with db.session() as session:
        row = CryptoException(
            rule_id=rule_id, appliance=appliance, domain=domain,
            object_pattern=object_pattern, reason=reason,
            expires_at=expires_at, created_by=principal.name, created_at=now)
        session.add(row)
        session.flush()
        found = _exception_json(row, now)
    reapply(now)
    make_logger("mast.crypto").info(
        "{} added the posture exception {} for {} on {}".format(
            principal.name, found["id"], rule_id, object_pattern))
    return found


def remove_exception(exception_id, principal, now=None):
    """
    _function_: `mast.datapower.crypto.posture.remove_exception(
    exception_id, principal, now=None)`

    Remove an exception: its findings count again. `principal` needs
    `manage-exception`.
    """
    from mast.jobs import db
    from mast.jobs.auth import authorize
    authorize(principal, MANAGE)
    with db.session() as session:
        row = session.get(CryptoException, exception_id)
        if row is None:
            raise ExceptionError("there is no exception {}".format(exception_id))
        session.execute(update(CryptoFinding)
                        .where(CryptoFinding.exception_id == exception_id)
                        .values(exception_id=None))
        session.delete(row)
    reapply(now)
    make_logger("mast.crypto").info("{} removed the posture exception {}"
                                    .format(principal.name, exception_id))


def list_exceptions(now=None):
    """
    _function_: `mast.datapower.crypto.posture.list_exceptions(now=None)`

    Every exception, the newest first, as dicts (with `expired`).
    """
    from mast.jobs import db
    now = now or _now()
    with db.session(write=False) as session:
        return [_exception_json(row, now) for row in session.scalars(
            select(CryptoException).order_by(CryptoException.id.desc()))]


# The commands
# ============

def _principal():
    """Who runs this command: who submitted its job, or the account of
    the operating system outside a job."""
    from mast.jobs import queue
    from mast.jobs.auth import LOCAL, principal_of
    from mast.jobs.runner import current_job
    job = current_job()
    return principal_of(queue.get(job).submitted_by) if job else LOCAL


def _findings_table(findings):
    order = {s: n for n, s in enumerate(SEVERITIES)}
    rows = sorted(findings, key=lambda f: (
        bool(f["exception"]), order.get(f["severity"], 9), f["appliance"],
        f["rule_id"], f["subject"]))
    return [[f["appliance"], f["severity"], f["rule_id"], f["subject"],
             f["message"], "exception {}".format(f["exception"]["id"])
             if f["exception"] else ""] for f in rows]


class Posture(Params):
    snapshot: int | None = Option(
        None, short="s", help="The id of the snapshot. Without it, the "
                              "newest snapshot of each appliance.")
    appliances: list[str] = Option(
        default_factory=list, short="a",
        help="Only the newest snapshots of these appliances.")


@cli.command("posture", category="inventory", exclusive=False)
def posture(params: Posture):
    """Run the posture rules again on the crypto inventory, and show the
findings and the MAST self-checks. Without --snapshot, the newest snapshot
of each appliance (or of the appliances given). Run it after changing
etc/local/crypto.conf; adding or removing an exception needs no run."""
    from mast import registry
    from mast.jobs import db
    if params.snapshot is not None and params.appliances:
        raise ValueError("give either a snapshot or appliances, not both")
    if params.snapshot is not None:
        ids = [params.snapshot]
    else:
        wanted = None
        if params.appliances:
            wanted = []
            for name in params.appliances:
                record = registry.by_name(name)
                if record is None:
                    raise ValueError("there is no appliance {}".format(name))
                wanted.append(record.id)
        with db.session(write=False) as session:
            ids = sorted(latest_snapshots(session, wanted).values())
    if not ids:
        raise ValueError("there is no snapshot yet: run mast-crypto inventory "
                         "first")
    for snapshot_id in ids:
        say("Snapshot {}: {} findings".format(snapshot_id,
                                              evaluate(snapshot_id)))
    findings = findings_of(ids)
    if params.snapshot is None:
        findings += self_check_findings()
    return Table(("Appliance", "Severity", "Rule", "Object", "Finding",
                  "Exception"), _findings_table(findings))


@cli.command("rules", category="inventory", exclusive=False)
def list_rules(params: Params):
    """List the posture rules: their id, pack, severity, and whether
they are on. The cnsa pack is off unless etc/local/crypto.conf turns it
on ([packs] disabled)."""
    from .rules import _load
    _load()
    current = settings()
    return Table(("Rule", "Pack", "Severity", "Title", "On", "What it finds"),
                 [[r.id, r.pack, r.severity, r.title,
                   "yes" if current.enabled(r) else "no", r.doc]
                  for r in RULES.values()])


class AddException(Params):
    rule: str = Option(..., short="r", help="The id of the rule "
                                            "(mast-crypto rules).")
    pattern: str = Option(
        ..., short="p", help="The name of the object (or file, appliance, "
                             "credential), or a shell pattern: lab-*")
    reason: str = Option(..., short="R", help="Why the finding is accepted.")
    expires: str = Option(
        ..., short="e", help="When the exception ends: a date (2027-03-31) "
                             "or a number of days (90d).")
    appliance: str | None = Option(
        None, short="a", help="Only on this appliance. The default is every "
                              "appliance.")
    domain: str | None = Option(
        None, short="d", help="Only in this domain. The default is every "
                              "domain.")


@cli.command("exception-add", category="inventory", exclusive=False)
def exception_add(params: AddException):
    """Accept the findings of a posture rule on some objects until a date,
for a reason. They stay on the Crypto page, marked with the exception,
and no longer lower the score. Admins only."""
    try:
        found = add_exception(params.rule, params.pattern, params.reason,
                              params.expires, _principal(),
                              appliance=params.appliance, domain=params.domain)
    except ExceptionError as exc:
        raise ValueError(str(exc))
    return _exceptions_table([found])


class RemoveException(Params):
    id: int = Option(..., short="i", help="The id of the exception "
                                          "(mast-crypto exceptions).")

    @field_validator("id")
    @classmethod
    def _positive(cls, value):
        if value < 1:
            raise ValueError("must be 1 or more")
        return value


@cli.command("exception-remove", category="inventory", exclusive=False)
def exception_remove(params: RemoveException):
    """Remove an exception of the posture rules: its findings count again.
Admins only."""
    try:
        remove_exception(params.id, _principal())
    except ExceptionError as exc:
        raise ValueError(str(exc))
    say("Removed the exception {}".format(params.id))
    return _exceptions_table(list_exceptions())


def _exceptions_table(rows):
    return Table(("Id", "Rule", "Appliance", "Domain", "Pattern", "Expires",
                  "Expired", "Reason", "By"),
                 [[str(e["id"]), e["rule_id"], e["appliance"] or "(all)",
                   e["domain"] or "(all)", e["object_pattern"], e["expires_at"],
                   "yes" if e["expired"] else "no", e["reason"],
                   e["created_by"]] for e in rows])


@cli.command("exceptions", category="inventory", exclusive=False)
def exceptions(params: Params):
    """List the exceptions of the posture rules, the newest first, with
those that have expired."""
    return _exceptions_table(list_exceptions())
