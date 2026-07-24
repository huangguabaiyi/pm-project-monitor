import html
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


_MAX_MARKDOWN_BLOCK_LENGTH = 3000
_ATOMIC_MARKUP_PATTERN = re.compile(
    r'(?:<at id="[^"]*">.*?</at>'
    r"|&(?:#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);"
    r"|\\.)"
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


def mention(open_id: str, name: str) -> str:
    return '<at id="{}">{}</at>'.format(
        html.escape(_plain(open_id), quote=True),
        html.escape(_plain(name), quote=False),
    )


def interactive_card(
    title: str, template: str, markdown_blocks: List[str]
) -> Dict[str, object]:
    elements = []
    for block in markdown_blocks or []:
        for chunk in _chunk_markdown_unit(_plain(block)):
            elements.append(
                {"tag": "div", "text": {"tag": "lark_md", "content": chunk}}
            )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": _plain(template) or "blue",
                "title": {"tag": "plain_text", "content": _plain(title)},
            },
            "elements": elements,
        },
    }


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

    project_risks: "OrderedDict[str, List[RequirementRisk]]" = OrderedDict()
    for risk in risks:
        project_risks.setdefault(risk.project, []).append(risk)

    units: List[str] = []
    for project, project_items in project_risks.items():
        units.extend(
            _daily_project_units(project, project_items, report.started_at.date())
        )
    if not units:
        units.append("暂无符合提醒条件的未完成节点。")
    if report.llm_attempted and report.llm_degraded:
        units.extend(["---", "AI 补充分析不可用，基础规则正常运行"])
    return interactive_card(title, _LEVEL_TEMPLATES[highest_level], units)


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
            _md(risk.requirement_name), _md(risk.requirement_id)
        ),
        "项目：{}".format(_md(risk.project)),
        "版本：{}".format(_md(risk.target_version)),
        "统一合板：{}".format(_format_datetime(risk.merge_at)),
        "当前预计完成：{}".format(_format_datetime(risk.predicted_completion)),
        "预计延期：{} 天".format(delayed_days),
        "受影响交付域：{}".format(_join_md(risk.affected_domains)),
        "判定原因：{}".format(_join_md(risk.reasons)),
        "阻塞：{}".format(_format_blockers(blockers, mention_owners=True)),
        "行动：{}".format(_join_md(actions)),
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
                "表名：{}".format(_md(issue.table_name)),
                "需求：{}".format(_md(issue.requirement_id or "未关联需求")),
                "记录标识：{}".format(_md(issue.record_id or "未提供")),
                "错误字段：{}".format(_md(issue.field_name)),
                "当前错误值：{}".format(_md(_issue_value(issue.current_value))),
                "预期格式：{}".format(_md(issue.expected_format or "未提供")),
                "修复建议：{}".format(_md(issue.fix_suggestion or "未提供")),
                "隔离范围：{}".format(scope_text),
                "是否跳过该需求：{}".format(skip_text),
                "错误说明：{}".format(_md(issue.message)),
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


def _daily_project_units(
    project: str, risks: List[RequirementRisk], report_day: date
) -> List[str]:
    units = ["## {}".format(_md(project))]
    for risk in risks:
        units.extend(_requirement_summary_units(risk))

    owners: "OrderedDict[Tuple[str, str], List[_DailyRow]]" = OrderedDict()
    later_counts: "OrderedDict[Tuple[str, str], int]" = OrderedDict()
    for risk in risks:
        for node in risk.node_risks:
            if _node_done(node):
                continue
            owner = (node.owner_id, node.owner_name)
            owners.setdefault(owner, [])
            later_counts.setdefault(owner, 0)
            if (node.planned_end.date() - report_day).days <= 7:
                owners[owner].append(_DailyRow(risk.requirement_name, node))
            else:
                later_counts[owner] += 1

    for owner, rows in owners.items():
        owner_id, owner_name = owner
        units.append("### {}".format(_md(owner_name)))
        units.append(
            "@负责人｜需求｜交付域｜节点｜"
            "计划 DDL｜最晚安全 DDL｜状态"
        )
        for row in sorted(rows, key=lambda item: _daily_sort_key(item, report_day)):
            rendered_owner = (
                mention(owner_id, owner_name)
                if row.node.level <= RiskLevel.WARNING
                else _md(owner_name)
            )
            units.append(
                "- {}｜{}｜{}｜{}｜{}｜{}｜{}（{}）".format(
                    rendered_owner,
                    _md(row.requirement_name),
                    _md(row.node.domain),
                    _md(row.node.node_name),
                    _format_datetime(row.node.planned_end),
                    _format_datetime(row.node.safe_deadline),
                    _md(row.node.status.value),
                    _LEVEL_LABELS[row.node.level],
                )
            )
        if not rows:
            units.append("- 未来 7 天无未完成节点")
        if later_counts[owner]:
            units.append("- 7 天后待办：{} 项".format(later_counts[owner]))
    return units


