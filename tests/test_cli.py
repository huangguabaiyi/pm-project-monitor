import pytest

from requirement_monitor import cli


def test_version_command(capsys):
    exit_code = cli.main(["version"])
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "requirement-monitor 0.1.0"


def test_build_parser_only_allows_version():
    parser = cli.build_parser()

    assert parser.parse_args(["version"]).command == "version"
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["unknown"])

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("flag", "apply"), (("--dry-run", False), ("--apply", True))
)
def test_init_table_parser_requires_an_explicit_mode(flag, apply):
    args = cli.build_parser().parse_args(["init-table", flag])

    assert args.command == "init-table"
    assert args.apply is apply


def test_init_table_parser_rejects_missing_or_conflicting_modes():
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["init-table"])
    with pytest.raises(SystemExit):
        parser.parse_args(["init-table", "--dry-run", "--apply"])


def test_init_table_dry_run_prints_operations_without_applying(capsys):
    calls = []

    def fake_initializer(bitable_url, *, apply):
        calls.append((bitable_url, apply))
        return [
            type(
                "FakeOperation",
                (),
                {"kind": "rename_table", "payload": {"name": "需求主表"}},
            )()
        ]

    exit_code = cli.main(
        ["init-table", "--dry-run"],
        initialize_schema_fn=fake_initializer,
        load_settings_fn=lambda path: type(
            "Settings", (), {"bitable_url": "https://example.feishu.cn/base/app"}
        )(),
    )

    assert exit_code == 0
    assert calls == [("https://example.feishu.cn/base/app", False)]
    assert "rename_table" in capsys.readouterr().out
