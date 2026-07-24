import html
import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Dict, Iterable, List, Optional, Tuple

from .models import (
    Blocker,
    NodeRisk,
    NodeStatus,
    RequirementRisk,
    RiskLevel,
    RunReport,
    SkipScope,
    ValidationIssue,
)


_MAX_PAYLOAD_BYTES = 18 * 1024
_MAX_ELEMENTS = 40
_MAX_ELEMENT_BYTES = 1800
_MAX_TITLE_BYTES = 240
_MAX_VALUE_BYTES = 480
_MAX_MENTION_BYTES = 256
_MAX_MENTION_ID_BYTES = 96
_TRUNCATION_NOTICE = "内容过长，请查看多维表格"
_ATOMIC_ESCAPED_PATTERN = re.compile(
    r"(?:&(?:#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);|\\.)"
)
_BLOCK_MARKER_PATTERN = re.compile(
    r"(^|\s)(#{1,6}|-{3,}|={3,}|[#>+\-=])(?=\s|$)"
)
_LEVEL_TEMPLATES = {
    RiskLevel.NORMAL: "blue",
    RiskLevel.WARNING: "yellow",
    RiskLevel.SEVERE: "red",
}
_LEVEL_LABELS = {
    RiskLevel.NORMAL: "普通",
    RiskLevel.WARNING: "预警",
    RiskLevel.SEVERE: "严重",
}
_SCOPE_TEXT: Dict[SkipScope, Tuple[str, str]] = {
    "record": ("仅跳过当前记录", "否"),
    "requirement": ("跳过当前需求", "是"),
    "run": ("停止本次运行", "整次运行停止"),
}


@dataclass(frozen=True)
class _DailyRow:
    requirement_name: str
    node: NodeRisk


@dataclass
class _OwnerDelivery:
    rows: List[_DailyRow]
    later_count: int = 0


def mention(
    open_id: str,
    name: str,
    max_bytes: int = _MAX_MENTION_BYTES,
) -> str:
    escaped_id = html.escape(_normalize_text(open_id), quote=True)
    escaped_id = _truncate_escaped_text(escaped_id, _MAX_MENTION_ID_BYTES)
    prefix = '<at id="{}">'.format(escaped_id)
    suffix = "</at>"
    name_budget = max(
        1,
        max_bytes
        - len(prefix.encode("utf-8"))
        - len(suffix.encode("utf-8")),
    )
    escaped_name = escape_value(name, max_bytes=name_budget)
    return "{}{}{}".format(prefix, escaped_name, suffix)


def escape_value(value: object, max_bytes: int = _MAX_VALUE_BYTES) -> str:
    normalized = _normalize_text(value)
    escaped = html.escape(normalized, quote=False)
    escaped = escaped.replace("\\", "\\\\")
    for character in ("`", "*", "_", "~", "[", "]", "(", ")", "|", "{", "}", "!"):
        escaped = escaped.replace(character, "\\{}".format(character))
    escaped = _BLOCK_MARKER_PATTERN.sub(
        lambda match: "{}\\{}".format(match.group(1), match.group(2)),
        escaped,
    )
    return _truncate_escaped_text(escaped, max_bytes)


def interactive_card(
    title: str, template: str, markdown_blocks: List[str]
) -> Dict[str, object]:
    safe_title = _truncate_utf8(_normalize_text(title), _MAX_TITLE_BYTES) or "通知"
    safe_template = template if template in {"blue", "yellow", "red"} else "blue"
    source_lines: List[str] = []
    discarded = False
    for block in markdown_blocks or []:
        lines = _trusted_business_lines(_plain(block))
        for line in lines:
            if not line.strip():
                continue
            if len(line.encode("utf-8")) > _MAX_ELEMENT_BYTES:
                discarded = True
                continue
            source_lines.append(line)

    if not source_lines:
        source_lines.append("暂无内容")

    full_elements = [_markdown_element(line) for line in source_lines]
    full_payload = _interactive_payload(safe_title, safe_template, full_elements)
    if (
        not discarded
        and len(full_elements) <= _MAX_ELEMENTS
        and _payload_bytes(full_payload) <= _MAX_PAYLOAD_BYTES
    ):
        return full_payload

    notice_element = _markdown_element(_TRUNCATION_NOTICE)
    kept_elements: List[Dict[str, object]] = []
    for element in full_elements:
        if len(kept_elements) >= _MAX_ELEMENTS - 1:
            break
        candidate = _interactive_payload(
            safe_title,
            safe_template,
            kept_elements + [element, notice_element],
        )
        if _payload_bytes(candidate) > _MAX_PAYLOAD_BYTES:
            break
        kept_elements.append(element)

    payload = _interactive_payload(
        safe_title,
        safe_template,
        kept_elements + [notice_element],
    )
    if _payload_bytes(payload) > _MAX_PAYLOAD_BYTES:
        payload = _interactive_payload("内容已截断", safe_template, [notice_element])
    return payload


