'''
***
Modified generic daemon class
***

Author:         http://www.jejik.com/articles/2007/02/
                        a_simple_unix_linux_daemon_in_python/www.boxedice.com

License:        http://creativecommons.org/licenses/by-sa/3.0/

Changes:        23rd Jan 2009 (David Mytton <david@boxedice.com>)
                - Replaced hard coded '/dev/null in __init__ with os.devnull
                - Added OS check to conditionally remove code that doesn't
                  work on OS X
                - Added output to console on completion
                - Tidied up formatting
                11th Mar 2009 (David Mytton <david@boxedice.com>)
                - Fixed problem with daemon exiting on Python 2.4
                  (before SystemExit was part of the Exception base)
                13th Aug 2010 (David Mytton <david@boxedice.com>
                - Fixed unhandled exception if PID file is empty
                2026 (McIndi Solutions)
                - stop() sends SIGTERM once and waits for the process
                - request_stop() is called on SIGTERM and SIGINT
                - status(), and start() removes a stale pidfile
'''

# Core modules
import atexit
import os
import sys
import time
import signal


def alive(pid):
    """Return True if the process pid runs."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It runs, as another user
        return True
    return True


def wait_gone(pid, poll_seconds=0.2):
    """Return when the process pid has ended."""
    while alive(pid):
        time.sleep(poll_seconds)


class Daemon(object):
    """
    A generic daemon class.

    Usage: subclass the Daemon class and override the run() method
    """
    def __init__(self, pidfile, stdin=os.devnull,
                 stdout=os.devnull, stderr=os.devnull,
                 home_dir='.', umask=0o22, verbose=1):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.pidfile = pidfile
        self.home_dir = home_dir
        self.verbose = verbose
        self.umask = umask
        self.daemon_alive = True

    def daemonize(self):
        """
        Do the UNIX double-fork magic, see Stevens' "Advanced
        Programming in the UNIX Environment" for details (ISBN 0201563177)
        http://www.erlenstar.demon.co.uk/unix/faq_2.html#SEC16
        """
        try:
            pid = os.fork()
            if pid > 0:
                # Exit first parent
                sys.exit(0)
        except OSError as e:
            sys.stderr.write(
                "fork #1 failed: %d (%s)\n" % (e.errno, e.strerror))
            sys.exit(1)

        # Decouple from parent environment
        os.chdir(self.home_dir)
        os.setsid()
        os.umask(self.umask)

        # Do second fork
        try:
            pid = os.fork()
            if pid > 0:
                # Exit from second parent
                sys.exit(0)
        except OSError as e:
            sys.stderr.write(
                "fork #2 failed: %d (%s)\n" % (e.errno, e.strerror))
            sys.exit(1)

        if sys.platform != 'darwin':  # This block breaks on OS X
            # Redirect standard file descriptors
            sys.stdout.flush()
            sys.stderr.flush()
            si = open(self.stdin, 'r')
            so = open(self.stdout, 'a+')
            if self.stderr:
                se = open(self.stderr, 'a+')
            else:
                se = so
            os.dup2(si.fileno(), sys.stdin.fileno())
            os.dup2(so.fileno(), sys.stdout.fileno())
            os.dup2(se.fileno(), sys.stderr.fileno())

        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, self._on_signal)

        if self.verbose >= 1:
            print("Started")

        # Write pidfile
        atexit.register(
            self.delpid)  # Make sure pid file is removed if we quit
        pid = str(os.getpid())
        with open(self.pidfile, 'w+') as pf:
            pf.write("%s\n" % pid)

    def _on_signal(self, signum, frame):
        self.request_stop()

    def request_stop(self):
        """
        Called in the daemon on SIGTERM and SIGINT. Override it to stop
        gracefully: return from run(). This one exits at once.
        """
        self.daemon_alive = False
        sys.exit()

    def delpid(self):
        try:
            os.remove(self.pidfile)
        except FileNotFoundError:
            pass

    def start(self, *args, **kwargs):
        """
        Start the daemon
        """

        if self.verbose >= 1:
            print("Starting...", flush=True)

        # Check for a pidfile to see if the daemon already runs
        pid = self.get_pid()
        if pid and alive(pid):
            message = "pidfile %s already exists. Is it already running?\n"
            sys.stderr.write(message % self.pidfile)
            sys.exit(1)
        if pid:
            sys.stderr.write(
                "Removing the pidfile %s of the process %d, which is not "
                "running\n" % (self.pidfile, pid))
            self.delpid()

        # Start the daemon
        self.daemonize()
        self.run(*args, **kwargs)

    # Printed by stop() while it waits; %d is the pid
    waiting_message = "Waiting for the process %d to stop."
    # Printed by stop() when Ctrl-C is pressed while it waits
    stop_now_message = "Stopping it now."

    def stop(self, poll_seconds=0.2):
        """
        Stop the daemon: send it SIGTERM once and wait until it has
        ended. On Ctrl-C, send SIGTERM again (which stops the daemon
        now, if it handles a second signal) and wait again.
        """

        if self.verbose >= 1:
            print("Stopping...", flush=True)

        # Get the pid from the pidfile
        pid = self.get_pid()

        if not pid or not alive(pid):
            message = "pidfile %s does not exist. Not running?\n"
            sys.stderr.write(message % self.pidfile)
            self.delpid()
            return  # Not an error in a restart

        try:
            os.kill(pid, signal.SIGTERM)
            print(self.waiting_message % pid, flush=True)
            try:
                wait_gone(pid, poll_seconds)
            except KeyboardInterrupt:
                print(self.stop_now_message, flush=True)
                os.kill(pid, signal.SIGTERM)
                wait_gone(pid, poll_seconds)
        except ProcessLookupError:
            pass
        except PermissionError as err:
            print(str(err))
            sys.exit(1)
        # The daemon removes it, unless it was killed
        self.delpid()

        if self.verbose >= 1:
            print("Stopped")

    def restart(self):
        """
        Restart the daemon
        """
        self.stop()
        self.start()

    def get_pid(self):
        try:
            with open(self.pidfile, 'r') as pf:
                return int(pf.read().strip())
        except (IOError, ValueError):
            return None

    def status(self):
        """
        Print whether the daemon runs, and return the exit status of
        an init script: 0 if it runs, 1 if it does not run but its
        pidfile remains, 3 if it does not run.
        """
        pid = self.get_pid()
        if pid is None:
            print("Not running")
            return 3
        if alive(pid):
            print("Running (pid %d)" % pid)
            return 0
        print("Not running, but the pidfile %s remains (pid %d)" % (
            self.pidfile, pid))
        return 1

    def is_running(self):
        return self.status() == 0

    def run(self):
        """
        You should override this method when you subclass Daemon.
        It will be called after the process has been
        daemonized by start() or restart().
        """
        raise NotImplementedError
