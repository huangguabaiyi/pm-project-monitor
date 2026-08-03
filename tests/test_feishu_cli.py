import os
import subprocess

import pytest

from requirement_monitor.feishu_cli import FeishuCLI, FeishuCLIError


def test_run_json_executes_feishu_without_shell_and_decodes_json(monkeypatch):
    calls = []
    monkeypatch.setenv("FEISHU_ACCESS_TOKEN", "feishu-auth-token")
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", "webhook-secret")
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_API_KEY", "llm-secret")
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_MODEL", "model-name")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"logged_in":true}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert FeishuCLI().run_json(["auth", "status"]) == {"logged_in": True}
    assert len(calls) == 1
    command, kwargs = calls[0]
    subprocess_env = kwargs.pop("env")
    assert command == ["feishu", "auth", "status"]
    assert kwargs == {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "timeout": 60,
        "shell": False,
    }
    assert subprocess_env["FEISHU_ACCESS_TOKEN"] == "feishu-auth-token"
    assert subprocess_env["REQUIREMENT_MONITOR_LLM_MODEL"] == "model-name"
    assert "REQUIREMENT_MONITOR_WEBHOOK_URL" not in subprocess_env
    assert "REQUIREMENT_MONITOR_LLM_API_KEY" not in subprocess_env
    assert os.environ["REQUIREMENT_MONITOR_WEBHOOK_URL"] == "webhook-secret"
    assert os.environ["REQUIREMENT_MONITOR_LLM_API_KEY"] == "llm-secret"


def test_run_json_raises_error_with_stderr_for_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "token_invalid"
        ),
    )

    with pytest.raises(FeishuCLIError, match="token_invalid"):
        FeishuCLI().run_json(["bitable", "meta", "app"])


def test_run_json_redacts_webhook_and_llm_secrets(monkeypatch):
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/webhook-secret"
    api_key = "sk-llm-secret-value"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            "",
            f'webhook_url="{webhook}" api_key="{api_key}"',
        ),
    )

    with pytest.raises(FeishuCLIError) as exc_info:
        FeishuCLI().run_json(["bitable", "meta", "app"])

    message = str(exc_info.value)
    assert webhook not in message
    assert api_key not in message
    assert "[REDACTED]" in message


def test_run_json_redacts_quoted_webhook_url_json_key(monkeypatch):
    webhook_url = "https://hooks.example.com/path/json-webhook-secret"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", f'{{"webhook_url":"{webhook_url}"}}'
        ),
    )

    with pytest.raises(FeishuCLIError) as exc_info:
        FeishuCLI().run_json(["bitable", "meta", "app"])

    message = str(exc_info.value)
    assert webhook_url not in message
    assert "json-webhook-secret" not in message


def test_run_json_redacts_nested_api_key_json_keys(monkeypatch):
    api_key = "nested-generic-api-key"
    llm_api_key = "nested-llm-api-key"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            "",
            (
                f'{{"provider":{{"api_key":"{api_key}",'
                f'"llm_api_key":"{llm_api_key}"}}}}'
            ),
        ),
    )

    with pytest.raises(FeishuCLIError) as exc_info:
        FeishuCLI().run_json(["bitable", "meta", "app"])

    message = str(exc_info.value)
    assert api_key not in message
    assert llm_api_key not in message


def test_run_json_redacts_monitor_env_secrets_and_bearer_tokens(monkeypatch):
    llm_api_key = "opaque.llm/key+with=arbitrary-value,llm-tail"
    webhook_url = "https://hooks.example.com/custom/path?secret=arbitrary-value"
    bearer_token = "opaque.bearer/token+with=arbitrary-value,bearer-tail"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            "",
            (
                f"REQUIREMENT_MONITOR_LLM_API_KEY={llm_api_key} "
                f'REQUIREMENT_MONITOR_WEBHOOK_URL="{webhook_url}" '
                f"Authorization: Bearer {bearer_token}"
            ),
        ),
    )

    with pytest.raises(FeishuCLIError) as exc_info:
        FeishuCLI().run_json(["bitable", "meta", "app"])

    message = str(exc_info.value)
    assert llm_api_key not in message
    assert webhook_url not in message
    assert bearer_token not in message
    assert "llm-tail" not in message
    assert "bearer-tail" not in message
    assert "REQUIREMENT_MONITOR_LLM_API_KEY=[REDACTED]" in message
    assert "REQUIREMENT_MONITOR_WEBHOOK_URL=[REDACTED]" in message
    assert "Bearer [REDACTED]" in message


