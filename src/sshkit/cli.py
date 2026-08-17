"""Command line interface."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import __version__, clipboard, compat, secrets, tui
from .dashboard import dashboard_backend, dashboard_hint, run_dashboard
from .model import (
    DEFAULT_MONITORS,
    MOSH_MODES,
    Host,
    all_hosts,
    enabled_monitors,
    find_host,
    format_monitors,
    host_to_dict,
    load_state,
    managed_hosts_from_state,
    save_state,
    validate_alias,
    write_ssh_config,
)
from .monitors import combined_monitor_script, remote_monitor_script
from .session import (
    mosh_usable,
    password_autofill_mode,
    quote_remote,
    run_mosh,
    run_ssh,
    run_ssh_auto,
    run_ssh_with_password,
)


def print_hosts() -> None:
    data = load_state()
    pins = set(data.get("pins", []))
    for host in all_hosts(data):
        pin = "*" if host.alias in pins else " "
        source = "managed" if host.source == "managed" else "config"
        key = f" key={host.identity_file}" if host.identity_file else ""
        jump = f" jump={host.proxy_jump}" if host.proxy_jump else ""
        dash = f" dashboard={format_monitors(host.monitors)}" if host.dashboard else ""
        mosh = f" mosh={host.mosh}" if host.mosh != "auto" else ""
        print(f"{pin} {host.alias:<24} {host.target:<48} {source}{key}{jump}{dash}{mosh}")


def add_or_update_from_args(args: argparse.Namespace) -> None:
    error = validate_alias(args.alias)
    if error:
        raise SystemExit(error)
    data = load_state()
    host = Host(
        alias=args.alias,
        hostname=args.hostname,
        user=args.user or "",
        port=str(args.port or "22"),
        identity_file=args.key or "",
        proxy_jump=args.jump or "",
        mosh=args.mosh,
        dashboard=args.dashboard,
        monitors=monitor_overrides_from_args(args, dict(DEFAULT_MONITORS)),
        notes=args.notes or "",
    )
    data.setdefault("hosts", {})[args.alias] = host_to_dict(host)
    save_state(data)
    write_ssh_config(data)
    print(f"Saved {args.alias}.")


def doctor() -> None:
    autofill = {
        "pty": "built-in pty wrapper",
        "conpty": "ConPTY wrapper (pywinpty)",
        "clipboard": "clipboard fallback (install pywinpty for autofill)",
    }[password_autofill_mode()]
    print(f"sshkit version: {__version__}")
    print(f"platform: {compat.platform_name()}{' (WSL)' if compat.is_wsl() else ''}")
    print(f"python: {sys.version.split()[0]} ({'frozen binary' if getattr(sys, 'frozen', False) else Path(sys.executable)})")
    print(f"state file: {compat.STATE_FILE}")
    print(f"ssh config: {compat.SSH_CONFIG}")
    print(f"ssh client: {compat.ssh_binary() or 'missing'}")
    print(f"mosh client: {compat.mosh_binary() or 'not installed (optional)'}")
    print(f"credential store: {secrets.store_label()} via {secrets.backend_name()}")
    print(f"clipboard: {clipboard.clipboard_tool() or 'missing'}")
    print(f"dashboard backend: {dashboard_backend() or f'missing - {dashboard_hint()}'}")
    print(f"password autofill: {autofill}")
    data = load_state()
    print(f"managed hosts: {len(data.get('hosts', {}))}")
    print(f"all visible hosts: {len(all_hosts(data))}")


def add_monitor_flags(parser: argparse.ArgumentParser) -> None:
    for name in DEFAULT_MONITORS:
        parser.add_argument(f"--{name}", dest=name, action="store_true", default=None)
        parser.add_argument(f"--no-{name}", dest=name, action="store_false")


def monitor_overrides_from_args(args: argparse.Namespace, base: dict[str, bool] | None = None) -> dict[str, bool]:
    config = dict(DEFAULT_MONITORS if base is None else base)
    for name in DEFAULT_MONITORS:
        value = getattr(args, name, None)
        if value is not None:
            config[name] = bool(value)
    return config


def configure_dashboard(args: argparse.Namespace) -> int:
    data = load_state()
    managed = managed_hosts_from_state(data)
    host = managed.get(args.alias)
    if host is None:
        existing = find_host(args.alias)
        if existing is None:
            print(f"Unknown host: {args.alias}", file=sys.stderr)
            return 1
        host = Host(**{**existing.__dict__, "source": "managed"})
    if args.on:
        host.dashboard = True
    if args.off:
        host.dashboard = False
    if args.all:
        host.monitors = dict(DEFAULT_MONITORS)
    if args.none:
        host.monitors = {name: False for name in DEFAULT_MONITORS}
    host.monitors = monitor_overrides_from_args(args, host.monitors)
    data.setdefault("hosts", {})[args.alias] = host_to_dict(host)
    save_state(data)
    write_ssh_config(data)
    state = "on" if host.dashboard else "off"
    print(f"Dashboard for {args.alias}: {state}; monitors={format_monitors(host.monitors) or 'none'}")
    return 0


def normalize_remote_command(remote_command: list[str]) -> list[str]:
    if remote_command and remote_command[0] == "--":
        return remote_command[1:]
    return remote_command


def run_monitor_pane(args: argparse.Namespace) -> int:
    """Run one monitor (or a combined view) in the current terminal pane.

    Dashboard backends invoke this instead of embedding shell scripts in their
    own command lines, which keeps pane commands free of metacharacters.
    """
    names = [args.name] if args.name else list(args.combined)
    unknown = [name for name in names if name not in DEFAULT_MONITORS]
    if unknown:
        print(f"Unknown monitor(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    if not names:
        print("Usage: sshkit monitor <alias> <gpu|cpu|users|processes>", file=sys.stderr)
        return 2
    script = remote_monitor_script(names[0]) if args.name else combined_monitor_script(names)
    return run_ssh_auto(args.alias, [quote_remote(["bash", "-lc", script])], allocate_tty=True)


def save_password_from_prompt(alias: str) -> int:
    password = getpass.getpass(f"Password for {alias}: ")
    if not password:
        print("Canceled.", file=sys.stderr)
        return 1
    if secrets.set_password(alias, password):
        print(f"Saved password for {alias} in the {secrets.store_label()}.")
        return 0
    print(
        f"Could not save password; no usable credential store on this system ({secrets.backend_name()}).",
        file=sys.stderr,
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sshkit", description="Cross-platform terminal SSH manager")
    parser.add_argument("--version", action="version", version=f"sshkit {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="List visible hosts")
    sub.add_parser("doctor", help="Show environment checks")

    connect_p = sub.add_parser("connect", help="Connect to an alias")
    connect_p.add_argument("alias")
    connect_p.add_argument("--password-helper", action="store_true", help="Require saved-password autofill")
    connect_p.add_argument("--no-password-helper", action="store_true", help="Use plain ssh without saved-password autofill")
    connect_p.add_argument("--dashboard", action="store_true", help="Launch the split-pane dashboard")
    connect_p.add_argument("--no-dashboard", action="store_true", help="Force plain connection even if dashboard is enabled")
    connect_p.add_argument("--mosh", action="store_true", help="Use mosh for this connection (falls back to ssh if mosh cannot connect)")
    connect_p.add_argument("--no-mosh", action="store_true", help="Use ssh even when mosh is available")
    add_monitor_flags(connect_p)

    dashboard_p = sub.add_parser("dashboard", help="Connect with main SSH pane and monitor panes")
    dashboard_p.add_argument("alias")
    add_monitor_flags(dashboard_p)

    config_p = sub.add_parser("dashboard-config", help="Configure a host's default dashboard")
    config_p.add_argument("alias")
    config_p.add_argument("--on", action="store_true")
    config_p.add_argument("--off", action="store_true")
    config_p.add_argument("--all", action="store_true", help="Enable all monitor panes")
    config_p.add_argument("--none", action="store_true", help="Disable all monitor panes")
    add_monitor_flags(config_p)

    monitor_p = sub.add_parser("monitor", help="Run a single monitor pane (used by dashboards)")
    monitor_p.add_argument("alias")
    monitor_p.add_argument("name", nargs="?", choices=sorted(DEFAULT_MONITORS))
    monitor_p.add_argument("--combined", nargs="*", default=[], metavar="MONITOR", help="Run several monitors in one pane")

    agent_connect_p = sub.add_parser("agent-connect", help="Agent-safe saved-password SSH shell")
    agent_connect_p.add_argument("alias")

    exec_p = sub.add_parser("exec", help="Run a remote command through sshkit")
    exec_p.add_argument("alias")
    exec_p.add_argument("remote_command", nargs=argparse.REMAINDER)

    exec_tty_p = sub.add_parser("exec-tty", help="Run a remote command with an allocated TTY")
    exec_tty_p.add_argument("alias")
    exec_tty_p.add_argument("remote_command", nargs=argparse.REMAINDER)

    agent_exec_p = sub.add_parser("agent-exec", help="Agent-safe alias for 'exec'")
    agent_exec_p.add_argument("alias")
    agent_exec_p.add_argument("remote_command", nargs=argparse.REMAINDER)

    password_p = sub.add_parser("password", help="Save an alias password to the system credential store")
    password_p.add_argument("alias")

    add_p = sub.add_parser("add", help="Add or update a managed host")
    add_p.add_argument("alias")
    add_p.add_argument("hostname")
    add_p.add_argument("-u", "--user", default=getpass.getuser())
    add_p.add_argument("-p", "--port", default="22")
    add_p.add_argument("-k", "--key", default="")
    add_p.add_argument("-j", "--jump", default="", help="ProxyJump alias or user@host")
    add_p.add_argument("--mosh", choices=MOSH_MODES, default="auto", help="Use mosh for this host: auto (when usable), always, never")
    add_p.add_argument("--dashboard", action="store_true", help="Make dashboard the default for this host")
    add_monitor_flags(add_p)
    add_p.add_argument("-n", "--notes", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "list":
        print_hosts()
        return 0
    if args.cmd == "doctor":
        doctor()
        return 0
    if args.cmd == "connect":
        host = find_host(args.alias)
        dashboard_enabled = bool(host and host.dashboard)
        if args.dashboard or (
            dashboard_enabled and not args.no_dashboard and not args.no_password_helper and not args.password_helper
        ):
            monitors = enabled_monitors(
                host or Host(args.alias),
                monitor_overrides_from_args(args, {}) if host else None,
            )
            return run_dashboard(args.alias, monitors)
        host_mosh = {"always": True, "never": False}.get(host.mosh if host else "auto")
        use_mosh = True if args.mosh else (False if args.no_mosh else host_mosh)
        if args.no_password_helper:
            if use_mosh or (use_mosh is None and mosh_usable(args.alias)):
                return run_mosh(args.alias)
            return run_ssh(args.alias)
        if args.password_helper:
            return run_ssh_with_password(args.alias)
        return run_ssh_auto(args.alias, use_mosh=use_mosh)
    if args.cmd == "dashboard":
        host = find_host(args.alias) or Host(args.alias)
        return run_dashboard(args.alias, enabled_monitors(host, monitor_overrides_from_args(args, {})))
    if args.cmd == "dashboard-config":
        return configure_dashboard(args)
    if args.cmd == "monitor":
        return run_monitor_pane(args)
    if args.cmd == "agent-connect":
        if not secrets.get_password(args.alias):
            print(f"No saved password for {args.alias}; refusing interactive agent prompt.", file=sys.stderr)
            return 1
        return run_ssh_with_password(args.alias)
    if args.cmd in {"exec", "exec-tty", "agent-exec"}:
        remote_command = normalize_remote_command(args.remote_command)
        if not remote_command:
            print(f"Usage: sshkit {args.cmd} <alias> -- <command>", file=sys.stderr)
            return 2
        if args.cmd == "agent-exec" and not secrets.get_password(args.alias):
            print(f"No saved password for {args.alias}; refusing interactive agent prompt.", file=sys.stderr)
            return 1
        return run_ssh_auto(args.alias, [quote_remote(remote_command)], allocate_tty=args.cmd == "exec-tty")
    if args.cmd == "password":
        return save_password_from_prompt(args.alias)
    if args.cmd == "add":
        add_or_update_from_args(args)
        return 0

    return tui.run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
