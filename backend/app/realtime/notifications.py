from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import WebSocket
from pydantic import BaseModel, Field
from redis.asyncio import Redis


logger = logging.getLogger(__name__)
REDIS_CHANNEL = "nckuall:notifications:v1"
MAX_TOPICS_PER_CONNECTION = 8
SEND_TIMEOUT_SECONDS = 2.0
TOPIC_PATTERN = re.compile(r"^department:[A-Za-z0-9_-]{1,120}$")


class NotificationPayload(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Literal["review.approved", "event.created"]
    topic: str = "all"
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=500)
    href: str = Field(pattern=r"^/[^/].*")
    resource_id: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )


@dataclass(eq=False)
class ClientConnection:
    websocket: WebSocket
    topics: set[str] = field(default_factory=lambda: {"all"})
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def normalize_topics(topics: list[str] | set[str]) -> set[str]:
    normalized = {"all"}
    for raw_topic in topics:
        topic = raw_topic.strip()
        if topic == "all" or TOPIC_PATTERN.fullmatch(topic):
            normalized.add(topic)
        if len(normalized) >= MAX_TOPICS_PER_CONNECTION:
            break
    return normalized


class ConnectionManager:
    """Manage local worker connections and topic subscriptions."""

    def __init__(self) -> None:
        self._connections: dict[int, ClientConnection] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        *,
        topics: set[str] | None = None,
    ) -> ClientConnection:
        await websocket.accept()
        connection = ClientConnection(
            websocket=websocket,
            topics=normalize_topics(topics or {"all"}),
        )
        async with self._lock:
            self._connections[id(websocket)] = connection
        return connection

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.pop(id(websocket), None)

    async def replace_topics(
        self,
        websocket: WebSocket,
        topics: set[str],
    ) -> set[str]:
        normalized = normalize_topics(topics)
        async with self._lock:
            connection = self._connections.get(id(websocket))
            if connection is not None:
                connection.topics = normalized
        return normalized

    async def send(
        self,
        connection: ClientConnection,
        payload: dict[str, Any],
    ) -> None:
        async with connection.send_lock:
            await connection.websocket.send_json(payload)

    async def broadcast(
        self,
        notification: NotificationPayload,
    ) -> int:
        async with self._lock:
            recipients = [
                connection
                for connection in self._connections.values()
                if notification.topic == "all"
                or notification.topic in connection.topics
            ]

        envelope = {
            "event": "notification",
            "data": notification.model_dump(mode="json"),
        }
        results = await asyncio.gather(
            *[
                asyncio.wait_for(
                    self.send(connection, envelope),
                    timeout=SEND_TIMEOUT_SECONDS,
                )
                for connection in recipients
            ],
            return_exceptions=True,
        )

        delivered = 0
        for connection, result in zip(
            recipients,
            results,
            strict=True,
        ):
            if isinstance(result, BaseException):
                await self.disconnect(connection.websocket)
                with suppress(Exception):
                    await connection.websocket.close(code=1011)
            else:
                delivered += 1
        return delivered

    @property
    def connection_count(self) -> int:
        return len(self._connections)


class NotificationBroker:
    """Fan out notifications across workers through Redis Pub/Sub."""

    def __init__(self, manager: ConnectionManager) -> None:
        self.manager = manager
        self._redis: Redis | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self, redis_client: Redis | None) -> None:
        self._redis = redis_client
        self._stopping.clear()
        if redis_client is not None:
            self._listener_task = asyncio.create_task(
                self._listen(),
                name="notification-redis-listener",
            )

    async def stop(self) -> None:
        self._stopping.set()
        if self._listener_task is not None:
            self._listener_task.cancel()
            await asyncio.gather(
                self._listener_task,
                return_exceptions=True,
            )
            self._listener_task = None
        self._redis = None

    async def publish(
        self,
        notification: NotificationPayload,
    ) -> None:
        if self._redis is not None:
            try:
                subscriber_count = await self._redis.publish(
                    REDIS_CHANNEL,
                    notification.model_dump_json(),
                )
                if subscriber_count > 0:
                    return
            except Exception:
                logger.exception(
                    "notification_redis_publish_failed",
                    extra={"notification_kind": notification.kind},
                )

        await self.manager.broadcast(notification)

    async def _listen(self) -> None:
        if self._redis is None:
            return

        backoff_seconds = 0.25
        while not self._stopping.is_set():
            try:
                async with self._redis.pubsub(
                    ignore_subscribe_messages=True
                ) as pubsub:
                    await pubsub.subscribe(REDIS_CHANNEL)
                    backoff_seconds = 0.25
                    while not self._stopping.is_set():
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True,
                            timeout=1.0,
                        )
                        if message is None:
                            continue
                        raw_data = message.get("data")
                        if isinstance(raw_data, bytes):
                            raw_data = raw_data.decode("utf-8")
                        notification = NotificationPayload.model_validate_json(
                            raw_data
                        )
                        await self.manager.broadcast(notification)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "notification_redis_listener_failed"
                )
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 10.0)


async def publish_notifications_via_redis(
    notifications: list[NotificationPayload],
    *,
    redis_url: str | None,
) -> int:
    """Publish from standalone crawlers that do not run FastAPI."""

    if not redis_url or not notifications:
        return 0

    redis_client = Redis.from_url(
        redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    try:
        for notification in notifications:
            await redis_client.publish(
                REDIS_CHANNEL,
                notification.model_dump_json(),
            )
        return len(notifications)
    finally:
        await redis_client.aclose()


notification_manager = ConnectionManager()
notification_broker = NotificationBroker(notification_manager)
