# Changelog

## 1.1.1

### Fixed
- Dashboards broke when mosh could not reach the host: the main pane picked
  mosh automatically, a failed mosh bootstrap (no `mosh-server` on the remote,
  or UDP filtered) exited immediately, and the pane's exit tore down the whole
  tmux session. The dashboard main pane and all dashboard fallbacks now always
  use ssh.
- Plain interactive connects now fall back to ssh automatically when mosh
  exits non-zero within 10 seconds (a failed bootstrap), so hosts without
  `mosh-server` keep working without `--no-mosh`.

## 1.1.0

### Added
- Mosh support. Plain interactive connects (`sshkit connect <alias>`, the
  browser, and dashboard main panes) use `mosh` automatically when it is
  installed, the session is a real terminal, and the alias has no saved
  password. Remote commands (`exec`, `exec-tty`, `monitor`) always use ssh.
- `sshkit connect --mosh` requires mosh (the mosh bootstrap prompts for a
  password itself if one is needed); `--no-mosh` forces plain ssh.
- `sshkit doctor` reports the detected mosh client.

Mosh 1.4's default `--experimental-remote-ip=proxy` resolves hosts through ssh
itself, so ssh-config aliases, ports, keys and `ProxyJump` keep working.

## 1.0.0

First public release. The macOS-only single-file script became a cross-platform
package.

### Added
- Windows support: Credential Manager passwords, ConPTY password autofill via
  `pywinpty`, `windows-curses` browser, Windows Terminal dashboards, OpenSSH
  discovery in `System32\OpenSSH`.
- Linux support: Secret Service passwords via `secret-tool` or `keyring`,
  `wl-copy`/`xclip`/`xsel` clipboard, WSL detection.
- `sshkit monitor <alias> <name>` — runs one monitor pane; dashboards now build
  pane commands as plain argv instead of embedded shell scripts.
- `sshkit --version`, and a platform-aware `sshkit doctor`.
- Test suite, CI on Linux/macOS/Windows, PyInstaller release workflow.

### Changed
- Split into a package under `src/sshkit/`; installable with `pip`, with a
  `sshkit` console script.
- `~/.ssh` and `~/.ssh/sshkit` are locked down with an ACL on Windows and mode
  `0700`/`0600` elsewhere.
- The ssh config block is always written with LF endings and quotes
  `IdentityFile` paths containing spaces.
- The browser caches keystore lookups instead of querying once per row per
  redraw.
- Locations are overridable with `SSHKIT_SSH_DIR`, `SSHKIT_DATA_DIR`,
  `SSHKIT_SSH_CONFIG`.

### Compatibility
- Existing `~/.ssh/sshkit/hosts.json` files are read unchanged.
