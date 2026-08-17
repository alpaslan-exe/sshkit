import sshkit.cli as cli
import sshkit.dashboard as dashboard
import sshkit.session as session
from sshkit.monitors import combined_monitor_script, remote_monitor_script


def test_parser_accepts_every_subcommand():
    parser = cli.build_parser()
    for argv in (
        ["list"],
        ["doctor"],
        ["connect", "a"],
        ["connect", "a", "--dashboard", "--no-gpu"],
        ["dashboard", "a"],
        ["dashboard-config", "a", "--on", "--all"],
        ["monitor", "a", "gpu"],
        ["monitor", "a", "--combined", "cpu", "gpu"],
        ["agent-connect", "a"],
        ["exec", "a", "--", "uptime"],
        ["exec-tty", "a", "--", "htop"],
        ["agent-exec", "a", "--", "ls"],
        ["password", "a"],
        ["add", "a", "1.2.3.4", "-u", "root"],
    ):
        assert parser.parse_args(argv).cmd == argv[0]


def test_normalize_remote_command():
    assert cli.normalize_remote_command(["--", "ls", "-la"]) == ["ls", "-la"]
    assert cli.normalize_remote_command(["ls"]) == ["ls"]
    assert cli.normalize_remote_command([]) == []


def test_monitor_scripts_exist_for_all_monitors():
    for name in cli.DEFAULT_MONITORS:
        assert "while true" in remote_monitor_script(name)
    combined = combined_monitor_script(["gpu", "cpu"])
    assert "=== GPU ===" in combined
    assert "=== CPU ===" in combined


def test_pane_commands_have_no_shell_metacharacters():
    """Dashboard pane commands are embedded in tmux/wt command lines."""
    argv = dashboard._pane_argv("phil", "gpu")
    assert argv[-2:] == ["monitor", "phil", "gpu"][-2:]
    assert not any(ch in token for token in argv for ch in ";|&")


def test_ssh_argv_shape(monkeypatch):
    monkeypatch.setattr(session, "ssh_binary", lambda: "/usr/bin/ssh")
    assert session.ssh_argv("host") == ["/usr/bin/ssh", "host"]
    assert session.ssh_argv("host", ["uptime"], allocate_tty=True) == ["/usr/bin/ssh", "-tt", "host", "uptime"]


def test_parser_accepts_mosh_flags():
    parser = cli.build_parser()
    args = parser.parse_args(["connect", "a", "--mosh"])
    assert args.mosh and not args.no_mosh
    args = parser.parse_args(["connect", "a", "--no-mosh"])
    assert args.no_mosh and not args.mosh


def test_mosh_argv_shape(monkeypatch):
    monkeypatch.setattr(session, "mosh_binary", lambda: "/usr/bin/mosh")
    assert session.mosh_argv("host") == ["/usr/bin/mosh", "host"]


def test_run_ssh_auto_picks_mosh_only_for_interactive(monkeypatch):
    calls = []
    monkeypatch.setattr(session, "run_mosh_session", lambda alias: calls.append(("mosh", alias)) or 0)
    monkeypatch.setattr(session, "run_ssh", lambda alias, remote_args=None, allocate_tty=False: calls.append(("ssh", alias)) or 0)
    monkeypatch.setattr(session, "mosh_usable", lambda alias: True)
    monkeypatch.setattr(session.secrets, "get_password", lambda alias: None)

    session.run_ssh_auto("host")
    session.run_ssh_auto("host", ["uptime"])
    session.run_ssh_auto("host", allocate_tty=True)
    session.run_ssh_auto("host", use_mosh=False)
    assert calls == [("mosh", "host"), ("ssh", "host"), ("ssh", "host"), ("ssh", "host")]


def test_run_ssh_auto_forced_mosh(monkeypatch):
    calls = []
    monkeypatch.setattr(session, "run_mosh_session", lambda alias: calls.append(("mosh", alias)) or 0)
    monkeypatch.setattr(session, "mosh_usable", lambda alias: False)
    session.run_ssh_auto("host", use_mosh=True)
    assert calls == [("mosh", "host")]


