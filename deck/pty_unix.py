"""POSIX pseudoterminal transport for the installed Aider executable."""

import codecs
import errno
import fcntl
import os
import select
import signal
import struct
import subprocess
import termios
import threading
import time
import tty


class UnixPTY:
    def __init__(self, argv, cwd, env, cols, rows):
        self._lock = threading.Lock()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        master, slave = os.openpty()
        try:
            tty.setraw(slave)
            self._master = master
            self.set_size(cols, rows)
            self._process = subprocess.Popen(
                argv, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True,
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)

    @property
    def pid(self):
        return self._process.pid

    def isalive(self):
        return self._process.poll() is None

    def get_exitstatus(self):
        return self._process.poll()

    def iseof(self):
        with self._lock:
            return self._master is None

    def read(self, blocking=True):
        # The reader owns its copy until read returns, even if close runs
        # concurrently and another session takes the original descriptor number.
        with self._lock:
            if self._master is None:
                return ""
            fd = os.dup(self._master)
        try:
            if not blocking:
                poll = select.poll()
                poll.register(fd, select.POLLIN)
            while blocking or poll.poll(0):
                try:
                    data = os.read(fd, 4096)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    data = b""  # Linux reports EOF with EIO, macOS with b"".
                if not data:
                    self.cancel_io()
                    return self._decoder.decode(b"", final=True)
                text = self._decoder.decode(data)
                if text:
                    return text
            return ""
        finally:
            os.close(fd)

    def write(self, text):
        data = memoryview(text.encode("utf-8"))
        with self._lock:
            if self._master is None:
                raise OSError(errno.EIO, "The Unix PTY is closed.")
            while data:
                data = data[os.write(self._master, data):]

    def set_size(self, cols, rows):
        with self._lock:
            if self._master is not None:
                size = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self._master, termios.TIOCSWINSZ, size)

    def _signal_group(self, sig):
        try:
            os.killpg(self.pid, sig)
        except ProcessLookupError:
            return False
        return True

    def terminate(self):
        if self._signal_group(signal.SIGTERM):
            deadline = time.monotonic() + 0.2
            while time.monotonic() < deadline:
                self._process.poll()
                # The group can outlive its leader.
                if not self._signal_group(0):
                    break
                time.sleep(0.01)
            else:
                self._signal_group(signal.SIGKILL)
        self._process.wait()
        self.cancel_io()

    def cancel_io(self):
        with self._lock:
            if self._master is not None:
                os.close(self._master)
                self._master = None
