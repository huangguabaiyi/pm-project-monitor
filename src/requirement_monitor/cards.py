import html
import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import risk_grouping
from .models import (
    Blocker,
    NodeRisk,
    NodeStatus,
    Person,
    RequirementRisk,
    RiskFinding,
    RiskGroup,
    RiskLevel,
    RunReport,
    SkipScope,
    ValidationIssue,
)
from .risk_grouping import group_risk_findings
from .schema import DEFAULT_PROCESS_NODES


_MAX_PAYLOAD_BYTES = 18 * 1024
_MAX_ELEMENTS = 40
_MAX_ELEMENT_BYTES = 1800
_MAX_TITLE_BYTES = 240
_MAX_SAFE_TITLE = "T" * _MAX_TITLE_BYTES
_MAX_VALUE_BYTES = 480
_MAX_MENTION_BYTES = 256
_MAX_MENTION_ID_BYTES = 96
_TRUNCATION_NOTICE = "内容过长，请查看多维表格"
_RISK_GROUP_OVERFLOW_NOTICE = "【风险组内容过长，已截断】"
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
_STAGE_ORDER = {
    stage: index for index, stage in enumerate(DEFAULT_PROCESS_NODES)
}
_UNCONFIRMED_SCHEDULE_RISK_CODES = {
    "schedule.buffer_low",
    "schedule.buffer_negative",
    "schedule.minimum_window_insufficient",
    "domain.completion_after_merge",
}


@dataclass(frozen=True)
class _DailyRow:
    requirement_name: str
    node: NodeRisk


@dataclass
class _OwnerDelivery:
    rows: List[_DailyRow]
    later_count: int = 0


@dataclass(frozen=True)
class _RiskFamilyView:
    code: str
    title: str
    level: RiskLevel
    stage_refs: Tuple[str, ...]
    domain_refs: Tuple[str, ...]
    source_findings: Tuple[RiskFinding, ...]


@dataclass(frozen=True)
class _RiskFamilyContext:
    stage_refs: Tuple[str, ...]
    domain_refs: Tuple[str, ...]
    unconfirmed_schedule_refs: Tuple[str, ...]
    unconfirmed_schedule_by_domain: Tuple[Tuple[str, Tuple[str, ...]], ...]
    confirmed_stage_refs: Tuple[str, ...]
    confirmed_domain_refs: Tuple[str, ...]
    owners: Tuple[Tuple[str, str], ...]
    earliest_safe_deadline: Optional[datetime]


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


def _build_daily_card_legacy(report: RunReport) -> Dict[str, object]:
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

    elements: List[Dict[str, object]] = []
    if report.llm_attempted and report.llm_degraded:
        elements.append(_markdown_element("**系统提示**：AI 补充分析不可用，基础规则正常运行"))
    for risk in risks:
        _append_requirement_elements(elements, risk)
    if not risks:
        elements.append(_markdown_element("暂无符合提醒条件的未完成节点。"))
    return _bounded_structured_payload(
        title,
        _LEVEL_TEMPLATES[highest_level],
        elements,
    )


def build_daily_card(report: RunReport) -> Dict[str, object]:
    payloads = build_daily_cards(report)
    if len(payloads) == 1:
        return payloads[0]
    first = payloads[0]
    card = first.get("card")
    if not isinstance(card, dict):
        return first
    elements = card.get("elements")
    if not isinstance(elements, list):
        return first
    notice = _markdown_element(_TRUNCATION_NOTICE)
    title = str(card.get("header", {}).get("title", {}).get("content", "通知"))
    template = str(card.get("header", {}).get("template", "blue"))
    candidate = _interactive_payload(title, template, elements + [notice])
    if len(elements) < _MAX_ELEMENTS and _payload_bytes(candidate) <= _MAX_PAYLOAD_BYTES:
        return candidate
    return _interactive_payload(title, template, elements[:-1] + [notice])


def build_daily_cards(report: RunReport) -> List[Dict[str, object]]:
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
    units: List[Tuple[Dict[str, object], bool]] = []
    if report.llm_attempted and report.llm_degraded:
        units.append(
            (_markdown_element("**系统提示**：AI 补充分析不可用，基础规则正常运行"), False)
        )
    for risk in risks:
        _append_grouped_requirement_units(units, risk)
    if not risks:
        units.append((_markdown_element("暂无符合提醒条件的未完成节点。"), False))
    return _split_grouped_units(title, _LEVEL_TEMPLATES[highest_level], units)


def _daily_interactive_card(
    title: str,
    template: str,
    owner_groups: List[str],
    required_system_units: List[str],
    secondary_units: List[str],
    summary_units: List[str],
) -> Dict[str, object]:
    if not owner_groups:
        return interactive_card(
            title,
            template,
            required_system_units + secondary_units + summary_units,
        )

    safe_title = _truncate_utf8(_normalize_text(title), _MAX_TITLE_BYTES) or "通知"
    safe_template = template if template in {"blue", "yellow", "red"} else "blue"
    owner_blocks = _pack_owner_groups(owner_groups)
    owner_elements = [_markdown_element(block) for block in owner_blocks]
    required_elements, _ = _optional_markdown_elements(required_system_units)
    required_payload = _interactive_payload(
        safe_title,
        safe_template,
        owner_elements + required_elements,
    )
    if (
        len(owner_elements) + len(required_elements) > _MAX_ELEMENTS
        or _payload_bytes(required_payload) > _MAX_PAYLOAD_BYTES
    ):
        return _truncated_owner_payload(
            safe_title,
            safe_template,
            owner_groups,
            required_system_units,
        )

    optional_elements, discarded = _optional_markdown_elements(
        secondary_units + summary_units
    )
    required_base = owner_elements + required_elements
    full_elements = required_base + optional_elements
    full_payload = _interactive_payload(safe_title, safe_template, full_elements)
    if (
        not discarded
        and len(full_elements) <= _MAX_ELEMENTS
        and _payload_bytes(full_payload) <= _MAX_PAYLOAD_BYTES
    ):
        return full_payload

    notice_element = _markdown_element(_TRUNCATION_NOTICE)
    kept_elements = list(required_base)
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


