#!/usr/bin/env python3
"""Capture real sshkit terminal output and render it to SVG.

Runs sshkit against a throwaway ~/.ssh directory full of demo hosts, drives it
through a pty, replays the output into a pyte terminal emulator and writes an
SVG "terminal window" per screen. Nothing here touches the real ~/.ssh.

    pip install pyte
    python tools/capture_screenshots.py
"""

from __future__ import annotations

import json
import os
import pty
import select
import shutil
import sys
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

import pyte

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "screenshots"
COLS, ROWS = 118, 26

BG = "#0d1117"
FG = "#c9d1d9"
DIM = "#6e7681"
ACCENT = "#58a6ff"
CHROME = "#161b22"
BORDER = "#30363d"
CELL_W = 8.4
CELL_H = 19.0
PAD_X = 18
PAD_TOP = 46
FONT = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', monospace"

DEMO_HOSTS = {
    "hosts": {
        "gpu-lab": {
            "hostname": "gpu-lab.example.edu",
            "user": "ada",
            "port": "22",
            "identity_file": "~/.ssh/id_ed25519",
            "proxy_jump": "",
            "dashboard": True,
            "monitors": {"gpu": True, "cpu": True, "users": True, "processes": True},
            "notes": "4x A100, shared with the vision group",
            "extra": {},
        },
        "build-farm": {
            "hostname": "10.14.2.31",
            "user": "ci",
            "port": "2222",
            "identity_file": "",
            "proxy_jump": "bastion",
            "dashboard": True,
            "monitors": {"gpu": False, "cpu": True, "users": True, "processes": True},
            "notes": "nightly builds",
            "extra": {},
        },
        "bastion": {
            "hostname": "bastion.example.edu",
            "user": "ada",
            "port": "22",
            "identity_file": "~/.ssh/id_ed25519",
            "proxy_jump": "",
            "dashboard": False,
            "monitors": {"gpu": False, "cpu": False, "users": False, "processes": False},
            "notes": "jump host",
            "extra": {},
        },
        "vps": {
            "hostname": "198.51.100.7",
            "user": "root",
            "port": "22",
            "identity_file": "",
            "proxy_jump": "",
            "dashboard": True,
            "monitors": {"gpu": False, "cpu": True, "users": True, "processes": True},
            "notes": "personal server, password login",
            "extra": {},
        },
    },
    "pins": ["gpu-lab"],
}

LEGACY_CONFIG = """Host old-desktop
  HostName 192.168.1.44
  User ada

Host *
  ServerAliveInterval 60
"""


def demo_env(tmp: Path) -> dict[str, str]:
    ssh_dir = tmp / ".ssh"
    data_dir = ssh_dir / "sshkit"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "hosts.json").write_text(json.dumps(DEMO_HOSTS, indent=2))
    (ssh_dir / "config").write_text(LEGACY_CONFIG)
    env = dict(os.environ)
    env.update(
        {
            "SSHKIT_SSH_DIR": str(ssh_dir),
            "SSHKIT_DATA_DIR": str(data_dir),
            "SSHKIT_SSH_CONFIG": str(ssh_dir / "config"),
            "SSHKIT_KEYRING_BACKEND": "none",  # never read the real keystore
            "PYTHONPATH": str(ROOT / "src"),
            "TERM": "xterm-256color",
            "LINES": str(ROWS),
            "COLUMNS": str(COLS),
        }
    )
    return env


def run_in_pty(argv: list[str], env: dict[str, str], keys: list[tuple[float, bytes]], quit_after: bool = True) -> str:
    """Run argv on a pty, send timed keystrokes, return everything it printed."""
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.clear()
        os.environ.update(env)
        os.execvp(argv[0], argv)

    try:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    except Exception:
        pass

    output = bytearray()
    deadline = time.time() + 12
    pending = list(keys)
    started = time.time()
    while time.time() < deadline:
        if pending and time.time() - started >= pending[0][0]:
            os.write(fd, pending.pop(0)[1])
        ready, _, _ = select.select([fd], [], [], 0.1)
        if fd in ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
        if not pending and time.time() - started > 2.5:
            break
    if quit_after:
        try:
            os.write(fd, b"q")
            time.sleep(0.2)
            ready, _, _ = select.select([fd], [], [], 0.2)
            if fd in ready:
                output.extend(os.read(fd, 65536))
        except OSError:
            pass
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
    return output.decode("utf-8", errors="replace")


