# -*- coding: utf-8 -*-
import sys
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from bot.config import settings
from bot.database.engine import init_db
from bot.database.seeder import seed_questions_if_needed
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.user import UserTrackerMiddleware
from bot.handlers import onboarding, trainer, exam, payments, referral, admin
from bot.services.logger import setup_logger
from bot.services.scheduler import start_scheduler

logger = logging.getLogger(__name__)

async def on_startup(bot: Bot):
    logger.info("Initializing database...")
    await init_db()
    logger.info("Seeding question database if needed...")
    await seed_questions_if_needed()
    
    logger.info("Starting background lives regeneration scheduler...")
    start_scheduler(bot)

    if settings.USE_WEBHOOK:
        webhook_url = f"{settings.WEBHOOK_DOMAIN}{settings.WEBHOOK_PATH}"
        logger.info(f"Setting webhook to {webhook_url}")
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.WEBHOOK_SECRET,
            drop_pending_updates=True
        )

async def on_shutdown(bot: Bot):
    logger.info("Shutting down bot...")
    if settings.USE_WEBHOOK:
        await bot.delete_webhook()

def main():
    bot = Bot(token=settings.BOT_TOKEN)
    setup_logger(bot)

    # Storage setup
    try:
        from redis.asyncio import Redis
        redis = Redis.from_url(settings.redis_url)
        storage = RedisStorage(redis=redis)
        logger.info("Using RedisStorage for FSM.")
    except Exception as e:
        logger.warning(f"Failed to connect to Redis ({e}), falling back to MemoryStorage.")
        storage = MemoryStorage()

    dp = Dispatcher(storage=storage)

    # Register Middlewares
    dp.update.middleware(DbSessionMiddleware())
    dp.update.middleware(UserTrackerMiddleware())

    # Register Routers
    dp.include_router(onboarding.router)
    dp.include_router(trainer.router)
    dp.include_router(exam.router)
    dp.include_router(payments.router)
    dp.include_router(referral.router)
    dp.include_router(admin.router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    if settings.USE_WEBHOOK:
        logger.info(f"Starting webhook server on port {settings.WEBHOOK_PORT}...")
        app = web.Application()
        
        # Healthcheck endpoint for Docker / Nginx
        async def health_check(request):
            return web.Response(text="OK", status=200)
            
        app.router.add_get("/health", health_check)

        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=settings.WEBHOOK_SECRET
        )
        webhook_requests_handler.register(app, path=settings.WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)

        web.run_app(app, host="0.0.0.0", port=settings.WEBHOOK_PORT)
    else:
        logger.info("Starting long polling mode...")
        import asyncio
        asyncio.run(dp.start_polling(bot))

if __name__ == "__main__":
    main()