@pytest.mark.parametrize("failure_source", ("stderr", "stdout", "exception"))
def test_run_json_redacts_secret_named_environment_values(
    monkeypatch, failure_source
):
    access_token = "feishu-access-token-from-environment"
    database_password = "database-password-from-environment"
    monkeypatch.setenv("FEISHU_ACCESS_TOKEN", access_token)
    monkeypatch.setenv("DATABASE_PASSWORD", database_password)

    def fake_run(command, **kwargs):
        assert kwargs["env"]["FEISHU_ACCESS_TOKEN"] == access_token
        detail = "{} {}".format(access_token, database_password)
        if failure_source == "exception":
            raise OSError(detail)
        return subprocess.CompletedProcess(
            command,
            1,
            detail if failure_source == "stdout" else "",
            detail if failure_source == "stderr" else "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FeishuCLIError) as exc_info:
        FeishuCLI().run_json(["auth", "status"])

    message = str(exc_info.value)
    assert access_token not in message
    assert database_password not in message
    assert "[REDACTED]" in message


def test_run_json_raises_clear_error_for_invalid_json(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "not-json", ""
        ),
    )

    with pytest.raises(FeishuCLIError, match="invalid JSON"):
        FeishuCLI().run_json(["auth", "status"])


def test_run_json_raises_clear_error_for_timeout(monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FeishuCLIError, match="timed out after 15 seconds"):
        FeishuCLI(timeout=15).run_json(["auth", "status"])


def test_run_json_raises_clear_error_when_feishu_is_missing(monkeypatch):
    def fake_run(command, **kwargs):
        raise FileNotFoundError("feishu")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FeishuCLIError, match="was not found"):
        FeishuCLI().run_json(["auth", "status"])


def test_run_json_wraps_os_errors(monkeypatch):
    def fake_run(command, **kwargs):
        raise OSError("operation not permitted")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FeishuCLIError, match="could not be executed"):
        FeishuCLI().run_json(["auth", "status"])


def test_run_json_wraps_unicode_decode_errors(monkeypatch):
    def fake_run(command, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FeishuCLIError, match="valid UTF-8"):
        FeishuCLI().run_json(["auth", "status"])


@pytest.mark.parametrize("output", ['["item"]', '"text"', "null", "42"])
def test_run_json_rejects_non_object_json(monkeypatch, output):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, output, ""),
    )

    with pytest.raises(FeishuCLIError, match="JSON object"):
        FeishuCLI().run_json(["auth", "status"])


