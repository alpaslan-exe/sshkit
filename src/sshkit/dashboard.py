"""Split-pane dashboards: one SSH shell plus live remote monitors.

Backends, picked in this order:

    tmux            macOS, Linux, WSL  - real split panes in the current terminal
    Windows Terminal Windows           - opens a new tab with split panes
    none            anywhere else      - falls back to a plain SSH session
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys

from .compat import IS_WINDOWS, self_command, which_first
from .session import run_ssh_auto


def _pane_argv(alias: str, monitor: str | None, monitors: list[str] | None = None) -> list[str]:
    argv = self_command()
    if monitor is not None:
        return [*argv, "monitor", alias, monitor]
    return [*argv, "monitor", alias, "--combined", *(monitors or [])]


def _main_argv(alias: str) -> list[str]:
    return [*self_command(), "connect", alias, "--no-dashboard"]


def dashboard_backend() -> str | None:
    if which_first("tmux"):
        return "tmux"
    if IS_WINDOWS and which_first("wt"):
        return "wt"
    return None


def dashboard_hint() -> str:
    if IS_WINDOWS:
        return "Install Windows Terminal (winget install Microsoft.WindowsTerminal), or run sshkit inside WSL with tmux."
    if sys.platform == "darwin":
        return "Install it with: brew install tmux"
    return "Install it with your package manager, e.g. apt install tmux"


# --------------------------------------------------------------------------
# tmux
# --------------------------------------------------------------------------


def tmux_session_name(alias: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", alias).strip("_") or "host"
    return f"sshkit_{safe}_{os.getpid()}"


def _tmux_kill(session: str) -> None:
    subprocess.call(
        ["tmux", "kill-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run_dashboard_tmux_combined(alias: str, monitors: list[str]) -> int:
    session = tmux_session_name(alias)
    main_cmd = f"{shlex.join(_main_argv(alias))}; tmux kill-session -t {shlex.quote(session)} 2>/dev/null"
    monitor_cmd = shlex.join(_pane_argv(alias, None, monitors))
    try:
        subprocess.check_call(["tmux", "new-session", "-d", "-s", session, "-n", alias, main_cmd])
        try:
            subprocess.check_call(["tmux", "split-window", "-v", "-p", "35", "-t", f"{session}:0", monitor_cmd])
        except subprocess.CalledProcessError:
            _tmux_kill(session)
            print("Terminal is too small for dashboard panes; falling back to plain SSH.", file=sys.stderr)
            return run_ssh_auto(alias)
        subprocess.call(["tmux", "select-pane", "-t", f"{session}:0.0"])
        return subprocess.call(["tmux", "attach-session", "-t", session])
    except KeyboardInterrupt:
        return 130
    except subprocess.CalledProcessError as exc:
        print(f"Could not start dashboard: {exc}", file=sys.stderr)
        _tmux_kill(session)
        return 1


def _run_dashboard_tmux(alias: str, monitors: list[str]) -> int:
    session = tmux_session_name(alias)
    main_cmd = f"{shlex.join(_main_argv(alias))}; tmux kill-session -t {shlex.quote(session)} 2>/dev/null"
    try:
        subprocess.check_call(["tmux", "new-session", "-d", "-s", session, "-n", alias, main_cmd])
        for option, value in (
            ("pane-border-status", "top"),
            ("pane-border-format", " #{pane_title} "),
            ("pane-border-lines", "simple"),
        ):
            subprocess.call(
                ["tmux", "set-option", "-t", session, option, value],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        first_monitor = monitors[0]
        first_cmd = shlex.join(_pane_argv(alias, first_monitor))
        try:
            bottom_pane = subprocess.check_output(
                ["tmux", "split-window", "-P", "-F", "#{pane_id}", "-v", "-p", "35", "-t", f"{session}:0", first_cmd],
                text=True,
            ).strip()
        except subprocess.CalledProcessError:
            try:
                bottom_pane = subprocess.check_output(
                    ["tmux", "split-window", "-P", "-F", "#{pane_id}", "-v", "-t", f"{session}:0", first_cmd],
                    text=True,
                ).strip()
            except subprocess.CalledProcessError:
                _tmux_kill(session)
                print("Terminal is too small for dashboard panes; falling back to plain SSH.", file=sys.stderr)
                return run_ssh_auto(alias)
        subprocess.call(["tmux", "select-pane", "-t", bottom_pane, "-T", first_monitor.upper()])

        remainder_pane = bottom_pane
        for index, monitor in enumerate(monitors[1:], start=1):
            remaining_after_this = len(monitors) - index
            split_percent = max(20, min(80, round(100 * remaining_after_this / (remaining_after_this + 1))))
            try:
                new_pane = subprocess.check_output(
                    [
                        "tmux",
                        "split-window",
                        "-P",
                        "-F",
                        "#{pane_id}",
                        "-h",
                        "-p",
                        str(split_percent),
                        "-t",
                        remainder_pane,
                        shlex.join(_pane_argv(alias, monitor)),
                    ],
                    text=True,
                ).strip()
                subprocess.call(["tmux", "select-pane", "-t", new_pane, "-T", monitor.upper()])
                remainder_pane = new_pane
            except subprocess.CalledProcessError:
                _tmux_kill(session)
                print(
                    "Terminal is too small for separate monitor panels; falling back to combined monitor pane.",
                    file=sys.stderr,
                )
                return _run_dashboard_tmux_combined(alias, monitors)

        subprocess.call(
            ["tmux", "select-layout", "-t", f"{session}:0", "main-horizontal"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.call(["tmux", "select-pane", "-t", f"{session}:0.0"])
        return subprocess.call(["tmux", "attach-session", "-t", session])
    except KeyboardInterrupt:
        return 130
    except subprocess.CalledProcessError as exc:
        print(f"Could not start dashboard: {exc}", file=sys.stderr)
        _tmux_kill(session)
        return 1


# --------------------------------------------------------------------------
# Windows Terminal
# --------------------------------------------------------------------------


def _run_dashboard_wt(alias: str, monitors: list[str]) -> int:
    """Open a Windows Terminal tab: SSH on top, one monitor pane per monitor.

    Windows Terminal uses ';' to separate sub-commands, so every pane command
    is a plain argv with no shell metacharacters (see `sshkit monitor`).
    """
    wt = which_first("wt")
    argv: list[str] = [wt, "new-tab", "--title", alias, "--", *_main_argv(alias)]
    argv += [";", "split-pane", "--horizontal", "--size", "0.35", "--title", monitors[0].upper(), "--"]
    argv += _pane_argv(alias, monitors[0])
    for monitor in monitors[1:]:
        argv += [";", "split-pane", "--vertical", "--title", monitor.upper(), "--"]
        argv += _pane_argv(alias, monitor)
    try:
        proc = subprocess.run(argv)
    except OSError as exc:
        print(f"Could not start Windows Terminal dashboard: {exc}", file=sys.stderr)
        return run_ssh_auto(alias)
    if proc.returncode != 0:
        print("Windows Terminal refused the dashboard layout; falling back to plain SSH.", file=sys.stderr)
        return run_ssh_auto(alias)
    print(f"[sshkit] Dashboard for {alias} opened in a new Windows Terminal tab.")
    return 0


# --------------------------------------------------------------------------


def run_dashboard(alias: str, monitors: list[str]) -> int:
    if not monitors:
        return run_ssh_auto(alias)
    backend = dashboard_backend()
    if backend == "tmux":
        return _run_dashboard_tmux(alias, monitors)
    if backend == "wt":
        return _run_dashboard_wt(alias, monitors)
    print(
        f"sshkit dashboard mode needs tmux or Windows Terminal. {dashboard_hint()}",
        file=sys.stderr,
    )
    return run_ssh_auto(alias)
