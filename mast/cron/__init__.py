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
# Copyright 2015-2024, McIndi Solutions, All rights reserved.

"""
_module_: `mast.cron`

This module does not provide any user-facing functionality. Interfacing
with this module should be done through the file `$MAST_HOME/etc/crontab`.

This module provides a `mastd_plugin` which will check for the existence
of `$MAST_HOME/etc/crontab`. If this file exists, it is scanned for
scheduled jobs. If scheduled jobs are present, they are checked to see
if they are due. If the scheduled job is due, then it is executed.

The file in `$MAST_HOME/etc/crontab` is expected to be a newline-seperated
list of [cron expressions](https://en.wikipedia.org/wiki/Cron#Format),
each followed by a command. The times are in UTC. The file is read at
the start of each minute, without the need to reload mastd. See
`mast.cron.Plugin` for how the tasks are run.

The schedules of the job queue (`mast-jobs schedule-add`, Admin ->
Schedules) are not in this file: the workers queue their jobs (see
`mast.jobs.worker`). This module parses their cron expressions.
"""

import os
import time
from mast.logging import make_logger
import datetime
import calendar
import threading
import platform
import subprocess
from mast import __version__

from mast.config import MAST_HOME

mast_home = MAST_HOME

__all__ = ["CronExpression", "parse_atom", "DEFAULT_EPOCH", "SUBSTITUTIONS",
           "parse_schedule", "is_due", "next_runs"]
__license__ = "Public Domain"

