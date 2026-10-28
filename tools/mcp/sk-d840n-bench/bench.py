"""The bench side of the MCP server: a client of the serial relay.

The relay (tools/serrelay.ps1) owns COM3 and writes everything the board says
to staging/ctl/serial.log.  A command is handed to it by writing the text
into staging/ctl/sercmd.txt, and the relay transmits it within about a
millisecond and then renames the file to .sent-<time>.

So this module never opens the serial port.  It writes one small file, waits
for the relay's own "sent:" marker to show up in the log - the only reliable
proof that the command really went out - and then reads the tail of the log
until the answer appears.

Why the confirmation step is not optional: writing the file is not the same
as sending it.  The relay may be down, it may be busy with somebody else's
command, or the board may simply not answer.  Without the marker the only
honest answer would be a guess.
"""

import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from . import parsers
except ImportError:                      # run as a plain script
    import parsers


# ------------------------------------------------------------------- errors

class BenchError(RuntimeError):
    """Anything that stops a bench call."""


class BenchBusy(BenchError):
    """Somebody else is using the bench right now."""


class RelayTimeout(BenchError):
    """The relay never confirmed that it sent the command."""


class StateMismatch(BenchError):
    """The board is not in the state the tool needs."""


class CommandRejected(BenchError):
    """The command is on the forbidden list."""


# ------------------------------------------------------------------ helpers

# The loader answers "Unknown command 'xxx' - try 'help'", but two of its
# known commands are not safe to type (tools/ubcmd.sh:13-16): zxboot with no
# arguments takes a Synchronous Abort and resets the CPU, and up_mode get
# reads the MTD label "others".
FORBIDDEN_HEADS = ("zxboot",)
FORBIDDEN_TAILS = ("up_mode get",)


def parse_int(value, name):
    """An integer from an int or from a hex string such as "0x140000"."""
    if isinstance(value, bool):
        raise ValueError("%s must be a number" % name)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError("%s must be a number" % name)
    try:
        return int(text, 0)
    except ValueError:
        raise ValueError("%s is not a number: %r" % (name, value))


def hex32(value):
    """The form the driver wants: base 0 means "140000" is decimal, so the
    0x always goes with it (np.c:1267-1270 lost a round of measurements to
    that)."""
    return "0x%x" % (value & 0xFFFFFFFF)


def check_command(cmd):
    """Refuse the two commands the loader must not be given."""
    text = cmd.strip()
    head = text.split()[0] if text.split() else ""
    if head in FORBIDDEN_HEADS:
        raise CommandRejected(
            "%r resets the CPU on the loader (Synchronous Abort); it is not "
            "on the list of commands this bench may send" % head)
    for tail in FORBIDDEN_TAILS:
        if tail in text:
            raise CommandRejected(
                "%r reads the MTD label 'others'; it is not on the list of "
                "commands this bench may send" % tail)
    return text


# The blocks the driver will read outside the NPPT window (np.c:1175-1194).
# A read that leaves them is not answered with zeros - it faults the bus -
# so the check is made here, before the command is sent.
SOC_BLOCKS = (
    (0x14F01000, 0x00004000),      # MDIO controller 0
    (0x14F02000, 0x00004000),      # MDIO controller 1
    (0x16000000, 0x00010000),      # PON SerDes
    (0x16010000, 0x00010000),      # PON SerDes PLL
    (0x16100000, 0x00100000),      # Uni SerDes
    (0x1A000000, 0x00800000),      # XPCS0
    (0x1B000000, 0x00800000),      # XPCS1
)


def soc_block_ok(addr, length):
    """True when the read is wholly inside one of the driver's blocks."""
    for start, size in SOC_BLOCKS:
        if addr >= start and addr + length <= start + size:
            return True
    return False


# -------------------------------------------------------------------- paths

