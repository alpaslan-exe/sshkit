"""Platform detection, paths and filesystem permissions.

sshkit runs on macOS, Linux (incl. WSL) and Windows. Everything that differs
between those platforms is funnelled through this module so the rest of the
codebase can stay platform-neutral.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

HOME = Path.home()
SSH_DIR = Path(os.environ.get("SSHKIT_SSH_DIR") or (HOME / ".ssh"))
DATA_DIR = Path(os.environ.get("SSHKIT_DATA_DIR") or (SSH_DIR / "sshkit"))
STATE_FILE = DATA_DIR / "hosts.json"
SSH_CONFIG = Path(os.environ.get("SSHKIT_SSH_CONFIG") or (SSH_DIR / "config"))

BEGIN_MARKER = "# >>> sshkit managed hosts >>>"
END_MARKER = "# <<< sshkit managed hosts <<<"


def platform_name() -> str:
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    if IS_LINUX:
        return "linux"
    return sys.platform


def is_wsl() -> bool:
    """True when running inside Windows Subsystem for Linux."""
    if not IS_LINUX:
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text(errors="replace").lower()
    except OSError:
        return False


def which_first(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _icacls(*args: str) -> bool:
    icacls = shutil.which("icacls")
    if not icacls:
        return False
    try:
        proc = subprocess.run(
            [icacls, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0


def _windows_principal() -> str:
    user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    if user and domain:
        return f"{domain}\\{user}"
    return user


def _restrict_windows_acl(path: Path) -> None:
    """Grant the current user exclusive access; inheritance off.

    The (OI)(CI) inheritance flags are only valid on directories - passing them
    for a file makes icacls fail *after* inheritance has been stripped, which
    would leave a file nobody can read. So the flags are chosen per type and
    inheritance is restored if the grant does not stick.
    """
    principal = _windows_principal()
    if not principal:
        return
    rights = "(OI)(CI)F" if path.is_dir() else "F"
    if not _icacls(str(path), "/inheritance:r", "/grant:r", f"{principal}:{rights}", "/Q"):
        _icacls(str(path), "/inheritance:e", "/Q")


def secure_dir(path: Path) -> None:
    """Create `path` if needed and make it private to the current user."""
    path.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        _restrict_windows_acl(path)
        return
    try:
        path.chmod(0o700)
    except OSError:
        pass


def secure_file(path: Path) -> None:
    """Make an existing file private to the current user."""
    if not path.exists():
        return
    if IS_WINDOWS:
        _restrict_windows_acl(path)
        return
    try:
        path.chmod(0o600)
    except OSError:
        pass


def ensure_dirs() -> None:
    secure_dir(SSH_DIR)
    secure_dir(DATA_DIR)


def ssh_binary() -> str | None:
    """Locate an OpenSSH client.

    Windows ships one at System32\\OpenSSH\\ssh.exe which is not always on PATH.
    """
    found = shutil.which("ssh")
    if found:
        return found
    if IS_WINDOWS:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")  # noqa: SIM112 - Windows spelling
        candidate = Path(system_root) / "System32" / "OpenSSH" / "ssh.exe"
        if candidate.exists():
            return str(candidate)
    return None


def self_command() -> list[str]:
    """Argv prefix that re-invokes sshkit, for commands embedded in panes."""
    if getattr(sys, "frozen", False):  # PyInstaller one-file build
        return [sys.executable]
    # Deliberately not shutil.which("sshkit"): a differently-installed sshkit
    # earlier on PATH would then drive the panes of this one.
    return [sys.executable, "-m", "sshkit"]
