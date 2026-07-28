"""End-to-end checks against a sandboxed ~/.ssh directory."""

from sshkit.model import Host, host_to_dict


def test_state_roundtrip_and_config_write(sandbox):
    model = sandbox
    data = model.load_state()
    assert data == {"hosts": {}, "pins": []}

    data["hosts"]["phil"] = host_to_dict(Host("phil", hostname="phil.example.edu", user="me"))
    data["pins"].append("phil")
    model.save_state(data)
    model.write_ssh_config(data)

    assert model.load_state()["hosts"]["phil"]["hostname"] == "phil.example.edu"
    text = model.compat.SSH_CONFIG.read_text()
    assert "Host phil" in text
    assert "HostName phil.example.edu" in text
    assert "\r\n" not in text


def test_existing_user_config_is_preserved(sandbox):
    model = sandbox
    model.compat.SSH_CONFIG.write_text("Host hand-written\n  HostName legacy.example.com\n")

    data = model.load_state()
    data["hosts"]["new"] = host_to_dict(Host("new", hostname="1.1.1.1"))
    model.write_ssh_config(data)

    text = model.compat.SSH_CONFIG.read_text()
    assert "hand-written" in text
    assert "Host new" in text

    # Writing twice must not duplicate the managed block.
    model.write_ssh_config(data)
    assert text.count(model.compat.BEGIN_MARKER) == 1


def test_config_hosts_are_discovered_and_merged(sandbox):
    model = sandbox
    model.compat.SSH_CONFIG.write_text(
        "Host legacy\n  HostName legacy.example.com\n  User bob\n\nHost *\n  ForwardAgent yes\n"
    )
    data = model.load_state()
    data["hosts"]["managed"] = host_to_dict(Host("managed", hostname="2.2.2.2"))
    model.save_state(data)

    aliases = {h.alias: h for h in model.all_hosts(model.load_state())}
    assert set(aliases) == {"legacy", "managed"}  # 'Host *' patterns are skipped
    assert aliases["legacy"].source == "config"
    assert aliases["managed"].source == "managed"


def test_remove_alias_from_unmanaged_config(sandbox):
    model = sandbox
    model.compat.SSH_CONFIG.write_text("Host one two\n  HostName shared.example.com\n\nHost solo\n  HostName solo\n")
    assert model.remove_alias_from_unmanaged_config("one") is True
    text = model.compat.SSH_CONFIG.read_text()
    assert "Host two" in text
    assert "Host one" not in text

    assert model.remove_alias_from_unmanaged_config("solo") is True
    assert "solo" not in model.compat.SSH_CONFIG.read_text()

    assert model.remove_alias_from_unmanaged_config("missing") is False


def test_corrupt_state_file_falls_back_to_empty(sandbox):
    model = sandbox
    model.compat.ensure_dirs()
    model.compat.STATE_FILE.write_text("{not json")
    assert model.load_state() == {"hosts": {}, "pins": []}