@dataclass
class BenchPaths:
    """Where the bench keeps its things."""

    root: Path
    ctl: Path
    log: Path
    cmd: Path
    reset: Path
    lock: Path

    @classmethod
    def from_root(cls, root):
        ctl = Path(root) / "staging" / "ctl"
        return cls(
            root=Path(root),
            ctl=ctl,
            log=ctl / "serial.log",
            cmd=ctl / "sercmd.txt",
            reset=ctl / "serreset",
            lock=ctl / ".bench.lock",
        )


def candidate_roots():
    """The places the bench root may be, in the order they are tried."""
    here = Path(__file__).resolve()
    # .../<root>/ci-build-repo/tools/mcp/sk-d840n-bench/bench.py
    guess = here.parents[4] if len(here.parents) > 4 else None
    out = []
    env = os.environ.get("SK_D840N_ROOT")
    if env:
        out.append(Path(env))
    if guess is not None:
        out.append(guess)
    return out


def resolve_root(explicit=None):
    """Where the bench lives: the SK-D840N_ROOT env, then the layout above."""
    tried = []
    if explicit:
        tried.append(Path(explicit))
    else:
        tried = candidate_roots()
    for candidate in tried:
        candidate = Path(candidate).expanduser()
        if (candidate / "staging" / "ctl").is_dir():
            return candidate.resolve()
    raise BenchError(
        "the bench root was not found; set SK_D840N_ROOT to the directory "
        "that holds staging/ctl.  Tried: %s"
        % (", ".join(str(item) for item in tried) or "(nothing)"))


# --------------------------------------------------------------------- lock

class FileLock:
    """The bench's mutex, kept in staging/ctl/.bench.lock.

    The lock is a file created with O_EXCL, so exactly one process can have
    it.  It holds the pid and the time it was taken, because a process that
    was killed leaves the file behind: a lock older than the stale window is
    taken over instead of blocking the bench forever.
    """

    def __init__(self, path, stale_after=600.0):
        self.path = Path(path)
        self.stale_after = stale_after
        self.held = False

    def age(self):
        """How old the lock is, in seconds, or None when there is no lock."""
        try:
            return time.time() - self.path.stat().st_mtime
        except OSError:
            return None

    def _create(self):
        try:
            handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        except OSError:
            return False
        with os.fdopen(handle, "w") as stream:
            stream.write("pid=%d ts=%.3f host=%s\n"
                         % (os.getpid(), time.time(), socket.gethostname()))
        return True

    def acquire(self):
        if self._create():
            self.held = True
            return
        age = self.age()
        if age is None or age <= self.stale_after:
            raise BenchBusy(
                "the bench lock is held by another call (%.0fs old); wait for "
                "it or take a look at %s" % (age or 0.0, self.path))
        # The holder was killed without cleaning up: take it over.
        try:
            self.path.unlink()
        except OSError:
            pass
        if not self._create():
            raise BenchBusy("the bench lock is held by another call")
        self.held = True

    def release(self):
        if not self.held:
            return
        self.held = False
        try:
            self.path.unlink()
        except OSError:
            pass


# -------------------------------------------------------------------- result

@dataclass
class Result:
    """What one bench call brought back."""

    cmd: str
    text: str = ""
    raw: str = ""
    sent_at: str = None
    confirmed: bool = False
    matched: bool = False
    notes: list = field(default_factory=list)

    def as_dict(self):
        return {
            "cmd": self.cmd,
            "sent_at": self.sent_at,
            "confirmed": self.confirmed,
            "matched": self.matched,
            "notes": list(self.notes),
            "output": self.text,
        }


# --------------------------------------------------------------------- bench

