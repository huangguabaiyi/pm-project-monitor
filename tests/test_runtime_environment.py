import subprocess
from pathlib import Path

import pytest

from requirement_monitor.runtime_environment import (
    RuntimeEnvironmentError,
    current_git_branch,
    validate_runtime_environment,
)


def test_current_git_branch_uses_symbolic_ref_for_repository():
    calls = []

    def command_runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")

    branch = current_git_branch(Path("/tmp/repository"), command_runner)

    assert branch == "main"
    assert calls == [
        (
            [
                "git",
                "-C",
                "/tmp/repository",
                "symbolic-ref",
                "--short",
                "HEAD",
            ],
            {
                "check": False,
                "capture_output": True,
                "text": True,
                "timeout": 5,
            },
        )
    ]


def test_current_git_branch_returns_none_for_detached_head_without_stderr(
    capsys,
):
    result = subprocess.CompletedProcess(
        ["git"], 1, stdout="", stderr="secret-token\n"
    )

    assert current_git_branch(Path("."), lambda *args, **kwargs: result) is None
    captured = capsys.readouterr()
    assert "secret-token" not in captured.out + captured.err


def test_current_git_branch_returns_none_for_blank_branch():
    result = subprocess.CompletedProcess(["git"], 0, stdout="\n", stderr="")

    assert current_git_branch(Path("."), lambda *args, **kwargs: result) is None


def test_current_git_branch_returns_none_when_git_cannot_run():
    def command_runner(*args, **kwargs):
        raise OSError("git failed with secret-token")

    assert current_git_branch(Path("."), command_runner) is None


def test_current_git_branch_returns_none_when_git_times_out(capsys):
    def command_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            args[0], timeout=kwargs["timeout"], stderr="secret-token"
        )

    assert current_git_branch(Path("."), command_runner) is None
    captured = capsys.readouterr()
    assert "secret-token" not in captured.out + captured.err


def test_prod_requires_exact_main_branch():
    validate_runtime_environment("prod", branch="main")


@pytest.mark.parametrize("branch", ["feature/x", "develop", "MAIN", None])
def test_prod_rejects_non_main_branch(branch):
    with pytest.raises(RuntimeEnvironmentError, match="main") as exc_info:
        validate_runtime_environment("prod", branch=branch)

    assert str(branch) not in str(exc_info.value)


@pytest.mark.parametrize("branch", ["main", "feature/x", None])
def test_test_environment_accepts_any_or_unknown_branch(branch):
    validate_runtime_environment("test", branch=branch)
