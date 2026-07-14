"""Curated outbound template catalogs for continuous recruiting automation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .db import WorkflowStore

CATALOG_VERSION = "automotive-wechat-v1"


@dataclass(frozen=True)
class CuratedTemplate:
    """An immutable approved message and the conditions for selecting it."""

    name: str
    body: str
    selection_guidance: str


AUTOMOTIVE_WECHAT_TEMPLATES = (
    CuratedTemplate(
        name="wechat_agreement_direct",
        body="OK，好的，交换个微信。",
        selection_guidance=(
            "Use when the candidate explicitly agrees to exchange WeChat or gives a clear affirmative "
            "response to an earlier contact-exchange suggestion. Do not use when they are asking what the "
            "business is."
        ),
    ),
    CuratedTemplate(
        name="wechat_agreement_warm",
        body="好的，可以的，我们交换个微信吧。",
        selection_guidance=(
            "Use when the candidate explicitly says communication can continue on WeChat and a warm, "
            "natural acknowledgement fits. Do not use for questions about the role or business."
        ),
    ),
    CuratedTemplate(
        name="wechat_candidate_requested",
        body="可以，方便的话我们交换一下微信。",
        selection_guidance=(
            "Use when the candidate asks to add or exchange WeChat, or directly asks for another contact "
            "channel. Do not use when the candidate has not shown willingness to move to WeChat."
        ),
    ),
    CuratedTemplate(
        name="automotive_warranty_intro_direct",
        body="我们在做汽车延长保修服务，感兴趣的话，交换一个微信。",
        selection_guidance=(
            "Use when the candidate asks what the company, business, or opportunity does and has not yet "
            "received the automotive extended-warranty explanation."
        ),
    ),
    CuratedTemplate(
        name="automotive_warranty_intro_conversational",
        body="我们主要做汽车延长保修服务，如果你感兴趣，可以交换微信进一步沟通。",
        selection_guidance=(
            "Use when the candidate asks for a concise business introduction or says they need more context "
            "before continuing. Avoid if the same explanation was already sent."
        ),
    ),
    CuratedTemplate(
        name="automotive_warranty_role_context",
        body="这个岗位与汽车延长保修服务相关，如果你想进一步了解，我们可以交换微信详聊。",
        selection_guidance=(
            "Use when the candidate specifically asks what the role or work is related to, and an invitation "
            "to discuss the automotive extended-warranty work on WeChat is appropriate."
        ),
    ),
)


def install_automotive_wechat_templates(
    store: WorkflowStore,
    *,
    retire_existing: bool = False,
    approved_by: str = "curated_catalog",
) -> dict[str, Any]:
    """Install and approve the automotive/WeChat catalog without changing its text."""
    desired = {(template.name, CATALOG_VERSION) for template in AUTOMOTIVE_WECHAT_TEMPLATES}
    installed: list[dict[str, Any]] = []
    retired: list[int] = []

    with store.transaction():
        if retire_existing:
            for existing in store.list_active_templates():
                key = (str(existing["name"]), str(existing["version"]))
                if key not in desired:
                    store.retire_template(int(existing["id"]))
                    retired.append(int(existing["id"]))

        for template in AUTOMOTIVE_WECHAT_TEMPLATES:
            existing = store.get_template_version(name=template.name, version=CATALOG_VERSION)
            if existing is not None:
                if existing["body"] != template.body or existing["selection_guidance"] != template.selection_guidance:
                    raise ValueError(
                        f"Template {template.name}:{CATALOG_VERSION} differs from the immutable catalog"
                    )
                template_id = int(existing["id"])
                if not existing["approved"] or not existing["active"] or existing["retired_at"] is not None:
                    store.set_template_approval(template_id, approved=True, approved_by=approved_by)
                status = "existing"
            else:
                template_id = store.upsert_template(
                    name=template.name,
                    version=CATALOG_VERSION,
                    body=template.body,
                    selection_guidance=template.selection_guidance,
                )
                persisted = store.get_template(template_id)
                if persisted is None or persisted["selection_guidance"] != template.selection_guidance:
                    raise ValueError(f"Template guidance for {template.name} was altered during persistence")
                store.set_template_approval(template_id, approved=True, approved_by=approved_by)
                status = "installed"
            installed.append({"id": template_id, "name": template.name, "status": status})

        store.append_event(
            event_type="template_catalog_installed",
            summary=f"Installed approved template catalog {CATALOG_VERSION}",
            details={
                "catalog_version": CATALOG_VERSION,
                "template_ids": [item["id"] for item in installed],
                "retired_template_ids": retired,
            },
        )

    return {
        "catalog_version": CATALOG_VERSION,
        "templates": installed,
        "retired_template_ids": retired,
    }