@pytest.fixture
def recorded_cli(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return FeishuCLI(), commands


@pytest.mark.parametrize(
    ("method_name", "method_args", "expected"),
    [
        ("auth_status", (), ["feishu", "auth", "status"]),
        ("meta", ("base-url",), ["feishu", "bitable", "meta", "base-url"]),
        (
            "fields",
            ("app", "table"),
            ["feishu", "bitable", "fields", "app", "table"],
        ),
        (
            "rename_table",
            ("app", "table", "新表"),
            [
                "feishu",
                "bitable",
                "rename-table",
                "app",
                "table",
                "--name",
                "新表",
            ],
        ),
        (
            "create_view",
            ("app", "table", "看板", "kanban"),
            [
                "feishu",
                "bitable",
                "create-view",
                "app",
                "table",
                "--name",
                "看板",
                "--type",
                "kanban",
            ],
        ),
    ],
)
def test_simple_helpers_build_verified_commands(
    recorded_cli, method_name, method_args, expected
):
    cli, commands = recorded_cli

    getattr(cli, method_name)(*method_args)

    assert commands == [expected]


def test_records_builds_pagination_and_automatic_field_arguments(recorded_cli):
    cli, commands = recorded_cli

    cli.records(
        "app",
        "table",
        page_size=500,
        page_token="next-page",
        view_id="view",
        automatic_fields=True,
    )

    assert commands == [
        [
            "feishu",
            "bitable",
            "records",
            "app",
            "table",
            "--page-size",
            "500",
            "--page-token",
            "next-page",
            "--view-id",
            "view",
            "--automatic-fields",
        ]
    ]


def test_search_builds_repeatable_and_json_arguments(recorded_cli):
    cli, commands = recorded_cli

    cli.search(
        "app",
        "table",
        filters=["状态=进行中", "数量>10"],
        filter_json={"conjunction": "and", "conditions": []},
        sorts=["更新时间 desc", "名称"],
        field_names=["名称", "状态"],
        page_size=100,
        page_token="next-page",
        view_id="view",
        automatic_fields=True,
    )

    assert commands == [
        [
            "feishu",
            "bitable",
            "search",
            "app",
            "table",
            "--filter",
            "状态=进行中",
            "--filter",
            "数量>10",
            "--filter-json",
            '{"conjunction": "and", "conditions": []}',
            "--sort",
            "更新时间 desc",
            "--sort",
            "名称",
            "--fields",
            "名称,状态",
            "--page-size",
            "100",
            "--page-token",
            "next-page",
            "--view-id",
            "view",
            "--automatic-fields",
        ]
    ]


def test_update_field_serializes_property_as_utf8_json(recorded_cli):
    cli, commands = recorded_cli

    cli.update_field(
        "app",
        "table",
        "field",
        name="新字段",
        property={"options": [{"name": "进行中"}]},
    )

    assert commands == [
        [
            "feishu",
            "bitable",
            "update-field",
            "app",
            "table",
            "field",
            "--name",
            "新字段",
            "--property",
            '{"options": [{"name": "进行中"}]}',
        ]
    ]


def test_create_table_serializes_fields_as_utf8_json(recorded_cli):
    cli, commands = recorded_cli

    cli.create_table(
        "app",
        "需求主表",
        [{"field_name": "需求名称", "type": 1}],
        default_view_name="全部需求",
    )

    assert commands == [
        [
            "feishu",
            "bitable",
            "create-table",
            "app",
            "--name",
            "需求主表",
            "--fields",
            '[{"field_name": "需求名称", "type": 1}]',
            "--default-view-name",
            "全部需求",
        ]
    ]


def test_create_field_builds_all_supported_options(recorded_cli):
    cli, commands = recorded_cli

    cli.create_field(
        "app",
        "table",
        "完成率",
        2,
        property={"formatter": "0%"},
        ui_type="Progress",
    )

    assert commands == [
        [
            "feishu",
            "bitable",
            "create-field",
            "app",
            "table",
            "--name",
            "完成率",
            "--type",
            "2",
            "--property",
            '{"formatter": "0%"}',
            "--ui-type",
            "Progress",
        ]
    ]


@pytest.mark.parametrize(
    ("method_name", "records", "expected_command"),
    [
        (
            "batch_create",
            [{"需求名称": "中文需求"}],
            "batch-create",
        ),
        (
            "batch_update",
            [{"id": "record", "fields": {"状态": "完成"}}],
            "batch-update",
        ),
    ],
)
def test_batch_helpers_serialize_records_as_utf8_json(
    recorded_cli, method_name, records, expected_command
):
    cli, commands = recorded_cli

    getattr(cli, method_name)("app", "table", records)

    assert commands == [
        [
            "feishu",
            "bitable",
            expected_command,
            "app",
            "table",
            "--records",
            json_text(records),
        ]
    ]


@pytest.mark.parametrize(
    ("method_name", "expected_command"),
    [("batch_create", "batch-create"), ("batch_update", "batch-update")],
)
def test_batch_helpers_support_file_input_without_payload_in_argv(
    recorded_cli, tmp_path, method_name, expected_command
):
    cli, commands = recorded_cli
    secret_payload = "敏感长内容" * 10000
    batch_file = tmp_path / "batch.json"
    batch_file.write_text(secret_payload, encoding="utf-8")

    getattr(cli, method_name)("app", "table", file_path=str(batch_file))

    assert commands == [
        [
            "feishu",
            "bitable",
            expected_command,
            "app",
            "table",
            "-f",
            str(batch_file),
        ]
    ]
    assert secret_payload not in " ".join(commands[0])


@pytest.mark.parametrize("method_name", ["batch_create", "batch_update"])
def test_batch_helpers_require_exactly_one_input_source(recorded_cli, method_name):
    cli, commands = recorded_cli

    with pytest.raises(FeishuCLIError, match="exactly one"):
        getattr(cli, method_name)("app", "table")
    with pytest.raises(FeishuCLIError, match="exactly one"):
        getattr(cli, method_name)(
            "app", "table", [{"字段": "值"}], file_path="batch.json"
        )

    assert commands == []


@pytest.mark.parametrize(
    "invalid_value",
    [float("nan"), object()],
)
def test_json_arguments_reject_non_standard_or_unserializable_values(
    monkeypatch, invalid_value
):
    def fail_run(command, **kwargs):
        raise AssertionError("subprocess must not run for invalid JSON arguments")

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(FeishuCLIError, match="could not be serialized"):
        FeishuCLI().batch_create(
            "app", "table", [{"field": invalid_value}]
        )


def json_text(value):
    import json

    return json.dumps(value, ensure_ascii=False)
