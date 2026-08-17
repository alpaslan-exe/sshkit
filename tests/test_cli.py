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
    monkeypatch.setattr(session, "run_mosh", lambda alias: calls.append(("mosh", alias)) or 0)
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
    monkeypatch.setattr(session, "run_mosh", lambda alias: calls.append(("mosh", alias)) or 0)
    monkeypatch.setattr(session, "mosh_usable", lambda alias: False)
    session.run_ssh_auto("host", use_mosh=True)
    assert calls == [("mosh", "host")]


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


def test_password_prompt_detection():
    assert session.PASSWORD_PROMPT.search("me@host's password: ")
    assert session.PASSWORD_PROMPT.search("Enter passphrase for key '/x': ")
    assert not session.PASSWORD_PROMPT.search("password changed successfully")
