import pytest

from bitbucket_local_mcp import tools
from bitbucket_local_mcp.bitbucket_cli import BitbucketConfig


def _allowlisted_config() -> BitbucketConfig:
    return BitbucketConfig(email="e", token="t", allowed_repos=frozenset({"ws/repo"}))


def test_get_pull_request_diff_rejects_invalid_repo(monkeypatch):
    monkeypatch.setattr(tools, "get_configuration", _allowlisted_config)

    with pytest.raises(ValueError):
        tools.get_pull_request_diff("ws", "other", 1)


def test_get_commit_diff_rejects_empty_hash(monkeypatch):
    monkeypatch.setattr(tools, "get_configuration", _allowlisted_config)

    with pytest.raises(ValueError):
        tools.get_commit_diff("ws", "repo", "")


def test_list_pull_request_comments_rejects_invalid_cursor(monkeypatch):
    monkeypatch.setattr(tools, "get_configuration", _allowlisted_config)

    with pytest.raises(ValueError):
        tools.list_pull_request_comments(
            "ws",
            "repo",
            1,
            cursor="https://evil.example/repositories/ws/repo/pullrequests/1/comments?pagelen=10",
        )


def test_list_pull_request_commits_rejects_invalid_cursor(monkeypatch):
    monkeypatch.setattr(tools, "get_configuration", _allowlisted_config)

    with pytest.raises(ValueError):
        tools.list_pull_request_commits(
            "ws",
            "repo",
            1,
            cursor="https://evil.example/repositories/ws/repo/pullrequests/1/commits?pagelen=10",
        )


def test_read_file_range_rejects_invalid_range(monkeypatch):
    monkeypatch.setattr(tools, "get_configuration", _allowlisted_config)

    with pytest.raises(ValueError):
        tools.read_file_range("ws", "repo", "main", "src/app.py", 10, 1)


def test_read_file_range_rejects_too_many_lines(monkeypatch):
    monkeypatch.setattr(tools, "get_configuration", _allowlisted_config)

    with pytest.raises(ValueError):
        tools.read_file_range("ws", "repo", "main", "src/app.py", 1, 9999)


def test_get_commit_rejects_empty_hash(monkeypatch):
    monkeypatch.setattr(tools, "get_configuration", _allowlisted_config)

    with pytest.raises(ValueError):
        tools.get_commit("ws", "repo", "")


def test_crawl_repository_rejects_invalid_root_path(monkeypatch):
    monkeypatch.setattr(tools, "get_configuration", _allowlisted_config)

    with pytest.raises(ValueError):
        tools.crawl_repository("ws", "repo", "main", root_path=".git")


def test_crawl_repository_caps_max_files(monkeypatch):
    monkeypatch.setattr(tools, "get_configuration", _allowlisted_config)

    # No network calls: stub list_files to return empty listing.
    monkeypatch.setattr(tools, "list_files", lambda *args, **kwargs: {"entries": [], "next_cursor": ""})

    result = tools.crawl_repository("ws", "repo", "main", max_files=9999)
    assert result["limits"]["max_files"] <= 200


def test_crawl_repository_include_exclude_filters(monkeypatch):
    monkeypatch.setattr(tools, "get_configuration", _allowlisted_config)

    def _fake_list_files(*args, **kwargs):
        return {
            "entries": [
                {"path": "src/app.py", "type": "commit_file", "size": 10},
                {"path": "docs/readme.md", "type": "commit_file", "size": 10},
            ],
            "next_cursor": "",
        }

    monkeypatch.setattr(tools, "list_files", _fake_list_files)
    monkeypatch.setattr(tools, "request_bytes", lambda *args, **kwargs: b"print('ok')\n")

    result = tools.crawl_repository(
        "ws",
        "repo",
        "main",
        include_paths=["docs"],
        exclude_paths=["docs/private"],
        include_suffixes=[".md"],
    )

    assert result["extracted_count"] == 1
    assert result["extracted_files"][0]["path"] == "docs/readme.md"
