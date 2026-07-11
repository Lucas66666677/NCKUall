from __future__ import annotations

import json
import logging
from os import getenv

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.realtime.notifications import (
    notification_manager,
    normalize_topics,
)


router = APIRouter(tags=["realtime"])
logger = logging.getLogger(__name__)
MAX_CLIENT_MESSAGE_BYTES = 4096


def _allowed_origins() -> set[str]:
    configured = getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    return {
        origin.strip().rstrip("/")
        for origin in configured.split(",")
        if origin.strip()
    }


@router.websocket("/ws/notifications")
async def websocket_notifications(
    websocket: WebSocket,
    department_id: str | None = Query(default=None, max_length=120),
) -> None:
    origin = websocket.headers.get("origin")
    if origin and origin.rstrip("/") not in _allowed_origins():
        await websocket.close(code=1008, reason="origin_not_allowed")
        return

    initial_topics = {"all"}
    if department_id:
        initial_topics.add(f"department:{department_id}")
    connection = await notification_manager.connect(
        websocket,
        topics=initial_topics,
    )

    try:
        await notification_manager.send(
            connection,
            {
                "event": "connected",
                "topics": sorted(connection.topics),
            },
        )
        while True:
            raw_message = await websocket.receive_text()
            if len(raw_message.encode("utf-8")) > MAX_CLIENT_MESSAGE_BYTES:
                await websocket.close(
                    code=1009,
                    reason="message_too_large",
                )
                return

            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                await notification_manager.send(
                    connection,
                    {"event": "error", "detail": "invalid_json"},
                )
                continue

            if not isinstance(message, dict):
                await notification_manager.send(
                    connection,
                    {"event": "error", "detail": "invalid_message"},
                )
                continue

            action = message.get("action")
            if action == "ping":
                await notification_manager.send(
                    connection,
                    {"event": "pong"},
                )
                continue

            if action == "subscribe":
                raw_topics = message.get("topics", [])
                if not isinstance(raw_topics, list):
                    await notification_manager.send(
                        connection,
                        {"event": "error", "detail": "invalid_topics"},
                    )
                    continue
                topics = await notification_manager.replace_topics(
                    websocket,
                    normalize_topics(
                        {
                            str(topic)
                            for topic in raw_topics
                        }
                    ),
                )
                await notification_manager.send(
                    connection,
                    {
                        "event": "subscribed",
                        "topics": sorted(topics),
                    },
                )
                continue

            await notification_manager.send(
                connection,
                {"event": "error", "detail": "unsupported_action"},
            )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "websocket_notification_connection_failed",
            extra={"department_id": department_id},
        )
        try:
            await websocket.close(code=1011, reason="internal_error")
        except Exception:
            pass
    finally:
        await notification_manager.disconnect(websocket)
