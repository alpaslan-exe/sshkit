"""Password storage backed by the operating system credential store.

Passwords are never written to disk by sshkit. Each platform has a native
store; the `keyring` package is used as a portable fallback when it is
installed (it is bundled into the released binaries).

    macOS    Keychain via the `security` CLI
    Linux    Secret Service via `secret-tool`, or `keyring`
    Windows  Credential Manager via `keyring`
"""

from __future__ import annotations

import os
import subprocess

from .compat import IS_LINUX, IS_MACOS, IS_WINDOWS, which_first

SERVICE_PREFIX = "sshkit"


def keychain_service(alias: str) -> str:
    return f"{SERVICE_PREFIX}:{alias}"


class Backend:
    name = "none"
    available = False

    def get(self, alias: str) -> str | None:  # pragma: no cover - interface
        return None

    def set(self, alias: str, password: str) -> bool:  # pragma: no cover
        return False

    def delete(self, alias: str) -> None:  # pragma: no cover
        return None


class SecurityBackend(Backend):
    """macOS Keychain through /usr/bin/security."""

    name = "macos-keychain"

    def __init__(self) -> None:
        self.binary = which_first("security")
        self.available = bool(self.binary) and IS_MACOS

    def get(self, alias: str) -> str | None:
        proc = subprocess.run(
            [self.binary, "find-generic-password", "-a", alias, "-s", keychain_service(alias), "-w"],
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.rstrip("\n")

    def set(self, alias: str, password: str) -> bool:
        proc = subprocess.run(
            [
                self.binary,
                "add-generic-password",
                "-a",
                alias,
                "-s",
                keychain_service(alias),
                "-w",
                password,
                "-U",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc.returncode == 0

    def delete(self, alias: str) -> None:
        subprocess.run(
            [self.binary, "delete-generic-password", "-a", alias, "-s", keychain_service(alias)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class SecretToolBackend(Backend):
    """Linux Secret Service through libsecret's secret-tool."""

    name = "secret-tool"

    def __init__(self) -> None:
        self.binary = which_first("secret-tool")
        self.available = bool(self.binary) and IS_LINUX

    def _attrs(self, alias: str) -> list[str]:
        return ["service", keychain_service(alias), "account", alias]

    def get(self, alias: str) -> str | None:
        proc = subprocess.run(
            [self.binary, "lookup", *self._attrs(alias)],
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        return proc.stdout.rstrip("\n")

    def set(self, alias: str, password: str) -> bool:
        proc = subprocess.run(
            [self.binary, "store", "--label", f"sshkit {alias}", *self._attrs(alias)],
            input=password,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc.returncode == 0

    def delete(self, alias: str) -> None:
        subprocess.run(
            [self.binary, "clear", *self._attrs(alias)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class KeyringBackend(Backend):
    """Portable backend using the `keyring` package."""

    name = "keyring"

    def __init__(self) -> None:
        self.module = None
        try:
            import keyring  # noqa: PLC0415 - optional dependency

            backend = keyring.get_keyring()
            if backend.__class__.__name__ == "fail.Keyring":
                raise RuntimeError("no usable keyring backend")
            self.module = keyring
            self.name = f"keyring:{backend.__class__.__name__}"
            self.available = True
        except Exception:
            self.available = False

    def get(self, alias: str) -> str | None:
        try:
            return self.module.get_password(keychain_service(alias), alias)
        except Exception:
            return None

    def set(self, alias: str, password: str) -> bool:
        try:
            self.module.set_password(keychain_service(alias), alias, password)
            return True
        except Exception:
            return False

    def delete(self, alias: str) -> None:
        try:
            self.module.delete_password(keychain_service(alias), alias)
        except Exception:
            pass


def _candidates() -> list[type[Backend]]:
    """Backend classes in preference order.

    Returned lazily: constructing KeyringBackend imports `keyring`, which on
    some Linux/conda setups drags in dbus bindings we do not want to touch
    unless the native backend is unavailable.
    """
    if IS_MACOS:
        return [SecurityBackend, KeyringBackend]
    if IS_WINDOWS:
        return [KeyringBackend]
    return [SecretToolBackend, KeyringBackend]


_backend: Backend | None = None


def backend() -> Backend:
    """Resolve (once) the credential backend for this machine."""
    global _backend
    if _backend is not None:
        return _backend
    forced = os.environ.get("SSHKIT_KEYRING_BACKEND", "").strip().lower()
    for candidate_class in _candidates():
        if forced and not candidate_class.name.startswith(forced):
            continue
        candidate = candidate_class()
        if candidate.available:
            _backend = candidate
            return _backend
    _backend = Backend()
    return _backend


def backend_name() -> str:
    return backend().name


def store_label() -> str:
    if IS_MACOS:
        return "macOS Keychain"
    if IS_WINDOWS:
        return "Windows Credential Manager"
    if IS_LINUX:
        return "Secret Service keyring"
    return "system credential store"


def get_password(alias: str) -> str | None:
    return backend().get(alias)


def set_password(alias: str, password: str) -> bool:
    return backend().set(alias, password)


def delete_password(alias: str) -> None:
    backend().delete(alias)
