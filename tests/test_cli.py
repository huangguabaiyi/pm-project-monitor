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