def _interactive_payload(
    title: str,
    template: str,
    elements: List[Dict[str, object]],
) -> Dict[str, object]:
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        },
    }


def _markdown_element(content: str) -> Dict[str, object]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _payload_bytes(payload: Dict[str, object]) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _trusted_business_lines(block: str) -> List[str]:
    normalized = block.replace("\r\n", "\n").replace("\r", "\n")
    return [normalized.replace("\t", " ")]


def build_daily_card(report: RunReport) -> Dict[str, object]:
    risks = report.requirement_risks
    levels = [_effective_level(risk) for risk in risks]
    highest_level = max(levels, default=RiskLevel.NORMAL)
    title = (
        "{} 需求进展日报｜需求 {}｜普通 {}｜预警 {}｜严重 {}".format(
            report.started_at.date().isoformat(),
            len(risks),
            levels.count(RiskLevel.NORMAL),
            levels.count(RiskLevel.WARNING),
            levels.count(RiskLevel.SEVERE),
        )
    )

    owner_groups, secondary, summaries = _daily_content_units(
        risks, report.started_at.date()
    )
    remaining_units = secondary
    if report.llm_attempted and report.llm_degraded:
        remaining_units = remaining_units + [
            "---",
            "AI 补充分析不可用，基础规则正常运行",
        ]
    remaining_units = remaining_units + summaries
    if not owner_groups and not remaining_units:
        remaining_units = ["暂无符合提醒条件的未完成节点。"]
    return _daily_interactive_card(
        title,
        _LEVEL_TEMPLATES[highest_level],
        owner_groups,
        remaining_units,
    )


def _daily_interactive_card(
    title: str,
    template: str,
    owner_groups: List[str],
    remaining_units: List[str],
) -> Dict[str, object]:
    if not owner_groups:
        return interactive_card(title, template, remaining_units)

    safe_title = _truncate_utf8(_normalize_text(title), _MAX_TITLE_BYTES) or "通知"
    safe_template = template if template in {"blue", "yellow", "red"} else "blue"
    owner_blocks = _pack_owner_groups(owner_groups)
    owner_elements = [_markdown_element(block) for block in owner_blocks]
    owner_payload = _interactive_payload(safe_title, safe_template, owner_elements)
    if (
        len(owner_elements) > _MAX_ELEMENTS
        or _payload_bytes(owner_payload) > _MAX_PAYLOAD_BYTES
    ):
        return _truncated_owner_payload(
            safe_title,
            safe_template,
            owner_groups,
        )

    optional_elements, discarded = _optional_markdown_elements(remaining_units)
    full_elements = owner_elements + optional_elements
    full_payload = _interactive_payload(safe_title, safe_template, full_elements)
    if (
        not discarded
        and len(full_elements) <= _MAX_ELEMENTS
        and _payload_bytes(full_payload) <= _MAX_PAYLOAD_BYTES
    ):
        return full_payload

    notice_element = _markdown_element(_TRUNCATION_NOTICE)
    kept_elements = list(owner_elements)
    for element in optional_elements:
        if len(kept_elements) >= _MAX_ELEMENTS - 1:
            break
        candidate = _interactive_payload(
            safe_title,
            safe_template,
            kept_elements + [element, notice_element],
        )
        if _payload_bytes(candidate) > _MAX_PAYLOAD_BYTES:
            break
        kept_elements.append(element)

    candidate = _interactive_payload(
        safe_title,
        safe_template,
        kept_elements + [notice_element],
    )
    if (
        len(kept_elements) < _MAX_ELEMENTS
        and _payload_bytes(candidate) <= _MAX_PAYLOAD_BYTES
    ):
        return candidate
    return _interactive_payload(safe_title, safe_template, kept_elements)


def _pack_owner_groups(owner_groups: List[str]) -> List[str]:
    blocks: List[str] = []
    current: List[str] = []
    for group in owner_groups:
        candidate = "\n\n".join(current + [group])
        if current and len(candidate.encode("utf-8")) > _MAX_ELEMENT_BYTES:
            blocks.append("\n\n".join(current))
            current = [group]
        else:
            current.append(group)
    if current:
        blocks.append("\n\n".join(current))
    return blocks


