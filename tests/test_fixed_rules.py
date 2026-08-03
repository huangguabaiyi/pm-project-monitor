from pathlib import Path

import pytest

from requirement_monitor.fixed_rules import (
    FixedRuleParseError,
    load_fixed_rules,
    parse_fixed_rules,
)


CURRENT_FIXED_RULES = (
    "服务端的上线时间固定为每周二和周四，需在前一天提交对应的 checklist 上线表格，且下午5点30分后禁止上线\n"
    "AT 测试第一轮默认 4 个工作日\n"
    "AT 测试第二轮默认 4 个工作日\n"
    "PV 测试第一轮默认 3 个工作日\n"
    "PV 测试第二轮默认 2 个工作日\n"
    "线上回归一般在 2 天左右\n"
)

FIVE_STAGE_RULES = (
    "服务端的上线时间固定为每周二和周四，需在前一天提交对应的 checklist 上线表格，且下午5点30分后禁止上线\n"
    "AT 测试第一轮一般需要 4 天\n"
    "AT 测试第二轮一般需要 5 天\n"
    "PV 测试第一轮一般需要 3 天\n"
    "PV 测试第二轮一般需要 2 天\n"
    "线上回归一般在3天左右\n"
)


def test_parse_current_fixed_business_rules():
    rules = parse_fixed_rules(CURRENT_FIXED_RULES)

    assert rules.server_launch_weekdays == {1, 3}
    assert rules.server_launch_cutoff == "17:30"
    assert rules.checklist_days_before == 1
    assert (rules.at1_days, rules.at2_days) == (4, 4)
    assert (rules.pv1_days, rules.pv2_days) == (3, 2)
    assert rules.regression_days == 2


def test_parse_fixed_rules_exposes_each_key_stage_duration():
    rules = parse_fixed_rules(FIVE_STAGE_RULES)

    assert (rules.at1_days, rules.at2_days) == (4, 5)
    assert (rules.pv1_days, rules.pv2_days) == (3, 2)
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
        "at1_days",
        "at2_days",
        "pv1_days",
        "pv2_days",
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