def test_forced_mosh_falls_back_to_ssh_password_on_fast_failure(monkeypatch):
    """A mosh=always host whose bootstrap dies must not lose the session."""
    calls = []
    clock = iter([100.0, 101.0])
    monkeypatch.setattr(session.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(session, "run_mosh_session", lambda alias: calls.append("mosh") or 255)
    monkeypatch.setattr(session, "run_ssh_with_password", lambda alias, remote_args=None, allocate_tty=False: calls.append("ssh-pw") or 0)
    monkeypatch.setattr(session.secrets, "get_password", lambda alias: "hunter2")
    assert session.run_ssh_auto("host", use_mosh=True) == 0
    assert calls == ["mosh", "ssh-pw"]


def test_run_mosh_session_uses_password_wrapper(monkeypatch):
    calls = []
    monkeypatch.setattr(session, "mosh_binary", lambda: "/usr/bin/mosh")
    monkeypatch.setattr(session.secrets, "get_password", lambda alias: "hunter2")
    monkeypatch.setattr(session, "_run_with_password_posix", lambda argv, password: calls.append((argv, password)) or 0)
    assert session.run_mosh_session("host") == 0
    assert calls == [(["/usr/bin/mosh", "host"], "hunter2")]


def test_mosh_usable_requires_no_saved_password(monkeypatch):
    monkeypatch.setattr(session, "mosh_binary", lambda: "/usr/bin/mosh")
    monkeypatch.setattr(session.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(session.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(session.secrets, "get_password", lambda alias: "hunter2")
    assert not session.mosh_usable("host")
    monkeypatch.setattr(session.secrets, "get_password", lambda alias: None)
    assert session.mosh_usable("host")
    monkeypatch.setattr(session, "mosh_binary", lambda: None)
    assert not session.mosh_usable("host")


def test_dashboard_main_pane_uses_plain_connect():
    argv = dashboard._main_argv("phil")
    assert argv[-2:] == ["phil", "--no-dashboard"]
    assert "--no-mosh" not in argv  # host mosh setting decides; connect falls back on failure


def test_run_ssh_auto_falls_back_to_ssh_on_fast_mosh_failure(monkeypatch):
    calls = []
    clock = iter([100.0, 101.0])  # mosh "ran" for 1 second
    monkeypatch.setattr(session.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(session, "mosh_usable", lambda alias: True)
    monkeypatch.setattr(session, "run_mosh", lambda alias: calls.append("mosh") or 255)
    monkeypatch.setattr(session, "run_ssh", lambda alias, remote_args=None, allocate_tty=False: calls.append("ssh") or 0)
    monkeypatch.setattr(session.secrets, "get_password", lambda alias: None)
    assert session.run_ssh_auto("host") == 0
    assert calls == ["mosh", "ssh"]


def test_run_ssh_auto_keeps_mosh_exit_after_real_session(monkeypatch):
    calls = []
    clock = iter([100.0, 200.0])  # session lasted 100 seconds
    monkeypatch.setattr(session.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(session, "mosh_usable", lambda alias: True)
    monkeypatch.setattr(session, "run_mosh", lambda alias: calls.append("mosh") or 1)
    monkeypatch.setattr(session, "run_ssh", lambda alias, remote_args=None, allocate_tty=False: calls.append("ssh") or 0)
    assert session.run_ssh_auto("host") == 1
    assert calls == ["mosh"]


def test_parser_accepts_add_mosh_mode():
    parser = cli.build_parser()
    assert parser.parse_args(["add", "a", "1.2.3.4", "--mosh", "never"]).mosh == "never"
    assert parser.parse_args(["add", "a", "1.2.3.4"]).mosh == "auto"


def test_mosh_mode_roundtrip_and_coercion():
    from sshkit.model import Host, host_from_dict, host_to_dict, parse_mosh_mode

    host = Host(alias="a", hostname="1.2.3.4", mosh="always")
    raw = host_to_dict(host)
    assert raw["mosh"] == "always"
    assert host_from_dict("a", raw).mosh == "always"
    assert host_from_dict("a", {"hostname": "1.2.3.4"}).mosh == "auto"  # pre-1.2 state files
    assert host_from_dict("a", {"hostname": "1.2.3.4", "mosh": "banana"}).mosh == "auto"
    assert parse_mosh_mode(" Never ") == "never"
    assert parse_mosh_mode("banana", "always") == "always"


def test_connect_flag_beats_host_mosh_setting(monkeypatch):
    from sshkit.model import Host

    calls = []
    monkeypatch.setattr(cli, "find_host", lambda alias: Host(alias=alias, mosh="always"))
    monkeypatch.setattr(cli, "run_ssh_auto", lambda alias, use_mosh=None: calls.append(use_mosh) or 0)
    cli.main(["connect", "a", "--no-mosh"])
    cli.main(["connect", "a", "--mosh"])
    cli.main(["connect", "a"])
    assert calls == [False, True, True]


def test_connect_host_mosh_never(monkeypatch):
    from sshkit.model import Host

    calls = []
    monkeypatch.setattr(cli, "find_host", lambda alias: Host(alias=alias, mosh="never"))
    monkeypatch.setattr(cli, "run_ssh_auto", lambda alias, use_mosh=None: calls.append(use_mosh) or 0)
    cli.main(["connect", "a"])
    assert calls == [False]


def test_child_env_strips_pyinstaller_state(monkeypatch):
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "/x/sshkit")
    monkeypatch.setenv("_MEIPASS", "/tmp/_MEI1")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = dashboard.child_env()
    assert "PATH" in env
    assert not any(k.startswith(("_PYI_", "_MEIPASS")) for k in env)


def test_password_prompt_detection():
    assert session.PASSWORD_PROMPT.search("me@host's password: ")
    assert session.PASSWORD_PROMPT.search("Enter passphrase for key '/x': ")
    assert not session.PASSWORD_PROMPT.search("password changed successfully")
