from requirement_monitor.cli import main


def test_version_command(capsys):
    exit_code = main(["version"])
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "requirement-monitor 0.1.0"
