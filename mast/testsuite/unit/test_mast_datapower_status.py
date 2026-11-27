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
Unit tests for the status of the appliances: the parsing of a poll,
`mast datapower collect-status` with fake appliances, the rollups and their
retention, and the Appliances page and its API.
"""
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock
from xml.etree import ElementTree as etree

from pydantic import ValidationError
from sqlalchemy import select

from mast import jobs, registry
from mast.cli.results import stored_credential, using_stored_credential
from mast.credentials import store as credentials
from mast.datapower.status import collector, page as status_page, store
from mast.datapower.status.models import (
    DAY, HOUR, StatusLatest, StatusRollup, StatusSample)
from mast.jobs import db, retention, schedules
from mast.jobs.models import PARTIAL, Schedule
from mast.testsuite.unit.test_mast_credentials import ADMIN
from mast.testsuite.unit.test_mast_jobs import PostgresMixin, SqliteCase
from mast.testsuite.unit.web_helpers import WebCase

UTC = timezone.utc
T0 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)

ENVELOPE = (
    '<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">'
    '<env:Body>'
    '<dp:response xmlns:dp="http://www.datapower.com/schemas/management">'
    '<dp:status>{}</dp:status>'
    '</dp:response></env:Body></env:Envelope>')

PROVIDERS = {
    "FirmwareVersion3": (
        "<FirmwareVersion3><Serial>S1</Serial><Version>IDG.11.0.0.3</Version>"
        "<DeliveryType>LTS</DeliveryType><MachineType>8436</MachineType>"
        "<ModelType>n/a</ModelType></FirmwareVersion3>"),
    "FirmwareVersion": (
        "<FirmwareVersion><Serial>S0</Serial><Version>IDG.10.5.0.23</Version>"
        "<ModelType>52X</ModelType></FirmwareVersion>"),
    "DateTimeStatus": (
        "<DateTimeStatus><time>Mon Nov 23 11:25:58 2026</time>"
        "<timezone>EST</timezone><uptime2>5 days 15:51:12</uptime2>"
        "</DateTimeStatus>"),
    "CPUUsage": "<CPUUsage><tenSeconds>11</tenSeconds>"
                "<oneMinute>6</oneMinute></CPUUsage>",
    "MemoryStatus": "<MemoryStatus><Usage>19</Usage></MemoryStatus>",
    "SystemUsage": "<SystemUsage><Load>7</Load></SystemUsage>",
    "FilesystemStatus": (
        "<FilesystemStatus><FreeEncrypted>900</FreeEncrypted>"
        "<FreeTemporary>250</FreeTemporary><TotalTemporary>1000"
        "</TotalTemporary><FreeInternal>800</FreeInternal>"
        "</FilesystemStatus>"),
}


def _object(_class, name, op="up", admin="enabled", code="0x00000000"):
    return ("<ObjectStatus><Class>{}</Class><OpState>{}</OpState>"
            "<AdminState>{}</AdminState><Name>{}</Name>"
            "<EventCode>{}</EventCode><ErrorCode/><ConfigState>saved"
            "</ConfigState></ObjectStatus>").format(_class, op, admin, name,
                                                    code)


OBJECTS = {
    "default": "".join((
        _object("Domain", "default"), _object("Domain", "app"),
        _object("Domain", "old", "down", "disabled"),
        _object("Domain", "broken", "down"),
        _object("XMLFirewallService", "fw1", "down", code="0x00b30001"),
        _object("SNMPSettings", "snmp", "down", "disabled"),
        _object("HTTPService", "web"),
        _object("Statistics", "default", "down", "disabled"))),
    "app": _object("MultiProtocolGateway", "mpg", "down", code="0x123"),
}


class Response(object):
    def __init__(self, body):
        self.xml = etree.fromstring(ENVELOPE.format(body))


class FakeAppliance(object):
    history = "request: x\nresponse: y"

    def __init__(self, hostname, old_firmware=False, fail=None):
        self.hostname = hostname
        self._record = registry.by_name(hostname)
        self.old_firmware = old_firmware
        self.fail = fail
        self.calls = []
        self.strict = []

    def get_status(self, provider, domain="default"):
        self.calls.append((provider, domain))
        self.strict.append(using_stored_credential())
        if self.fail:
            raise self.fail
        if provider == "ObjectStatus":
            return Response(OBJECTS.get(domain, ""))
        if provider == "FirmwareVersion3" and self.old_firmware:
            raise RuntimeError("no such provider")
        return Response(PROVIDERS[provider])


class FakeEnvironment(object):
    """Environment, with the fake appliances of `appliances`."""
    appliances_by_name = {}

    def __init__(self, hostnames, credentials=None, timeout=120,
                 check_hostname=True):
        self.appliances = [
            self.appliances_by_name.get(name) or FakeAppliance(name)
            for name in hostnames]


class TestPoll(SqliteCase):

    def test_parse_uptime(self):
        for text, seconds in (("5 days 15:51:12", 5 * 86400 + 57072),
                              ("1 day 00:00:01", 86401), ("03:00:00", 10800),
                              ("", None), (None, None), ("soon", None)):
            with self.subTest(text=text):
                self.assertEqual(collector.parse_uptime(text), seconds)

    def test_a_poll(self):
        appliance = FakeAppliance("dp1")
        values = collector.poll(appliance, now=T0, delay=0)
        detail = values.pop("detail")
        self.assertEqual(values, {
            "firmware": "IDG.11.0.0.3", "delivery": "LTS", "model": "8436",
            "serial": "S1", "uptime_seconds": 5 * 86400 + 57072,
            "appliance_time": "Mon Nov 23 11:25:58 2026 EST",
            "cpu_10s": 11.0, "cpu_1m": 6.0, "mem_used_pct": 19.0,
            "load_pct": 7.0, "fs_free_encrypted_mb": 900,
            "fs_free_temporary_mb": 250, "fs_total_temporary_mb": 1000,
            "fs_free_internal_mb": 800, "domains_total": 4,
            "domains_down": 1, "objects_down": 3})
        self.assertEqual(sorted(values), sorted(collector.VALUES))
        # Disabled objects are not down; the broken domain is
        self.assertEqual(
            [(o["domain"], o["class"], o["name"], o["event_code"])
             for o in detail["objects_down"]],
            [("default", "Domain", "broken", "0x00000000"),
             ("default", "XMLFirewallService", "fw1", "0x00b30001"),
             ("app", "MultiProtocolGateway", "mpg", "0x123")])
        self.assertEqual([d["name"] for d in detail["domains"]],
                         ["app", "broken", "default", "old"])
        # Only the domains that are up are asked for their objects
        self.assertEqual([c for c in appliance.calls if c[0] == "ObjectStatus"],
                         [("ObjectStatus", "default"), ("ObjectStatus", "app")])
        self.assertEqual(set(detail), {"domains", "objects_down",
                                       "statistics", "objects_checked_at"})
        self.assertEqual(detail["statistics"], "disabled")

    def test_the_other_domains_are_asked_less_often(self):
        first = collector.poll(FakeAppliance("dp1"), now=T0, delay=0)
        again = FakeAppliance("dp1")
        values = collector.poll(again, previous=first["detail"],
                                now=T0 + timedelta(minutes=5), delay=0,
                                object_status_minutes=15)
        self.assertNotIn(("ObjectStatus", "app"), again.calls)
        # The objects of app are kept from the last time
        self.assertEqual(values["objects_down"], 3)
        later = FakeAppliance("dp1")
        collector.poll(later, previous=values["detail"],
                       now=T0 + timedelta(minutes=16), delay=0,
                       object_status_minutes=15)
        self.assertIn(("ObjectStatus", "app"), later.calls)

    def test_older_firmware(self):
        values = collector.poll(FakeAppliance("dp1", old_firmware=True),
                                now=T0, delay=0)
        self.assertEqual((values["firmware"], values["model"],
                          values["delivery"], values["serial"]),
                         ("IDG.10.5.0.23", "52X", None, "S0"))

    def test_requests_to_one_appliance_wait_between_them(self):
        with mock.patch.object(collector.time, "sleep") as sleep:
            collector.poll(FakeAppliance("dp1"), now=T0, delay=0.25)
        self.assertTrue(sleep.call_args_list)
        self.assertEqual({c.args for c in sleep.call_args_list}, {(0.25,)})


class CollectCase(SqliteCase):

    def setUp(self):
        super().setUp()
        registry.add("dp1", "10.0.0.1", poll=True)
        registry.add("dp2", "10.0.0.2", poll=True)
        registry.add("dp3", "10.0.0.3")
        FakeEnvironment.appliances_by_name = {}
        patcher = mock.patch("mast.plugin_utils.params.Environment",
                             FakeEnvironment)
        patcher.start()
        self.addCleanup(patcher.stop)

    def latest(self, name):
        with db.session(write=False) as session:
            return session.get(StatusLatest, registry.by_name(name).id)


class TestCollect(CollectCase):

    def test_no_appliances_means_the_polled_ones(self):
        params = collector.Collect(credentials=["u:p"])
        self.assertEqual(params.appliances, ["dp1", "dp2"])
        self.assertEqual(collector.Collect(appliances=["dp3"]).appliances,
                         ["dp3"])
        registry.update("dp1", poll=False)
        registry.update("dp2", poll=False)
        with self.assertRaises(ValidationError) as found:
            collector.Collect()
        self.assertIn("no appliance is polled", str(found.exception))

    def test_a_passive_command_only_reads(self):
        from mast.cli import Cli
        cli = Cli(provider="t")
        with self.assertRaises(ValueError):
            @cli.command("x", passive=True, effect="change")
            def x(params: collector.Collect):
                pass
        self.assertTrue(collector.cli.commands["collect-status"].passive)
        # Its jobs are passive whoever submits them
        job_id = jobs.submit("datapower.collect-status", {"credentials": ["u:p"]})
        self.assertTrue(jobs.get(job_id).passive)

    def test_a_collect_keeps_the_status(self):
        FakeEnvironment.appliances_by_name["dp2"] = FakeAppliance(
            "dp2", fail=RuntimeError("the appliance is away"))
        with mock.patch.object(collector, "_now", return_value=T0):
            result = collector.collect(credentials=["u:p"], request_delay=0)
        self.assertEqual(result.outcome, PARTIAL)
        rows = {row[0]: row for row in result.data()["rows"]}
        self.assertEqual(rows["dp1"][1:4], ["yes", "IDG.11.0.0.3", "6"])
        self.assertEqual(rows["dp2"][1], "no")
        self.assertIn("the appliance is away", rows["dp2"][-1])
        dp1, dp2 = self.latest("dp1"), self.latest("dp2")
        self.assertTrue(dp1.ok)
        self.assertEqual((dp1.collected_at, dp1.last_ok_at), (T0, T0))
        self.assertEqual((dp1.cpu_1m, dp1.objects_down), (6.0, 3))
        self.assertFalse(dp2.ok)
        self.assertEqual(dp2.error, "RuntimeError: the appliance is away")
        self.assertIsNone(dp2.last_ok_at)
        with db.session(write=False) as session:
            self.assertEqual(len(session.scalars(select(StatusSample)).all()), 2)
        # A later poll of dp1 that fails keeps the values of the last one
        # that worked
        FakeEnvironment.appliances_by_name["dp1"] = FakeAppliance(
            "dp1", fail=RuntimeError("gone"))
        later = T0 + timedelta(minutes=5)
        with mock.patch.object(collector, "_now", return_value=later):
            collector.collect(credentials=["u:p"], request_delay=0)
        dp1 = self.latest("dp1")
        self.assertFalse(dp1.ok)
        self.assertEqual((dp1.collected_at, dp1.last_ok_at), (later, T0))
        self.assertEqual(dp1.cpu_1m, 6.0)

    def test_an_appliance_that_is_not_registered_is_not_kept(self):
        result = collector.collect(appliances=["10.9.9.9"],
                                   credentials=["u:p"], request_delay=0)
        self.assertEqual(result.data()["rows"][0][-1],
                         "Not registered: not kept")
        with db.session(write=False) as session:
            self.assertEqual(session.scalars(select(StatusLatest)).all(), [])

    def test_the_polls_run_with_the_checks_of_a_stored_credential(self):
        """The polls run in threads; each must still refuse what a
        stored credential refuses."""
        appliances = {n: FakeAppliance(n) for n in ("dp1", "dp2")}
        FakeEnvironment.appliances_by_name.update(appliances)
        threads = set()
        real = FakeAppliance.get_status

        def get_status(appliance, provider, domain="default"):
            threads.add(threading.current_thread().name)
            return real(appliance, provider, domain)

        with mock.patch.object(FakeAppliance, "get_status", get_status), \
                stored_credential():
            collector.collect(credentials=["u:p"], request_delay=0)
        self.assertTrue(all(name.startswith("mast-status") for name in threads))
        for appliance in appliances.values():
            self.assertTrue(appliance.strict)
            self.assertTrue(all(appliance.strict), appliance.hostname)
        # The name of the credential is kept with the status
        self.assertIsNone(self.latest("dp1").credential)
        with stored_credential("collector"):
            collector.collect(credentials=["u:p"], request_delay=0)
        self.assertEqual(self.latest("dp1").credential, "collector")
        # And without a stored credential, they do not
        plain = FakeAppliance("dp1")
        FakeEnvironment.appliances_by_name["dp1"] = plain
        collector.collect(appliances=["dp1"], credentials=["u:p"],
                          request_delay=0)
        self.assertFalse(any(plain.strict))

    def test_only_the_listed_values_are_kept(self):
        collector.collect(credentials=["u:p"], request_delay=0)
        row = self.latest("dp1")
        self.assertEqual(set(row.detail), {"domains", "objects_down",
                                           "statistics", "objects_checked_at"})
        for found in row.detail["objects_down"]:
            self.assertEqual(set(found), {"class", "name", "domain",
                                          "event_code"})
        for found in row.detail["domains"]:
            self.assertEqual(set(found), {"name", "op_state", "admin_state"})


class TestCollectPostgres(PostgresMixin, TestCollect):
    pass


class TestRollups(CollectCase):

    def poll_at(self, when, fail=False):
        appliance = FakeAppliance("dp1", fail=RuntimeError("x") if fail
                                  else None)
        FakeEnvironment.appliances_by_name["dp1"] = appliance
        with mock.patch.object(collector, "_now", return_value=when):
            collector.collect(appliances=["dp1"], credentials=["u:p"],
                              request_delay=0)

    def rollups(self, period):
        with db.session(write=False) as session:
            return list(session.scalars(
                select(StatusRollup).where(StatusRollup.period == period)
                .order_by(StatusRollup.start)))

    def test_hourly_and_daily_rollups(self):
        # Three polls in 10:00, one failed; then one in 11:00, which
        # rolls up 10:00
        for minutes, fail in ((0, False), (20, True), (40, False)):
            self.poll_at(T0 + timedelta(minutes=minutes), fail)
        self.assertEqual(self.rollups(HOUR), [])
        self.poll_at(T0 + timedelta(hours=1, minutes=5))
        hours = self.rollups(HOUR)
        self.assertEqual(len(hours), 1)
        hour = hours[0]
        self.assertEqual((hour.start, hour.samples), (T0, 3))
        self.assertAlmostEqual(hour.ok_ratio, 2 / 3)
        self.assertEqual((hour.cpu_avg, hour.cpu_max, hour.objects_down_max),
                         (6.0, 6.0, 3))
        # The next day, the days roll up from the hours
        self.assertEqual(store.roll_up_days(T0 + timedelta(days=1)), 1)
        day = self.rollups(DAY)[0]
        self.assertEqual((day.start, day.samples),
                         (T0.replace(hour=0), 3))
        self.assertEqual(store.roll_up_days(T0 + timedelta(days=1)), 0)

    def test_two_collects_at_once_make_one_rollup(self):
        self.poll_at(T0)
        appliance_id = registry.by_name("dp1").id
        store.roll_up_hours([appliance_id], T0 + timedelta(hours=1))
        # Again, as if another collect had made it first
        with mock.patch.object(store, "_last_start", return_value=None):
            store.roll_up_hours([appliance_id], T0 + timedelta(hours=1))
        self.assertEqual(len(self.rollups(HOUR)), 1)

    def test_retention(self):
        self.poll_at(T0)
        self.poll_at(T0 + timedelta(hours=1))

        def prune(days, dry_run=False):
            with mock.patch.object(retention, "_prune_logs"), \
                    mock.patch.object(retention, "_prune_uploads"):
                return retention.prune(now=T0 + timedelta(days=days),
                                       dry_run=dry_run)
        # The next day: the day is rolled up, and nothing is old yet
        done = prune(1)
        self.assertEqual([done[r] for r in ("status_samples_days",
                                            "status_hourly_days",
                                            "status_daily_days")], [0, 0, 0])
        self.assertEqual(len(self.rollups(DAY)), 1)
        # 40 days later the samples and the hours are old, the day is not
        self.assertEqual(prune(40, dry_run=True)["status_samples_days"], 2)
        done = prune(40)
        self.assertEqual([done[r] for r in ("status_samples_days",
                                            "status_hourly_days",
                                            "status_daily_days")], [2, 1, 0])
        self.assertEqual(self.rollups(HOUR), [])
        self.assertEqual(len(self.rollups(DAY)), 1)
        self.assertEqual(prune(500)["status_daily_days"], 1)

    def test_history_uses_samples_then_rollups(self):
        self.poll_at(T0)
        self.poll_at(T0 + timedelta(hours=1))
        appliance_id = registry.by_name("dp1").id
        kind, points = store.history(appliance_id, T0 - timedelta(hours=1),
                                     T0 + timedelta(hours=2))
        self.assertEqual((kind, len(points)), ("samples", 2))
        self.assertEqual(points[0]["cpu"], 6.0)
        kind, points = store.history(appliance_id, T0 - timedelta(days=3),
                                     T0 + timedelta(hours=2))
        self.assertEqual((kind, len(points)), (HOUR, 1))


class TestRollupsPostgres(PostgresMixin, TestRollups):
    pass


class TestAppliancesPage(WebCase):

    def setUp(self):
        super().setUp()
        self.key_file = os.path.join(tempfile.mkdtemp(dir=self.tmp), "k")
        patcher = mock.patch("mast.jobs.config.credentials_key_file",
                             return_value=self.key_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        registry.add("dp1", "10.0.0.1", poll=True)
        registry.add("dp2", "10.0.0.2", poll=True)
        registry.env_add("lab", members=["dp1", "dp2"])
        credentials.create("collector", "reader", "Read-0nly-pass",
                           "collect", ["lab"], principal=ADMIN)
        credentials.record_test("collector", True, can_write=True,
                                principal=ADMIN)
        FakeEnvironment.appliances_by_name = {}
        patcher = mock.patch("mast.plugin_utils.params.Environment",
                             FakeEnvironment)
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch.object(collector, "_now",
                               return_value=datetime.now(UTC)):
            collector.collect(appliances=["dp1"], credentials=["u:p"],
                              credential=None, request_delay=0)
        # As if the poll had used the stored credential
        with db.session() as session:
            session.get(StatusLatest, registry.by_name("dp1").id) \
                .credential = "collector"

    def call(self, method, path, body=None, client=None):
        client = client or self.client
        return client.open("/api/v1" + path, method=method, json=body,
                           headers={"X-CSRF-Token": self.token(client)})

    def test_it_is_the_first_page(self):
        self.login("viewer")
        answer = self.client.get("/")
        self.assertEqual(answer.status_code, 302)
        self.assertTrue(answer.location.endswith("/appliances"))
        page = self.client.get("/appliances").get_data(as_text=True)
        self.assertIn('href="/appliances"', page)
        # Its script and style sheet load, as modules of the same site
        for name, kind in (("appliances.js", "javascript"),
                           ("appliances.css", "text/css")):
            self.assertIn("/static/status/" + name, page)
            answer = self.client.get("/static/status/" + name)
            self.assertEqual(answer.status_code, 200, name)
            self.assertIn(kind, answer.content_type)
        self.assertIn('<script type="module"', page)

    def test_the_status_of_every_appliance(self):
        self.login("viewer")
        answer = self.call("GET", "/appliances-status").get_json()
        found = {a["name"]: a for a in answer["appliances"]}
        self.assertEqual(sorted(found), ["dp1", "dp2"])
        self.assertIsNone(found["dp2"]["status"])
        status = found["dp1"]["status"]
        self.assertTrue(status["ok"])
        self.assertEqual((status["cpu_1m"], status["objects_down"]), (6.0, 3))
        self.assertTrue(status["credential_can_write"])
        # The fake has Statistics disabled: the page says why no CPU
        self.assertTrue(status["statistics_off"])
        # A viewer does not see the name of the credential
        self.assertNotIn("credential", status)
        self.assertNotIn("detail", status)
        self.assertEqual(found["dp1"]["environments"], ["lab"])
        self.assertEqual(answer["polling"],
                         {"schedules": [], "may_manage": False})
        operator = self.app.test_client()
        self.login("operator", client=operator)
        status = {a["name"]: a for a in self.call(
            "GET", "/appliances-status", client=operator)
            .get_json()["appliances"]}["dp1"]["status"]
        self.assertEqual(status["credential"], "collector")

    def test_one_appliance(self):
        self.login("viewer")
        answer = self.call("GET", "/appliances-status/dp1?hours=24")
        self.assertEqual(answer.status_code, 200)
        found = answer.get_json()
        self.assertEqual((found["kind"], len(found["points"]), found["hours"]),
                         ("samples", 1, 24))
        self.assertEqual(len(found["status"]["detail"]["objects_down"]), 3)
        self.assertEqual(self.call("GET", "/appliances-status/nothing")
                         .status_code, 404)
        for hours in ("x", "0", str(24 * 401)):
            self.assertEqual(self.call(
                "GET", "/appliances-status/dp1?hours=" + hours)
                .status_code, 422)

    def test_an_admin_starts_the_polling(self):
        operator = self.app.test_client()
        self.login("operator", client=operator)
        self.assertEqual(self.call("POST", "/appliances-status/polling",
                                   {"credential": "collector"},
                                   client=operator).status_code, 403)
        self.login("admin")
        self.assertEqual(self.call("POST", "/appliances-status/polling",
                                   {}).status_code, 422)
        self.assertEqual(self.call("POST", "/appliances-status/polling",
                                   {"credential": "collector",
                                    "minutes": 7}).status_code, 422)
        answer = self.call("POST", "/appliances-status/polling",
                           {"credential": "collector", "minutes": 5})
        self.assertEqual(answer.status_code, 200, answer.get_data())
        info = schedules.get(status_page.SCHEDULE, principal=ADMIN)
        self.assertEqual((info.command, info.cron, info.enabled,
                          info.params["credential"]),
                         ("datapower.collect-status", "*/5 * * * *", True, "collector"))
        # Again: the same schedule, changed and enabled
        schedules.update(status_page.SCHEDULE, principal=ADMIN, enabled=False)
        self.call("POST", "/appliances-status/polling",
                  {"credential": "collector", "minutes": 60})
        info = schedules.get(status_page.SCHEDULE, principal=ADMIN)
        self.assertEqual((info.cron, info.enabled), ("0 * * * *", True))
        polling = self.call("GET", "/appliances-status").get_json()["polling"]
        self.assertEqual(polling["schedules"][0]["name"], "status-poll")
        self.assertTrue(polling["may_manage"])
        # The schedule fires a job of the polled appliances
        job_id = schedules.run_now(status_page.SCHEDULE, principal=ADMIN)
        # A poll is passive: a deployment does not hold it up
        self.assertTrue(jobs.get(job_id).passive)
        self.assertEqual(jobs.get(job_id).params["appliances"],
                         ["dp1", "dp2"])
        with db.session(write=False) as session:
            self.assertEqual(len(session.scalars(select(Schedule)).all()), 1)


class TestMigration(CollectCase):
    """0008 goes down with the status and up again; the appliances
    stay. Removing an appliance removes its status."""

    def tables(self):
        from sqlalchemy import inspect
        engine = db._create_engine(self.url)
        self.addCleanup(engine.dispose)
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())

    def test_down_and_up(self):
        collector.collect(appliances=["dp1"], credentials=["u:p"],
                          request_delay=0)
        store.roll_up_hours([registry.by_name("dp1").id],
                            datetime.now(UTC) + timedelta(hours=2))
        db.downgrade("0007")
        self.assertEqual(db.revision(), "0007")
        self.assertFalse({"status_latest", "status_samples",
                          "status_rollups"} & self.tables())
        db.configure(self.url)
        self.assertEqual([a.name for a in registry.list_appliances()],
                         ["dp1", "dp2", "dp3"])
        self.assertEqual(db.revision(), db.HEAD)
        self.assertEqual(store.latest(), {})
        collector.collect(appliances=["dp1"], credentials=["u:p"],
                          request_delay=0)
        self.assertEqual(len(store.latest()), 1)

    def test_removing_an_appliance_removes_its_status(self):
        collector.collect(appliances=["dp1", "dp2"], credentials=["u:p"],
                          request_delay=0)
        registry.remove("dp1")
        self.assertEqual(list(store.latest()), [registry.by_name("dp2").id])
        with db.session(write=False) as session:
            self.assertEqual(
                {s.appliance_id for s in session.scalars(select(StatusSample))},
                {registry.by_name("dp2").id})


class TestMigrationPostgres(PostgresMixin, TestMigration):
    pass