DAY_NAMES = list(zip(('sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'), list(range(7))))
MINUTES = (0, 59)
HOURS = (0, 23)
DAYS_OF_MONTH = (1, 31)
MONTHS = (1, 12)
DAYS_OF_WEEK = (0, 6)
L_FIELDS = (DAYS_OF_WEEK, DAYS_OF_MONTH)
FIELD_RANGES = (MINUTES, HOURS, DAYS_OF_MONTH, MONTHS, DAYS_OF_WEEK)
MONTH_NAMES = list(zip(('jan', 'feb', 'mar', 'apr', 'may', 'jun',
                   'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), list(range(1, 13))))
DEFAULT_EPOCH = (1970, 1, 1, 0, 0, 0)
SUBSTITUTIONS = {
    "@yearly": "0 0 1 1 *",
    "@anually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *"
}


class CronExpression(object):
    """
    _class_: `mast.cron.CronExpression(object)`

    This class represents a single cron expression. This should not
    need to be used directly, please use `$MAST_HOME/etc/crontab` to
    modify the behavior of this module.
    """
    def __init__(self, line, epoch=DEFAULT_EPOCH, epoch_utc_offset=0):
        """
        _method_: `mast.cron.CronExpression.__init__(self, line, epoch=DEFAULT_EPOCH, epoch_utc_offset=0)`

        Instantiates a CronExpression object with an optionally defined epoch.
        If the epoch is defined, the UTC offset can be specified one of two
        ways: as the sixth element in 'epoch' or supplied in epoch_utc_offset.
        The epoch should be defined down to the minute sorted by
        descending significance.
        """
        for key, value in list(SUBSTITUTIONS.items()):
            if line.startswith(key):
                line = line.replace(key, value)
                break

        fields = line.split(None, 5)
        if len(fields) == 5:
            fields.append('')

        minutes, hours, dom, months, dow, self.comment = fields

        dow = dow.replace('7', '0').replace('?', '*')
        dom = dom.replace('?', '*')

        for monthstr, monthnum in MONTH_NAMES:
            months = months.lower().replace(monthstr, str(monthnum))

        for dowstr, downum in DAY_NAMES:
            dow = dow.lower().replace(dowstr, str(downum))

        self.string_tab = [minutes, hours, dom.upper(), months, dow.upper()]
        self.compute_numtab()
        if len(epoch) == 5:
            y, mo, d, h, m = epoch
            self.epoch = (y, mo, d, h, m, epoch_utc_offset)
        else:
            self.epoch = epoch

    def __str__(self):
        """
        _method_: `mast.cron.CronExpression.__str__(self)`

        Returns a `str` representation of this cron Expression.
        """
        base = self.__class__.__name__ + "(%s)"
        cron_line = self.string_tab + [str(self.comment)]
        if not self.comment:
            cron_line.pop()
        arguments = '"' + ' '.join(cron_line) + '"'
        if self.epoch != DEFAULT_EPOCH:
            return base % (arguments + ", epoch=" + repr(self.epoch))
        else:
            return base % arguments

    def __repr__(self):
        """
        _method_: `mast.cron.CronExpression.__repr__(self)`

        Returns a rerpresentation (same as `__str__`) of
        this cron expression.
        """
        return str(self)

    def compute_numtab(self):
        """
        _method_: `mast.cron.CronExpression.compute_numtab(self)`

        Recomputes the sets for the static ranges of the trigger time.

        This method should only be called by the user if the string_tab
        member is modified.
        """
        self.numerical_tab = []

        for field_str, span in zip(self.string_tab, FIELD_RANGES):
            split_field_str = field_str.split(',')
            if len(split_field_str) > 1 and "*" in split_field_str:
                raise ValueError("\"*\" must be alone in a field.")

            unified = set()
            for cron_atom in split_field_str:
                # parse_atom only handles static cases
                for special_char in ('%', '#', 'L', 'W'):
                    if special_char in cron_atom:
                        break
                else:
                    unified.update(parse_atom(cron_atom, span))

            self.numerical_tab.append(unified)

        if self.string_tab[2] == "*" and self.string_tab[4] != "*":
            self.numerical_tab[2] = set()

    def check_trigger(self, date_tuple, utc_offset=0):
        """
        _method_: `mast.cron.CronExpression.check_trigger(self, date_tuple, utc_offset=0)`

        Returns boolean indicating if the trigger is active at the given time.
        The date tuple should be in the local time. Unless periodicities are
        used, utc_offset does not need to be specified. If periodicities are
        used, specifically in the hour and minutes fields, it is crucial that
        the utc_offset is specified.
        """
        year, month, day, hour, mins = date_tuple
        given_date = datetime.date(year, month, day)
        zeroday = datetime.date(*self.epoch[:3])
        last_dom = calendar.monthrange(year, month)[-1]
        dom_matched = True

        # In calendar and datetime.date.weekday, Monday = 0
        given_dow = (datetime.date.weekday(given_date) + 1) % 7
        first_dow = (given_dow + 1 - day) % 7

        # Figure out how much time has passed from the epoch to the given date
        utc_diff = utc_offset - self.epoch[5]
        mod_delta_yrs = year - self.epoch[0]
        mod_delta_mon = month - self.epoch[1] + mod_delta_yrs * 12
        mod_delta_day = (given_date - zeroday).days
        mod_delta_hrs = hour - self.epoch[3] + mod_delta_day * 24 + utc_diff
        mod_delta_min = mins - self.epoch[4] + mod_delta_hrs * 60

        # Makes iterating through like components easier.
        quintuple = list(zip(
            (mins, hour, day, month, given_dow),
            self.numerical_tab,
            self.string_tab,
            (mod_delta_min, mod_delta_hrs, mod_delta_day, mod_delta_mon,
                mod_delta_day),
            FIELD_RANGES))

        for value, valid_values, field_str, delta_t, field_type in quintuple:
            # All valid, static values for the fields are stored in sets
            if value in valid_values:
                continue

            # The following for loop implements the logic for context
            # sensitive and epoch sensitive constraints. break statements,
            # which are executed when a match is found, lead to a continue
            # in the outer loop. If there are no matches found, the given date
            # does not match expression constraints, so the function returns
            # False as seen at the end of this for...else... construct.
            for cron_atom in field_str.split(','):
                if cron_atom[0] == '%':
                    if not(delta_t % int(cron_atom[1:])):
                        break

                elif field_type == DAYS_OF_WEEK and '#' in cron_atom:
                    D, N = int(cron_atom[0]), int(cron_atom[2])
                    # Computes Nth occurence of D day of the week
                    if (((D - first_dow) % 7) + 1 + 7 * (N - 1)) == day:
                        break

                elif field_type == DAYS_OF_MONTH and cron_atom[-1] == 'W':
                    target = min(int(cron_atom[:-1]), last_dom)
                    lands_on = (first_dow + target - 1) % 7
                    if lands_on == 0:
                        # Shift from Sun. to Mon. unless Mon. is next month
                        target += 1 if target < last_dom else -2
                    elif lands_on == 6:
                        # Shift from Sat. to Fri. unless Fri. in prior month
                        target += -1 if target > 1 else 2

                    # Break if the day is correct, and target is a weekday
                    if target == day and (first_dow + target - 7) % 7 > 1:
                        break

                elif field_type in L_FIELDS and cron_atom.endswith('L'):
                    # In dom field, L means the last day of the month
                    target = last_dom

                    if field_type == DAYS_OF_WEEK:
                        # Calculates the last occurence of given day of week
                        desired_dow = int(cron_atom[:-1])
                        target = (((desired_dow - first_dow) % 7) + 29)
                        target -= 7 if target > last_dom else 0

                    if target == day:
                        break
            else:
                # See 2010.11.15 of CHANGELOG
                if field_type == DAYS_OF_MONTH and self.string_tab[4] != '*':
                    dom_matched = False
                    continue
                elif field_type == DAYS_OF_WEEK and self.string_tab[2] != '*':
                    # If we got here, then days of months validated so it does
                    # not matter that days of the week failed.
                    return dom_matched

                # None of the expressions matched which means this field fails
                return False

        # Arriving at this point means the date landed within the constraints
        # of all fields; the associated trigger should be fired.
        return True


def parse_atom(parse, minmax):
    """
    _function_: `mast.cron.parse_atom(parse, minmax)`

    Returns a set containing valid values for a given cron-style range of
    numbers. The 'minmax' arguments is a two element iterable containing the
    inclusive upper and lower limits of the expression.

    Examples:

        :::python
        >>> parse_atom("1-5",(0,6))
        set([1, 2, 3, 4, 5])

        >>> parse_atom("*/6",(0,23))
        set([0, 6, 12, 18])

        >>> parse_atom("18-6/4",(0,23))
        set([18, 22, 0, 4])

        >>> parse_atom("*/9",(0,23))
        set([0, 9, 18])
    """
    parse = parse.strip()
    increment = 1
    if parse == '*':
        return set(range(minmax[0], minmax[1] + 1))
    elif parse.isdigit():
        # A single number still needs to be returned as a set
        value = int(parse)
        if value >= minmax[0] and value <= minmax[1]:
            return set((value,))
        else:
            raise ValueError("Invalid bounds: \"%s\"" % parse)
    elif '-' in parse or '/' in parse:
        divide = parse.split('/')
        subrange = divide[0]

        if len(divide) == 2:
            # Example: 1-3/5 or */7 increment should be 5 and 7 respectively
            increment = int(divide[1])

        if '-' in subrange:
            # Example: a-b
            prefix, suffix = [int(n) for n in subrange.split('-')]
            if prefix < minmax[0] or suffix > minmax[1]:
                raise ValueError("Invalid bounds: \"%s\"" % parse)
        elif subrange == '*':
            # Include all values with the given range
            prefix, suffix = minmax
        else:
            raise ValueError("Unrecognized symbol: \"%s\"" % subrange)

        if prefix < suffix:
            # Example: 7-10
            return set(range(prefix, suffix + 1, increment))
        else:
            # Example: 12-4/2; (12, 12 + n, ..., 12 + m*n) U (n_0, ..., 4)
            noskips = list(range(prefix, minmax[1] + 1))
            noskips += list(range(minmax[0], suffix + 1))
            return set(noskips[::increment])


def parse_schedule(text):
    """
    _function_: `mast.cron.parse_schedule(text)`

    Return the `CronExpression` of the five fields `text` (minute,
    hour, day of the month, month, day of the week, in UTC; or one of
    `SUBSTITUTIONS`, such as `@daily`). Raises ValueError, with a
    message for a person, if it is not one.
    """
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("A schedule needs a cron expression, for example "
                         "'0 2 * * *' (02:00 UTC each day).")
    try:
        expression = CronExpression(text)
        if expression.comment.strip():
            raise ValueError("it has more than five fields")
        # The special atoms are checked only when a time is checked
        expression.check_trigger((2026, 1, 1, 0, 0))
    except Exception as exc:
        raise ValueError(
            "{!r} is not a cron expression of five fields (minute hour "
            "day-of-month month day-of-week): {}".format(text, exc))
    return expression


def is_due(expression, when):
    """
    _function_: `mast.cron.is_due(expression, when)`

    Return True if the `CronExpression` `expression` is due in the
    minute of the `datetime` `when` (in UTC).
    """
    when = when.astimezone(datetime.timezone.utc)
    return bool(expression.check_trigger(
        (when.year, when.month, when.day, when.hour, when.minute)))


def next_runs(expression, after, count=5, days=732):
    """
    _function_: `mast.cron.next_runs(expression, after, count=5, days=732)`

    Return the next `count` times (UTC `datetime`s) after the
    `datetime` `after` at which the `CronExpression` `expression` is
    due, looking at most `days` ahead. Only the minutes and hours that
    the expression can match are tried, so a rare schedule is quick.
    """
    if isinstance(expression, str):
        expression = parse_schedule(expression)
    after = after.astimezone(datetime.timezone.utc).replace(
        second=0, microsecond=0)
    minutes_str, hours_str = expression.string_tab[:2]

    def candidates(field, values, span):
        if "%" in field or not values:
            return list(range(span[0], span[1] + 1))
        return sorted(values)

    minutes = candidates(minutes_str, expression.numerical_tab[0], MINUTES)
    hours = candidates(hours_str, expression.numerical_tab[1], HOURS)
    found = []
    day = after.date()
    for _ in range(days + 1):
        for hour in hours:
            for minute in minutes:
                when = datetime.datetime(day.year, day.month, day.day, hour,
                                         minute, tzinfo=datetime.timezone.utc)
                if when <= after:
                    continue
                if expression.check_trigger(
                        (day.year, day.month, day.day, hour, minute)):
                    found.append(when)
                    if len(found) >= count:
                        return found
        day += datetime.timedelta(days=1)
    return found


def _minute_tuple(minute):
    """The UTC (year, month, day, hour, minute) of a minute since 1970."""
    return time.gmtime(minute * 60)[:5]


class Plugin(threading.Thread):
    """
    _class_: `mast.cron.Plugin(threading.Thread)`

    This class is a mastd plugin, which is a subclass of
    `threading.Thread`. At the start of each minute (UTC), it reads
    `$MAST_HOME/etc/crontab` and starts the tasks that are due.

    * Each task runs in its own thread, so a long task does not delay
    the others.
    * A task is not started again while it still runs from an earlier
    minute. That is logged.
    * If the scheduler checks late (the server was busy or suspended,
    or its clock jumped), it starts the tasks of the minutes that it
    missed, once each, up to `CATCH_UP_MINUTES` minutes back. If the
    clock goes back, the minutes that were already checked are not
    checked again.
    * The exit status and the output of each task are logged in the
    log `mast.cron`.
    * Empty lines and lines that start with `#` are ignored. A line that
    is not a valid task is logged when the file changes.
    """
    # How many missed minutes to catch up. Beyond that, the missed
    # minutes are skipped, and that is logged.
    CATCH_UP_MINUTES = 60

    def __init__(self, crontab=os.path.join(mast_home, "etc", "crontab"),
                 clock=time.time):
        """
        _method_: `mast.cron.Plugin.__init__(self, crontab=os.path.join(mast_home, "etc", "crontab"), clock=time.time)`

        Parameters:

        * `crontab`: The file of tasks.
        * `clock`: A function that returns the time, as `time.time`
        does. For the tests.

        To stop this thread call the stop() method of your instance."""
        super(Plugin, self).__init__()
        self.daemon = True
        self.crontab = crontab
        self.clock = clock
        self.logger = make_logger("mast.cron")
        self._stopping = threading.Event()
        self._aborted = threading.Event()
        self._lock = threading.Lock()
        # The command of each running task -> its thread
        self._running = {}
        self._last_minute = None
        self._last_text = None
        self._tasks = []

    def stop(self):
        """
        _method_: `mast.cron.Plugin.stop(self)`

        Start no more tasks. The thread ends when the running tasks
        end.
        """
        self._stopping.set()

    def abort(self):
        """
        _method_: `mast.cron.Plugin.abort(self)`

        End the thread now. The running tasks are not waited for.
        """
        self._aborted.set()
        self._stopping.set()

    def running(self):
        """
        _method_: `mast.cron.Plugin.running(self)`

        Return the commands of the tasks that run now.
        """
        with self._lock:
            return [command for command, thread in self._running.items()
                    if thread.is_alive()]

    def read_crontab(self):
        """
        _method_: `mast.cron.Plugin.read_crontab(self)`

        Return the tasks of the crontab, a list of `CronExpression`. The
        file is parsed again only when it changed, and the lines that are
        not valid tasks are logged then.
        """
        try:
            with open(self.crontab, "r") as fin:
                text = fin.read()
        except FileNotFoundError:
            if self._last_text is not None or self._last_minute is None:
                self.logger.info(
                    "There is no crontab. To schedule tasks, create the "
                    "file {}".format(self.crontab))
            self._last_text, self._tasks = None, []
            return []
        if text == self._last_text:
            return self._tasks
        tasks = []
        for number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                task = CronExpression(line)
                if not task.comment.strip():
                    raise ValueError("there is no command")
            except Exception as e:
                self.logger.error(
                    "Line {} of {} is not a valid task, and it is "
                    "ignored: {!r} ({})".format(number, self.crontab, line, e))
                continue
            tasks.append(task)
        self.logger.info("Read {} task(s) from {}".format(
            len(tasks), self.crontab))
        self._last_text, self._tasks = text, tasks
        return tasks

    def check(self, minute):
        """
        _method_: `mast.cron.Plugin.check(self, minute)`

        Start the tasks that are due in `minute` (minutes since 1970,
        UTC) and in the minutes that were missed since the last check.
        Return the commands that were started.
        """
        last = self._last_minute
        if last is None:
            minutes = [minute]
        elif minute <= last:
            # Already checked, or the clock went back
            return []
        elif minute - last > self.CATCH_UP_MINUTES:
            self.logger.warning(
                "The scheduler did not check for {} minutes. The tasks of "
                "those minutes were not started.".format(minute - last - 1))
            minutes = [minute]
        else:
            minutes = list(range(last + 1, minute + 1))
        if len(minutes) > 1:
            self.logger.warning(
                "The scheduler checks {} minute(s) late. It starts the "
                "tasks of those minutes now.".format(len(minutes) - 1))
        tasks = self.read_crontab()
        self._last_minute = minute
        started = []
        for task in tasks:
            try:
                due = any(task.check_trigger(_minute_tuple(m))
                          for m in minutes)
            except Exception:
                self.logger.exception(
                    "Could not check whether the task {!r} is due".format(
                        task.comment.strip()))
                continue
            if due and self._start(task.comment.strip()):
                started.append(task.comment.strip())
        return started

    def _start(self, command):
        with self._lock:
            thread = self._running.get(command)
            if thread is not None and thread.is_alive():
                self.logger.warning(
                    "Task '{}' is due, but it still runs from an earlier "
                    "minute. It is not started again.".format(command))
                return False
            thread = threading.Thread(
                target=self._run_task, args=(command,),
                name="mast-cron-task", daemon=True)
            self._running[command] = thread
        thread.start()
        return True

    def _run_task(self, command):
        self.logger.info("Task '{}' started".format(command))
        kwargs = {}
        if "Windows" in platform.system():
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = startupinfo
        try:
            result = subprocess.run(
                command, shell=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", **kwargs)
        except Exception:
            self.logger.exception(
                "Task '{}' could not be started".format(command))
            return
        output = result.stdout.strip()
        output = "Output:\n{}".format(output) if output else "No output."
        if result.returncode == 0:
            self.logger.info("Task '{}' succeeded. {}".format(
                command, output))
        else:
            self.logger.error(
                "Task '{}' failed with the exit status {}. {}".format(
                    command, result.returncode, output))

    def run(self):
        """
        _method_: `mast.cron.Plugin.run(self)`

        Check for due tasks at the start of each minute until `stop` is
        called, then wait for the running tasks to end (unless `abort` is
        called).
        """
        while not self._stopping.is_set():
            try:
                self.check(int(self.clock() // 60))
            except Exception:
                self.logger.exception(
                    "Could not check for tasks. Trying again next minute.")
            # Wake just after the start of the next minute
            delay = 60.0 - self.clock() % 60 + 0.05
            self._stopping.wait(min(max(delay, 0.05), 60.05))
        while self.running() and not self._aborted.is_set():
            self.logger.info("Waiting for these tasks to end: {}".format(
                ", ".join(self.running())))
            self._aborted.wait(10)
