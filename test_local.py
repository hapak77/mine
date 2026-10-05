#!/usr/bin/env python3
"""Локальные тесты связки Telegram → 1win sub1 → postback → активация."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Env ДО импорта bot
os.environ["BOT_TOKEN"] = "000000000:TEST_TOKEN_FOR_LOCAL_ONLY"
os.environ["ADMIN_ID"] = "1"
os.environ["LANDING_URL"] = (
    "https://r1wtvmb.life/casino/list?open=register&p=fiyw"
)
os.environ["PUBLIC_BASE_URL"] = "https://bot.example.com"
os.environ["POSTBACK_SECRET"] = "testsecret"
os.environ["POSTBACK_SUCCESS_EVENT"] = "registration"
os.environ["POSTBACK_CLICK_ID_PARAM"] = "click_id"
os.environ["POSTBACK_EVENT_PARAM"] = "event"
os.environ["ACTIVATION_NOTIFICATION_DELAY"] = "3600"
os.environ["ADMIN_NOTIFY_ON_NEW_USER"] = "false"
os.environ["ADMIN_NOTIFY_ON_ACTIVATION"] = "false"
os.environ["ADMIN_NOTIFY_ON_SIGNAL"] = "false"
os.environ["HTTP_PORT"] = "18080"

import bot as app
from aiohttp.test_utils import TestClient, TestServer

PASSED = 0
FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  OK  {name}")
    else:
        FAILED += 1
        print(f" FAIL {name} {detail}")


async def run_tests() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mineslot_1win_"))
    db_path = tmp / "test.db"
    app.DB_PATH = db_path
    app.db = app.Database(db_path)
    app.db.init()

    # 1. PUBLIC_BASE_URL validation
    check("public url ok", app.validate_public_base_url("https://bot.example.com") is None)
    check(
        "localhost rejected",
        app.validate_public_base_url("https://localhost") is not None,
    )
    check(
        "empty public url rejected",
        app.validate_public_base_url("") is not None,
    )
    check(
        "http rejected",
        app.validate_public_base_url("http://bot.example.com") is not None,
    )

    # 2–4. create user + click_id + landing with sub1
    user = await app.db.create_user(111, "tester", "Test", "promo1")
    check("user created", user["telegram_id"] == 111)
    check("not activated on create", int(user["is_activated"]) == 0)
    check("click_id uuid-like", len(user["click_id"]) == 32)
    check("start_param saved", user["start_param"] == "promo1")

    landing = app.build_landing_url(user["click_id"])
    qs = parse_qs(urlparse(landing).query)
    check("landing has open=register", qs.get("open") == ["register"])
    check("landing has p=fiyw", qs.get("p") == ["fiyw"])
    check("landing has sub1=click_id", qs.get("sub1") == [user["click_id"]])
    check("landing does NOT use telegram id as sub1", qs.get("sub1") != ["111"])

    # 5. parse postback
    parsed = app.parse_postback_params(
        {
            "click_id": user["click_id"],
            "event": "registration",
            "secret": "testsecret",
        }
    )
    check("parse click_id", parsed["click_id"] == user["click_id"])
    check("parse event registration", parsed["event"] == "registration")

    # alias sub1 → click_id
    parsed2 = app.parse_postback_params({"sub1": user["click_id"], "event": "registration"})
    check("alias sub1 as click_id", parsed2["click_id"] == user["click_id"])

    images = app.list_signal_images()
    check("signals present", len(images) >= 1)

    http_app = app.create_http_app()
    async with TestClient(TestServer(http_app)) as client:
        # 15. bad secret
        r = await client.get(
            "/postback",
            params={
                "click_id": user["click_id"],
                "event": "registration",
                "secret": "wrong",
            },
        )
        check("bad secret -> 401", r.status == 401)

        # 15. unknown click_id
        r = await client.get(
            "/postback",
            params={
                "click_id": "deadbeefdeadbeefdeadbeefdeadbeef",
                "event": "registration",
                "secret": "testsecret",
            },
        )
        check("unknown click_id -> 404", r.status == 404)

        # 16. wrong event (deposit/revenue must NOT activate)
        for bad_event in ("deposit", "revenue", "income", "ftd"):
            r = await client.get(
                "/postback",
                params={
                    "click_id": user["click_id"],
                    "event": bad_event,
                    "secret": "testsecret",
                },
            )
            data = await r.json()
            check(
                f"event={bad_event} ignored",
                r.status == 200 and data.get("accepted") is False,
                str(data),
            )

        user_mid = await app.db.get_user(111)
        check(
            "still not activated after non-registration events",
            int(user_mid["is_activated"]) == 0,
        )

        # 5–7. successful registration postback
        r = await client.get(
            "/postback",
            params={
                "click_id": user["click_id"],
                "event": "registration",
                "secret": "testsecret",
            },
        )
        data = await r.json()
        check("registration postback ok", r.status == 200 and data.get("ok") is True)
        check("activated flag in response", data.get("activated") is True)

        user2 = await app.db.get_user(111)
        check("user activated in DB", int(user2["is_activated"]) == 1)
        check("activated_at set", user2["activated_at"] is not None)

        # 9. duplicate postback idempotency
        r = await client.get(
            "/postback",
            params={
                "click_id": user["click_id"],
                "event": "registration",
                "secret": "testsecret",
            },
        )
        data = await r.json()
        check(
            "duplicate postback already_activated",
            r.status == 200 and data.get("already_activated") is True,
        )

        # signals_count must NOT increase from postback
        user3 = await app.db.get_user(111)
        check("signals_count still 0 after postbacks", int(user3["signals_count"]) == 0)

        r = await client.get("/health")
        check("health endpoint", r.status == 200)

    # 8. activation notification once
    task = app._pending_notifications.pop(111, None)
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    marked1 = await app.db.mark_activation_notification_sent(111)
    marked2 = await app.db.mark_activation_notification_sent(111)
    check("activation_notification_sent once", marked1 is True and marked2 is False)

    # 11–12. signal records + no consecutive same image
    sig1 = await app.db.create_signal(111, images[0].name)
    check("signal created", sig1["telegram_id"] == 111)
    check("expires_at set", sig1["expires_at"] is not None)
    user4 = await app.db.get_user(111)
    check("signals_count incremented", int(user4["signals_count"]) == 1)

    if len(images) > 1:
        for _ in range(15):
            p = app.pick_signal_image(exclude=images[0].name)
            assert p is not None
            if p.name == images[0].name:
                check("exclude previous image", False, p.name)
                break
        else:
            check("exclude previous image", True)
    else:
        check("single image fallback", True)

    # 14. stats
    s = await app.db.stats()
    check("stats total>=1", s["total"] >= 1)
    check("stats activated>=1", s["activated"] >= 1)
    check("stats signals>=1", s["total_signals"] >= 1)

    # 14. /users data
    recent = await app.db.recent_users(5)
    check("recent users", len(recent) >= 1 and recent[0]["click_id"] == user["click_id"])

    # postbacks table written
    pb = await app.db.fetchone("SELECT COUNT(*) AS c FROM postbacks")
    check("postbacks logged", int(pb["c"]) >= 3)

    print()
    print(f"Passed: {PASSED}, Failed: {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run_tests()))
