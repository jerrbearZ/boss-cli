"""Workflow commands for Boss-only recruiting automation dry runs."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import click
from rich.table import Table

from ..auth import Credential, load_from_env
from ..client import BossClient
from ..constants import CREDENTIAL_FILE
from ..workflow import init_db
from ..workflow.poller import sync_inbox
from ._common import _output_structured, console, handle_command, require_auth, structured_output_options


DEFAULT_RULES: dict[str, Any] = {
    "required_keywords": [],
    "preferred_keywords": [
        "效果商务",
        "商务",
        "BD",
        "增长",
        "投放",
        "广告",
        "渠道",
    ],
    "reject_keywords": [],
    "min_years_experience": None,
    "reply_template": "",
}


@click.group()
def workflow() -> None:
    """招聘自动化工作流 (dry-run first)"""


@workflow.command("init-db")
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="SQLite workflow database path (default: $BOSS_WORKFLOW_DB or ~/.local/share/boss-cli/workflow.db)",
)
@structured_output_options
def init_db_command(db_path: Path | None, as_json: bool, as_yaml: bool) -> None:
    """Initialize the local workflow SQLite database."""
    store = init_db(db_path)
    try:
        data = {
            "db": str(store.path),
            "schema_version": store.current_schema_version(),
            "tables": store.table_names(),
        }
    finally:
        store.close()

    if as_json or as_yaml or not sys.stdout.isatty():
        _output_structured(data, as_json=as_json, as_yaml=as_yaml)
        return

    console.print("[bold cyan]Boss workflow database initialized[/bold cyan]")
    console.print(f"  db={data['db']}")
    console.print(f"  schema_version={data['schema_version']}")
    console.print(f"  tables={len(data['tables'])}")


@workflow.command("sync")
@click.option("--db", "db_path", type=click.Path(dir_okay=False, path_type=Path), default=None, help="SQLite workflow database path")
@click.option("--job", "enc_job_id", default="", help="按职位 encryptJobId 筛选")
@click.option("--label", "label_id", default=0, type=int, help="按 Boss 标签筛选 (0=全部)")
@click.option("-n", "--limit", default=100, type=click.IntRange(min=0), show_default=True, help="最多读取候选人数 (0=不限)")
@click.option("--max-pages", default=20, type=click.IntRange(min=1), show_default=True, help="单次同步页数上限")
@click.option(
    "--history",
    "history_mode",
    type=click.Choice(["none", "changed", "all"]),
    default="changed",
    show_default=True,
    help="聊天历史读取策略",
)
@click.option("--history-budget", default=20, type=click.IntRange(min=0), show_default=True, help="单次最多读取历史的会话数")
@click.option("--history-count", default=50, type=click.IntRange(min=1), show_default=True, help="每次历史请求的消息数")
@click.option("--max-history-messages", default=200, type=click.IntRange(min=1), show_default=True, help="每位候选人的历史消息上限")
@click.option("--include-profile", is_flag=True, help="读取聊天候选人摘要；不读取可能触发提醒的完整简历")
@click.option("--full-scan", is_flag=True, help="完整无筛选扫描后将未出现的候选人标记为 inactive")
@click.option("--resume/--no-resume", default=True, show_default=True, help="从未完成同步的已提交页继续")
@click.option(
    "--allow-browser-auth",
    is_flag=True,
    help="允许自动读取浏览器 Cookie；无人值守同步建议保持关闭",
)
@structured_output_options
def sync_command(
    db_path: Path | None,
    enc_job_id: str,
    label_id: int,
    limit: int,
    max_pages: int,
    history_mode: str,
    history_budget: int,
    history_count: int,
    max_history_messages: int,
    include_profile: bool,
    full_scan: bool,
    resume: bool,
    allow_browser_auth: bool,
    as_json: bool,
    as_yaml: bool,
) -> None:
    """增量读取 Boss 收件箱并安全写入本地 SQLite；不会发送消息。"""
    credential = _get_workflow_credential(
        allow_browser_auth=allow_browser_auth,
        as_json=as_json,
        as_yaml=as_yaml,
    )

    def _action(client: BossClient) -> dict[str, Any]:
        effective_credential = client.credential if isinstance(client.credential, Credential) else credential
        with init_db(db_path) as store:
            return sync_inbox(
                store,
                client,
                effective_credential,
                enc_job_id=enc_job_id,
                label_id=label_id,
                limit=limit,
                max_pages=max_pages,
                history_mode=history_mode,  # type: ignore[arg-type]
                history_budget=history_budget,
                history_count=history_count,
                max_history_messages=max_history_messages,
                include_profile=include_profile,
                full_scan=full_scan,
                resume=resume,
                requested_by="cli",
            )

    handle_command(
        credential,
        action=_action,
        render=_render_sync,
        as_json=as_json,
        as_yaml=as_yaml,
    )


def _render_sync(data: dict[str, Any]) -> None:
    console.print("[bold cyan]Boss inbox synchronization complete[/bold cyan]")
    console.print(f"  account={data.get('account_id')} pages={data.get('pages')} seen={data.get('seen')}")
    console.print(
        "  candidates="
        f"{data.get('candidates_upserted')} messages_inserted={data.get('messages_inserted')} "
        f"history={data.get('history_conversations')} errors={data.get('errors')}"
    )


@workflow.command("dry-run")
@click.option("--job", "enc_job_id", default="", help="按职位 encryptJobId 筛选")
@click.option("--label", "label_id", default=0, type=int, help="按 Boss 标签筛选 (0=全部)")
@click.option("-p", "--page", default=1, type=int, help="候选人列表页码")
@click.option("-n", "--limit", default=10, type=int, help="最多评估候选人数")
@click.option("--rules-file", type=click.Path(exists=True, dir_okay=False), help="JSON 规则文件路径")
@click.option("--include-chat/--no-include-chat", default=True, help="是否读取聊天记录用于判断")
@click.option("--chat-count", default=20, type=int, help="每位候选人最多读取聊天消息数")
@click.option(
    "--allow-browser-auth",
    is_flag=True,
    help="允许自动读取浏览器 Cookie；可能触发系统钥匙串/浏览器权限等待，默认关闭",
)
@click.option(
    "--fetch-resume",
    is_flag=True,
    help="读取完整简历资料；可能触发 Boss 端候选人被查看提醒，默认关闭",
)
@structured_output_options
def dry_run(
    enc_job_id: str,
    label_id: int,
    page: int,
    limit: int,
    rules_file: str | None,
    include_chat: bool,
    chat_count: int,
    allow_browser_auth: bool,
    fetch_resume: bool,
    as_json: bool,
    as_yaml: bool,
) -> None:
    """只读评估 Boss 候选人并输出下一步建议，不发送消息。"""
    cred = _get_workflow_credential(allow_browser_auth=allow_browser_auth, as_json=as_json, as_yaml=as_yaml)
    rules = _load_rules(rules_file)

    def _action(client: BossClient) -> dict[str, Any]:
        return _run_boss_dry_run(
            client,
            rules=rules,
            enc_job_id=enc_job_id,
            label_id=label_id,
            page=page,
            limit=limit,
            include_chat=include_chat,
            chat_count=chat_count,
            fetch_resume=fetch_resume,
        )

    handle_command(
        cred,
        action=_action,
        render=lambda data: _render_dry_run(data, rules_file=rules_file),
        as_json=as_json,
        as_yaml=as_yaml,
    )


def _load_rules(rules_file: str | None) -> dict[str, Any]:
    """Load matching rules from JSON, merged over safe defaults."""
    rules = dict(DEFAULT_RULES)
    if not rules_file:
        return rules

    path = Path(rules_file)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise click.ClickException("rules-file must contain a JSON object")
    for key, value in data.items():
        if key in rules:
            rules[key] = value
    return rules


def _get_workflow_credential(*, allow_browser_auth: bool, as_json: bool, as_yaml: bool) -> Credential:
    """Load credentials for automation without blocking on browser extraction by default."""
    if allow_browser_auth:
        return require_auth()

    cred = _load_saved_credential_no_refresh() or load_from_env()
    if cred:
        return cred

    message = (
        "No saved Boss credential or BOSS_COOKIES environment value found. "
        "Run `boss login` once, set BOSS_COOKIES, or retry with `--allow-browser-auth`."
    )
    if as_json or as_yaml or not sys.stdout.isatty():
        envelope = {
            "ok": False,
            "schema_version": "1",
            "data": None,
            "error": {
                "code": "not_authenticated",
                "message": message,
            },
        }
        if as_yaml:
            try:
                import yaml

                click.echo(yaml.dump(envelope, allow_unicode=True, default_flow_style=False))
            except ImportError:
                click.echo(json.dumps(envelope, indent=2, ensure_ascii=False))
        else:
            click.echo(json.dumps(envelope, indent=2, ensure_ascii=False))
    else:
        console.print(f"[yellow]{message}[/yellow]")
    raise SystemExit(1)


def _load_saved_credential_no_refresh() -> Credential | None:
    """Load saved credential without the auth module's browser refresh fallback."""
    if not CREDENTIAL_FILE.exists():
        return None
    try:
        data = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cred = Credential.from_dict(data)
    return cred if cred.is_valid else None


