import html
import math
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import RiskLevel, ValidationIssue


_MAX_MARKDOWN_BLOCK_LENGTH = 3000
_DONE_STATUSES = {"已完成", "已跳过", "已取消", "completed", "skipped", "cancelled"}
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
        for chunk in _chunk_markdown(_plain(block)):
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


def build_daily_card(report: Any) -> Dict[str, object]:
    entries = [_normalize_entry(item) for item in _report_entries(report)]
    entries = [entry for entry in entries if entry is not None]
    report_day = _report_date(report)
    highest_level = max(
        (_entry_level(entry) for entry in entries),
        default=_report_highest_level(report),
    )
    counts = _daily_counts(report, entries)
    title = (
        "{} 需求进展日报｜需求 {}｜普通 {}｜预警 {}｜严重 {}".format(
            report_day.isoformat(),
            counts["requirements"],
            counts["normal"],
            counts["warning"],
            counts["severe"],
        )
    )

    project_entries: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for entry in entries:
        project = _plain(
            _value(entry["requirement"], "project", default=None)
            or _value(entry["risk"], "project", default="未分组项目")
        )
        project_entries.setdefault(project, []).append(entry)

    blocks: List[str] = []
    for project, items in project_entries.items():
        blocks.append(_daily_project_block(project, items, report_day))
    if not blocks:
        blocks.append("暂无符合提醒条件的未完成节点。")
    if _llm_attempt_failed(report, entries):
        blocks.append("---\nAI 补充分析不可用，基础规则正常运行")

    return interactive_card(title, _LEVEL_TEMPLATES[highest_level], blocks)


def build_severe_card(risk: Any) -> Dict[str, object]:
    entry = _normalize_entry(risk)
    if entry is None:
        entry = _empty_entry(risk)
    requirement = entry["requirement"]
    requirement_risk = entry["risk"]
    nodes = entry["nodes"]
    blockers = [item for item in entry["blockers"] if not _is_done(item)]

    requirement_name = _plain(
        _value(requirement, "name", default=None)
        or _value(requirement_risk, "requirement_name", default="未命名需求")
    )
    requirement_id = _plain(
        _value(requirement, "requirement_id", default=None)
        or _value(requirement_risk, "requirement_id", default="")
    )
    project = _plain(
        _value(requirement, "project", default=None)
        or _value(requirement_risk, "project", default="未分组项目")
    )
    version = _plain(_value(requirement, "target_version", default="未提供"))
    merge_at = _value(requirement, "merge_at", default=None)
    predicted = _value(requirement_risk, "predicted_completion", default=None)
    project_owner_id = _plain(
        _value(requirement, "project_owner_id", default="unknown-project-owner")
    )
    project_owner_name = _plain(
        _value(requirement, "project_owner_name", default="项目负责人")
    )

    delayed_days = _value(risk, "delayed_days", "delay_days", default=None)
    if delayed_days is None:
        delayed_days = _delay_days(predicted, merge_at)
    affected_domains = _strings(
        _value(requirement_risk, "affected_domains", default=[])
    )
    reasons = _strings(_value(requirement_risk, "reasons", default=[]))
    actions = _severe_actions(entry)
    latest_time = _latest_action_time(entry)
    node_owners = _node_owners(nodes, requirement_risk)

    lines = [
        "{} **请立即处理严重交付风险**".format(
            mention(project_owner_id, project_owner_name)
        ),
        "**需求：{}{}**".format(
            _md(requirement_name),
            "（{}）".format(_md(requirement_id)) if requirement_id else "",
        ),
        "项目：{}".format(_md(project)),
        "版本：{}".format(_md(version)),
        "统一合板：{}".format(_format_datetime(merge_at)),
        "当前预计完成：{}".format(_format_datetime(predicted)),
        "预计延期：{} 天".format(_format_number(delayed_days)),
        "受影响交付域：{}".format(_join_md(affected_domains)),
        "判定原因：{}".format(_join_md(reasons)),
        "阻塞：{}".format(_format_blockers(blockers, mention_owners=True)),
        "行动：{}".format(_join_md(actions)),
        "最晚处理时间：{}".format(_format_datetime(latest_time)),
        "节点负责人：{}".format(node_owners or "未提供"),
        "项目负责人：{}".format(
            mention(project_owner_id, project_owner_name)
        ),
    ]
    if _llm_attempt_failed(risk, [entry]):
        lines.extend(["---", "AI 补充分析不可用，基础规则正常运行"])

    title = "严重风险｜{}｜{}".format(project, requirement_name)
    return interactive_card(title, "red", ["\n".join(lines)])


