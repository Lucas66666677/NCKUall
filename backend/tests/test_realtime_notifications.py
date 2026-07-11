from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket

from app.realtime.notifications import (
    ConnectionManager,
    NotificationBroker,
    NotificationPayload,
    normalize_topics,
)


pytestmark = pytest.mark.asyncio


def make_websocket() -> WebSocket:
    websocket = AsyncMock()
    websocket.accept = AsyncMock()
    websocket.send_json = AsyncMock()
    websocket.close = AsyncMock()
    return cast(WebSocket, cast(Any, websocket))


async def test_topic_broadcast_only_reaches_matching_subscribers() -> None:
    manager = ConnectionManager()
    photonics_socket = make_websocket()
    electrical_socket = make_websocket()
    campus_socket = make_websocket()

    await manager.connect(
        photonics_socket,
        topics={"department:DPS"},
    )
    await manager.connect(
        electrical_socket,
        topics={"department:EE"},
    )
    await manager.connect(campus_socket)

    delivered = await manager.broadcast(
        NotificationPayload(
            kind="review.approved",
            topic="department:DPS",
            title="光電系熱門評論",
            summary="新評論已通過審核。",
            href="/life#review-test",
        )
    )

    assert delivered == 1
    cast(AsyncMock, photonics_socket.send_json).assert_awaited_once()
    cast(AsyncMock, electrical_socket.send_json).assert_not_awaited()
    cast(AsyncMock, campus_socket.send_json).assert_not_awaited()


async def test_campus_broadcast_reaches_every_connection() -> None:
    manager = ConnectionManager()
    first_socket = make_websocket()
    second_socket = make_websocket()
    await manager.connect(first_socket, topics={"department:DPS"})
    await manager.connect(second_socket, topics={"department:EE"})

    delivered = await manager.broadcast(
        NotificationPayload(
            kind="event.created",
            topic="all",
            title="單車節售票",
            summary="活動資訊已更新。",
            href="/events#event-test",
        )
    )

    assert delivered == 2
    cast(AsyncMock, first_socket.send_json).assert_awaited_once()
    cast(AsyncMock, second_socket.send_json).assert_awaited_once()


async def test_broker_falls_back_to_local_fanout_without_redis() -> None:
    manager = ConnectionManager()
    broker = NotificationBroker(manager)
    websocket = make_websocket()
    await manager.connect(websocket)

    await broker.publish(
        NotificationPayload(
            kind="event.created",
            title="舞會售票",
            summary="售票連結已公開。",
            href="/events#event-dance",
        )
    )

    cast(AsyncMock, websocket.send_json).assert_awaited_once()


async def test_invalid_topics_are_discarded_and_all_is_always_present() -> None:
    topics = normalize_topics(
        {
            "department:DPS",
            "department:../../admin",
            "private:user-123",
        }
    )

    assert topics == {"all", "department:DPS"}