def _run_boss_dry_run(
    client: BossClient,
    *,
    rules: dict[str, Any],
    enc_job_id: str,
    label_id: int,
    page: int,
    limit: int,
    include_chat: bool,
    chat_count: int,
    fetch_resume: bool,
) -> dict[str, Any]:
    """Collect Boss-side read-only data and classify candidate next actions."""
    friend_data = client.get_boss_friend_list(label_id=label_id, enc_job_id=enc_job_id, page=page)
    friend_rows = friend_data.get("result", [])[: max(limit, 0)]
    friend_ids = [row["friendId"] for row in friend_rows if row.get("friendId")]

    if not friend_ids:
        return {
            "mode": "dry_run",
            "sent_messages": 0,
            "fetch_resume": fetch_resume,
            "summary": {
                "total": 0,
                "matched": 0,
                "needs_review": 0,
                "rejected": 0,
                "errors": 0,
            },
            "candidates": [],
        }

    detail_payload = client.get_boss_friend_details(friend_ids)
    details = detail_payload.get("friendList", [])
    last_messages = client.get_boss_last_messages(friend_ids[:50])
    last_message_by_uid = _index_last_messages(last_messages)

    candidates: list[dict[str, Any]] = []
    summary = {"total": 0, "matched": 0, "needs_review": 0, "rejected": 0, "errors": 0}

    for detail in details:
        summary["total"] += 1
        friend_id = int(detail.get("friendId") or 0)
        uid = int(detail.get("uid") or 0)
        last_message = last_message_by_uid.get(uid, {})
        chat_payload: dict[str, Any] | None = None
        resume_payload: dict[str, Any] | None = None
        errors: list[str] = []

        if include_chat and friend_id:
            try:
                chat_payload = client.get_boss_chat_history(gid=friend_id, count=chat_count)
            except Exception as exc:  # noqa: BLE001 - keep dry-run resilient per candidate.
                errors.append(f"chat_history: {exc}")

        if fetch_resume:
            try:
                encrypt_geek_id = _extract_encrypt_geek_id(detail)
                encrypt_job_id = detail.get("encryptJobId") or enc_job_id
                if encrypt_geek_id and encrypt_job_id:
                    resume_payload = client.get_boss_view_geek(
                        encrypt_geek_id=encrypt_geek_id,
                        encrypt_job_id=encrypt_job_id,
                        security_id=detail.get("securityId", ""),
                    )
                else:
                    errors.append("resume: missing encryptGeekId or encryptJobId")
            except Exception as exc:  # noqa: BLE001 - keep dry-run resilient per candidate.
                errors.append(f"resume: {exc}")

        evidence_text = _candidate_text(detail, last_message, chat_payload, resume_payload)
        decision = _classify_candidate(evidence_text, rules)
        if errors:
            decision["warnings"] = [*decision.get("warnings", []), *errors]
            summary["errors"] += 1

        status = decision["status"]
        summary[status] += 1
        candidates.append(
            {
                "friend_id": friend_id,
                "uid": uid,
                "name": detail.get("name", ""),
                "job_name": detail.get("jobName", ""),
                "encrypt_geek_id": _extract_encrypt_geek_id(detail),
                "security_id_present": bool(detail.get("securityId")),
                "last_message_preview": _last_message_preview(last_message),
                "decision": decision,
                "proposed_reply": rules.get("reply_template", ""),
                "would_send_message": False,
            }
        )

    return {
        "mode": "dry_run",
        "sent_messages": 0,
        "fetch_resume": fetch_resume,
        "summary": summary,
        "rules": _summarize_rules(rules),
        "candidates": candidates,
    }