def _bounded_structured_payload(
    title: str,
    template: str,
    elements: List[Dict[str, object]],
) -> Dict[str, object]:
    safe_title = _truncate_utf8(_normalize_text(title), _MAX_TITLE_BYTES) or "通知"
    kept: List[Dict[str, object]] = []
    notice = _markdown_element(_TRUNCATION_NOTICE)
    for element in elements:
        if len(kept) >= _MAX_ELEMENTS - 1:
            break
        candidate = _interactive_payload(safe_title, template, kept + [element])
        if _payload_bytes(candidate) > _MAX_PAYLOAD_BYTES:
            break
        kept.append(element)
    if len(kept) < len(elements):
        candidate = _interactive_payload(safe_title, template, kept + [notice])
        if _payload_bytes(candidate) <= _MAX_PAYLOAD_BYTES:
            kept.append(notice)
    return _interactive_payload(safe_title, template, kept)


def _report_has_structured_findings(report: RunReport) -> bool:
    return any(_risk_has_structured_findings(risk) for risk in report.requirement_risks)


def _risk_has_structured_findings(risk: RequirementRisk) -> bool:
    return bool(risk.findings or any(node.findings for node in risk.node_risks))


def _risk_findings(risk: RequirementRisk) -> List[RiskFinding]:
    findings = list(risk.findings)
    for node in risk.node_risks:
        findings.extend(node.findings)
    known_texts = {finding.reason_text for finding in findings}
    for node in risk.node_risks:
        for reason in node.reasons:
            if reason in known_texts:
                continue
            findings.append(
                RiskFinding(
                    reason_code="legacy:{}".format(reason),
                    reason_text=reason,
                    stage_refs=[node.node_name],
                    domain_refs=[node.domain],
                    level=node.level,
                    source="legacy",
                )
            )
            known_texts.add(reason)
    for reason in risk.reasons:
        if reason in known_texts:
            continue
        findings.append(
            RiskFinding(
                reason_code="legacy:{}".format(reason),
                reason_text=reason,
                stage_refs=([] if risk.current_stage == "未提供" else [risk.current_stage]),
                domain_refs=list(risk.affected_domains),
                level=risk.level,
                source="legacy",
            )
        )
        known_texts.add(reason)
    return findings


def _risk_groups(risk: RequirementRisk) -> List[RiskGroup]:
    return group_risk_findings(
        _risk_findings(risk), risk.stage_order or _STAGE_ORDER
    )


def _risk_families(risk: RequirementRisk) -> List[object]:
    findings = _risk_findings(risk)
    stage_order = risk.stage_order or _STAGE_ORDER
    family_builder = getattr(risk_grouping, "group_risk_families", None)
    if callable(family_builder):
        return list(family_builder(findings, stage_order))
    return [
        _RiskFamilyView(
            code=group.reason_code,
            title=group.reason_text,
            level=group.level,
            stage_refs=tuple(group.stage_refs),
            domain_refs=tuple(group.domain_refs),
            source_findings=tuple(group.source_findings),
        )
        for group in group_risk_findings(findings, stage_order)
    ]


def _risk_group_element(
    index: int, group: RiskGroup, *, heading: bool = False
) -> Dict[str, object]:
    lines: List[str] = []
    if heading:
        lines.append("**风险原因**")
    lines.append(
        "**{}. {}**".format(index, _bounded_escaped_text(group.reason_text, 720))
    )
    schedule_by_domain = _schedule_refs_by_domain(
        group.source_findings,
        group.stage_refs,
    )
    if (
        group.reason_code in _UNCONFIRMED_SCHEDULE_RISK_CODES
        and schedule_by_domain
    ):
        lines.append("未确定排期")
        lines.extend(_schedule_domain_lines(schedule_by_domain, 620))
    else:
        stage_label = (
            "未确定排期"
            if group.stage_refs
            and group.reason_code in _UNCONFIRMED_SCHEDULE_RISK_CODES
            else "环节"
        )
        lines.append(
            "{}：{}".format(
                stage_label,
                _bounded_escaped_text("、".join(group.stage_refs), 460)
                if group.stage_refs
                else "未标注"
            )
        )
        lines.append(
            "交付域：{}".format(
                _bounded_escaped_text("、".join(group.domain_refs), 460)
                if group.domain_refs
                else "未标注"
            )
        )
    content = "\n".join(lines)
    if len(content.encode("utf-8")) > _MAX_ELEMENT_BYTES:
        content = "{}\n{}".format(
            _truncate_utf8(
                content,
                _MAX_ELEMENT_BYTES
                - len(_RISK_GROUP_OVERFLOW_NOTICE.encode("utf-8"))
                - 1,
            ),
            _RISK_GROUP_OVERFLOW_NOTICE,
        )
    return _markdown_element(content)


def _bounded_escaped_text(value: object, max_bytes: int) -> str:
    escaped = escape_value(value, max_bytes=100000)
    if len(escaped.encode("utf-8")) <= max_bytes:
        return escaped
    notice_bytes = len(_RISK_GROUP_OVERFLOW_NOTICE.encode("utf-8"))
    return "{}{}".format(
        _truncate_escaped_text(escaped, max(1, max_bytes - notice_bytes)),
        _RISK_GROUP_OVERFLOW_NOTICE,
    )


