"""Cross-platform path and API identity tests."""

from pathlib import Path

from boss_cli.platform import build_api_headers, camoufox_os_name, get_app_paths


def test_windows_paths_use_local_app_data_with_spaces_and_unicode():
    paths = get_app_paths(
        system="Windows",
        environ={"LOCALAPPDATA": r"C:\Users\招聘 机器人\AppData\Local"},
        home=Path(r"C:\Users\ignored"),
    )

    assert str(paths.workflow_db).endswith("BossCLI/data/workflow.db")
    assert str(paths.secrets_file).endswith("BossCLI/secrets/secrets.dpapi")
    assert str(paths.deployment_config).endswith("BossCLI/config/deployment.json")
    assert paths.index_cache_file.parent == paths.data_dir


def test_macos_and_linux_existing_config_and_data_defaults_are_preserved():
    home = Path("/Users/example")
    mac = get_app_paths(system="Darwin", environ={}, home=home)
    linux = get_app_paths(system="Linux", environ={"XDG_DATA_HOME": "/srv/data"}, home=Path("/home/example"))

    assert mac.credential_file == home / ".config" / "boss-cli" / "credential.json"
    assert mac.workflow_db == home / ".local" / "share" / "boss-cli" / "workflow.db"
    assert linux.workflow_db == Path("/srv/data/boss-cli/workflow.db")
    assert linux.index_cache_file == Path("/home/example/.config/boss-cli/index_cache.json")


def test_linux_honors_xdg_config_data_and_state_homes():
    paths = get_app_paths(
        system="Linux",
        environ={
            "XDG_CONFIG_HOME": "/srv/config",
            "XDG_DATA_HOME": "/srv/data",
            "XDG_STATE_HOME": "/srv/state",
        },
        home=Path("/home/example"),
    )

    assert paths.config_dir == Path("/srv/config/boss-cli")
    assert paths.data_dir == Path("/srv/data/boss-cli")
    assert paths.log_dir == Path("/srv/state/boss-cli/logs")
    assert paths.linux is True


def test_linux_ignores_relative_xdg_homes():
    home = Path("/home/example")
    paths = get_app_paths(
        system="Linux",
        environ={"XDG_CONFIG_HOME": "relative", "XDG_DATA_HOME": "also-relative", "XDG_STATE_HOME": "state"},
        home=home,
    )

    assert paths.config_dir == home / ".config/boss-cli"
    assert paths.data_dir == home / ".local/share/boss-cli"
    assert paths.log_dir == home / ".local/state/boss-cli/logs"


def test_api_headers_advertise_consistent_desktop_platforms():
    windows = build_api_headers("Windows")
    mac = build_api_headers("Darwin")
    linux = build_api_headers("Linux")

    assert windows["sec-ch-ua-platform"] == '"Windows"'
    assert "Windows NT 10.0; Win64; x64" in windows["User-Agent"]
    assert mac["sec-ch-ua-platform"] == '"macOS"'
    assert "Macintosh; Intel Mac OS X 10_15_7" in mac["User-Agent"]
    assert linux["sec-ch-ua-platform"] == '"Linux"'
    assert "X11; Linux x86_64" in linux["User-Agent"]


def test_camoufox_fingerprint_os_matches_host_platform():
    assert camoufox_os_name("Windows") == "windows"
    assert camoufox_os_name("Darwin") == "macos"
    assert camoufox_os_name("Linux") == "linux"