def _classify_candidate(text: str, rules: dict[str, Any]) -> dict[str, Any]:
    normalized = text.lower()
    required = _as_str_list(rules.get("required_keywords"))
    preferred = _as_str_list(rules.get("preferred_keywords"))
    rejected = _as_str_list(rules.get("reject_keywords"))
    min_years = rules.get("min_years_experience")

    missing_required = [kw for kw in required if kw.lower() not in normalized]
    matched_preferred = [kw for kw in preferred if kw.lower() in normalized]
    matched_rejected = [kw for kw in rejected if kw.lower() in normalized]
    years = _extract_years_experience(text)

    reasons: list[str] = []
    warnings: list[str] = []

    if matched_rejected:
        return {
            "status": "rejected",
            "reason": "matched reject keywords",
            "matched_reject_keywords": matched_rejected,
            "matched_preferred_keywords": matched_preferred,
            "missing_required_keywords": missing_required,
            "years_experience": years,
        }

    if missing_required:
        return {
            "status": "needs_review",
            "reason": "missing required keywords",
            "matched_preferred_keywords": matched_preferred,
            "missing_required_keywords": missing_required,
            "years_experience": years,
        }

    if min_years is not None:
        try:
            min_years_num = float(min_years)
        except (TypeError, ValueError):
            min_years_num = None
            warnings.append("invalid min_years_experience rule")
        if min_years_num is not None and (years is None or years < min_years_num):
            return {
                "status": "needs_review",
                "reason": "experience below threshold or unavailable",
                "matched_preferred_keywords": matched_preferred,
                "missing_required_keywords": missing_required,
                "years_experience": years,
                "warnings": warnings,
            }

    if matched_preferred or not preferred:
        if matched_preferred:
            reasons.append("matched preferred keywords")
        else:
            reasons.append("no preferred keyword rule configured")
        return {
            "status": "matched",
            "reason": "; ".join(reasons),
            "matched_preferred_keywords": matched_preferred,
            "missing_required_keywords": [],
            "years_experience": years,
            "warnings": warnings,
        }

    return {
        "status": "needs_review",
        "reason": "no preferred keywords matched",
        "matched_preferred_keywords": [],
        "missing_required_keywords": [],
        "years_experience": years,
        "warnings": warnings,
    }