def test_server_weekdays_require_explicit_fixed_launch_time_structure():
    rules_text = CURRENT_FIXED_RULES.replace(
        "服务端的上线时间固定为每周二和周四",
        "服务端每周二和周四上线",
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert "server_launch_weekdays" in exc_info.value.missing_rules


def test_unrelated_1730_does_not_satisfy_server_launch_cutoff():
    rules_text = CURRENT_FIXED_RULES.replace(
        "，且下午5点30分后禁止上线", ""
    )
    rules_text += "每日例会时间为17:30\n"

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("server_launch_cutoff",)


def test_client_checklist_does_not_satisfy_server_checklist_rule():
    rules_text = CURRENT_FIXED_RULES.replace(
        "需在前一天提交对应的 checklist 上线表格，", ""
    )
    rules_text += "客户端需在前一天提交 checklist 上线表格\n"

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("checklist_days_before",)


def test_client_checklist_subclause_is_not_a_server_checklist_rule():
    rules_text = CURRENT_FIXED_RULES.replace(
        "需在前一天提交对应的 checklist 上线表格",
        "客户端需在前一天提交对应的 checklist 上线表格",
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == (
        "server_launch_cutoff",
        "checklist_days_before",
    )


def test_unneeded_server_checklist_is_rejected():
    rules_text = CURRENT_FIXED_RULES.replace(
        "需在前一天提交对应的 checklist 上线表格",
        "无需在前一天提交对应的 checklist 上线表格",
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("checklist_days_before",)


def test_negated_server_launch_weekdays_are_rejected():
    rules_text = CURRENT_FIXED_RULES.replace(
        "服务端的上线时间固定为每周二和周四",
        "服务端并非周二周四上线",
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("server_launch_weekdays",)


def test_negated_at_duration_is_rejected():
    rules_text = CURRENT_FIXED_RULES.replace(
        "AT 测试第一轮默认 4 个工作日", "AT 测试第一轮不需要 4 个工作日"
    ).replace(
        "AT 测试第二轮默认 4 个工作日", "AT 测试第二轮不需要 4 个工作日"
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("at1_days", "at2_days")


@pytest.mark.parametrize(
    ("source", "replacement", "missing_rule"),
    (
        (
            "AT 测试第一轮默认 4 个工作日",
            "AT 测试第一轮最多 4 个工作日",
            "at1_days",
        ),
        (
            "AT 测试第二轮默认 4 个工作日",
            "AT 测试第二轮一般在 4 个工作日以下",
            "at2_days",
        ),
    ),
)
def test_at_duration_requires_positive_structure(source, replacement, missing_rule):
    rules_text = CURRENT_FIXED_RULES.replace(source, replacement)

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == (missing_rule,)


def test_pv_total_days_do_not_count_as_pv_test_days():
    rules_text = CURRENT_FIXED_RULES.replace(
        "PV 测试第一轮默认 3 个工作日",
        "PV 测试总计大约 5 天",
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("pv1_days",)


def test_ambiguous_pv_test_durations_are_rejected():
    rules_text = CURRENT_FIXED_RULES.replace(
        "PV 测试第一轮默认 3 个工作日",
        "PV 测试第一轮默认 3 个工作日，PV 测试第一轮默认 5 个工作日",
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("pv1_days",)


def test_regression_days_require_explicit_positive_structure():
    rules_text = CURRENT_FIXED_RULES.replace(
        "线上回归一般在 2 天左右", "线上回归最多 2 天"
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("regression_days",)


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


def test_server_cutoff_does_not_inherit_across_semicolon():
    rules_text = CURRENT_FIXED_RULES.replace(
        "，且下午5点30分后禁止上线", "；且下午5点30分后禁止上线"
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("server_launch_cutoff",)


def test_client_cutoff_clause_does_not_inherit_server_context():
    rules_text = CURRENT_FIXED_RULES.replace(
        "且下午5点30分后禁止上线", "客户端下午5点30分后禁止上线"
    )

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == ("server_launch_cutoff",)


@pytest.mark.parametrize(
    ("rules_text", "missing_rules"),
    (
        (
            CURRENT_FIXED_RULES
            + "服务端上线时间固定为每周二和周四\n",
            ("server_launch_weekdays",),
        ),
        (
            CURRENT_FIXED_RULES + "服务端17:30后禁止上线\n",
            ("server_launch_cutoff",),
        ),
        (
            CURRENT_FIXED_RULES.replace(
                "，且下午5点30分后禁止上线",
                "，需要在前一天提交 checklist，且下午5点30分后禁止上线",
            ),
            ("checklist_days_before",),
        ),
    ),
)
def test_duplicate_service_rules_are_rejected(rules_text, missing_rules):
    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == missing_rules


@pytest.mark.parametrize(
    ("conflicting_rule", "missing_rules"),
    (
        (
            "服务端上线时间固定为每周一和周三\n",
            ("server_launch_weekdays",),
        ),
    ),
)
def test_conflicting_service_rules_are_rejected(
    conflicting_rule, missing_rules
):
    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(CURRENT_FIXED_RULES + conflicting_rule)

    assert exc_info.value.missing_rules == missing_rules


@pytest.mark.parametrize(
    ("source", "replacement", "missing_rule"),
    (
        (
            "PV 测试第一轮默认 3 个工作日",
            "PV 测试第一轮默认 3 个工作日以下",
            "pv1_days",
        ),
        (
            "PV 测试第二轮默认 2 个工作日",
            "PV 测试第二轮默认 2 个工作日以下",
            "pv2_days",
        ),
        (
            "线上回归一般在 2 天左右",
            "线上回归一般在 2 天左右以下",
            "regression_days",
        ),
    ),
)
def test_duration_rules_reject_reverse_clause_suffix(
    source, replacement, missing_rule
):
    rules_text = CURRENT_FIXED_RULES.replace(source, replacement)

    with pytest.raises(FixedRuleParseError) as exc_info:
        parse_fixed_rules(rules_text)

    assert exc_info.value.missing_rules == (missing_rule,)


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


def test_load_repository_fixed_rules_file():
    rules_path = Path(__file__).resolve().parents[1] / "固定业务规则"

    rules = load_fixed_rules(rules_path)

    assert rules.server_launch_weekdays == {1, 3}
    assert rules.server_launch_cutoff == "17:30"
    assert rules.checklist_days_before == 1
    assert (rules.at1_days, rules.at2_days) == (4, 4)
    assert (rules.pv1_days, rules.pv2_days) == (3, 2)
    assert rules.regression_days == 2
