import json
import os
import re
import subprocess
from typing import Any, Dict, Mapping, Optional, Sequence


JsonObject = Dict[str, Any]


class FeishuCLIError(RuntimeError):
    """Raised when the authenticated Feishu CLI cannot return valid JSON."""


class FeishuCLI:
    def __init__(self, timeout: float = 60) -> None:
        self.timeout = timeout

    def run_json(self, arguments: Sequence[str]) -> JsonObject:
        command = ["feishu", *arguments]
        environment = os.environ.copy()
        for variable_name in _SECRET_ENVIRONMENT_VARIABLES:
            environment.pop(variable_name, None)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout,
                shell=False,
                env=environment,
            )
        except FileNotFoundError:
            raise FeishuCLIError(
                "Feishu CLI executable 'feishu' was not found"
            ) from None
        except subprocess.TimeoutExpired:
            raise FeishuCLIError(
                f"Feishu CLI timed out after {self.timeout:g} seconds"
            ) from None
        except UnicodeDecodeError:
            raise FeishuCLIError(
                "Feishu CLI output was not valid UTF-8"
            ) from None
        except OSError as error:
            detail = _sanitize_error(str(error)).strip()
            message = "Feishu CLI could not be executed"
            if detail:
                message = f"{message}: {detail}"
            raise FeishuCLIError(message) from None

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            if detail:
                raise FeishuCLIError(
                    f"Feishu CLI failed: {_sanitize_error(detail)}"
                )
            raise FeishuCLIError(
                f"Feishu CLI failed with exit code {result.returncode}"
            )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise FeishuCLIError("Feishu CLI returned invalid JSON") from None
        except UnicodeDecodeError:
            raise FeishuCLIError(
                "Feishu CLI output was not valid UTF-8"
            ) from None
        if not isinstance(payload, dict):
            raise FeishuCLIError(
                "Feishu CLI returned JSON but not a JSON object"
            )
        return payload

    def auth_status(self) -> JsonObject:
        return self.run_json(["auth", "status"])

    def meta(self, url_or_token: str) -> JsonObject:
        return self.run_json(["bitable", "meta", url_or_token])

    def fields(self, app_token: str, table_id: str) -> JsonObject:
        return self.run_json(["bitable", "fields", app_token, table_id])

    def records(
        self,
        app_token: str,
        table_id: str,
        *,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
        view_id: Optional[str] = None,
        automatic_fields: bool = False,
    ) -> JsonObject:
        arguments = ["bitable", "records", app_token, table_id]
        _append_option(arguments, "--page-size", page_size)
        _append_option(arguments, "--page-token", page_token)
        _append_option(arguments, "--view-id", view_id)
        if automatic_fields:
            arguments.append("--automatic-fields")
        return self.run_json(arguments)

    def search(
        self,
        app_token: str,
        table_id: str,
        *,
        filters: Sequence[str] = (),
        filter_json: Optional[Mapping[str, Any]] = None,
        sorts: Sequence[str] = (),
        field_names: Sequence[str] = (),
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
        view_id: Optional[str] = None,
        automatic_fields: bool = False,
    ) -> JsonObject:
        arguments = ["bitable", "search", app_token, table_id]
        for filter_expression in filters:
            arguments.extend(["--filter", filter_expression])
        if filter_json is not None:
            arguments.extend(["--filter-json", _json_argument(filter_json)])
        for sort_expression in sorts:
            arguments.extend(["--sort", sort_expression])
        if field_names:
            arguments.extend(["--fields", ",".join(field_names)])
        _append_option(arguments, "--page-size", page_size)
        _append_option(arguments, "--page-token", page_token)
        _append_option(arguments, "--view-id", view_id)
        if automatic_fields:
            arguments.append("--automatic-fields")
        return self.run_json(arguments)

    def rename_table(
        self, app_token: str, table_id: str, name: str
    ) -> JsonObject:
        return self.run_json(
            ["bitable", "rename-table", app_token, table_id, "--name", name]
        )

    def update_field(
        self,
        app_token: str,
        table_id: str,
        field_id: str,
        *,
        name: Optional[str] = None,
        property: Optional[Mapping[str, Any]] = None,
    ) -> JsonObject:
        arguments = ["bitable", "update-field", app_token, table_id, field_id]
        _append_option(arguments, "--name", name)
        if property is not None:
            arguments.extend(["--property", _json_argument(property)])
        return self.run_json(arguments)

    def create_table(
        self,
        app_token: str,
        name: str,
        fields: Sequence[Mapping[str, Any]],
        *,
        default_view_name: Optional[str] = None,
    ) -> JsonObject:
        arguments = [
            "bitable",
            "create-table",
            app_token,
            "--name",
            name,
            "--fields",
            _json_argument(fields),
        ]
        _append_option(arguments, "--default-view-name", default_view_name)
        return self.run_json(arguments)

    def create_field(
        self,
        app_token: str,
        table_id: str,
        name: str,
        field_type: int,
        *,
        property: Optional[Mapping[str, Any]] = None,
        ui_type: Optional[str] = None,
    ) -> JsonObject:
        arguments = [
            "bitable",
            "create-field",
            app_token,
            table_id,
            "--name",
            name,
            "--type",
            str(field_type),
        ]
        if property is not None:
            arguments.extend(["--property", _json_argument(property)])
        _append_option(arguments, "--ui-type", ui_type)
        return self.run_json(arguments)

    def create_view(
        self,
        app_token: str,
        table_id: str,
        name: str,
        view_type: str = "grid",
    ) -> JsonObject:
        return self.run_json(
            [
                "bitable",
                "create-view",
                app_token,
                table_id,
                "--name",
                name,
                "--type",
                view_type,
            ]
        )

    def batch_create(
        self,
        app_token: str,
        table_id: str,
        records: Optional[Sequence[Mapping[str, Any]]] = None,
        *,
        file_path: Optional[str] = None,
    ) -> JsonObject:
        return self.run_json(
            _batch_arguments(
                "batch-create", app_token, table_id, records, file_path
            )
        )

    def batch_update(
        self,
        app_token: str,
        table_id: str,
        records: Optional[Sequence[Mapping[str, Any]]] = None,
        *,
        file_path: Optional[str] = None,
    ) -> JsonObject:
        return self.run_json(
            _batch_arguments(
                "batch-update", app_token, table_id, records, file_path
            )
        )


