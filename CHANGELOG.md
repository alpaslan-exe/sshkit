# Changelog

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