def build_data_error_card(
    issues: List[ValidationIssue],
) -> Dict[str, object]:
    blocks = []
    for index, issue in enumerate(issues or [], start=1):
        requirement_id = _value(issue, "requirement_id", default=None)
        record_id = _value(issue, "record_id", default=None)
        current_value = _value(
            issue,
            "current_value",
            "invalid_value",
            "value",
            default="未提供",
        )
        expected_format = _value(
            issue,
            "expected_format",
            "expected",
            default="请参考字段定义",
        )
        suggestion = _value(
            issue,
            "suggestion",
            "repair_suggestion",
            "fix",
            default="请按错误说明修复后重试",
        )
        skipped = _value(
            issue,
            "skip_requirement",
            "skipped_requirement",
            "skipped",
            default=requirement_id is not None,
        )
        lines = [
            "**异常 {}**".format(index),
            "表名：{}".format(_md(_value(issue, "table_name", default="未提供"))),
            "需求：{}".format(_md(requirement_id or "未关联需求")),
            "记录标识：{}".format(_md(record_id or "未提供")),
            "错误字段：{}".format(
                _md(_value(issue, "field_name", default="未提供"))
            ),
            "当前错误值：{}".format(_md(current_value)),
            "预期格式：{}".format(_md(expected_format)),
            "修复建议：{}".format(_md(suggestion)),
            "是否跳过该需求：{}".format("是" if bool(skipped) else "否"),
            "错误说明：{}".format(_md(_value(issue, "message", default="未提供"))),
        ]
        blocks.append("\n".join(lines))

    if not blocks:
        blocks.append("未提供具体数据异常记录。")
    return interactive_card(
        "数据异常｜{} 条记录需修复".format(len(issues or [])),
        "red",
        blocks,
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


def _daily_project_block(
    project: str, entries: List[Dict[str, Any]], report_day: date
) -> str:
    lines = ["## {}".format(_md(project))]
    for entry in entries:
        lines.extend(_requirement_summary(entry))

    owners: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()
    for entry in entries:
        requirement = entry["requirement"]
        requirement_risk = entry["risk"]
        requirement_name = _plain(
            _value(requirement, "name", default=None)
            or _value(requirement_risk, "requirement_name", default="未命名需求")
        )
        for delivery_node in entry["nodes"]:
            if _is_done(delivery_node):
                continue
            owner_id = _plain(_value(delivery_node, "owner_id", default="unknown-owner"))
            owner_name = _plain(
                _value(delivery_node, "owner_name", default="未指定负责人")
            )
            node_risk = _matching_node_risk(requirement_risk, delivery_node)
            planned_end = _value(delivery_node, "planned_end", default=None)
            node_level = _risk_level(
                _value(
                    node_risk,
                    "level",
                    default=_value(
                        delivery_node,
                        "risk_level",
                        default=RiskLevel.NORMAL,
                    ),
                )
            )
            row = {
                "owner_id": owner_id,
                "owner_name": owner_name,
                "requirement_name": requirement_name,
                "domain": _plain(_value(delivery_node, "domain", default="未提供")),
                "node_name": _plain(_value(delivery_node, "name", default="未命名节点")),
                "planned_end": planned_end,
                "safe_deadline": _value(
                    node_risk,
                    "safe_deadline",
                    default=_value(delivery_node, "safe_deadline", default=None),
                ),
                "status": _status_text(delivery_node),
                "level": node_level,
            }
            owner_group = owners.setdefault(
                (owner_id, owner_name), {"visible": [], "later": 0}
            )
            if _within_daily_window(planned_end, report_day):
                owner_group["visible"].append(row)
            else:
                owner_group["later"] += 1

    for (owner_id, owner_name), owner_group in owners.items():
        lines.append("### {}".format(_md(owner_name)))
        lines.append("@负责人｜需求｜交付域｜节点｜计划 DDL｜最晚安全 DDL｜状态")
        visible = sorted(
            owner_group["visible"],
            key=lambda item: _daily_sort_key(item, report_day),
        )
        for row in visible:
            owner = (
                mention(owner_id, owner_name)
                if row["level"] <= RiskLevel.WARNING
                else _md(owner_name)
            )
            lines.append(
                "- {}｜{}｜{}｜{}｜{}｜{}｜{}（{}）".format(
                    owner,
                    _md(row["requirement_name"]),
                    _md(row["domain"]),
                    _md(row["node_name"]),
                    _format_datetime(row["planned_end"]),
                    _format_datetime(row["safe_deadline"]),
                    _md(row["status"]),
                    _LEVEL_LABELS[row["level"]],
                )
            )
        if not visible:
            lines.append("- 未来 7 天无未完成节点")
        if owner_group["later"]:
            lines.append("- 7 天后待办：{} 项".format(owner_group["later"]))

    return "\n".join(lines)


def _requirement_summary(entry: Dict[str, Any]) -> List[str]:
    requirement = entry["requirement"]
    requirement_risk = entry["risk"]
    name = _plain(
        _value(requirement, "name", default=None)
        or _value(requirement_risk, "requirement_name", default="未命名需求")
    )
    version = _value(requirement, "target_version", default="未提供")
    merge_at = _value(requirement, "merge_at", default=None)
    launch_at = _value(requirement, "launch_at", default=None)
    buffer_days = _value(requirement_risk, "buffer_days", default=None)
    blockers = [item for item in entry["blockers"] if not _is_done(item)]
    return [
        "**需求概览｜{}**".format(_md(name)),
        "目标版本：{}｜合板：{}｜上线：{}｜缓冲：{} 天｜阻塞：{}".format(
            _md(version),
            _format_datetime(merge_at),
            _format_datetime(launch_at),
            _format_number(buffer_days),
            _format_blockers(blockers),
        ),
    ]


def _report_entries(report: Any) -> List[Any]:
    if isinstance(report, Sequence) and not isinstance(report, (str, bytes, bytearray)):
        return list(report)
    values = _value(
        report,
        "requirements",
        "items",
        "entries",
        "requirement_reports",
        "risks",
        default=[],
    )
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
        return list(values)
    return []


def _normalize_entry(item: Any) -> Optional[Dict[str, Any]]:
    if item is None:
        return None
    if isinstance(item, tuple):
        values = list(item) + [None] * 5
        return {
            "requirement": values[0],
            "risk": values[1],
            "nodes": _list(values[2]),
            "blockers": _list(values[3]),
            "enrichment": values[4],
        }

    requirement = _value(item, "requirement", default=None)
    requirement_risk = _value(item, "risk", "requirement_risk", default=None)
    if requirement is None and _value(item, "target_version", default=None) is not None:
        requirement = item
    if requirement_risk is None and _value(item, "level", default=None) is not None:
        requirement_risk = item
    if requirement is None and requirement_risk is None:
        return None
    nodes = _list(_value(item, "nodes", "delivery_nodes", default=[]))
    if not nodes:
        nodes = [
            _node_from_risk(node_risk)
            for node_risk in _list(
                _value(requirement_risk, "node_risks", default=[])
            )
        ]
    return {
        "requirement": requirement or {},
        "risk": requirement_risk or {},
        "nodes": nodes,
        "blockers": _list(_value(item, "blockers", default=[])),
        "enrichment": _value(item, "enrichment", "llm", "llm_enrichment", default=None),
    }


def _empty_entry(risk: Any) -> Dict[str, Any]:
    return {
        "requirement": {},
        "risk": risk or {},
        "nodes": [],
        "blockers": [],
        "enrichment": None,
    }


def _node_from_risk(node_risk: Any) -> Dict[str, Any]:
    return {
        "record_id": _value(node_risk, "node_record_id", default="unknown-node"),
        "requirement_id": _value(node_risk, "requirement_id", default="unknown"),
        "name": _value(node_risk, "node_name", default="未命名节点"),
        "domain": _value(node_risk, "domain", default="未提供"),
        "owner_id": _value(node_risk, "owner_id", default="unknown-owner"),
        "owner_name": _value(node_risk, "owner_name", default="未指定负责人"),
        "planned_end": _value(
            node_risk, "predicted_completion", "safe_deadline", default=None
        ),
        "safe_deadline": _value(node_risk, "safe_deadline", default=None),
        "status": "未提供",
        "risk_level": _value(node_risk, "level", default=RiskLevel.NORMAL),
    }


def _daily_counts(report: Any, entries: List[Dict[str, Any]]) -> Dict[str, int]:
    if entries:
        levels = [_entry_level(entry) for entry in entries]
        return {
            "requirements": len(entries),
            "normal": levels.count(RiskLevel.NORMAL),
            "warning": levels.count(RiskLevel.WARNING),
            "severe": levels.count(RiskLevel.SEVERE),
        }
    return {
        "requirements": _int_value(
            _value(
                report,
                "processed_requirements",
                "eligible_requirement_count",
                "total_requirements",
                default=0,
            )
        ),
        "normal": _int_value(_value(report, "normal_requirements", default=0)),
        "warning": _int_value(_value(report, "warning_requirements", default=0)),
        "severe": _int_value(_value(report, "severe_requirements", default=0)),
    }


def _report_highest_level(report: Any) -> RiskLevel:
    if _int_value(_value(report, "severe_requirements", default=0)):
        return RiskLevel.SEVERE
    if _int_value(_value(report, "warning_requirements", default=0)):
        return RiskLevel.WARNING
    return RiskLevel.NORMAL


def _entry_level(entry: Dict[str, Any]) -> RiskLevel:
    level = _risk_level(_value(entry["risk"], "level", default=RiskLevel.NORMAL))
    enrichment = entry["enrichment"]
    if enrichment is not None and bool(_value(enrichment, "available", default=False)):
        level = max(
            level,
            _risk_level(_value(enrichment, "effective_level", default=level)),
        )
    return level


def _llm_attempt_failed(report: Any, entries: List[Dict[str, Any]]) -> bool:
    enrichments = [entry["enrichment"] for entry in entries if entry["enrichment"] is not None]
    if any(not bool(_value(item, "available", default=False)) for item in enrichments):
        return True
    attempted = bool(_value(report, "llm_attempted", default=False))
    available = _value(report, "llm_available", default=None)
    if attempted and available is False:
        return True
    return bool(_value(report, "llm_degraded", default=False))


def _daily_sort_key(item: Dict[str, Any], report_day: date) -> Tuple[int, str, str, str]:
    planned = _as_datetime(item["planned_end"])
    planned_day = planned.date() if planned is not None else date.max
    if planned_day < report_day:
        bucket = 0
    elif planned_day == report_day:
        bucket = 1
    elif item["level"] >= RiskLevel.WARNING:
        bucket = 2
    else:
        bucket = 3
    return (
        bucket,
        planned.isoformat() if planned is not None else "9999-12-31T23:59:59",
        item["requirement_name"],
        item["node_name"],
    )


def _within_daily_window(value: Any, report_day: date) -> bool:
    planned = _as_datetime(value)
    if planned is None:
        return True
    return (planned.date() - report_day).days <= 7


def _matching_node_risk(requirement_risk: Any, delivery_node: Any) -> Any:
    record_id = _value(delivery_node, "record_id", default=None)
    node_name = _value(delivery_node, "name", default=None)
    domain = _value(delivery_node, "domain", default=None)
    for node_risk in _list(_value(requirement_risk, "node_risks", default=[])):
        if record_id is not None and _value(
            node_risk, "node_record_id", default=None
        ) == record_id:
            return node_risk
        if (
            node_name is not None
            and _value(node_risk, "node_name", default=None) == node_name
            and _value(node_risk, "domain", default=None) == domain
        ):
            return node_risk
    return {}


def _severe_actions(entry: Dict[str, Any]) -> List[str]:
    enrichment = entry["enrichment"]
    if enrichment is not None and bool(_value(enrichment, "available", default=False)):
        actions = _strings(_value(enrichment, "actions", default=[]))
        if actions:
            return actions
    return _strings(_value(entry["risk"], "actions", default=[]))


def _latest_action_time(entry: Dict[str, Any]) -> Any:
    candidates = []
    risk = entry["risk"]
    for node_risk in _list(_value(risk, "node_risks", default=[])):
        value = _as_datetime(_value(node_risk, "safe_deadline", default=None))
        if value is not None:
            candidates.append(value)
    for node in entry["nodes"]:
        value = _as_datetime(_value(node, "safe_deadline", default=None))
        if value is not None:
            candidates.append(value)
    for blocker in entry["blockers"]:
        if _is_done(blocker):
            continue
        value = _as_datetime(
            _value(blocker, "planned_resolution_at", default=None)
        )
        if value is not None:
            candidates.append(value)
    return min(candidates) if candidates else None


def _node_owners(nodes: List[Any], requirement_risk: Any) -> str:
    owners: "OrderedDict[Tuple[str, str], None]" = OrderedDict()
    for item in list(nodes) + _list(
        _value(requirement_risk, "node_risks", default=[])
    ):
        owner_id = _value(item, "owner_id", default=None)
        owner_name = _value(item, "owner_name", default=None)
        if owner_id and owner_name:
            owners[(_plain(owner_id), _plain(owner_name))] = None
    return "、".join(mention(owner_id, name) for owner_id, name in owners)


def _format_blockers(blockers: Iterable[Any], mention_owners: bool = False) -> str:
    rendered = []
    for blocker in blockers:
        title = _md(_value(blocker, "title", default="未命名阻塞"))
        status = _md(_value(blocker, "status", default="状态未提供"))
        owner_id = _value(blocker, "owner_id", default=None)
        owner_name = _value(blocker, "owner_name", default=None)
        owner = ""
        if owner_name:
            owner = (
                mention(_plain(owner_id or "unknown-blocker-owner"), _plain(owner_name))
                if mention_owners
                else _md(owner_name)
            )
        due_at = _value(blocker, "planned_resolution_at", default=None)
        details = [value for value in (owner, status, _format_datetime(due_at)) if value != "—"]
        rendered.append("{}{}".format(title, "（{}）".format("｜".join(details)) if details else ""))
    return "；".join(rendered) if rendered else "无"


def _delay_days(predicted: Any, merge_at: Any) -> int:
    predicted_at = _as_datetime(predicted)
    merge_time = _as_datetime(merge_at)
    if predicted_at is None or merge_time is None or predicted_at <= merge_time:
        return 0
    return int(math.ceil((predicted_at - merge_time).total_seconds() / 86400))


def _report_date(report: Any) -> date:
    value = _value(report, "report_date", "date", "started_at", "now", default=None)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return datetime.now().date()


def _risk_level(value: Any) -> RiskLevel:
    if isinstance(value, RiskLevel):
        return value
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "normal": RiskLevel.NORMAL,
            "普通": RiskLevel.NORMAL,
            "warning": RiskLevel.WARNING,
            "预警": RiskLevel.WARNING,
            "severe": RiskLevel.SEVERE,
            "严重": RiskLevel.SEVERE,
        }
        if normalized in aliases:
            return aliases[normalized]
    try:
        return RiskLevel(int(value))
    except (TypeError, ValueError):
        return RiskLevel.NORMAL


