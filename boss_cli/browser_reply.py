"""Browser-backed recruiter reply support.

Normal Boss chat messages are sent through the web app's websocket client,
not the ``fastReply/sendReplyMsg`` HTTP endpoint.  This module keeps the CLI
in charge of target selection and verification while delegating the actual
send to the initialized Boss web app running in a browser context.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .auth import Credential
from .client import BossClient
from .constants import WEB_BOSS_CHAT_URL
from .exceptions import BossApiError

BrowserEngine = Literal["auto", "camoufox", "chrome"]


class BrowserReplyError(BossApiError):
    """Raised when browser-backed message sending cannot complete."""

    def __init__(self, message: str, code: str = "browser_send_failed"):
        super().__init__(message, code=code)


@dataclass(frozen=True)
class BrowserReplyTarget:
    """Minimal target data needed by the Boss web chat bridge."""

    friend_id: int
    friend_source: int
    encrypt_uid: str
    name: str = ""
    job_name: str = ""

    def safe_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrowserReplyResult:
    """Result envelope returned by browser-backed reply commands."""

    ok: bool
    friend_id: int
    sent: bool
    verified: bool
    engine: str
    method: str
    target: dict[str, Any]
    verification: dict[str, Any]
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_browser_reply_target(client: BossClient, friend_id: int) -> BrowserReplyTarget:
    """Resolve a recruiter inbox friend id into browser-send target data."""
    data = client.get_boss_friend_details([friend_id])
    friend_list = data.get("friendList", []) if isinstance(data, dict) else []
    detail = next(
        (
            item for item in friend_list
            if int(item.get("uid") or item.get("friendId") or 0) == friend_id
        ),
        friend_list[0] if friend_list else None,
    )
    if not detail:
        raise BrowserReplyError(f"未找到 friendId={friend_id} 的候选人详情", code="target_not_found")

    uid = int(detail.get("uid") or detail.get("friendId") or friend_id)
    encrypt_uid = str(
        detail.get("encryptUid")
        or detail.get("encryptFriendId")
        or detail.get("encryptGeekId")
        or ""
    )
    if not encrypt_uid:
        raise BrowserReplyError(
            f"候选人 friendId={friend_id} 缺少 encryptUid，无法通过 Boss Web 发送",
            code="target_missing_context",
        )

    friend_source = int(
        detail.get("friendSource")
        or detail.get("source")
        or detail.get("geekSource")
        or 0
    )

    return BrowserReplyTarget(
        friend_id=uid,
        friend_source=friend_source,
        encrypt_uid=encrypt_uid,
        name=str(detail.get("name") or ""),
        job_name=str(detail.get("jobName") or ""),
    )


def make_dry_run_result(target: BrowserReplyTarget, *, engine: str = "browser") -> BrowserReplyResult:
    """Return the command result for a no-send preview."""
    return BrowserReplyResult(
        ok=True,
        friend_id=target.friend_id,
        sent=False,
        verified=False,
        engine=engine,
        method="dry-run",
        target=target.safe_dict(),
        verification={"status": "not_run"},
        dry_run=True,
    )


def send_boss_message_via_browser(
    credential: Credential,
    target: BrowserReplyTarget,
    message: str,
    *,
    engine: BrowserEngine = "auto",
    headless: bool = False,
    timeout_ms: int = 45_000,
    verify_timeout_s: int = 20,
) -> BrowserReplyResult:
    """Send a Boss recruiter chat message through the official web app."""
    if not message.strip():
        raise BrowserReplyError("消息内容不能为空", code="invalid_message")

    engines = ["camoufox", "chrome"] if engine == "auto" else [engine]
    errors: list[str] = []
    for selected_engine in engines:
        try:
            method = _send_once_with_engine(
                credential,
                target,
                message,
                engine=selected_engine,
                headless=headless,
                timeout_ms=timeout_ms,
            )
            verification = verify_latest_message(
                credential,
                target.friend_id,
                message,
                timeout_s=verify_timeout_s,
            )
            return BrowserReplyResult(
                ok=verification.get("matched") is True,
                friend_id=target.friend_id,
                sent=True,
                verified=verification.get("matched") is True,
                engine=selected_engine,
                method=method,
                target=target.safe_dict(),
                verification=verification,
            )
        except BrowserReplyError as exc:
            errors.append(f"{selected_engine}: {exc}")
            if engine != "auto":
                raise

    raise BrowserReplyError("浏览器发送失败: " + " | ".join(errors))


def verify_latest_message(
    credential: Credential,
    friend_id: int,
    expected_message: str,
    *,
    timeout_s: int = 20,
    poll_interval_s: float = 1.5,
) -> dict[str, Any]:
    """Poll Boss latest-message API until the outgoing text is visible."""
    deadline = time.time() + timeout_s
    last_seen: dict[str, Any] = {}

    with BossClient(credential, request_delay=0.2) as client:
        while time.time() <= deadline:
            messages = client.get_boss_last_messages([friend_id])
            last_seen = _extract_latest_message(messages, friend_id)
            text = last_seen.get("text", "")
            if text == expected_message or expected_message in text:
                return {
                    "status": "matched",
                    "matched": True,
                    "last_text": text,
                    "last_time": last_seen.get("last_time", ""),
                }
            time.sleep(poll_interval_s)

    return {
        "status": "not_matched",
        "matched": False,
        "last_text": last_seen.get("text", ""),
        "last_time": last_seen.get("last_time", ""),
    }


def _send_once_with_engine(
    credential: Credential,
    target: BrowserReplyTarget,
    message: str,
    *,
    engine: str,
    headless: bool,
    timeout_ms: int,
) -> str:
    if engine == "camoufox":
        return _send_with_camoufox(credential, target, message, headless=headless, timeout_ms=timeout_ms)
    if engine == "chrome":
        return _send_with_playwright_chrome(credential, target, message, headless=headless, timeout_ms=timeout_ms)
    raise BrowserReplyError(f"不支持的浏览器引擎: {engine}", code="unsupported_browser_engine")


def _send_with_camoufox(
    credential: Credential,
    target: BrowserReplyTarget,
    message: str,
    *,
    headless: bool,
    timeout_ms: int,
) -> str:
    try:
        from camoufox.sync_api import Camoufox
    except ImportError as exc:
        raise BrowserReplyError(
            "camoufox 未安装。安装: pip install 'kabi-boss-cli[browser]'",
            code="browser_backend_missing",
        ) from exc

    try:
        with Camoufox(headless=headless) as browser:
            context = browser.new_context(locale="zh-CN")
            _add_cookies_to_context(context, credential)
            page = context.new_page()
            return _send_from_page(page, target, message, timeout_ms=timeout_ms)
    except BrowserReplyError:
        raise
    except Exception as exc:
        raise BrowserReplyError(
            f"Camoufox 启动或发送失败: {exc}",
            code="browser_engine_failed",
        ) from exc


def _send_with_playwright_chrome(
    credential: Credential,
    target: BrowserReplyTarget,
    message: str,
    *,
    headless: bool,
    timeout_ms: int,
) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserReplyError(
            "playwright 未安装。建议使用 camoufox: pip install 'kabi-boss-cli[browser]'",
            code="browser_backend_missing",
        ) from exc

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                channel="chrome",
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:
            raise BrowserReplyError(f"无法启动 Chrome: {exc}", code="browser_engine_failed") from exc

        try:
            context = browser.new_context(locale="zh-CN")
            context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = window.chrome || { runtime: {} };
                """
            )
            _add_cookies_to_context(context, credential)
            page = context.new_page()
            return _send_from_page(page, target, message, timeout_ms=timeout_ms)
        finally:
            browser.close()


