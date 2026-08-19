from requirement_monitor.cli import build_parser, main


def test_cli_contains_current_commands():
    parser = build_parser()
    help_text = parser.format_help()
    assert "api" in help_text
    assert "run-once" in help_text
    assert "seed-demo" in help_text


def test_version_command(capsys):
    assert main(["version"]) == 0
    assert "requirement-monitor" in capsys.readouterr().out
