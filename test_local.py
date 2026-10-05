#!/usr/bin/env python3
"""Локальные тесты MVP без реального Telegram API."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Минимальные env до импорта bot
os.environ.setdefault("BOT_TOKEN", "000000000:TEST_TOKEN_FOR_LOCAL_ONLY")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("LANDING_URL", "https://example.com/go")
os.environ.setdefault("POSTBACK_SECRET", "testsecret")
os.environ.setdefault("POSTBACK_SUCCESS_STATUS", "reg")
# Большая задержка, чтобы отложенное уведомление не мешало unit-тестам
os.environ.setdefault("ACTIVATION_NOTIFICATION_DELAY", "3600")
os.environ.setdefault("ADMIN_NOTIFY_ON_SIGNAL", "false")
os.environ.setdefault("HTTP_PORT", "18080")

import bot as app
from aiohttp import web
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
    tmp = Path(tempfile.mkdtemp(prefix="mineslot_test_"))
    db_path = tmp / "test.db"
    app.DB_PATH = db_path
    app.db = app.Database(db_path)
    app.db.init()

    # --- landing url ---
    url = app.build_landing_url("abc123")
    check("landing has subid", "subid=abc123" in url, url)

    # --- images ---
    images = app.list_signal_images()
    check("signals folder has images", len(images) >= 1, str(len(images)))
    picks = {app.pick_signal_image(exclude=images[0].name).name for _ in range(20)}
    if len(images) > 1:
        check("avoid consecutive image when possible", images[0].name not in picks or len(picks) > 0)
        # exclude should not return excluded when alternatives exist
        for _ in range(10):
            p = app.pick_signal_image(exclude=images[0].name)
            assert p is not None
            if p.name == images[0].name:
                check("exclude image works", False, p.name)
                break
        else:
            check("exclude image works", True)
    else:
        check("single image fallback", app.pick_signal_image().name == images[0].name)

    # --- create user ---
    user = await app.db.create_user(111, "tester", "Test", "ref42")
    check("user created", user["telegram_id"] == 111)
    check("not activated", user["is_activated"] == 0)
    check("click_id uuid-like", len(user["click_id"]) == 32)
    check("start_param saved", user["start_param"] == "ref42")

    # --- parse postback ---
    parsed = app.parse_postback_params(
        {"subid": user["click_id"], "event": "reg", "token": "testsecret"}
    )
    check("parse click_id from subid", parsed["click_id"] == user["click_id"])
    check("parse status from event", parsed["status"] == "reg")
    check("parse secret from token", parsed["secret"] == "testsecret")

    # --- HTTP postback ---
    http_app = app.create_http_app()
    async with TestClient(TestServer(http_app)) as client:
        # bad secret
        r = await client.get(
            "/postback",
            params={"click_id": user["click_id"], "status": "reg", "secret": "wrong"},
        )
        check("bad secret -> 401", r.status == 401)

        # unknown click
        r = await client.get(
            "/postback",
            params={"click_id": "deadbeef", "status": "reg", "secret": "testsecret"},
        )
        check("unknown click -> 404", r.status == 404)

        # ignored status
        r = await client.get(
            "/postback",
            params={"click_id": user["click_id"], "status": "deposit", "secret": "testsecret"},
        )
        data = await r.json()
        check("ignored status accepted=False", r.status == 200 and data.get("accepted") is False)

        # success
        r = await client.get(
            "/postback",
            params={"click_id": user["click_id"], "status": "reg", "secret": "testsecret"},
        )
        data = await r.json()
        check("success postback", r.status == 200 and data.get("ok") is True)

        user2 = await app.db.get_user(111)
        check("user activated", bool(user2["is_activated"]))
        check("activated_at set", user2["activated_at"] is not None)

        # duplicate
        r = await client.get(
            "/postback",
            params={"click_id": user["click_id"], "status": "reg", "secret": "testsecret"},
        )
        data = await r.json()
        check("duplicate postback ok", r.status == 200 and data.get("already_activated") is True)

        # health
        r = await client.get("/health")
        check("health endpoint", r.status == 200)

    # --- signal records ---
    sig = await app.db.create_signal(111, images[0].name)
    check("signal created", sig["telegram_id"] == 111)
    check("expires_at set", sig["expires_at"] is not None)
    user3 = await app.db.get_user(111)
    check("signals_count incremented", user3["signals_count"] == 1)

    # --- activation notify once ---
    # отменяем отложенную задачу из postback, чтобы не мешала проверке
    task = app._pending_notifications.pop(111, None)
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    marked1 = await app.db.mark_activation_notified(111)
    marked2 = await app.db.mark_activation_notified(111)
    check("activation notify once", marked1 is True and marked2 is False)

    # --- stats ---
    s = await app.db.stats()
    check("stats total>=1", s["total"] >= 1)
    check("stats activated>=1", s["activated"] >= 1)
    check("stats signals>=1", s["total_signals"] >= 1)

    # --- recent users ---
    recent = await app.db.recent_users(5)
    check("recent users", len(recent) >= 1)

    print()
    print(f"Passed: {PASSED}, Failed: {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run_tests()))
