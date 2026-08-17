"""Running ssh, with optional saved-password autofill.

Autofill needs a pseudo terminal, because OpenSSH deliberately reads the
password straight from the tty. Two implementations exist:

    POSIX    os.pty + termios raw mode
    Windows  ConPTY through the bundled `pywinpty` package

If neither is usable the password is copied to the clipboard and plain ssh is
started, so the user only has to paste.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time

from . import clipboard, secrets
from .compat import IS_WINDOWS, mosh_binary, ssh_binary

PASSWORD_PROMPT = re.compile(r"(?i)(password|passphrase).*:\s*$")
HOSTKEY_PROMPT = "are you sure you want to continue connecting"

# A mosh exit this early is a failed bootstrap, not a finished session.
MOSH_FAST_FAIL_SECONDS = 10.0


def _ssh() -> str:
    binary = ssh_binary()
    if not binary:
        print(
            "No OpenSSH client found. Install OpenSSH and make sure 'ssh' is on PATH.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return binary


def ssh_argv(alias: str, remote_args: list[str] | None = None, allocate_tty: bool = False) -> list[str]:
    argv = [_ssh()]
    if allocate_tty:
        argv.append("-tt")
    argv.append(alias)
    argv.extend(remote_args or [])
    return argv


def run_ssh(alias: str, remote_args: list[str] | None = None, allocate_tty: bool = False) -> int:
    try:
        return subprocess.call(ssh_argv(alias, remote_args, allocate_tty))
    except KeyboardInterrupt:
        return 130


def mosh_argv(alias: str) -> list[str]:
    binary = mosh_binary()
    if not binary:
        print(
            "mosh not found. Install mosh and make sure it is on PATH.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    # mosh 1.4's default --experimental-remote-ip=proxy resolves the host
    # through ssh itself, so ssh-config aliases, ports, keys and ProxyJump
    # all keep working.
    return [binary, alias]


def run_mosh(alias: str) -> int:
    try:
        return subprocess.call(mosh_argv(alias))
    except KeyboardInterrupt:
        return 130


def run_mosh_session(alias: str) -> int:
    """Run mosh; if a password is saved, auto-type it at the bootstrap prompt.

    mosh starts mosh-server through a regular ssh child, so the saved-password
    pty wrapper works on its prompt exactly as it does for plain ssh. mosh
    never runs on Windows, so the POSIX wrapper is the only one needed.
    """
    password = secrets.get_password(alias)
    if password and not IS_WINDOWS:
        return _run_with_password_posix(mosh_argv(alias), password)
    return run_mosh(alias)


def mosh_usable(alias: str) -> bool:
    """True when an interactive session for `alias` should use mosh unasked.

    Auto mode stays conservative: only on a real terminal and only for aliases
    without a saved password (password hosts tend to be the legacy boxes least
    likely to run mosh-server, and a failed attempt costs a connect round
    trip). Password autofill itself works fine with mosh — set the host's mosh
    mode to `always` to get it.
    """
    if not mosh_binary():
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    return not secrets.get_password(alias)


def enable_windows_vt() -> None:
    """Turn on ANSI escape handling for the current Windows console."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for handle_id in (-11, -12):  # stdout, stderr
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def terminal_size() -> tuple[int, int]:
    size = os.get_terminal_size() if sys.stdout.isatty() else os.terminal_size((80, 24))
    return size.lines, size.columns


# --------------------------------------------------------------------------
# POSIX
# --------------------------------------------------------------------------


def _wait_status_to_exit_code(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 1


def _run_with_password_posix(argv: list[str], password: str) -> int:
    import pty
    import select
    import signal
    import termios
    import tty

    stdin_fd = sys.stdin.fileno()
    old_term = None
    prompt_buffer = ""
    sent_password_at = -1
    sent_hostkey_at = -1

    try:
        pid, master_fd = pty.fork()
    except OSError as exc:
        print(f"Could not create terminal for ssh: {exc}", file=sys.stderr)
        return 1

    if pid == 0:
        os.execvp(argv[0], argv)

    stdin_is_tty = os.isatty(stdin_fd)
    if stdin_is_tty:
        try:
            old_term = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd)
        except termios.error:
            old_term = None

    def restore_terminal() -> None:
        if old_term is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_term)
            except termios.error:
                pass

    try:
        while True:
            read_fds = [master_fd]
            if stdin_is_tty:
                read_fds.append(stdin_fd)
            try:
                ready, _, _ = select.select(read_fds, [], [])
            except InterruptedError:
                continue

            if master_fd in ready:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
                prompt_buffer = (prompt_buffer + data.decode(errors="ignore"))[-2000:]
                lower = prompt_buffer.lower()

                if HOSTKEY_PROMPT in lower and sent_hostkey_at != len(prompt_buffer):
                    os.write(master_fd, b"yes\r")
                    sent_hostkey_at = len(prompt_buffer)
                    prompt_buffer = ""
                    continue

                if PASSWORD_PROMPT.search(prompt_buffer) and sent_password_at != len(prompt_buffer):
                    os.write(master_fd, (password + "\r").encode())
                    sent_password_at = len(prompt_buffer)
                    prompt_buffer = ""
                    continue

            if stdin_is_tty and stdin_fd in ready:
                try:
                    user_data = os.read(stdin_fd, 4096)
                except OSError:
                    user_data = b""
                if user_data:
                    os.write(master_fd, user_data)

    except KeyboardInterrupt:
        try:
            os.kill(pid, signal.SIGINT)
        except OSError:
            pass
        return 130
    finally:
        restore_terminal()

    try:
        _, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return 0
    return _wait_status_to_exit_code(status)


