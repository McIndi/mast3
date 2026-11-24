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
_module_: `mast.datapower.status.page`

The Appliances page of the web UI: the status of every appliance, from
the last poll of `mast-status collect`. It is the first page after the
login. The script (`static/appliances.js`) asks these routes:

* `GET /api/v1/appliances-status`: every appliance of the registry with
its last status, its crypto posture (from the newest snapshot of the
crypto inventory), and whether a schedule polls them.
* `GET /api/v1/appliances-status/<name>?hours=24`: one appliance, with
the domains and the objects that are down, and the points of its charts
(the samples, or the hourly or daily rollups for longer ranges).
* `POST /api/v1/appliances-status/polling`: `{credential, minutes}`, an
admin sets up the schedule `status-poll` that polls every appliance
whose status is collected, with that stored credential.

No answer has a secret. A viewer sees the status, not the name of the
credential.
"""
from datetime import datetime, timedelta, timezone

import flask
from sqlalchemy import select

from mast.auth import roles
from mast.jobs.auth import Forbidden
from mast.web.page import Page

__all__ = ["page", "COMMAND", "SCHEDULE"]

COMMAND = "status.collect"
SCHEDULE = "status-poll"
MAX_HOURS = 24 * 400

blueprint = flask.Blueprint(
    "appliances_status", __name__, template_folder="templates",
    static_folder="static", static_url_path="/static/status")


def _user():
    return flask.g.mast_user


def _json(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _error(status, message):
    return flask.jsonify({"error": message}), status


def _may_see_credentials():
    return roles.allowed(_user().role, "submit")


def _can_write(name, cache):
    """Whether the stored credential `name` could change the appliances
    at its last test (None if not known)."""
    if not name:
        return None
    if name not in cache:
        from mast.credentials import store
        try:
            cache[name] = store.get(name).can_write
        except Exception:
            cache[name] = None
    return cache[name]


def _status_json(found, cache, detail=False):
    if found is None:
        return None
    status = {k: _json(v) for k, v in found.items()
              if k != "detail" and k != "credential"}
    # DataPower measures the CPU only with Statistics enabled in default
    state = (found.get("detail") or {}).get("statistics")
    status["statistics_off"] = state is not None and state != "enabled"
    status["credential_can_write"] = _can_write(found.get("credential"), cache)
    if _may_see_credentials():
        status["credential"] = found.get("credential")
    if detail:
        status["detail"] = found.get("detail") or {}
    return status


def _crypto():
    """The posture of each appliance from the crypto inventory, or {} if
    it cannot be read (the page shows the status anyway)."""
    try:
        from mast.datapower.crypto import posture
        return posture.summary()
    except Exception:
        from mast.logging import make_logger
        make_logger("gui").exception("The crypto posture could not be read")
        return {}


def _appliance_json(appliance, status, cache, detail=False, crypto=None):
    found = (crypto or {}).get(appliance.id)
    return {
        "name": appliance.name,
        "environments": list(appliance.environments),
        "poll": appliance.poll,
        "tls_mode": appliance.tls_mode,
        "pinned": bool(appliance.pin_sha256 or appliance.ssh_host_key),
        "pin_changed": bool(appliance.tls_mismatch or appliance.ssh_mismatch),
        "status": _status_json(status, cache, detail),
        # From the newest snapshot of the crypto inventory, if any
        "crypto": None if found is None else {
            "score": found["score"], "counts": found["counts"],
            "nearest_expiry": found["nearest_expiry"],
            "collected_at": found["collected_at"]},
    }


def _polling():
    """The schedules of `status.collect`."""
    from mast.jobs import db
    from mast.jobs.models import Schedule
    with db.session(write=False) as session:
        rows = session.scalars(
            select(Schedule).where(Schedule.command == COMMAND)
            .order_by(Schedule.name))
        found = [{"name": row.name, "enabled": row.enabled, "cron": row.cron,
                  "credential": (row.params or {}).get("credential")
                  if _may_see_credentials() else None}
                 for row in rows]
    return {"schedules": found,
            "may_manage": roles.allowed(_user().role, "manage-schedule")}


@blueprint.get("/appliances")
def appliances_page():
    """
    _function_: `mast.datapower.status.page.appliances_page()`

    The Appliances page.

    Exposed at: `/appliances` via GET
    """
    return flask.render_template("appliances_status.html")


@blueprint.get("/api/v1/appliances-status")
def appliances_status():
    """
    _function_: `mast.datapower.status.page.appliances_status()`

    Every appliance of the registry with its last status (or null), and
    the schedules that poll them.

    Exposed at: `/api/v1/appliances-status` via GET
    """
    from mast import registry
    from mast.datapower.status import store
    latest = store.latest()
    cache = {}
    crypto = _crypto()
    return flask.jsonify({
        "appliances": [_appliance_json(a, latest.get(a.id), cache,
                                       crypto=crypto)
                       for a in registry.list_appliances()],
        "polling": _polling(),
        "now": datetime.now(timezone.utc).isoformat(),
    })


@blueprint.get("/api/v1/appliances-status/<name>")
def appliance_status(name):
    """
    _function_: `mast.datapower.status.page.appliance_status(name)`

    One appliance: its last status with the domains and the objects that
    are down, and the points of its charts for the last `hours` (24 by
    default).

    Exposed at: `/api/v1/appliances-status/<name>` via GET
    """
    from mast import registry
    from mast.datapower.status import store
    appliance = registry.by_name(name)
    if appliance is None:
        return _error(404, "There is no appliance {}.".format(name))
    try:
        hours = int(flask.request.args.get("hours", 24))
    except ValueError:
        return _error(422, "hours is a whole number.")
    if not 1 <= hours <= MAX_HOURS:
        return _error(422, "hours is from 1 to {}.".format(MAX_HOURS))
    now = datetime.now(timezone.utc)
    kind, points = store.history(appliance.id, now - timedelta(hours=hours),
                                 now)
    answer = _appliance_json(appliance, store.latest().get(appliance.id), {},
                             detail=True)
    answer.update({"hours": hours, "kind": kind, "points": points,
                   "now": now.isoformat()})
    return flask.jsonify(answer)


@blueprint.post("/api/v1/appliances-status/polling")
def set_up_polling():
    """
    _function_: `mast.datapower.status.page.set_up_polling()`

    Make (or change, and enable) the schedule `status-poll`, which polls
    every appliance whose status is collected with the stored credential
    `credential`, every `minutes` (5 by default). Admins only: the
    schedule checks the role and the credential.

    Exposed at: `/api/v1/appliances-status/polling` via POST
    """
    from mast.jobs import schedules
    from mast.web.auth import principal
    body = flask.request.get_json(silent=True) or {}
    credential = str(body.get("credential") or "").strip()
    try:
        minutes = int(body.get("minutes") or 5)
    except ValueError:
        minutes = 0
    if not credential:
        return _error(422, "Choose a stored credential for collecting.")
    if minutes not in (1, 2, 3, 5, 10, 15, 20, 30, 60):
        return _error(422, "Poll every 1, 2, 3, 5, 10, 15, 20, 30 or 60 "
                           "minutes.")
    cron_text = "0 * * * *" if minutes == 60 else "*/{} * * * *".format(minutes)
    params = {"credential": credential}
    who = principal()
    try:
        try:
            schedules.get(SCHEDULE, principal=who)
        except schedules.UnknownSchedule:
            info = schedules.add(
                SCHEDULE, COMMAND, params, cron_text, principal=who,
                description="The status of the appliances, for the "
                            "Appliances page")
        else:
            info = schedules.update(SCHEDULE, principal=who,
                                    cron_text=cron_text, params=params,
                                    enabled=True)
    except schedules.ScheduleError as exc:
        return _error(422, str(exc))
    except Forbidden as exc:
        return _error(403, str(exc))
    return flask.jsonify({"schedule": info.name, "cron": info.cron,
                          "enabled": info.enabled})


page = Page(name="appliances", title="Appliances", order=10,
            min_role="viewer", blueprint=blueprint)
