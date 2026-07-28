from sshkit.model import (
    DEFAULT_MONITORS,
    Host,
    format_monitors,
    host_from_dict,
    host_to_dict,
    parse_bool,
    parse_monitor_list,
    render_managed_block,
    strip_managed_block,
    validate_alias,
)


def test_target_formatting():
    assert Host("box", hostname="10.0.0.1", user="root").target == "root@10.0.0.1"
    assert Host("box", hostname="10.0.0.1", user="root", port="2222").target == "root@10.0.0.1:2222"
    assert Host("box").target == "box"


def test_validate_alias():
    assert validate_alias("ok-host") is None
    assert validate_alias("") is not None
    assert validate_alias("has space") is not None
    assert validate_alias("glob*") is not None
    assert validate_alias("-lead") is not None


def test_parse_bool():
    assert parse_bool("y") is True
    assert parse_bool("no") is False
    assert parse_bool("", default=True) is True
    assert parse_bool(None, default=False) is False


def test_monitor_parsing_roundtrip():
    assert parse_monitor_list("gpu,cpu", DEFAULT_MONITORS) == {
        "gpu": True,
        "cpu": True,
        "users": False,
        "processes": False,
    }
    assert parse_monitor_list("none", DEFAULT_MONITORS) == dict.fromkeys(DEFAULT_MONITORS, False)
    assert parse_monitor_list("all", {}) == dict(DEFAULT_MONITORS)
    assert parse_monitor_list("", DEFAULT_MONITORS) == dict(DEFAULT_MONITORS)
    assert format_monitors({"gpu": True, "cpu": False, "users": False, "processes": False}) == "gpu"


def test_host_dict_roundtrip():
    host = Host(
        alias="phil",
        hostname="phil.example.edu",
        user="me",
        port="2222",
        identity_file="~/.ssh/id_ed25519",
        proxy_jump="bastion",
        dashboard=True,
        notes="gpu box",
    )
    restored = host_from_dict("phil", host_to_dict(host))
    assert restored == Host(**{**host.__dict__, "source": "managed"})


def test_host_from_dict_accepts_ssh_config_casing():
    host = host_from_dict("x", {"HostName": "h", "User": "u", "Port": "22", "ProxyJump": "j"})
    assert (host.hostname, host.user, host.proxy_jump) == ("h", "u", "j")


def test_render_and_strip_managed_block_is_lossless():
    hosts = {"a": Host("a", hostname="1.2.3.4", user="root", identity_file="/keys/id", proxy_jump="jump")}
    block = render_managed_block(hosts)
    assert "Host a" in block
    assert "IdentitiesOnly yes" in block
    assert "ProxyJump jump" in block

    user_config = "Host mine\n  HostName mine.example.com\n"
    combined = block + "\n" + user_config
    assert strip_managed_block(combined).strip() == user_config.strip()


def test_render_quotes_paths_with_spaces():
    hosts = {"a": Host("a", hostname="h", identity_file=r"C:\Users\Some One\.ssh\id_ed25519")}
    assert 'IdentityFile "C:\\Users\\Some One\\.ssh\\id_ed25519"' in render_managed_block(hosts)
