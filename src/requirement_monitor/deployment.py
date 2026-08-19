from __future__ import annotations

import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


TRUE_VALUES = {"1", "true", "yes", "on"}


class DeploymentUpdateManager:
    def __init__(self, repo_dir: Optional[Path] = None) -> None:
        self.repo_dir = repo_dir or Path(__file__).resolve().parents[2]
        self.script_path = self.repo_dir / "deploy" / "update-from-github.sh"
        self._lock = threading.Lock()
        self._running = False
        self._last_started_at: Optional[datetime] = None
        self._last_finished_at: Optional[datetime] = None
        self._last_exit_code: Optional[int] = None
        self._last_output = ""

    def enabled(self) -> bool:
        return os.getenv("REQUIREMENT_MONITOR_DEPLOY_UPDATE_ENABLED", "").lower() in TRUE_VALUES

    def status(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled(),
            "running": self._running,
            "repo_path": str(self.repo_dir),
            "script_exists": self.script_path.exists(),
            "branch": self._current_branch(),
            "last_started_at": self._last_started_at,
            "last_finished_at": self._last_finished_at,
            "last_exit_code": self._last_exit_code,
            "last_output": self._last_output[-12000:],
        }

    def check_updates(self) -> Dict[str, object]:
        self._ensure_enabled()
        result = self._run_script(["--check"], timeout=90)
        return {**self.status(), "last_exit_code": result.returncode, "last_output": result.stdout}

    def start_update(self, *, skip_backup: bool = False) -> Dict[str, object]:
        self._ensure_enabled()
        args = ["--apply"]
        if skip_backup:
            args.append("--skip-backup")
        with self._lock:
            if self._running:
                raise RuntimeError("deployment update is already running")
            self._running = True
            self._last_started_at = datetime.now(timezone.utc)
            self._last_finished_at = None
            self._last_exit_code = None
            self._last_output = "Deployment update started.\n"
            thread = threading.Thread(target=self._run_update_thread, args=(args,), daemon=True)
            thread.start()
        return self.status()

    def _ensure_enabled(self) -> None:
        if not self.enabled():
            raise RuntimeError("deployment update is disabled")
        if not self.script_path.exists():
            raise RuntimeError("deployment update script not found")

    def _run_update_thread(self, args: List[str]) -> None:
        try:
            result = self._run_script(args, timeout=None)
            exit_code = result.returncode
            output = result.stdout
        except Exception as error:  # pragma: no cover - defensive path for subprocess startup failures.
            exit_code = 1
            output = f"Deployment update failed to start: {error}"
        with self._lock:
            self._last_exit_code = exit_code
            self._last_output = output
            self._last_finished_at = datetime.now(timezone.utc)
            self._running = False

    def _run_script(self, args: List[str], *, timeout: Optional[int]) -> subprocess.CompletedProcess[str]:
        command = [str(self.script_path), *args]
        return subprocess.run(
            command,
            cwd=self.repo_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )

    def _current_branch(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.repo_dir,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except Exception:
            return None
        branch = result.stdout.strip()
        return branch or None


deployment_updates = DeploymentUpdateManager()
