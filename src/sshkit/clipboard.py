"""Cross-platform clipboard copy."""

from __future__ import annotations

import subprocess

from .compat import IS_MACOS, IS_WINDOWS, is_wsl, which_first

# Ordered candidates: (binary names, argv builder, needs stdin)
_POSIX_TOOLS: list[tuple[tuple[str, ...], list[str]]] = [
    (("pbcopy",), []),
    (("wl-copy",), []),
    (("xclip",), ["-selection", "clipboard"]),
    (("xsel",), ["--clipboard", "--input"]),
    (("clip.exe",), []),  # WSL
]


def clipboard_tool() -> str | None:
    """Name of the clipboard helper that will be used, or None."""
    if IS_WINDOWS:
        return which_first("clip", "powershell")
    for names, _ in _POSIX_TOOLS:
        if IS_MACOS and names[0] != "pbcopy":
            continue
        found = which_first(*names)
        if found:
            return found
    return None


def copy(text: str) -> bool:
    if IS_WINDOWS:
        clip = which_first("clip")
        if clip:
            return _run([clip], text)
        powershell = which_first("powershell", "pwsh")
        if powershell:
            return _run([powershell, "-NoProfile", "-Command", "$input | Set-Clipboard"], text)
        return False

    for names, extra in _POSIX_TOOLS:
        if names[0] == "clip.exe" and not is_wsl():
            continue
        found = which_first(*names)
        if not found:
            continue
        if _run([found, *extra], text):
            return True
    return False


def _run(argv: list[str], text: str) -> bool:
    try:
        proc = subprocess.run(argv, input=text, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return proc.returncode == 0