def _split_grouped_units(
    title: str,
    template: str,
    units: Sequence[Tuple[Dict[str, object], bool]],
) -> List[Dict[str, object]]:
    safe_title = _truncate_utf8(_normalize_text(title), _MAX_TITLE_BYTES) or "通知"
    safe_template = template if template in {"blue", "yellow", "red"} else "blue"
    if not any(is_atomic for _, is_atomic in units):
        return [_bounded_structured_payload(safe_title, safe_template, [element for element, _ in units])]

    elements = [element for element, _ in units]
    full_payload = _interactive_payload(safe_title, safe_template, elements)
    if (
        len(elements) <= _MAX_ELEMENTS
        and all(
            len(_element_text(element).encode("utf-8")) <= _MAX_ELEMENT_BYTES
            for element, _ in units
        )
        and _payload_bytes(full_payload) <= _MAX_PAYLOAD_BYTES
    ):
        return [full_payload]

    parts: List[List[Dict[str, object]]] = []
    current: List[Dict[str, object]] = []
    overflow_element = _markdown_element(_TRUNCATION_NOTICE)
    for element, is_atomic in units:
        candidate = _interactive_payload(safe_title, safe_template, current + [element])
        fits = (
            len(current) + 1 <= _MAX_ELEMENTS
            and len(_element_text(element).encode("utf-8")) <= _MAX_ELEMENT_BYTES
            and _payload_bytes(candidate) <= _MAX_PAYLOAD_BYTES
        )
        if fits:
            current.append(element)
            continue
        if current:
            parts.append(current)
        single_candidate = _interactive_payload(safe_title, safe_template, [element])
        single_fits = (
            len([element]) <= _MAX_ELEMENTS
            and (
                not _is_markdown_text_element(element)
                or len(_element_text(element).encode("utf-8"))
                <= _MAX_ELEMENT_BYTES
            )
            and _payload_bytes(single_candidate) <= _MAX_PAYLOAD_BYTES
        )
        if single_fits or (
            not is_atomic and not _is_markdown_text_element(element)
        ):
            current = [element]
        else:
            current = [overflow_element]
    if current:
        parts.append(current)

    total = len(parts)
    return [
        _interactive_payload(
            _continuation_title(safe_title, index, total) if total > 1 else safe_title,
            safe_template,
            part,
        )
        for index, part in enumerate(parts, start=1)
    ]


def _continuation_title(title: str, index: int, total: int) -> str:
    suffix = "｜第 {}/{} 部分".format(index, total)
    return _title_with_suffix(title, suffix)


def _title_with_suffix(prefix: str, suffix: str) -> str:
    safe_suffix = _truncate_utf8(suffix, _MAX_TITLE_BYTES)
    prefix_budget = max(
        0,
        _MAX_TITLE_BYTES - len(safe_suffix.encode("utf-8")),
    )
    return "{}{}".format(_truncate_utf8(prefix, prefix_budget), safe_suffix)


def _element_text(element: Dict[str, object]) -> str:
    text = element.get("text")
    if isinstance(text, dict) and isinstance(text.get("content"), str):
        return text["content"]
    return json.dumps(element, ensure_ascii=False, sort_keys=True)


def _is_markdown_text_element(element: Dict[str, object]) -> bool:
    text = element.get("text")
    return isinstance(text, dict) and isinstance(text.get("content"), str)


def _append_requirement_elements(
    elements: List[Dict[str, object]], risk: RequirementRisk
) -> None:
    elements.append(_markdown_element(_requirement_summary(risk)))
    link_element = _link_actions(risk)
    if link_element is not None:
        elements.append(link_element)
    rows = [node for node in risk.node_risks if _node_is_current(node, risk.current_stage)]
    if not rows and risk.current_stage == "未提供":
        rows = list(risk.node_risks)
    if rows:
        elements.append(_stage_header())
        elements.extend(_stage_row(node) for node in rows)
    if risk.reasons:
        elements.append(
            _markdown_element(
                "**风险/阻塞**：{}".format(_join_values(risk.reasons, max_bytes=900))
            )
        )
    reminder = _process_reminder_text(risk)
    if reminder:
        elements.append(_markdown_element(reminder))


def _append_grouped_requirement_units(
    units: List[Tuple[Dict[str, object], bool]], risk: RequirementRisk
) -> None:
    units.append((_markdown_element(_requirement_summary(risk)), True))
    units.extend((element, True) for element in _link_action_units(risk))
    rows = [node for node in risk.node_risks if _node_is_current(node, risk.current_stage)]
    if not rows and risk.current_stage == "未提供":
        rows = list(risk.node_risks)
    if rows:
        units.append((_stage_header(), True))
        for node in rows:
            units.extend((element, True) for element in _stage_row_units(node))
    groups = _risk_groups(risk)
    for index, group in enumerate(groups, start=1):
        units.append((_risk_group_element(index, group), True))
    reminder = _process_reminder_text(risk)
    if reminder:
        units.append((_markdown_element(reminder), True))