def _truncated_owner_payload(
    title: str,
    template: str,
    owner_groups: List[str],
) -> Dict[str, object]:
    total = len(owner_groups)
    best_count = 0
    best_elements: List[Dict[str, object]] = []
    best_notice = _owner_count_notice(0, total)
    for count in range(1, total + 1):
        blocks = _pack_owner_groups(owner_groups[:count])
        notice = _owner_count_notice(count, total)
        elements = [_markdown_element(block) for block in blocks]
        elements.append(_markdown_element(notice))
        candidate = _interactive_payload(title, template, elements)
        if (
            len(elements) > _MAX_ELEMENTS
            or _payload_bytes(candidate) > _MAX_PAYLOAD_BYTES
        ):
            break
        best_count = count
        best_elements = elements[:-1]
        best_notice = notice
    return _interactive_payload(
        title,
        template,
        best_elements + [_markdown_element(best_notice)],
    )


def _owner_count_notice(shown: int, total: int) -> str:
    return "负责人信息已展示 {}/{}，剩余 {} 位请查看多维表格".format(
        shown,
        total,
        total - shown,
    )


def _optional_markdown_elements(
    units: List[str],
) -> Tuple[List[Dict[str, object]], bool]:
    elements: List[Dict[str, object]] = []
    discarded = False
    for unit in units:
        for block in _trusted_business_lines(_plain(unit)):
            if not block.strip():
                continue
            if len(block.encode("utf-8")) > _MAX_ELEMENT_BYTES:
                discarded = True
                continue
            elements.append(_markdown_element(block))
    return elements, discarded


def build_severe_card(risk: RequirementRisk) -> Dict[str, object]:
    blockers = [blocker for blocker in risk.blockers if not _blocker_done(blocker)]
    delayed_days = _delay_days(risk.predicted_completion, risk.merge_at)
    actions = risk.actions
    if (
        risk.llm_enrichment is not None
        and risk.llm_enrichment.available
        and risk.llm_enrichment.actions
    ):
        actions = risk.llm_enrichment.actions

    units = [
        "{} **请立即处理严重交付风险**".format(
            mention(risk.project_owner_id, risk.project_owner_name)
        ),
        "**需求：{}（{}）**".format(
            escape_value(risk.requirement_name, 600),
            escape_value(risk.requirement_id, 160),
        ),
        "项目：{}".format(escape_value(risk.project, 240)),
        "版本：{}".format(escape_value(risk.target_version, 160)),
        "统一合板：{}".format(_format_datetime(risk.merge_at)),
        "当前预计完成：{}".format(_format_datetime(risk.predicted_completion)),
        "预计延期：{} 天".format(delayed_days),
        "受影响交付域：{}".format(_join_values(risk.affected_domains)),
        "判定原因：{}".format(_join_values(risk.reasons)),
        "阻塞：{}".format(_format_blockers(blockers, mention_owners=True)),
        "行动：{}".format(_join_values(actions)),
        "最晚处理时间：{}".format(_format_datetime(_latest_action_time(risk))),
        "节点负责人：{}".format(_node_owners(risk.node_risks)),
        "项目负责人：{}".format(
            mention(risk.project_owner_id, risk.project_owner_name)
        ),
    ]
    if risk.llm_enrichment is not None and not risk.llm_enrichment.available:
        units.extend(["---", "AI 补充分析不可用，基础规则正常运行"])
    return interactive_card(
        "严重风险｜{}｜{}".format(risk.project, risk.requirement_name),
        "red",
        units,
    )


def build_data_error_card(
    issues: list[ValidationIssue],
) -> Dict[str, object]:
    units: List[str] = []
    for index, issue in enumerate(issues, start=1):
        scope_text, skip_text = _SCOPE_TEXT[issue.skip_scope]
        units.extend(
            [
                "**异常 {}**".format(index),
                "表名：{}".format(escape_value(issue.table_name, 240)),
                "需求：{}".format(
                    escape_value(issue.requirement_id or "未关联需求", 240)
                ),
                "记录标识：{}".format(
                    escape_value(issue.record_id or "未提供", 240)
                ),
                "错误字段：{}".format(escape_value(issue.field_name, 240)),
                "当前错误值：{}".format(
                    escape_value(_issue_value(issue.current_value), 1000)
                ),
                "预期格式：{}".format(
                    escape_value(issue.expected_format or "未提供", 600)
                ),
                "修复建议：{}".format(
                    escape_value(issue.fix_suggestion or "未提供", 1000)
                ),
                "隔离范围：{}".format(scope_text),
                "是否跳过该需求：{}".format(skip_text),
                "错误说明：{}".format(escape_value(issue.message, 1000)),
            ]
        )
    if not units:
        units.append("未提供具体数据异常记录。")
    return interactive_card(
        "数据异常｜{} 条记录需修复".format(len(issues)),
        "red",
        units,
    )