def _candidate_text(
    detail: dict[str, Any],
    last_message: dict[str, Any],
    chat_payload: dict[str, Any] | None,
    resume_payload: dict[str, Any] | None,
) -> str:
    parts: list[str] = [json.dumps(detail, ensure_ascii=False, default=str)]
    if last_message:
        parts.append(json.dumps(last_message, ensure_ascii=False, default=str))
    if chat_payload:
        parts.append(json.dumps(chat_payload, ensure_ascii=False, default=str))
    if resume_payload:
        parts.append(json.dumps(resume_payload, ensure_ascii=False, default=str))
    return "\n".join(parts)


def _extract_years_experience(text: str) -> float | None:
    """Extract a rough years-of-experience signal from Chinese text."""
    patterns = [
        r"(\d+(?:\.\d+)?)\s*年以上",
        r"(\d+(?:\.\d+)?)\s*年经验",
        r"(\d+(?:\.\d+)?)\s*年工作",
        r"(\d+(?:\.\d+)?)\s*年",
    ]
    values: list[float] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            values.append(float(match.group(1)))
    if values:
        return max(values)

    range_match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*年", text)
    if range_match:
        return float(range_match.group(1))
    return None


def _index_last_messages(last_messages: Any) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    if not isinstance(last_messages, list):
        return result
    for msg in last_messages:
        if not isinstance(msg, dict):
            continue
        uid = msg.get("uid")
        if uid:
            result[int(uid)] = msg
    return result


def _last_message_preview(last_message: dict[str, Any]) -> str:
    info = last_message.get("lastMsgInfo", {}) if isinstance(last_message, dict) else {}
    if isinstance(info, dict):
        return str(info.get("showText") or info.get("text") or "")[:80]
    return ""


def _extract_encrypt_geek_id(detail: dict[str, Any]) -> str:
    return str(
        detail.get("encryptGeekId")
        or detail.get("encryptUid")
        or detail.get("encryptFriendId")
        or ""
    )


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def _summarize_rules(rules: dict[str, Any]) -> dict[str, Any]:
    return {
        "required_keywords": _as_str_list(rules.get("required_keywords")),
        "preferred_keywords": _as_str_list(rules.get("preferred_keywords")),
        "reject_keywords": _as_str_list(rules.get("reject_keywords")),
        "min_years_experience": rules.get("min_years_experience"),
        "reply_template_configured": bool(rules.get("reply_template")),
    }


def _render_dry_run(data: dict[str, Any], *, rules_file: str | None) -> None:
    summary = data.get("summary", {})
    console.print("[bold cyan]Boss workflow dry run[/bold cyan]")
    console.print(
        "  "
        f"total={summary.get('total', 0)} "
        f"matched={summary.get('matched', 0)} "
        f"needs_review={summary.get('needs_review', 0)} "
        f"rejected={summary.get('rejected', 0)} "
        f"errors={summary.get('errors', 0)} "
        "sent_messages=0"
    )
    console.print(f"  rules={rules_file or 'default'} fetch_resume={data.get('fetch_resume', False)}")

    candidates = data.get("candidates", [])
    if not candidates:
        console.print("[yellow]No candidates found for this dry run.[/yellow]")
        return

    table = Table(title="Dry-run candidate decisions", show_lines=True)
    table.add_column("friendId", style="dim", width=10)
    table.add_column("Name", style="cyan", max_width=12)
    table.add_column("Job", style="green", max_width=20)
    table.add_column("Decision", style="yellow", max_width=12)
    table.add_column("Reason", max_width=40)
    table.add_column("Last message", style="dim", max_width=30)

    for candidate in candidates:
        decision = candidate.get("decision", {})
        table.add_row(
            str(candidate.get("friend_id", "")),
            candidate.get("name", ""),
            candidate.get("job_name", ""),
            decision.get("status", ""),
            decision.get("reason", ""),
            candidate.get("last_message_preview", ""),
        )

    console.print(table)
    console.print("[dim]Dry run only: no messages were sent.[/dim]")