class Bench:
    """The bench: a client of the relay that owns COM3."""

    def __init__(self, root=None, offline=None, stale_lock=600.0,
                 state_ttl=10.0):
        self.paths = BenchPaths.from_root(resolve_root(root))
        if offline is None:
            offline = os.environ.get("SK_D840N_BENCH_OFFLINE") == "1"
        self.offline = bool(offline)
        self.stale_lock = stale_lock
        self.state_ttl = state_ttl
        self._gate = threading.Lock()
        self._state = None
        self._state_at = 0.0
        self._state_evidence = []

    # -- the log ----------------------------------------------------------

    def log_size(self):
        try:
            return self.paths.log.stat().st_size
        except OSError:
            return 0

    def read_slice(self, offset):
        """The log from `offset` to the end, without loading the whole file.

        The log is over a hundred megabytes and grows while it is read, so
        it is never loaded whole and never read further back than needed.
        """
        try:
            size = self.paths.log.stat().st_size
        except OSError:
            return ""
        if size <= offset:
            return ""
        try:
            with open(self.paths.log, "rb") as stream:
                stream.seek(offset)
                data = stream.read(size - offset)
        except OSError:
            return ""
        return data.decode("utf-8", errors="replace")

    def tail_text(self, lines=100):
        """The last `lines` lines of the log, read backwards."""
        try:
            size = self.paths.log.stat().st_size
        except OSError:
            return ""
        chunks = []
        position = size
        try:
            with open(self.paths.log, "rb") as stream:
                while position > 0 and len(chunks) < 128:
                    step = min(65536, position)
                    position -= step
                    stream.seek(position)
                    chunks.append(stream.read(step))
        except OSError:
            return ""
        body = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
        body = body.replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(body.split("\n")[-max(1, lines):])

    def recent_markers(self, count=5):
        """The last few of the relay's own bookkeeping lines."""
        out = []
        for raw in self.tail_text(400).split("\n"):
            if parsers.is_relay_marker(raw):
                out.append(parsers.strip_prompt(raw).strip())
        return out[-count:]

    def marker_age(self):
        """How long ago the relay last said something, in seconds.

        The relay only writes when something happens - a command goes out, a
        command is confirmed, the board reboots - so a quiet bench has a
        stale log and that is normal, not a fault.
        """
        for marker in reversed(self.recent_markers(20)):
            match = re.search(r"\b(\d{2}):(\d{2}):(\d{2})\b", marker)
            if not match:
                continue
            now = time.localtime()
            stamp = time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                                 int(match.group(1)), int(match.group(2)),
                                 int(match.group(3)), 0, 0, -1))
            age = time.time() - stamp
            if age < -3600:            # the stamp is from yesterday
                age += 86400
            return round(max(age, 0.0), 1)
        return None

    # -- the lock ---------------------------------------------------------

    def inbox_busy(self, stale_after=120.0):
        """The age of a command waiting in the relay's inbox, if any.

        The relay takes the file within about a millisecond, so one that has
        been sitting there for two minutes is not in flight: the relay is
        down.  A fresh one belongs to somebody else and must not be
        overwritten.
        """
        try:
            stat = self.paths.cmd.stat()
        except OSError:
            return None
        age = time.time() - stat.st_mtime
        if age > stale_after:
            return None
        return age

    def lock_held(self):
        """The age of the bench lock, if somebody holds it."""
        return FileLock(self.paths.lock, self.stale_lock).age()

    # -- delivery ---------------------------------------------------------

    def deliver(self, cmd):
        """Put the command into the relay's inbox, atomically.

        The relay renames the inbox away as soon as it has read it, and on
        Windows that rename needs the delete right on the old file, so the
        replace can lose a race with it.  That is why the replace is
        retried, the same way staging/ctl/win1xx.sh does it.
        """
        tmp = self.paths.ctl / (".sercmd.mcp.%d.tmp" % os.getpid())
        last = None
        for _ in range(5):
            try:
                with open(tmp, "w", encoding="utf-8", newline="") as stream:
                    stream.write(cmd)
                os.replace(str(tmp), str(self.paths.cmd))
                return
            except PermissionError as exc:
                last = exc
                time.sleep(0.2)
        raise BenchBusy("the command could not be handed to the relay: %s" % last)

    # -- one call ---------------------------------------------------------

    def run(self, cmd, expect=None, timeout=15.0, settle=1.0, settle_after=0.0):
        """Send one command and bring back what the board said.

        `expect` is a regular expression: the call waits until the answer
        shows it, and gives up with the partial answer after `timeout`.
        The wait starts *after* the relay's own "sent:" marker, so a pattern
        that also matches the marker itself cannot end the wait too early.

        `settle_after` is the extra time to wait after the answer showed up,
        for the commands whose answer has no known last line.
        """
        cmd = check_command(cmd)
        if self.offline:
            return self._offline_result(cmd, expect)

        notes = []
        busy = self.inbox_busy()
        if busy is not None:
            raise BenchBusy(
                "the relay's inbox holds a command of somebody else (%.1fs "
                "old); the bench is busy, wait for it" % busy)

        with self._gate:
            lock = FileLock(self.paths.lock, self.stale_lock)
            lock.acquire()
            try:
                offset = self.log_size()
                self.deliver(cmd)

                sent_at, marker_at, confirmed = self._wait_sent(offset, cmd, timeout)
                if not confirmed:
                    raise RelayTimeout(
                        "the relay did not confirm that it sent %r within %.0fs; "
                        "the relay may be down - look at staging/ctl/relayloop.log "
                        "and at the tail of staging/ctl/serial.log"
                        % (cmd, timeout))

                if expect is not None:
                    body, matched = self._wait_expect(offset, marker_at, expect, timeout)
                    if not matched:
                        refusal = parsers.find_shell_error(body[marker_at:])
                        if refusal:
                            notes.append("the board refused the command: %s" % refusal)
                        else:
                            notes.append("the answer did not match %r before the "
                                         "timeout; the partial output is included" % expect)
                    elif settle_after > 0:
                        time.sleep(settle_after)
                        body = parsers.normalize(self.read_slice(offset))
                else:
                    time.sleep(settle)
                    body = parsers.normalize(self.read_slice(offset))
                    matched = True
            finally:
                lock.release()

        return Result(
            cmd=cmd,
            text=parsers.strip_noise(body, echo=cmd),
            raw=body,
            sent_at=sent_at,
            confirmed=True,
            matched=matched,
            notes=notes,
        )

    def _wait_sent(self, offset, cmd, timeout):
        """Wait for the relay's own "sent:" marker.

        The relay writes "=== >>> HH:MM:SS sent: <cmd> ===" right after it
        has handed the text to the port, and that is the only evidence that
        the command really went out.  The .sent-* files cannot be used for
        this: the relay's clean-up keeps the twenty oldest and deletes the
        newest (tools/serrelay.ps1:168-170), so today's are gone.
        """
        pattern = re.compile(
            r"===\s*>>>\s*(\d{2}:\d{2}:\d{2})\s+sent:\s*%s\s*===" % re.escape(cmd))
        deadline = time.time() + timeout
        while time.time() < deadline:
            body = parsers.normalize(self.read_slice(offset))
            match = pattern.search(body)
            if match:
                # The wait for the answer starts after the marker, not at
                # it: the marker sits on the shell's prompt line, and a
                # pattern that matches that line would end the wait at once.
                return match.group(1), match.end(), True
            time.sleep(0.05)
        return None, 0, False

    def _wait_expect(self, offset, marker_at, expect, timeout):
        # The answer is looked for in the normalised text: the board's shell
        # puts colour codes around every field, so a pattern written for a
        # plain console would not match otherwise.
        #
        # The shell's own refusal ends the wait too.  Without that, a
        # command the driver turns down ("ash: write error: Invalid
        # argument") looks like silence and the call waits out its whole
        # timeout.
        pattern = re.compile(expect, re.M)
        deadline = time.time() + timeout
        body = ""
        while True:
            body = parsers.normalize(self.read_slice(offset))
            if pattern.search(body, marker_at):
                return body, True
            if parsers.find_shell_error(body[marker_at:]):
                return body, False
            if time.time() >= deadline:
                return body, False
            time.sleep(0.1)

    # -- the state --------------------------------------------------------

    def board_state(self, refresh=False):
        """Which of the two the board is in: loader, kernel or unknown.

        The probe is `uname -a`.  The loader has no uname and says so; the
        kernel answers with its uname line.  The answer is cached for ten
        seconds, because a probe is a round trip over a 115200 baud line and
        the state does not change that fast.

        The wait is on the *answer*, not on the word "Linux": while the board
        is booting, the log is full of "Linux version ..." and a looser
        wait would call that a kernel long before its shell is up - and
        every kernel tool would then send its command into a board that is
        not listening yet.
        """
        if not refresh and self._state is not None:
            if time.time() - self._state_at < self.state_ttl:
                return self._state
        if self.offline:
            body = self._offline_slice()
            state = parsers.detect_state(body)
        else:
            body = ""
            state = "unknown"
            try:
                result = self.run(
                    "uname -a",
                    expect=r"Unknown command 'uname'|Linux\s+ImmortalWrt",
                    timeout=40.0, settle=0.5)
                body = result.raw
                state = parsers.detect_state(body)
            except BenchError:
                # The board did not answer the probe: it is booting, busy or
                # unreachable.  "unknown" makes every tool refuse, which is
                # the right answer in all three cases.
                state = "unknown"
        self._state = state
        self._state_at = time.time()
        self._state_evidence = [
            parsers.strip_prompt(line).strip()
            for line in parsers.lines_of(body)
            if "Unknown command" in line or "Linux ImmortalWrt" in line
        ][:4]
        return state

    def state_evidence(self):
        return list(self._state_evidence)

    def require_state(self, want):
        """Refuse to act when the board is not where the tool needs it."""
        state = self.board_state()
        if state != want:
            if want == "loader":
                hint = ("the board is in the %s state; the loader's md is only "
                        "reachable at the loader's prompt (boot the kernel's "
                        "image with bootm, or reset the board)" % state)
            else:
                hint = ("the board is in the %s state; the kernel's debugfs "
                        "console only exists once the kernel is up" % state)
            raise StateMismatch(hint)
        return state

    # -- offline ----------------------------------------------------------

    def _offline_slice(self):
        """The saved log slice the offline mode reads instead of the board."""
        path = os.environ.get("SK_D840N_BENCH_OFFLINE_LOG")
        if path:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as stream:
                    return stream.read()
            except OSError:
                return ""
        return self.tail_text(400)

    def _offline_result(self, cmd, expect):
        body = self._offline_slice()
        matched = True
        if expect is not None:
            matched = bool(re.search(expect, body, re.M))
        return Result(
            cmd=cmd,
            text=parsers.strip_noise(body, echo=cmd),
            raw=body,
            sent_at=None,
            confirmed=False,
            matched=matched,
            notes=["offline: nothing was sent to the board"],
        )

    # -- the report -------------------------------------------------------

    def status(self):
        """Everything an agent needs to know before it touches the bench."""
        try:
            log_size = self.paths.log.stat().st_size
            log_age = time.time() - self.paths.log.stat().st_mtime
        except OSError:
            log_size = 0
            log_age = None
        state = None
        try:
            state = self.board_state()
        except BenchError:
            state = "unknown"
        busy = self.inbox_busy()
        return {
            "root": str(self.paths.root),
            "ctl": str(self.paths.ctl),
            "offline": self.offline,
            "state": state,
            "state_evidence": self.state_evidence(),
            "relay": {
                "log": str(self.paths.log),
                "log_size": log_size,
                "log_age_s": None if log_age is None else round(log_age, 1),
                "last_marker_age_s": self.marker_age(),
                "markers": self.recent_markers(5),
                "note": ("the log only grows when the board or the relay says "
                         "something, so a quiet bench has a stale mtime and "
                         "that is normal - the relay is alive if it answers "
                         "the next command"),
            },
            "inbox": {
                "path": str(self.paths.cmd),
                "busy": busy is not None,
                "age_s": None if busy is None else round(busy, 1),
            },
            "lock": {
                "path": str(self.paths.lock),
                "held": self.lock_held() is not None,
                "age_s": None if self.lock_held() is None else round(self.lock_held(), 1),
            },
            "busy": busy is not None,
        }
