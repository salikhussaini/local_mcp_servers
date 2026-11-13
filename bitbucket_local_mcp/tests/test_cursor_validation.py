from urllib.parse import urlsplit

import pytest

from bitbucket_local_mcp.bitbucket_cli import BitbucketConfig, build_source_url


def _validate_cursor(expected_url: str, cursor: str) -> None:
    expected = urlsplit(expected_url)
    supplied = urlsplit(cursor)

    if (
        supplied.scheme != "https"
        or supplied.netloc != expected.netloc
        or supplied.path != expected.path
        or supplied.fragment
        or not supplied.query
    ):
        raise ValueError("Invalid pagination cursor.")


def test_cursor_validation_accepts_same_endpoint():
    config = BitbucketConfig(email="e", token="t", allowed_repos=frozenset({"ws/repo"}))
    expected_url = build_source_url(config, "ws", "repo", "main", "")
    cursor = f"{expected_url}?pagelen=100&page=2"

    _validate_cursor(expected_url, cursor)


@pytest.mark.parametrize(
    "cursor",
    [
        "http://api.bitbucket.org/2.0/repositories/ws/repo/src/main/?pagelen=100",
        "https://evil.example/repositories/ws/repo/src/main/?pagelen=100",
        "https://api.bitbucket.org/2.0/repositories/ws/repo/src/main/#frag?x=1",
        "https://api.bitbucket.org/2.0/repositories/ws/repo/src/other/?pagelen=100",
        "https://api.bitbucket.org/2.0/repositories/ws/repo/src/main/",
    ],
)
def test_cursor_validation_rejects_invalid(cursor):
    config = BitbucketConfig(email="e", token="t", allowed_repos=frozenset({"ws/repo"}))
    expected_url = build_source_url(config, "ws", "repo", "main", "")

    with pytest.raises(ValueError):
        _validate_cursor(expected_url, cursor)