def _status_text(item: Any) -> str:
    value = _value(item, "status", default="未提供")
    if isinstance(value, Enum):
        value = value.value
    return _plain(value)


def _is_done(item: Any) -> bool:
    return _status_text(item).strip().lower() in _DONE_STATUSES


def _format_datetime(value: Any) -> str:
    parsed = _as_datetime(value)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d %H:%M")
    if value:
        return _md(value)
    return "—"


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _format_number(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _md(value)
    if number.is_integer():
        return str(int(number))
    return "{:.2f}".format(number).rstrip("0").rstrip(".")


def _join_md(values: Iterable[Any]) -> str:
    rendered = [_md(value) for value in values if _plain(value)]
    return "、".join(rendered) if rendered else "未提供"


def _strings(values: Any) -> List[str]:
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
        return [_plain(value) for value in values if _plain(value)]
    if values is None:
        return []
    return [_plain(values)]


def _value(source: Any, *names: str, default: Any = None) -> Any:
    if source is None:
        return default
    for name in names:
        if isinstance(source, Mapping):
            if name not in source:
                continue
            value = source[name]
        else:
            value = getattr(source, name, None)
        if value is not None:
            return value
    return default


def _list(value: Any) -> List[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        value = value.value
    return str(value)


def _md(value: Any) -> str:
    escaped = html.escape(_plain(value), quote=False)
    escaped = escaped.replace("\\", "\\\\")
    for character in ("`", "*", "_", "~", "[", "]"):
        escaped = escaped.replace(character, "\\{}".format(character))
    return escaped


def _chunk_markdown(content: str) -> List[str]:
    if len(content) <= _MAX_MARKDOWN_BLOCK_LENGTH:
        return [content]
    return [
        content[index : index + _MAX_MARKDOWN_BLOCK_LENGTH]
        for index in range(0, len(content), _MAX_MARKDOWN_BLOCK_LENGTH)
    ]