def _requirement_summary(risk: RequirementRisk, delayed_days: Optional[int] = None) -> str:
    names = [escape_value(risk.requirement_name, 260)]
    if risk.project != risk.requirement_name:
        names.append("OKR：{}".format(escape_value(risk.project, 200)))
    parts = ["**{}**".format("｜".join(names))]
    if risk.target_version:
        parts.append("版本：{}".format(escape_value(risk.target_version, 100)))
    parts.extend(
        [
            "当前环节：{}".format(escape_value(risk.current_stage, 120)),
            "服务端上线：{}".format(_format_date(risk.launch_at)),
            "合板：{}".format(_format_date(risk.merge_at)),
        ]
    )
    if delayed_days is not None:
        parts.append("预计延期：{} 天".format(delayed_days))
    if risk.schedule_formula is not None:
        formula = risk.schedule_formula
        unit = "工作日" if formula.duration_mode == "workday" else "自然日"
        terms = []
        for term in formula.terms:
            suffix = (
                "（{}）".format(escape_value(term.source, 180))
                if term.source
                else ""
            )
            terms.append(
                "{} {} 个{}{}".format(
                    escape_value(term.label, 180), term.days, unit, suffix
                )
            )
        parts.extend(
            [
                "关键路径：{}".format(escape_value(formula.domain, 120)),
                "预计完成：{}".format(_format_date(formula.predicted_completion)),
                "计算公式：{} + {} = {}".format(
                    _format_datetime(formula.started_at),
                    " + ".join(terms),
                    _format_datetime(formula.predicted_completion),
                ),
            ]
        )
        if delayed_days is not None and risk.predicted_completion is not None:
            parts.append(
                "延期计算：向上取整（（{} - 合板 {}）÷ 24小时） = {} 个自然日".format(
                    _format_datetime(risk.predicted_completion),
                    _format_datetime(risk.merge_at),
                    delayed_days,
                )
            )
    return "\n".join(parts)


def _severe_requirement_summary(
    risk: RequirementRisk, delayed_days: int
) -> str:
    identity = [escape_value(risk.requirement_name, 260)]
    if risk.project != risk.requirement_name:
        identity.append("OKR：{}".format(escape_value(risk.project, 200)))
    identity.extend(
        [
            "版本：{}".format(escape_value(risk.target_version, 100)),
            "当前环节：{}".format(escape_value(risk.current_stage, 120)),
        ]
    )
    formula = risk.schedule_formula
    predicted_completion = (
        formula.predicted_completion
        if formula is not None
        else risk.predicted_completion
    )
    key_path = (
        formula.domain
        if formula is not None
        else _bounded_join(
            [escape_value(domain, 120) for domain in risk.affected_domains],
            "、",
            360,
        )
    )
    if not key_path:
        key_path = "项目排期"
    lines = [
        "**{}**".format("｜".join(identity)),
        "合板 {}｜预计 {}｜延期 {} 自然日｜关键路径 {}".format(
            _format_short_date(risk.merge_at),
            _format_short_date(predicted_completion),
            delayed_days,
            key_path,
        ),
    ]
    if formula is not None:
        unit = "工作日" if formula.duration_mode == "workday" else "自然日"
        terms = []
        for term in formula.terms:
            suffix = (
                "（{}）".format(escape_value(term.source, 180))
                if term.source
                else ""
            )
            terms.append(
                "{} {} {}{}".format(
                    escape_value(term.label, 180),
                    term.days,
                    unit,
                    suffix,
                )
            )
        lines.append(
            "计算：{} → {}".format(
                _bounded_join(terms, " + ", 900),
                _format_short_datetime(formula.predicted_completion),
            )
        )
    return "\n".join(lines)


def _link_actions(risk: RequirementRisk) -> Optional[Dict[str, object]]:
    actions = _link_action_buttons(risk)
    return {"tag": "action", "actions": actions} if actions else None


def _link_action_buttons(risk: RequirementRisk) -> List[Dict[str, object]]:
    links = [
        ("需求文档", risk.requirement_doc_url),
        ("Meego", risk.meego_url),
        ("多语言翻译", risk.translation_url),
    ]
    return [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": label},
            "type": "default",
            "url": url,
        }
        for label, url in links
        if url
    ]


def _link_action_units(risk: RequirementRisk) -> List[Dict[str, object]]:
    units: List[Dict[str, object]] = []
    for action in _link_action_buttons(risk):
        unit = {"tag": "action", "actions": [action]}
        unit_payload = _interactive_payload(_MAX_SAFE_TITLE, "blue", [unit])
        if (
            len(_element_text(unit).encode("utf-8")) > _MAX_PAYLOAD_BYTES
            or _payload_bytes(unit_payload) > _MAX_PAYLOAD_BYTES
        ):
            units.append(_markdown_element("链接内容过长，请查看多维表格"))
            continue
        units.append(unit)
    return units


def _stage_header() -> Dict[str, object]:
    return _column_set(
        ["环节", "交付域", "负责人", "计划开始", "计划完成", "最晚安全DDL", "状态"],
        header=True,
    )


def _stage_row(node: NodeRisk) -> Dict[str, object]:
    return _column_set(
        [
            escape_value(node.node_name, 120),
            escape_value(node.domain, 80),
            _node_owner_mentions(node),
            _format_date(node.planned_start),
            _format_date(node.planned_end),
            _format_date(node.safe_deadline),
            escape_value(node.status.value, 60),
        ]
    )


def _stage_row_units(node: NodeRisk) -> List[Dict[str, object]]:
    row = _stage_row(node)
    if len(_element_text(row).encode("utf-8")) <= _MAX_ELEMENT_BYTES:
        return [row]
    compact_row = _column_set(
        [
            escape_value(node.node_name, 120),
            escape_value(node.domain, 80),
            "负责人信息见下方",
            _format_date(node.planned_start),
            _format_date(node.planned_end),
            _format_date(node.safe_deadline),
            escape_value(node.status.value, 60),
        ]
    )
    return [compact_row] + [
        _markdown_element(
            "**{}负责人**：{}".format(
                escape_value(node.node_name, 120),
                mention(person.open_id, person.name, 180),
            )
        )
        for person in _node_people(node)
    ]