def emulate(text: str) -> pyte.Screen:
    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.Stream(screen)
    stream.feed(text)
    return screen


def svg_from_screen(screen: pyte.Screen, title: str) -> str:
    width = COLS * CELL_W + PAD_X * 2
    height = ROWS * CELL_H + PAD_TOP + 18
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" font-family="{FONT}" font-size="13">',
        f'<rect x="0" y="0" width="{width:.0f}" height="{height:.0f}" rx="10" fill="{BG}" stroke="{BORDER}"/>',
        f'<rect x="0" y="0" width="{width:.0f}" height="30" rx="10" fill="{CHROME}"/>',
        f'<rect x="0" y="20" width="{width:.0f}" height="10" fill="{CHROME}"/>',
        '<circle cx="18" cy="15" r="5" fill="#ff5f57"/>',
        '<circle cx="36" cy="15" r="5" fill="#febc2e"/>',
        '<circle cx="54" cy="15" r="5" fill="#28c840"/>',
        f'<text x="{width / 2:.0f}" y="20" fill="{DIM}" font-size="12" text-anchor="middle">{escape(title)}</text>',
    ]

    for row_index in range(ROWS):
        row = screen.buffer[row_index]
        y = PAD_TOP + row_index * CELL_H
        col = 0
        while col < COLS:
            char = row[col]
            run = char.data
            start = col
            col += 1
            while col < COLS:
                nxt = row[col]
                if (nxt.reverse, nxt.bold, nxt.fg) != (char.reverse, char.bold, char.fg):
                    break
                run += nxt.data
                col += 1
            if not run.strip() and not char.reverse:
                continue
            x = PAD_X + start * CELL_W
            fill = FG
            if char.fg not in ("default", "white"):
                fill = ACCENT
            if char.reverse:
                parts.append(
                    f'<rect x="{x:.1f}" y="{y - 14:.1f}" width="{len(run) * CELL_W:.1f}" '
                    f'height="{CELL_H:.1f}" fill="{ACCENT}" opacity="0.22"/>'
                )
                fill = "#e6edf3"
            weight = ' font-weight="600"' if char.bold else ""
            parts.append(
                f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}"{weight} xml:space="preserve">{escape(run)}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def capture(
    name: str,
    title: str,
    argv: list[str],
    env: dict[str, str],
    keys: list[tuple[float, bytes]],
    quit_after: bool = True,
) -> None:
    text = run_in_pty(argv, env, keys, quit_after=quit_after)
    screen = emulate(text)
    target = OUT_DIR / f"{name}.svg"
    target.write_text(svg_from_screen(screen, title))
    print(f"wrote {target.relative_to(ROOT)}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="sshkit-demo-"))
    try:
        env = demo_env(tmp)
        python = sys.executable
        capture("browser", "sshkit - host browser", [python, "-m", "sshkit"], env, [(0.8, b"j")])
        capture(
            "filter",
            "sshkit - filtering hosts",
            [python, "-m", "sshkit"],
            env,
            [(0.8, b"/"), (1.1, b"lab\r")],
        )
        capture(
            "add-host",
            "sshkit - adding a host",
            [python, "-m", "sshkit"],
            env,
            [(0.8, b"a"), (1.4, b"edge-node\r")],
            quit_after=False,
        )
        capture("list", "sshkit list", [python, "-m", "sshkit", "list"], env, [])
        capture("help", "sshkit --help", [python, "-m", "sshkit", "--help"], env, [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    if shutil.which("tmux") is None:
        print("note: tmux not found; dashboard screenshots are not captured here", file=sys.stderr)
    raise SystemExit(main())
