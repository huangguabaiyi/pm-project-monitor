from pathlib import Path

import pytest

from requirement_monitor.fixed_rules import (
    FixedRuleParseError,
    load_fixed_rules,
    parse_fixed_rules,
)


CURRENT_FIXED_RULES = (
    "服务端的上线时间固定为每周二和周四，需在前一天提交对应的 checklist 上线表格，且下午5点30分后禁止上线\n"
    "AT1轮加二轮的测试周期一般需要一周半以上，\n"
    "PV 测试一般在 3 天左右，加上 2 天解 Bug 的时间，总计大约 5 天\n"
    "线上回归一般在3天左右\n"
)


def test_parse_current_fixed_business_rules():
    rules = parse_fixed_rules(CURRENT_FIXED_RULES)

    assert rules.server_launch_weekdays == {1, 3}
    assert rules.server_launch_cutoff == "17:30"
    assert rules.checklist_days_before == 1
    assert rules.at_workdays == 8
    assert rules.at_natural_days == 11
    assert rules.pv_days == 3
    assert rules.bugfix_days == 2
    assert rules.regression_days == 3


def test_parse_fixed_rules_lists_every_missing_rule_once():
    incomplete_rules = "服务端的上线时间固定为每周二。"

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(incomplete_rules)

    message = str(exc_info.value)
    expected_missing = {
        "server_launch_weekdays",
        "server_launch_cutoff",
        "checklist_days_before",
        "at_workdays",
        "at_natural_days",
        "pv_days",
        "bugfix_days",
        "regression_days",
    }
    for missing_rule in expected_missing:
        assert message.count(missing_rule) == 1


def test_parse_fixed_rules_rejects_additional_server_launch_weekday():
    rules_text = CURRENT_FIXED_RULES.replace(
        "每周二和周四", "每周一、周二和周四"
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("server_launch_weekdays",)


def test_unrelated_1730_does_not_satisfy_server_launch_cutoff():
    rules_text = CURRENT_FIXED_RULES.replace(
        "，且下午5点30分后禁止上线", ""
    )
    rules_text += "每日例会时间为17:30\n"

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("server_launch_cutoff",)


@pytest.mark.parametrize(
    "cutoff_text", ("17:30前禁止上线", "下午5点30分前禁止上线")
)
def test_before_1730_does_not_satisfy_server_launch_cutoff(cutoff_text):
    rules_text = CURRENT_FIXED_RULES.replace(
        "下午5点30分后禁止上线", cutoff_text
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("server_launch_cutoff",)


def test_load_fixed_rules_reads_utf8_without_writing(tmp_path, monkeypatch):
    rules_path = tmp_path / "固定业务规则"
    rules_path.write_text(CURRENT_FIXED_RULES, encoding="utf-8")
    original_bytes = rules_path.read_bytes()

    def fail_if_called(self, *args, **kwargs):
        raise AssertionError(f"unexpected write to {self}")

    monkeypatch.setattr(Path, "write_text", fail_if_called)

    rules = load_fixed_rules(rules_path)

    assert rules.server_launch_weekdays == {1, 3}
    assert rules_path.read_bytes() == original_bytes