def _column_set(values: List[str], header: bool = False) -> Dict[str, object]:
    weights = [13, 10, 17, 13, 13, 17, 10]
    columns = []
    for value, weight in zip(values, weights):
        content = "**{}**".format(value) if header else value
        columns.append(
            {
                "tag": "column",
                "width": "weighted",
                "weight": weight,
                "elements": [_markdown_element(content)],
            }
        )
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "default",
        "columns": columns,
    }


def _node_owner_mentions(node: NodeRisk) -> str:
    return "、".join(
        mention(person.open_id, person.name, 180) for person in _node_people(node)
    )


def _node_people(node: NodeRisk) -> List[Person]:
    return node.owners or [Person(open_id=node.owner_id, name=node.owner_name)]


def _node_is_current(node: NodeRisk, current_stage: str) -> bool:
    if node.node_name == current_stage:
        return True
    normalized_node = "".join(node.node_name.upper().split())
    normalized_stage = "".join(current_stage.upper().split())
    if normalized_node == normalized_stage:
        return True
    if current_stage in {"开发", "各端开发"}:
        return "开发" in node.node_name or node.node_name == "各端开发"
    return normalized_stage in normalized_node or normalized_node in normalized_stage


def _format_date(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d") if value is not None else "—"


def _format_short_date(value: Optional[datetime]) -> str:
    return value.strftime("%m-%d") if value is not None else "待确认"


def _format_short_datetime(value: Optional[datetime]) -> str:
    return value.strftime("%m-%d %H:%M") if value is not None else "待确认"


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
    required_system_units: List[str],
) -> Dict[str, object]:
    total = len(owner_groups)
    best_count = 0
    best_elements: List[Dict[str, object]] = []
    best_notice = _owner_count_notice(0, total)
    required_elements, _ = _optional_markdown_elements(required_system_units)
    for count in range(1, total + 1):
        blocks = _pack_owner_groups(owner_groups[:count])
        notice = _owner_count_notice(count, total)
        elements = [_markdown_element(block) for block in blocks]
        elements.extend(required_elements)
        elements.append(_markdown_element(notice))
        candidate = _interactive_payload(title, template, elements)
        if (
            len(elements) > _MAX_ELEMENTS
            or _payload_bytes(candidate) > _MAX_PAYLOAD_BYTES
        ):
            break
        best_count = count
        best_elements = elements[: len(blocks)]
        best_notice = notice
    return _interactive_payload(
        title,
        template,
        best_elements + required_elements + [_markdown_element(best_notice)],
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


def _build_severe_card_legacy(risk: RequirementRisk) -> Dict[str, object]:
    blockers = [blocker for blocker in risk.blockers if not _blocker_done(blocker)]
    delayed_days = _delay_days(risk.predicted_completion, risk.merge_at)
    actions = risk.actions
    if (
        risk.llm_enrichment is not None
        and risk.llm_enrichment.available
        and risk.llm_enrichment.actions
    ):
        actions = risk.llm_enrichment.actions

    elements: List[Dict[str, object]] = [
        _markdown_element(
            "{} **请立即处理严重交付风险**".format(
                mention(risk.project_owner_id, risk.project_owner_name)
            )
        ),
        _markdown_element(_requirement_summary(risk, delayed_days=delayed_days)),
    ]
    link_element = _link_actions(risk)
    if link_element is not None:
        elements.append(link_element)
    elements.append(_stage_header())
    risk_rows = _severe_risk_nodes(risk)
    elements.extend(_stage_row(node) for node in risk_rows)
    elements.append(
        _markdown_element(
            "**风险原因**：{}\n**处理动作**：{}".format(
                _join_values(risk.reasons, max_bytes=1000),
                _join_values(actions, max_bytes=800) if actions else "请负责人立即确认排期",
            )
        )
    )
    if blockers:
        elements.append(_markdown_element("**阻塞项**：{}".format(_format_blockers(blockers, 800))))
    elements.append(
        _markdown_element(
                "**相关负责人**：{}、{}".format(
                mention(risk.project_owner_id, risk.project_owner_name),
                _node_owners(risk_rows, max_bytes=900),
            )
        )
    )
    if risk.llm_enrichment is not None and not risk.llm_enrichment.available:
        elements.append(_markdown_element("**系统提示**：AI 补充分析不可用，基础规则正常运行"))
    return _bounded_structured_payload(
        "严重风险｜{}｜{}".format(risk.project, risk.requirement_name),
        "red",
        elements,
    )


def build_severe_card(risk: RequirementRisk) -> Dict[str, object]:
    payloads = build_severe_cards(risk)
    if len(payloads) == 1:
        return payloads[0]
    return _add_visible_card_notice(
        payloads[0], "内容已拆分为多张卡片，请继续查看后续部分"
    )


def _add_visible_card_notice(
    payload: Dict[str, object], notice_text: str
) -> Dict[str, object]:
    card = payload.get("card")
    if not isinstance(card, dict):
        return payload
    elements = card.get("elements")
    header = card.get("header")
    if not isinstance(elements, list) or not isinstance(header, dict):
        return payload
    title = header.get("title")
    template = header.get("template")
    if not isinstance(title, dict) or not isinstance(template, str):
        return payload
    title_content = title.get("content")
    if not isinstance(title_content, str):
        return payload
    notice = _markdown_element(notice_text)
    candidate = _interactive_payload(title_content, template, elements + [notice])
    if (
        len(elements) < _MAX_ELEMENTS
        and _payload_bytes(candidate) <= _MAX_PAYLOAD_BYTES
    ):
        return candidate
    titled_notice = _title_with_suffix(title_content, "｜{}".format(notice_text))
    titled_candidate = _interactive_payload(
        titled_notice,
        template,
        elements,
    )
    if _payload_bytes(titled_candidate) <= _MAX_PAYLOAD_BYTES:
        return titled_candidate
    return _interactive_payload(titled_notice, template, elements)


def build_severe_cards(risk: RequirementRisk) -> List[Dict[str, object]]:
    blockers = [blocker for blocker in risk.blockers if not _blocker_done(blocker)]
    delayed_days = _delay_days(risk.predicted_completion, risk.merge_at)
    actions = risk.actions
    if (
        risk.llm_enrichment is not None
        and risk.llm_enrichment.available
        and risk.llm_enrichment.actions
    ):
        actions = risk.llm_enrichment.actions

    units: List[Tuple[Dict[str, object], bool]] = [
        (
            _markdown_element(
                "{} **请立即处理严重交付风险**".format(
                    mention(risk.project_owner_id, risk.project_owner_name)
                )
            ),
            False,
        ),
        (
            _markdown_element(
                _severe_requirement_summary(risk, delayed_days=delayed_days)
            ),
            False,
        ),
    ]
    units.extend((element, True) for element in _link_action_units(risk))
    families = _risk_families(risk)
    severe_families = [
        family
        for family in families
        if getattr(family, "level", RiskLevel.NORMAL) >= RiskLevel.SEVERE
    ]
    warning_families = [
        family
        for family in families
        if getattr(family, "level", RiskLevel.NORMAL) < RiskLevel.SEVERE
    ]
    for index, family in enumerate(severe_families, start=1):
        units.extend(
            (element, True)
            for element in _risk_family_elements(index, family, risk)
        )
    for index, family in enumerate(warning_families):
        units.extend(
            (element, True)
            for element in _warning_family_elements(
                family,
                risk,
                heading=index == 0,
            )
        )
    units.append(
        (
            _markdown_element(
                "**处理动作**：{}".format(
                    _join_values(actions, max_bytes=800)
                    if actions
                    else "请负责人立即确认排期"
                )
            ),
            True,
        )
    )
    if blockers:
        units.append(
            (_markdown_element("**阻塞项**：{}".format(_format_blockers(blockers, 800))), True)
        )
    if risk.llm_enrichment is not None and not risk.llm_enrichment.available:
        units.append(
            (_markdown_element("**系统提示**：AI 补充分析不可用，基础规则正常运行"), True)
        )
    reminder = _process_reminder_text(risk)
    if reminder:
        units.append((_markdown_element(reminder), True))
    return _split_grouped_units(
        "严重风险｜{}｜{}".format(risk.project, risk.requirement_name),
        "red",
        units,
    )


def _risk_family_elements(
    index: int, family: object, risk: RequirementRisk
) -> List[Dict[str, object]]:
    context = _risk_family_context(risk, family)
    lines = [
        "**{}. {}｜严重**".format(
            index,
            _bounded_escaped_text(_family_title(family), 620),
        ),
    ]
    lines.extend(_risk_family_scope_lines(context))
    lines.extend(
        [
            "最早安全 DDL：{}".format(
            _format_short_date(context.earliest_safe_deadline)
            if context.earliest_safe_deadline is not None
            else "项目排期"
            ),
        ]
    )
    return _risk_family_owner_elements(lines, context.owners)


def _warning_family_elements(
    family: object, risk: RequirementRisk, *, heading: bool
) -> List[Dict[str, object]]:
    context = _risk_family_context(risk, family)
    lines: List[str] = []
    if heading:
        lines.append("**其他预警**")
    lines.append(
        "- **{}**".format(
            _bounded_escaped_text(_family_title(family), 620)
        )
    )
    lines.extend(_risk_family_scope_lines(context))
    lines.extend(
        [
            "最早安全 DDL：{}".format(
                _format_short_date(context.earliest_safe_deadline)
                if context.earliest_safe_deadline is not None
                else "项目排期"
            ),
        ]
    )
    return _risk_family_owner_elements(lines, context.owners)


def _risk_family_context(
    risk: RequirementRisk, family: object
) -> _RiskFamilyContext:
    stage_refs = tuple(dict.fromkeys(getattr(family, "stage_refs", []) or []))
    domain_refs = tuple(dict.fromkeys(getattr(family, "domain_refs", []) or []))
    source_findings = getattr(family, "source_findings", []) or []
    family_code = str(getattr(family, "code", "") or "")
    fallback_schedule_scope = (
        not source_findings
        and family_code in _UNCONFIRMED_SCHEDULE_RISK_CODES
        and bool(stage_refs)
    )
    schedule_findings = [
        finding
        for finding in source_findings
        if finding.reason_code in _UNCONFIRMED_SCHEDULE_RISK_CODES
        and finding.stage_refs
    ]
    confirmed_findings = [
        finding
        for finding in source_findings
        if finding.reason_code not in _UNCONFIRMED_SCHEDULE_RISK_CODES
        or not finding.stage_refs
    ]
    unconfirmed_schedule_set = (
        set(stage_refs)
        if fallback_schedule_scope
        else {
            stage_ref
            for finding in schedule_findings
            for stage_ref in finding.stage_refs
        }
    )
    unconfirmed_schedule_refs = tuple(
        stage_ref
        for stage_ref in stage_refs
        if stage_ref in unconfirmed_schedule_set
    )
    unconfirmed_schedule_by_domain = _schedule_refs_by_domain(
        schedule_findings,
        stage_refs,
    )
    if fallback_schedule_scope:
        confirmed_stage_set = set()
        confirmed_domain_set = set(domain_refs)
    elif source_findings:
        confirmed_stage_set = {
            stage_ref
            for finding in confirmed_findings
            for stage_ref in finding.stage_refs
        }
        confirmed_domain_set = {
            domain_ref
            for finding in confirmed_findings
            for domain_ref in finding.domain_refs
        }
    else:
        confirmed_stage_set = set(stage_refs)
        confirmed_domain_set = set(domain_refs)
    confirmed_stage_refs = tuple(
        stage_ref for stage_ref in stage_refs if stage_ref in confirmed_stage_set
    )
    confirmed_domain_refs = tuple(
        domain_ref for domain_ref in domain_refs if domain_ref in confirmed_domain_set
    )
    stage_set = set(stage_refs)
    domain_set = set(domain_refs)

    def matches_scope(node: NodeRisk) -> bool:
        stage_matches = not stage_set or node.node_name in stage_set
        domain_matches = not domain_set or node.domain in domain_set
        return stage_matches and domain_matches

    matching_nodes = [
        node
        for node in risk.node_risks
        if matches_scope(node)
    ]
    owners: "OrderedDict[Tuple[str, str], None]" = OrderedDict()
    for node in matching_nodes:
        for person in _node_people(node):
            owners[(person.open_id, person.name)] = None
    if not owners:
        owners[(risk.project_owner_id, risk.project_owner_name)] = None
    deadlines = [
        node.safe_deadline
        for node in matching_nodes
        if node.safe_deadline is not None
        and node.status
        not in {
            NodeStatus.COMPLETED,
            NodeStatus.SKIPPED,
            NodeStatus.CANCELLED,
        }
    ]
    return _RiskFamilyContext(
        stage_refs=stage_refs,
        domain_refs=domain_refs,
        unconfirmed_schedule_refs=unconfirmed_schedule_refs,
        unconfirmed_schedule_by_domain=unconfirmed_schedule_by_domain,
        confirmed_stage_refs=confirmed_stage_refs,
        confirmed_domain_refs=confirmed_domain_refs,
        owners=tuple(owners),
        earliest_safe_deadline=min(deadlines) if deadlines else None,
    )


def _family_title(family: object) -> str:
    title = getattr(family, "title", None)
    if title:
        return str(title)
    reason_text = getattr(family, "reason_text", None)
    return str(reason_text or "风险待确认")


def _risk_family_scope_lines(context: _RiskFamilyContext) -> List[str]:
    if context.unconfirmed_schedule_by_domain:
        lines = ["未确定排期"]
        lines.extend(
            _schedule_domain_lines(context.unconfirmed_schedule_by_domain, 620)
        )
    elif context.unconfirmed_schedule_refs:
        lines = [
            "未确定排期：{}".format(
                _bounded_escaped_text(
                    "、".join(context.unconfirmed_schedule_refs), 620
                )
            )
        ]
    else:
        lines = []
    if context.confirmed_stage_refs:
        lines.append(
            "环节：{}".format(
                _bounded_escaped_text(
                    "、".join(context.confirmed_stage_refs), 620
                )
            )
        )
    elif not context.unconfirmed_schedule_refs:
        lines.append("环节：项目排期")
    if context.confirmed_domain_refs:
        lines.append(
            "交付域：{}".format(
                _bounded_escaped_text(
                    "、".join(context.confirmed_domain_refs), 620
                )
            )
        )
    return lines


def _schedule_refs_by_domain(
    findings: Sequence[RiskFinding],
    ordered_stage_refs: Sequence[str],
) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    stages_by_domain: "OrderedDict[str, OrderedDict[str, None]]" = OrderedDict()
    for finding in findings:
        if finding.reason_code not in _UNCONFIRMED_SCHEDULE_RISK_CODES:
            continue
        if not finding.stage_refs:
            continue
        for domain_ref in finding.domain_refs:
            domain_stages = stages_by_domain.setdefault(domain_ref, OrderedDict())
            for stage_ref in finding.stage_refs:
                domain_stages[stage_ref] = None
    stage_order = {
        stage_ref: index for index, stage_ref in enumerate(ordered_stage_refs)
    }
    return tuple(
        (
            domain_ref,
            tuple(
                sorted(
                    domain_stages,
                    key=lambda stage_ref: (
                        stage_order.get(stage_ref, len(stage_order)),
                        first_seen[stage_ref],
                    ),
                )
            ),
        )
        for domain_ref, domain_stages in stages_by_domain.items()
        for first_seen in [
            {stage_ref: index for index, stage_ref in enumerate(domain_stages)}
        ]
    )


def _schedule_domain_lines(
    schedule_by_domain: Sequence[Tuple[str, Sequence[str]]],
    max_bytes: int,
) -> List[str]:
    return [
        "{}：{}".format(
            _bounded_escaped_text(domain_ref, 160),
            _bounded_escaped_text("、".join(stage_refs), max_bytes),
        )
        for domain_ref, stage_refs in schedule_by_domain
    ]


def _risk_family_owner_elements(
    base_lines: List[str], owners: Tuple[Tuple[str, str], ...]
) -> List[Dict[str, object]]:
    owner_mentions = [mention(open_id, name, 180) for open_id, name in owners]
    base_content = "\n".join(base_lines)
    full_content = "{}\n负责人：{}".format(
        base_content,
        "、".join(owner_mentions),
    )
    if len(full_content.encode("utf-8")) <= _MAX_ELEMENT_BYTES:
        return [_markdown_element(full_content)]

    elements = _bounded_markdown_line_elements(base_lines)
    current: List[str] = []
    for owner_mention in owner_mentions:
        prefix = "**负责人**：" if len(elements) == 1 else "**负责人（续）**："
        candidate = "{}{}".format(prefix, "、".join(current + [owner_mention]))
        if current and len(candidate.encode("utf-8")) > _MAX_ELEMENT_BYTES:
            elements.append(
                _markdown_element("{}{}".format(prefix, "、".join(current)))
            )
            current = [owner_mention]
        else:
            current.append(owner_mention)
    if current:
        prefix = "**负责人**：" if len(elements) == 1 else "**负责人（续）**："
        elements.append(
            _markdown_element("{}{}".format(prefix, "、".join(current)))
        )
    return elements


def _bounded_markdown_line_elements(
    lines: Sequence[str],
) -> List[Dict[str, object]]:
    chunks: List[str] = []
    current: List[str] = []
    for line in lines:
        safe_line = line
        if len(safe_line.encode("utf-8")) > _MAX_ELEMENT_BYTES:
            safe_line = "{}\n{}".format(
                _truncate_utf8(
                    safe_line,
                    _MAX_ELEMENT_BYTES
                    - len(_RISK_GROUP_OVERFLOW_NOTICE.encode("utf-8"))
                    - 1,
                ),
                _RISK_GROUP_OVERFLOW_NOTICE,
            )
        candidate = "\n".join(current + [safe_line])
        if current and len(candidate.encode("utf-8")) > _MAX_ELEMENT_BYTES:
            chunks.append("\n".join(current))
            current = [safe_line]
        else:
            current.append(safe_line)
    if current:
        chunks.append("\n".join(current))
    return [_markdown_element(chunk) for chunk in chunks]


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
        for (owner_id, owner_name), delivery in owners.items():
            rows = sorted(
                delivery.rows,
                key=lambda item: _daily_sort_key(item, report_day),
            )
            owner_lines = []
            if first_owner:
                owner_lines.append(
                    "**项目：{}**".format(escape_value(project, 160))
                )
                first_owner = False
            if rows:
                owner_lines.append(_render_daily_row(rows[0], compact=True))
                secondary.extend(_render_daily_row(row) for row in rows[1:])
            else:
                owner_lines.append(
                    "负责人：{}\n• 未来 7 天无未完成节点".format(
                        mention(owner_id, owner_name, 112)
                    )
                )
            if delivery.later_count:
                later_line = "负责人：{}\n• 7 天后待办：{} 项".format(
                    mention(owner_id, owner_name, 112),
                    delivery.later_count,
                )
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
    rendered_owner = mention(
        row.node.owner_id,
        row.node.owner_name,
        owner_budget,
    )
    return (
        "• 负责人：{}\n"
        "需求：{}｜交付域：{}\n"
        "**节点：{}**\n"
        "计划开始：{}\n"
        "计划完成：{}｜最晚安全 DDL：{}\n"
        "状态：{}（{}）"
    ).format(
        rendered_owner,
        escape_value(row.requirement_name, requirement_budget),
        escape_value(row.node.domain, domain_budget),
        escape_value(row.node.node_name, node_budget),
        _format_datetime(row.node.planned_start),
        _format_datetime(row.node.planned_end),
        _format_datetime(row.node.safe_deadline),
        escape_value(row.node.status.value, status_budget),
        _LEVEL_LABELS[row.node.level],
    )


def _requirement_summary_unit(risk: RequirementRisk) -> str:
    blockers = [blocker for blocker in risk.blockers if not _blocker_done(blocker)]
    summary = (
        "**需求摘要｜{}**｜项目：{}｜版本：{}｜服务端上线：{}｜合板：{}｜"
        "缓冲：{} 天｜阻塞：{}"
    ).format(
        escape_value(risk.requirement_name, 240),
        escape_value(risk.project, 120),
        escape_value(risk.target_version, 120),
        _format_datetime(risk.launch_at),
        _format_datetime(risk.merge_at),
        _format_number(risk.buffer_days),
        _format_blockers(blockers),
    )
    reminder = _process_reminder_text(risk)
    if reminder:
        return "{}\n{}".format(summary, reminder)
    return summary


def _process_reminder_text(risk: RequirementRisk) -> str:
    stages = sorted(
        set(risk.process_reminders),
        key=lambda stage: (_STAGE_ORDER.get(stage, len(_STAGE_ORDER)), stage),
    )
    if not stages:
        return ""
    return "流程补充提醒：尚未维护 {}；如项目涉及，请后续补充。".format(
        _bounded_escaped_text("、".join(stages), 900)
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


def _node_owners(nodes: List[NodeRisk], max_bytes: int = 1200) -> str:
    owners: "OrderedDict[Tuple[str, str], None]" = OrderedDict()
    for node in nodes:
        people = node.owners or []
        if people:
            for person in people:
                owners[(person.open_id, person.name)] = None
        else:
            owners[(node.owner_id, node.owner_name)] = None
    if not owners:
        return "未提供"
    return _bounded_join(
        [mention(owner_id, name, 180) for owner_id, name in owners],
        "、",
        max_bytes,
    )


def _format_blockers(
    blockers: Iterable[Blocker], max_bytes: int = 1200
) -> str:
    rendered = []
    for blocker in blockers:
        rendered.append(
            "{}（{}｜{}｜{}）".format(
                escape_value(blocker.title, 320),
                mention(blocker.owner_id, blocker.owner_name),
                escape_value(blocker.status, 80),
                _format_datetime(blocker.planned_resolution_at),
            )
        )
    return _bounded_join(rendered, "；", max_bytes) if rendered else "无"


def _delay_days(predicted: Optional[datetime], merge_at: datetime) -> int:
    if predicted is None or predicted <= merge_at:
        return 0
    return int(math.ceil((predicted - merge_at).total_seconds() / 86400))


def _node_done(node: NodeRisk) -> bool:
    return node.status in {
        NodeStatus.COMPLETED,
        NodeStatus.SKIPPED,
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


def _join_values(values: Iterable[str], max_bytes: int = 1200) -> str:
    rendered = [escape_value(value, 320) for value in values if value]
    return _bounded_join(rendered, "、", max_bytes) if rendered else "未提供"


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