def _requirement_summary_units(risk: RequirementRisk) -> List[str]:
    blockers = [blocker for blocker in risk.blockers if not _blocker_done(blocker)]
    return [
        "**需求概览｜{}**".format(_md(risk.requirement_name)),
        (
            "目标版本：{}｜合板：{}｜上线：{}｜"
            "缓冲：{} 天｜阻塞：{}"
        ).format(
            _md(risk.target_version),
            _format_datetime(risk.merge_at),
            _format_datetime(risk.launch_at),
            _format_number(risk.buffer_days),
            _format_blockers(blockers),
        ),
    ]


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
    return "、".join(mention(owner_id, name) for owner_id, name in owners)


def _format_blockers(
    blockers: Iterable[Blocker], mention_owners: bool = False
) -> str:
    rendered = []
    for blocker in blockers:
        owner = (
            mention(blocker.owner_id, blocker.owner_name)
            if mention_owners
            else _md(blocker.owner_name)
        )
        rendered.append(
            "{}（{}｜{}｜{}）".format(
                _md(blocker.title),
                owner,
                _md(blocker.status),
                _format_datetime(blocker.planned_resolution_at),
            )
        )
    return "；".join(rendered) if rendered else "无"


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


def _join_md(values: Iterable[str]) -> str:
    rendered = [_md(value) for value in values if value]
    return "、".join(rendered) if rendered else "未提供"


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


def _md(value: object) -> str:
    escaped = html.escape(_plain(value), quote=False)
    escaped = escaped.replace("\\", "\\\\")
    for character in ("`", "*", "_", "~", "[", "]"):
        escaped = escaped.replace(character, "\\{}".format(character))
    return escaped


def _chunk_markdown_unit(content: str) -> List[str]:
    lines = content.splitlines(keepends=True) or [""]
    chunks: List[str] = []
    current = ""
    for line in lines:
        if len(line) > _MAX_MARKDOWN_BLOCK_LENGTH:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_line(line))
            continue
        if current and len(current) + len(line) > _MAX_MARKDOWN_BLOCK_LENGTH:
            chunks.append(current)
            current = ""
        current += line
    if current or not chunks:
        chunks.append(current)
    return chunks


def _split_long_line(line: str) -> List[str]:
    tokens: List[Tuple[bool, str]] = []
    position = 0
    for match in _ATOMIC_MARKUP_PATTERN.finditer(line):
        if match.start() > position:
            tokens.append((False, line[position : match.start()]))
        tokens.append((True, match.group(0)))
        position = match.end()
    if position < len(line):
        tokens.append((False, line[position:]))

    chunks: List[str] = []
    current = ""
    for atomic, token in tokens:
        if atomic:
            if current and len(current) + len(token) > _MAX_MARKDOWN_BLOCK_LENGTH:
                chunks.append(current)
                current = ""
            current += token
            continue
        remaining = token
        while remaining:
            room = _MAX_MARKDOWN_BLOCK_LENGTH - len(current)
            if room == 0:
                chunks.append(current)
                current = ""
                room = _MAX_MARKDOWN_BLOCK_LENGTH
            current += remaining[:room]
            remaining = remaining[room:]
            if len(current) == _MAX_MARKDOWN_BLOCK_LENGTH:
                chunks.append(current)
                current = ""
    if current:
        chunks.append(current)
    return chunks
