import pytest

from bitbucket_local_mcp.bitbucket_cli import (
    BitbucketConfig,
    get_configuration,
    validate_path,
    validate_pull_request_id,
)


def test_get_configuration_rejects_missing_env(monkeypatch):
    monkeypatch.delenv("BITBUCKET_EMAIL", raising=False)
    monkeypatch.delenv("BITBUCKET_API_TOKEN", raising=False)
    monkeypatch.delenv("BITBUCKET_ALLOWED_REPOS", raising=False)

    with pytest.raises(ValueError):
        get_configuration()


def test_get_configuration_parses_allowlist(monkeypatch):
    monkeypatch.setenv("BITBUCKET_EMAIL", "me@example.com")
    monkeypatch.setenv("BITBUCKET_API_TOKEN", "token")
    monkeypatch.setenv("BITBUCKET_ALLOWED_REPOS", "ws/repo-one, WS/repo-two ")

    config = get_configuration()
    assert isinstance(config, BitbucketConfig)
    assert "ws/repo-one" in config.allowed_repos
    assert "ws/repo-two" in config.allowed_repos


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".git/config",
        "dir/.hidden/file.txt",
        "../secrets.txt",
        "dir/../secrets.txt",
        "id_rsa",
        "certs/server.key",
    ],
)
def test_validate_path_blocks_hidden_and_secrets(path):
    with pytest.raises(ValueError):
        validate_path(path)


def test_validate_path_allows_normal_paths():
    validate_path("src/main.py")
    validate_path("docs/readme.txt")


@pytest.mark.parametrize("value", [1, "2", " 003 "])
def test_validate_pull_request_id_accepts_positive_ints(value):
    assert validate_pull_request_id(value) > 0


@pytest.mark.parametrize("value", [0, -1, "", "abc", "1.2", "-3"])
def test_validate_pull_request_id_rejects_invalid(value):
    with pytest.raises(ValueError):
        validate_pull_request_id(value)
