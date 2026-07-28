import importlib
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Reload sshkit with ~/.ssh redirected into a temp directory."""
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    monkeypatch.setenv("SSHKIT_SSH_DIR", str(ssh_dir))
    monkeypatch.setenv("SSHKIT_DATA_DIR", str(ssh_dir / "sshkit"))
    monkeypatch.setenv("SSHKIT_SSH_CONFIG", str(ssh_dir / "config"))

    import sshkit.compat as compat_mod

    importlib.reload(compat_mod)
    import sshkit.model as model_mod

    importlib.reload(model_mod)
    yield model_mod
    importlib.reload(compat_mod)
    importlib.reload(model_mod)