def _add_cookies_to_context(context: Any, credential: Credential) -> None:
    cookies = [
        {
            "name": name,
            "value": value,
            "domain": ".zhipin.com",
            "path": "/",
        }
        for name, value in credential.cookies.items()
        if value
    ]
    if cookies:
        context.add_cookies(cookies)


def _send_from_page(page: Any, target: BrowserReplyTarget, message: str, *, timeout_ms: int) -> str:
    response = page.goto(WEB_BOSS_CHAT_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    status = getattr(response, "status", None)
    if status and status >= 400:
        raise BrowserReplyError(f"Boss Web 打开失败，HTTP {status}", code="browser_page_load_failed")

    try:
        page.wait_for_function(
            """
            () => window.location.href !== 'about:blank'
              && window.iBossRoot
              && window.iBossRoot.chat
              && (
                typeof window.iBossRoot.chat.sendMessage === 'function'
                || (window.top && window.top.mediator && typeof window.top.mediator.publish === 'function')
              )
            """,
            timeout=timeout_ms,
        )
    except Exception as exc:
        current_url = getattr(page, "url", "")
        if current_url == "about:blank":
            raise BrowserReplyError(
                "Boss Web 关闭了自动化页面；请改用 --engine camoufox，或完成浏览器登录后重试",
                code="browser_blocked",
            ) from exc
        body_text = _safe_body_text(page)
        if "登录" in body_text or "扫码" in body_text:
            raise BrowserReplyError("Boss Web 需要重新登录，请先运行 boss login", code="browser_login_required") from exc
        raise BrowserReplyError("Boss Web 聊天环境未就绪", code="browser_chat_not_ready") from exc

    try:
        page.wait_for_function(
            "() => window.iBossRoot.chat.wsConnect && window.iBossRoot.chat.wsConnect()",
            timeout=min(timeout_ms, 20_000),
        )
    except Exception as exc:
        raise BrowserReplyError("Boss WebSocket 未连接，无法确认发送通道可用", code="browser_ws_not_connected") from exc

    result = page.evaluate(
        """
        ({ friendId, friendSource, encryptUid, message }) => {
          const root = window.iBossRoot;
          const user = {
            uid: Number(friendId),
            friendSource: Number(friendSource || 0),
            encryptUid: String(encryptUid || "")
          };
          if (root && root.chat && typeof root.chat.sendMessage === 'function') {
            root.chat.sendMessage(message, 'text', user);
            return { ok: true, method: 'iBossRoot.chat.sendMessage' };
          }
          if (window.top && window.top.mediator && typeof window.top.mediator.publish === 'function') {
            window.top.mediator.publish('SEND_MESSAGE_VUE', {
              text: message,
              geekInfo: {
                geekId: Number(friendId),
                geekSource: Number(friendSource || 0),
                encryptGeekId: String(encryptUid || '')
              }
            });
            return { ok: true, method: 'mediator.SEND_MESSAGE_VUE' };
          }
          return { ok: false, reason: 'send_bridge_missing' };
        }
        """,
        {
            "friendId": target.friend_id,
            "friendSource": target.friend_source,
            "encryptUid": target.encrypt_uid,
            "message": message,
        },
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise BrowserReplyError(f"浏览器发送桥不可用: {result}", code="browser_send_bridge_missing")

    page.wait_for_timeout(2_000)
    return str(result.get("method") or "browser")


def _safe_body_text(page: Any) -> str:
    try:
        return page.locator("body").inner_text(timeout=2_000)
    except Exception:
        return ""


def _extract_latest_message(messages: Any, friend_id: int) -> dict[str, Any]:
    if isinstance(messages, dict):
        messages = messages.get("data") or messages.get("zpData") or messages.get("result") or []
    if not isinstance(messages, list):
        return {}

    for item in messages:
        if int(item.get("uid") or item.get("friendId") or 0) != friend_id:
            continue
        info = item.get("lastMsgInfo") or item.get("lastMessageInfo") or {}
        text = (
            info.get("showText")
            or info.get("text")
            or item.get("lastMsg")
            or item.get("showText")
            or ""
        )
        return {
            "text": str(text),
            "last_time": item.get("lastTime") or item.get("time") or "",
            "raw_type": info.get("type") or item.get("type"),
        }
    return {}
