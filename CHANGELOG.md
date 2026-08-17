# Changelog

## 1.2.3

### Fixed
- mosh with a saved password crashed instantly ("Error: vector"): the
  password-autofill pty reported a 0x0 window and mosh-client sizes its
  framebuffer from it. The wrapper now seeds the child pty with the real
  terminal size and forwards SIGWINCH, which also gives correctly sized
  vim/htop inside password-autofilled ssh sessions.

## 1.2.2

### Fixed
- Dashboards from the compiled binary died instantly with two "no server
  running" errors and a bogus "Terminal is too small" fallback. The frozen
  binary started the tmux server with PyInstaller bootloader state
  (`_PYI_*`, `_MEIPASS`) in its environment; the main pane's sshkit child
  aborted in the bootloader within milliseconds and its kill-session chain
  destroyed the session before the splits were created. All tmux/Windows
  Terminal launches now scrub that state from the environment.

## 1.2.1

### Fixed
- Dashboards ignored a host's `mosh: always` setting: 1.1.1 hard-forced the
  main pane to ssh. The main pane now runs a plain `connect`, which honors the
  host's mosh mode; it is safe because every mosh attempt (auto or forced)
  that fails within 10 seconds now falls back to ssh inside the same process,
  so a failed bootstrap can no longer tear down the tmux session.
- Saved passwords now autofill for mosh too: the bootstrap ssh prompt is typed
  through the same pty wrapper used for plain ssh. A `mosh: always` host with
  a stored password connects hands-free, in dashboards and plain connects.

## 1.2.0

### Added
- Per-host mosh setting: `auto` (default — mosh when usable), `always`, or
  `never`. Set it with `sshkit add <alias> <host> --mosh never`, or in the
  browser's add/edit form ("Mosh: auto/always/never"). Stored in `hosts.json`;
  older state files read as `auto`. `sshkit list` shows `mosh=always|never`
  when set.
- Precedence: an explicit `connect --mosh`/`--no-mosh` flag beats the host
  setting; the host setting beats auto-detection. Dashboard panes stay on ssh
  regardless. A host set to `always` fails loudly when mosh is missing, like
  `--mosh`.

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