def _batch_arguments(
    command: str,
    app_token: str,
    table_id: str,
    records: Optional[Sequence[Mapping[str, Any]]],
    file_path: Optional[str],
) -> list:
    has_records = records is not None
    has_file = isinstance(file_path, str) and bool(file_path.strip())
    if has_records == has_file:
        raise FeishuCLIError(
            "Bitable batch operation requires exactly one input source"
        )
    arguments = ["bitable", command, app_token, table_id]
    if has_file:
        arguments.extend(["-f", file_path])
    else:
        arguments.extend(["--records", _json_argument(records)])
    return arguments


def _append_option(
    arguments: list, option: str, value: Optional[Any]
) -> None:
    if value is not None:
        arguments.extend([option, str(value)])


def _json_argument(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise FeishuCLIError(
            "Feishu CLI JSON argument could not be serialized"
        ) from None


_SECRET_ENVIRONMENT_VARIABLES = frozenset(
    {
        "REQUIREMENT_MONITOR_WEBHOOK_URL",
        "REQUIREMENT_MONITOR_LLM_API_KEY",
    }
)


_WEBHOOK_PATTERN = re.compile(
    r"https?://[^\s\"']+/open-apis/bot/v2/hook/[^\s\"']+",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?ix)("
    r"(?<![A-Za-z0-9_])"
    r"(?P<key_quote>[\"']?)"
    r"(?:requirement_monitor_(?:llm_api_key|webhook_url)|"
    r"llm[_-]?api[_-]?key|api[_-]?key|llm[_-]?secret|webhook[_-]?url)"
    r"(?P=key_quote)\s*[:=]\s*"
    r")"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s]+)"
)
_BEARER_TOKEN_PATTERN = re.compile(
    r"(?i)(\bBearer\s+)(?:\"[^\"]*\"|'[^']*'|[^\s\"']+)"
)
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def _sanitize_error(message: str) -> str:
    sanitized = _WEBHOOK_PATTERN.sub("[REDACTED]", message)
    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}[REDACTED]", sanitized
    )
    sanitized = _BEARER_TOKEN_PATTERN.sub(
        lambda match: f"{match.group(1)}[REDACTED]", sanitized
    )
    return _OPENAI_KEY_PATTERN.sub("[REDACTED]", sanitized)
