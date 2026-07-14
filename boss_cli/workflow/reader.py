"""Read-only Boss API boundary for workflow synchronization."""

from __future__ import annotations

from typing import Any, Protocol


class BossReadGateway(Protocol):
    """The only Boss operations available to the synchronization service."""

    @property
    def request_stats(self) -> dict[str, int | float]: ...

    def get_user_info(self) -> dict[str, Any]: ...

    def get_boss_chatted_jobs(self) -> list[dict[str, Any]]: ...

    def get_boss_friend_list(
        self,
        label_id: int = 0,
        enc_job_id: str = "",
        sort: str = "",
        page: int = 1,
    ) -> dict[str, Any]: ...

    def get_boss_friend_details(self, friend_ids: list[int]) -> dict[str, Any]: ...

    def get_boss_last_messages(self, friend_ids: list[int], src: int = 0) -> list[dict[str, Any]]: ...

    def get_boss_chat_history(
        self,
        gid: int,
        count: int = 20,
        max_msg_id: int = 0,
    ) -> dict[str, Any]: ...

    def get_boss_chat_geek_info(
        self,
        encrypt_geek_id: str,
        security_id: str,
        job_id: int,
    ) -> dict[str, Any]: ...
