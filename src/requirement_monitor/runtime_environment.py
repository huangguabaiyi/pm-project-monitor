import subprocess
from pathlib import Path
from typing import Optional


GIT_COMMAND_TIMEOUT_SECONDS = 5


class RuntimeEnvironmentError(ValueError):
    pass


def current_git_branch(
    repository: Path,
    command_runner=subprocess.run,
) -> Optional[str]:
    try:
        result = command_runner(
            [
                "git",
                "-C",
                str(repository),
                "symbolic-ref",
                "--short",
                "HEAD",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def validate_runtime_environment(
    environment: str,
    *,
    branch: Optional[str],
) -> None:
    if environment == "prod" and branch != "main":
        raise RuntimeEnvironmentError(
            "Production Webhook is only available from the main branch."
        )