def build_plain_text_fallback(
    title: str, lines: List[str]
) -> Dict[str, object]:
    content = [_plain(title)]
    content.extend(_plain(line) for line in (lines or []))
    return {
        "msg_type": "text",
        "content": {"text": "\n".join(content)},
    }


def _daily_content_units(
    risks: List[RequirementRisk], report_day: date
) -> Tuple[List[str], List[str], List[str]]:
    projects: "OrderedDict[str, OrderedDict[Tuple[str, str], _OwnerDelivery]]" = (
        OrderedDict()
    )
    project_risks: "OrderedDict[str, List[RequirementRisk]]" = OrderedDict()
    for risk in risks:
        project_risks.setdefault(risk.project, []).append(risk)
        owners = projects.setdefault(risk.project, OrderedDict())
        for node in risk.node_risks:
            if _node_done(node):
                continue
            owner = (node.owner_id, node.owner_name)
            delivery = owners.setdefault(owner, _OwnerDelivery(rows=[]))
            if (node.planned_end.date() - report_day).days <= 7:
                delivery.rows.append(_DailyRow(risk.requirement_name, node))
            else:
                delivery.later_count += 1

    primary: List[str] = []
    secondary: List[str] = []
    summaries: List[str] = []
    for project, owners in projects.items():
        first_owner = True
        for (_, owner_name), delivery in owners.items():
            rows = sorted(
                delivery.rows,
                key=lambda item: _daily_sort_key(item, report_day),
            )
            owner_lines = []
            if first_owner:
                owner_lines.extend(
                    [
                        "## {}".format(escape_value(project, 160)),
                        (
                            "@负责人｜需求｜交付域｜节点｜"
                            "计划 DDL｜最晚安全 DDL｜状态"
                        ),
                    ]
                )
                first_owner = False
            owner_lines.append("### {}".format(escape_value(owner_name, 80)))
            if rows:
                owner_lines.append(_render_daily_row(rows[0], compact=True))
                secondary.extend(_render_daily_row(row) for row in rows[1:])
            else:
                owner_lines.append("- 未来 7 天无未完成节点")
            if delivery.later_count:
                later_line = "- 7 天后待办：{} 项".format(delivery.later_count)
                if rows:
                    secondary.append(later_line)
                else:
                    owner_lines.append(later_line)
            primary.append("\n".join(owner_lines))

        summaries.extend(
            _requirement_summary_unit(risk)
            for risk in project_risks.get(project, [])
        )

    return primary, secondary, summaries


def _render_daily_row(row: _DailyRow, compact: bool = False) -> str:
    if compact:
        owner_budget = 112
        requirement_budget = 72
        domain_budget = 36
        node_budget = 72
        status_budget = 36
    else:
        owner_budget = _MAX_MENTION_BYTES
        requirement_budget = 300
        domain_budget = 120
        node_budget = 300
        status_budget = 80
    rendered_owner = (
        mention(row.node.owner_id, row.node.owner_name, owner_budget)
        if row.node.level <= RiskLevel.WARNING
        else escape_value(row.node.owner_name, owner_budget)
    )
    return "- {}｜{}｜{}｜{}｜{}｜{}｜{}（{}）".format(
        rendered_owner,
        escape_value(row.requirement_name, requirement_budget),
        escape_value(row.node.domain, domain_budget),
        escape_value(row.node.node_name, node_budget),
        _format_datetime(row.node.planned_end),
        _format_datetime(row.node.safe_deadline),
        escape_value(row.node.status.value, status_budget),
        _LEVEL_LABELS[row.node.level],
    )


def _requirement_summary_unit(risk: RequirementRisk) -> str:
    blockers = [blocker for blocker in risk.blockers if not _blocker_done(blocker)]
    return (
        "**需求摘要｜{}**｜项目：{}｜版本：{}｜合板：{}｜上线：{}｜"
        "缓冲：{} 天｜阻塞：{}"
    ).format(
        escape_value(risk.requirement_name, 240),
        escape_value(risk.project, 120),
        escape_value(risk.target_version, 120),
        _format_datetime(risk.merge_at),
        _format_datetime(risk.launch_at),
        _format_number(risk.buffer_days),
        _format_blockers(blockers),
    )


def _effective_level(risk: RequirementRisk) -> RiskLevel:
    if risk.llm_enrichment is None or not risk.llm_enrichment.available:
        return risk.level
    return max(risk.level, risk.llm_enrichment.effective_level)