# --------------------------------------------------------------------------
# Windows (ConPTY)
# --------------------------------------------------------------------------


def winpty_available() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winpty  # noqa: F401, PLC0415

        return True
    except Exception:
        return False


def _run_with_password_windows(argv: list[str], password: str) -> int:
    import msvcrt
    import threading

    from winpty import PtyProcess

    enable_windows_vt()
    rows, cols = terminal_size()
    try:
        proc = PtyProcess.spawn(argv, dimensions=(rows, cols))
    except Exception as exc:
        print(f"Could not create terminal for ssh: {exc}", file=sys.stderr)
        return 1

    state = {"buffer": "", "password_sent": False, "hostkey_sent": False}

    def pump_output() -> None:
        while True:
            try:
                data = proc.read(4096)
            except EOFError:
                return
            except Exception:
                return
            if not data:
                if not proc.isalive():
                    return
                continue
            sys.stdout.write(data)
            sys.stdout.flush()
            state["buffer"] = (state["buffer"] + data)[-2000:]
            lower = state["buffer"].lower()
            if HOSTKEY_PROMPT in lower and not state["hostkey_sent"]:
                proc.write("yes\r")
                state["hostkey_sent"] = True
                state["buffer"] = ""
                continue
            if PASSWORD_PROMPT.search(state["buffer"]) and not state["password_sent"]:
                proc.write(password + "\r")
                state["password_sent"] = True
                state["buffer"] = ""

    reader = threading.Thread(target=pump_output, daemon=True)
    reader.start()

    try:
        while proc.isalive():
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in ("\x00", "\xe0"):  # extended key: read and translate
                    code = msvcrt.getwch()
                    char = _WIN_EXTENDED_KEYS.get(code, "")
                if char:
                    try:
                        proc.write(char)
                    except Exception:
                        break
            else:
                reader.join(0.01)
    except KeyboardInterrupt:
        try:
            proc.write("\x03")
        except Exception:
            pass
        return 130
    finally:
        reader.join(0.2)

    try:
        return int(proc.exitstatus or 0)
    except Exception:
        return 0


# Arrow/navigation keys arrive as a two-byte sequence on the Windows console.
_WIN_EXTENDED_KEYS = {
    "H": "\x1b[A",  # up
    "P": "\x1b[B",  # down
    "M": "\x1b[C",  # right
    "K": "\x1b[D",  # left
    "G": "\x1b[H",  # home
    "O": "\x1b[F",  # end
    "R": "\x1b[2~",  # insert
    "S": "\x1b[3~",  # delete
    "I": "\x1b[5~",  # page up
    "Q": "\x1b[6~",  # page down
}


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------


def password_autofill_mode() -> str:
    """How saved passwords will be delivered on this machine."""
    if not IS_WINDOWS:
        return "pty"
    if winpty_available():
        return "conpty"
    return "clipboard"


def run_ssh_with_password(
    alias: str,
    remote_args: list[str] | None = None,
    allocate_tty: bool = False,
) -> int:
    password = secrets.get_password(alias)
    if not password:
        print(f"No saved password for {alias}.", file=sys.stderr)
        return 1

    argv = ssh_argv(alias, remote_args, allocate_tty)
    mode = password_autofill_mode()

    if mode == "pty":
        return _run_with_password_posix(argv, password)
    if mode == "conpty":
        return _run_with_password_windows(argv, password)

    # Clipboard fallback: no ConPTY available on this Windows machine.
    if clipboard.copy(password):
        print(f"[sshkit] Password for {alias} copied to clipboard - paste it at the prompt.")
    else:
        print(
            f"[sshkit] Saved password for {alias} could not be auto-typed on this system; "
            "type it manually at the prompt.",
            file=sys.stderr,
        )
    return run_ssh(alias, remote_args, allocate_tty)


def run_ssh_auto(
    alias: str,
    remote_args: list[str] | None = None,
    allocate_tty: bool = False,
    use_mosh: bool | None = None,
) -> int:
    """Connect with the best available transport.

    `use_mosh` True forces mosh, False disables it, None (default) picks mosh
    for plain interactive connects when it is usable (see `mosh_usable`).
    Remote commands always go over plain ssh.
    """
    interactive = not remote_args and not allocate_tty
    if interactive and (use_mosh or (use_mosh is None and mosh_usable(alias))):
        started = time.monotonic()
        code = run_mosh_session(alias)
        # A host without mosh-server (or with UDP filtered at connect
        # time) makes the mosh bootstrap fail within seconds. Treat that
        # as "mosh not available for this host" and retry over plain ssh.
        if code in (0, 130) or time.monotonic() - started >= MOSH_FAST_FAIL_SECONDS:
            return code  # clean exit, user interrupt, or a real session
        print(f"[sshkit] mosh could not reach {alias}; falling back to ssh.", file=sys.stderr)
    if secrets.get_password(alias):
        return run_ssh_with_password(alias, remote_args, allocate_tty=allocate_tty)
    return run_ssh(alias, remote_args, allocate_tty)


def quote_remote(remote_command: list[str]) -> str:
    """Join a remote command for execution by the remote POSIX shell."""
    return shlex.join(remote_command)
