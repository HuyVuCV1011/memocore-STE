from __future__ import annotations

from copy import deepcopy
from typing import Any

from telegram import Update
from telegram.ext import ApplicationBuilder


MESSAGE_UPDATE: dict[str, Any] = {
    "update_id": 100001,
    "message": {
        "message_id": 501,
        "from": {
            "id": 9001,
            "is_bot": False,
            "first_name": "Huy",
            "username": "huyvu",
            "language_code": "vi",
        },
        "chat": {
            "id": 9001,
            "first_name": "Huy",
            "username": "huyvu",
            "type": "private",
        },
        "date": 1780790400,
        "text": "Nhắc tôi 7h sáng mai họp với Alex",
    },
}

COMMAND_UPDATE: dict[str, Any] = {
    "update_id": 100002,
    "message": {
        "message_id": 502,
        "from": {
            "id": 9001,
            "is_bot": False,
            "first_name": "Huy",
            "username": "huyvu",
            "language_code": "vi",
        },
        "chat": {
            "id": 9001,
            "first_name": "Huy",
            "username": "huyvu",
            "type": "private",
        },
        "date": 1780790401,
        "text": "/today",
        "entities": [{"offset": 0, "length": 6, "type": "bot_command"}],
    },
}

START_UPDATE: dict[str, Any] = {
    "update_id": 100003,
    "message": {
        "message_id": 503,
        "from": {
            "id": 9001,
            "is_bot": False,
            "first_name": "Huy",
            "username": "huyvu",
            "language_code": "vi",
        },
        "chat": {
            "id": 9001,
            "first_name": "Huy",
            "username": "huyvu",
            "type": "private",
        },
        "date": 1780790402,
        "text": "/start",
        "entities": [{"offset": 0, "length": 6, "type": "bot_command"}],
    },
}

EDITED_MESSAGE_UPDATE: dict[str, Any] = {
    "update_id": 100004,
    "edited_message": {
        "message_id": 504,
        "from": {
            "id": 9001,
            "is_bot": False,
            "first_name": "Huy",
            "username": "huyvu",
            "language_code": "vi",
        },
        "chat": {
            "id": 9001,
            "first_name": "Huy",
            "username": "huyvu",
            "type": "private",
        },
        "date": 1780790403,
        "edit_date": 1780790440,
        "text": "Nhắc tôi 8h sáng mai họp với Alex",
    },
}

CALLBACK_QUERY_UPDATE: dict[str, Any] = {
    "update_id": 100005,
    "callback_query": {
        "id": "callback-1",
        "from": {
            "id": 9001,
            "is_bot": False,
            "first_name": "Huy",
            "username": "huyvu",
            "language_code": "vi",
        },
        "message": {
            "message_id": 505,
            "from": {
                "id": 42,
                "is_bot": True,
                "first_name": "MemoCore",
                "username": "memocore_bot",
            },
            "chat": {
                "id": 9001,
                "first_name": "Huy",
                "username": "huyvu",
                "type": "private",
            },
            "date": 1780790404,
            "text": "Choose an action",
        },
        "chat_instance": "chat-instance-1",
        "data": "confirm:reminder:501",
    },
}

DOCUMENT_UPDATE: dict[str, Any] = {
    "update_id": 100006,
    "message": {
        "message_id": 506,
        "from": {
            "id": 9001,
            "is_bot": False,
            "first_name": "Huy",
            "username": "huyvu",
            "language_code": "vi",
        },
        "chat": {
            "id": 9001,
            "first_name": "Huy",
            "username": "huyvu",
            "type": "private",
        },
        "date": 1780790405,
        "document": {
            "file_name": "brief.txt",
            "mime_type": "text/plain",
            "file_id": "doc-file-id",
            "file_unique_id": "doc-file-unique-id",
            "file_size": 128,
        },
        "caption": "Tài liệu họp với Alex",
    },
}

VOICE_UPDATE: dict[str, Any] = {
    "update_id": 100007,
    "message": {
        "message_id": 507,
        "from": {
            "id": 9001,
            "is_bot": False,
            "first_name": "Huy",
            "username": "huyvu",
            "language_code": "vi",
        },
        "chat": {
            "id": 9001,
            "first_name": "Huy",
            "username": "huyvu",
            "type": "private",
        },
        "date": 1780790406,
        "voice": {
            "duration": 5,
            "mime_type": "audio/ogg",
            "file_id": "voice-file-id",
            "file_unique_id": "voice-file-unique-id",
            "file_size": 2048,
        },
    },
}


def copy_update(update_dict: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(update_dict)


def build_update(update_dict: dict[str, Any]) -> Update:
    app = ApplicationBuilder().token("test").build()
    return Update.de_json(deepcopy(update_dict), app.bot)