def _daily_sort_key(row: _DailyRow, report_day: date) -> Tuple[int, str, str, str]:
    planned_day = row.node.planned_end.date()
    if planned_day < report_day:
        bucket = 0
    elif planned_day == report_day:
        bucket = 1
    elif row.node.level >= RiskLevel.WARNING:
        bucket = 2
    else:
        bucket = 3
    return (
        bucket,
        row.node.planned_end.isoformat(),
        row.requirement_name,
        row.node.node_name,
    )


def _latest_action_time(risk: RequirementRisk) -> Optional[datetime]:
    candidates = [
        node.safe_deadline
        for node in risk.node_risks
        if node.safe_deadline is not None and not _node_done(node)
    ]
    candidates.extend(
        blocker.planned_resolution_at
        for blocker in risk.blockers
        if not _blocker_done(blocker)
    )
    return min(candidates) if candidates else None


def _node_owners(nodes: List[NodeRisk]) -> str:
    owners: "OrderedDict[Tuple[str, str], None]" = OrderedDict()
    for node in nodes:
        owners[(node.owner_id, node.owner_name)] = None
    if not owners:
        return "未提供"
    return _bounded_join(
        [mention(owner_id, name) for owner_id, name in owners],
        "、",
        1200,
    )


def _format_blockers(
    blockers: Iterable[Blocker], mention_owners: bool = False
) -> str:
    rendered = []
    for blocker in blockers:
        owner = (
            mention(blocker.owner_id, blocker.owner_name)
            if mention_owners
            else escape_value(blocker.owner_name, 180)
        )
        rendered.append(
            "{}（{}｜{}｜{}）".format(
                escape_value(blocker.title, 320),
                owner,
                escape_value(blocker.status, 80),
                _format_datetime(blocker.planned_resolution_at),
            )
        )
    return _bounded_join(rendered, "；", 1200) if rendered else "无"


def _delay_days(predicted: Optional[datetime], merge_at: datetime) -> int:
    if predicted is None or predicted <= merge_at:
        return 0
    return int(math.ceil((predicted - merge_at).total_seconds() / 86400))


def _node_done(node: NodeRisk) -> bool:
    return node.status in {
        NodeStatus.COMPLETED,
        NodeStatus.SKIPPED,
        NodeStatus.CANCELLED,
    }


def _blocker_done(blocker: Blocker) -> bool:
    return blocker.status.strip().lower() in {
        "已完成",
        "已解决",
        "已关闭",
        "completed",
        "resolved",
        "closed",
    }


def _format_datetime(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else "—"


def _format_number(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value.is_integer():
        return str(int(value))
    return "{:.2f}".format(value).rstrip("0").rstrip(".")


def _join_values(values: Iterable[str]) -> str:
    rendered = [escape_value(value, 320) for value in values if value]
    return _bounded_join(rendered, "、", 1200) if rendered else "未提供"


def _issue_value(value: Optional[str]) -> str:
    if value is None:
        return "未提供"
    if value == "":
        return "空字符串"
    return value


def _plain(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _normalize_text(value: object) -> str:
    text = _plain(value)
    text = re.sub(r"[\r\n\t]+", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    ellipsis = "…"
    budget = max(0, max_bytes - len(ellipsis.encode("utf-8")))
    truncated = encoded[:budget].decode("utf-8", errors="ignore")
    return truncated + ellipsis


def _truncate_escaped_text(value: str, max_bytes: int) -> str:
    if len(value.encode("utf-8")) <= max_bytes:
        return value
    ellipsis = "…"
    budget = max(0, max_bytes - len(ellipsis.encode("utf-8")))
    output: List[str] = []
    used = 0
    position = 0
    while position < len(value):
        match = _ATOMIC_ESCAPED_PATTERN.match(value, position)
        token = match.group(0) if match is not None else value[position]
        token_bytes = len(token.encode("utf-8"))
        if used + token_bytes > budget:
            break
        output.append(token)
        used += token_bytes
        position += len(token)
    return "".join(output) + ellipsis


def _bounded_join(
    values: Iterable[str], separator: str, max_bytes: int
) -> str:
    selected: List[str] = []
    for value in values:
        candidate = separator.join(selected + [value])
        if len(candidate.encode("utf-8")) > max_bytes:
            if selected:
                overflow = separator.join(selected + ["…"])
                if len(overflow.encode("utf-8")) <= max_bytes:
                    return overflow
            return "…"
        selected.append(value)
    return separator.join(selected)
