"""Curses host browser."""

from __future__ import annotations

import getpass
import sys
from typing import Any

from . import clipboard, secrets
from .dashboard import run_dashboard
from .model import (
    DEFAULT_MONITORS,
    Host,
    all_hosts,
    enabled_monitors,
    format_monitors,
    host_to_dict,
    load_state,
    managed_hosts_from_state,
    parse_bool,
    parse_monitor_list,
    parse_mosh_mode,
    remove_alias_from_unmanaged_config,
    save_state,
    validate_alias,
    write_ssh_config,
)
from .session import enable_windows_vt, run_ssh_auto, run_ssh_with_password

HELP_LINE = (
    "Enter ssh/default  D dashboard  x force pw  a add  i import  e edit  "
    "d delete  p pin  s password  c copy pw  / filter  q quit"
)


def import_curses() -> Any:
    try:
        import curses  # noqa: PLC0415

        return curses
    except ImportError:  # pragma: no cover - Windows without windows-curses
        print(
            "The interactive browser needs curses.\n"
            "On Windows install it with:  pip install windows-curses\n"
            "(the released sshkit.exe already bundles it)\n"
            "Meanwhile, the command line still works: sshkit list / sshkit connect <alias>",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


class App:
    def __init__(self, stdscr: Any, curses_mod: Any) -> None:
        self.curses = curses_mod
        self.stdscr = stdscr
        self.data = load_state()
        self.hosts: list[Host] = []
        self.selected = 0
        self.filter_text = ""
        self.status = ""
        self.password_cache: dict[str, bool] = {}
        self.reload()

    # -- data ---------------------------------------------------------------

    def reload(self) -> None:
        hosts = all_hosts(self.data)
        if self.filter_text:
            needle = self.filter_text.lower()
            hosts = [
                h
                for h in hosts
                if needle in h.alias.lower()
                or needle in h.hostname.lower()
                or needle in h.user.lower()
                or needle in h.proxy_jump.lower()
                or needle in h.notes.lower()
            ]
        self.hosts = hosts
        if self.selected >= len(self.hosts):
            self.selected = max(0, len(self.hosts) - 1)

    def current(self) -> Host | None:
        if not self.hosts:
            return None
        return self.hosts[self.selected]

    def has_password(self, alias: str) -> bool:
        """Cached lookup: hitting the OS keystore per row per redraw is slow."""
        if alias not in self.password_cache:
            self.password_cache[alias] = bool(secrets.get_password(alias))
        return self.password_cache[alias]

    # -- input --------------------------------------------------------------

    def prompt(self, label: str, default: str = "", secret: bool = False) -> str | None:
        curses = self.curses
        h, w = self.stdscr.getmaxyx()
        curses.echo(not secret)
        curses.curs_set(1)
        self.stdscr.move(h - 2, 0)
        self.stdscr.clrtoeol()
        prompt = f"{label}"
        if default and not secret:
            prompt += f" [{default}]"
        prompt += ": "
        self.stdscr.addnstr(h - 2, 0, prompt, max(0, w - 1), curses.A_BOLD)
        self.stdscr.refresh()
        try:
            value = self.stdscr.getstr(h - 2, min(len(prompt), max(0, w - 1)), 4096).decode()
        except (KeyboardInterrupt, curses.error, UnicodeDecodeError):
            value = ""
        curses.noecho()
        curses.curs_set(0)
        if value == "":
            return default if default else None
        return value.strip()

    def confirm(self, question: str) -> bool:
        answer = self.prompt(f"{question} (y/N)", "")
        return bool(answer and answer.lower().startswith("y"))

    # -- drawing ------------------------------------------------------------

    def draw(self) -> None:
        curses = self.curses
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        pins = set(self.data.get("pins", []))
        title = "sshkit - SSH Manager"
        if self.filter_text:
            title += f"    filter: {self.filter_text}"
        self.stdscr.addnstr(0, 0, title, max(0, w - 1), curses.A_BOLD)
        self.stdscr.addnstr(1, 0, HELP_LINE, max(0, w - 1))
        self.stdscr.hline(2, 0, "-", max(0, w - 1))

        if not self.hosts:
            self.stdscr.addnstr(4, 0, "No hosts yet. Press 'a' to add one.", max(0, w - 1))
        else:
            visible_rows = max(1, h - 6)
            start = max(0, min(self.selected - visible_rows + 1, len(self.hosts) - visible_rows))
            for row, host in enumerate(self.hosts[start : start + visible_rows], start=3):
                idx = start + row - 3
                selected = idx == self.selected
                attr = curses.A_REVERSE if selected else curses.A_NORMAL
                pin = "*" if host.alias in pins else " "
                source = "M" if host.source == "managed" else "C"
                pw = "pw" if self.has_password(host.alias) else "  "
                key = "key" if host.identity_file else "   "
                dash = "dash" if host.dashboard else "    "
                jump = f" via {host.proxy_jump}" if host.proxy_jump else ""
                line = f"{pin} {source} {pw} {key} {dash}  {host.alias:<24} {host.target:<42}{jump:<28} {host.notes}"
                self.stdscr.addnstr(row, 0, line, max(0, w - 1), attr)

        self.stdscr.hline(max(0, h - 3), 0, "-", max(0, w - 1))
        self.stdscr.addnstr(max(0, h - 2), 0, self.status, max(0, w - 1))
        self.stdscr.addnstr(
            max(0, h - 1),
            0,
            f"Legend: M managed, C config-only, * pinned. Passwords live in the {secrets.store_label()}.",
            max(0, w - 1),
            curses.A_DIM,
        )
        self.stdscr.refresh()

    # -- actions ------------------------------------------------------------

    def edit_host(self, existing: Host | None = None) -> None:
        is_edit = existing is not None
        old_alias = existing.alias if existing else ""
        alias = self.prompt("Alias", old_alias)
        if alias is None:
            self.status = "Canceled."
            return
        alias = alias.strip()
        error = validate_alias(alias)
        if error:
            self.status = error
            return
        managed = managed_hosts_from_state(self.data)
        if alias != old_alias and alias in managed:
            self.status = f"{alias} already exists as a managed host."
            return

        host = Host(alias=alias)
        if existing:
            host = Host(**{**existing.__dict__, "alias": alias, "source": "managed"})

        hostname = self.prompt("HostName / IP", host.hostname)
        if hostname is None:
            self.status = "HostName is required."
            return
        user = self.prompt("User", host.user or getpass.getuser()) or ""
        port = self.prompt("Port", host.port or "22") or "22"
        identity_file = self.prompt("IdentityFile path (blank for password/default agent)", host.identity_file) or ""
        proxy_jump = self.prompt("ProxyJump alias/user@host (blank for direct)", host.proxy_jump) or ""
        dashboard = parse_bool(self.prompt("Dashboard by default (y/N)", "y" if host.dashboard else "n"), host.dashboard)
        monitors = parse_monitor_list(
            self.prompt("Dashboard monitors: gpu,cpu,users,processes", format_monitors(host.monitors)),
            host.monitors,
        )
        mosh = parse_mosh_mode(self.prompt("Mosh: auto/always/never", host.mosh), host.mosh)
        notes = self.prompt("Notes", host.notes) or ""

        host.hostname = hostname
        host.user = user
        host.port = port
        host.identity_file = identity_file
        host.proxy_jump = proxy_jump
        host.dashboard = dashboard
        host.monitors = monitors
        host.mosh = mosh
        host.notes = notes
        host.source = "managed"

        self.data.setdefault("hosts", {})
        if is_edit and old_alias != alias:
            self.data["hosts"].pop(old_alias, None)
            if old_alias in self.data.get("pins", []):
                self.data["pins"] = [alias if p == old_alias else p for p in self.data["pins"]]
        self.data["hosts"][alias] = host_to_dict(host)
        save_state(self.data)
        write_ssh_config(self.data)
        self.reload()
        self.status = f"Saved {alias}."

    def import_current(self) -> None:
        host = self.current()
        if not host:
            self.status = "No host selected."
            return
        if host.source == "managed":
            self.status = f"{host.alias} is already managed."
            return
        self.data.setdefault("hosts", {})[host.alias] = host_to_dict(Host(**{**host.__dict__, "source": "managed"}))
        save_state(self.data)
        write_ssh_config(self.data)
        self.reload()
        self.status = f"Imported {host.alias}; it can now be edited or deleted in sshkit."

    def delete_current(self) -> None:
        host = self.current()
        if not host:
            self.status = "No host selected."
            return
        if host.source != "managed":
            if not self.confirm(f"Remove config-only alias {host.alias} from the ssh config"):
                self.status = "Canceled."
                return
            if remove_alias_from_unmanaged_config(host.alias):
                self.data["pins"] = [p for p in self.data.get("pins", []) if p != host.alias]
                save_state(self.data)
                self.reload()
                self.status = f"Removed config alias {host.alias}."
            else:
                self.status = f"Could not remove {host.alias} from the ssh config."
            return
        if not self.confirm(f"Delete managed host {host.alias}"):
            self.status = "Canceled."
            return
        self.data.get("hosts", {}).pop(host.alias, None)
        self.data["pins"] = [p for p in self.data.get("pins", []) if p != host.alias]
        if self.confirm(f"Delete saved password for {host.alias} from the {secrets.store_label()}"):
            secrets.delete_password(host.alias)
            self.password_cache.pop(host.alias, None)
        save_state(self.data)
        write_ssh_config(self.data)
        self.reload()
        self.status = f"Deleted {host.alias}."

    def toggle_pin(self) -> None:
        host = self.current()
        if not host:
            self.status = "No host selected."
            return
        pins = self.data.setdefault("pins", [])
        if host.alias in pins:
            self.data["pins"] = [p for p in pins if p != host.alias]
            self.status = f"Unpinned {host.alias}."
        else:
            pins.append(host.alias)
            self.status = f"Pinned {host.alias}."
        save_state(self.data)
        self.reload()

    def save_password(self) -> None:
        host = self.current()
        if not host:
            self.status = "No host selected."
            return
        password = self.prompt(f"Password for {host.alias}", "", secret=True)
        if not password:
            self.status = "Canceled."
            return
        if secrets.set_password(host.alias, password):
            self.password_cache[host.alias] = True
            self.status = f"Saved password for {host.alias} in the {secrets.store_label()}."
        else:
            self.status = f"Could not save password; no usable credential store ({secrets.backend_name()})."

    def copy_password(self) -> None:
        host = self.current()
        if not host:
            self.status = "No host selected."
            return
        password = secrets.get_password(host.alias)
        if not password:
            self.status = f"No saved password for {host.alias}."
            return
        if clipboard.copy(password):
            self.status = f"Copied password for {host.alias} to clipboard."
        else:
            self.status = "Could not copy password; no clipboard tool found."

    def connect(self, password_helper: bool = False, dashboard: bool = False) -> None:
        curses = self.curses
        host = self.current()
        if not host:
            self.status = "No host selected."
            return
        code = 0
        curses.def_prog_mode()
        curses.endwin()
        try:
            if dashboard or (host.dashboard and not password_helper):
                code = run_dashboard(host.alias, enabled_monitors(host))
            elif password_helper:
                code = run_ssh_with_password(host.alias)
            else:
                code = run_ssh_auto(host.alias, use_mosh={"always": True, "never": False}.get(host.mosh))
        finally:
            try:
                input("\nPress Enter to return to sshkit...")
            except (KeyboardInterrupt, EOFError):
                print()
            curses.reset_prog_mode()
            curses.curs_set(0)
            self.status = f"Returned from {host.alias}."
            if code == 130:
                self.status = f"Connection to {host.alias} interrupted."

    def filter(self) -> None:
        value = self.prompt("Filter", self.filter_text)
        self.filter_text = value or ""
        self.selected = 0
        self.reload()
        self.status = "Filter updated." if self.filter_text else "Filter cleared."

    # -- main loop ----------------------------------------------------------

    def loop(self) -> None:
        curses = self.curses
        curses.curs_set(0)
        self.stdscr.keypad(True)
        while True:
            self.draw()
            ch = self.stdscr.getch()
            if ch in (ord("q"), 27):
                return
            if ch in (curses.KEY_UP, ord("k")):
                self.selected = max(0, self.selected - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.selected = min(max(0, len(self.hosts) - 1), self.selected + 1)
            elif ch in (curses.KEY_ENTER, 10, 13):
                self.connect(False)
            elif ch == ord("D"):
                self.connect(False, dashboard=True)
            elif ch == ord("x"):
                self.connect(True)
            elif ch == ord("a"):
                self.edit_host(None)
            elif ch == ord("i"):
                self.import_current()
            elif ch == ord("e"):
                host = self.current()
                if not host:
                    self.status = "No host selected."
                elif host.source != "managed":
                    self.status = "Import config-only hosts before editing them."
                else:
                    self.edit_host(host)
            elif ch == ord("d"):
                self.delete_current()
            elif ch == ord("p"):
                self.toggle_pin()
            elif ch == ord("s"):
                self.save_password()
            elif ch == ord("c"):
                self.copy_password()
            elif ch == ord("/"):
                self.filter()
            elif ch == ord("r"):
                self.data = load_state()
                self.password_cache.clear()
                self.reload()
                self.status = "Reloaded."


def run() -> int:
    curses_mod = import_curses()
    enable_windows_vt()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "sshkit's browser needs an interactive terminal. Try 'sshkit list' or 'sshkit connect <alias>'.",
            file=sys.stderr,
        )
        return 1
    try:
        curses_mod.wrapper(lambda stdscr: App(stdscr, curses_mod).loop())
    except KeyboardInterrupt:
        return 130
    except curses_mod.error as exc:
        print(f"Terminal error: {exc}. Is the window at least 20x5?", file=sys.stderr)
        return 1
    return 0


__all__ = ["App", "DEFAULT_MONITORS", "run"]
