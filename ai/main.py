import datetime as dt
import logging
import threading

import pytz
from telegram.ext import Application, MessageHandler, CommandHandler, filters
from telegram.request import HTTPXRequest

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

from config import BOT_TOKEN
from handlers.commands import start_command
from handlers.messages import handle_message
from handlers.photos import handle_photo
from handlers.scheduled_posts import post_stupids_of_day, post_daily_fact
from utils.health_server import run_health_server


def main():
    threading.Thread(target=run_health_server, daemon=True).start()

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    # Schedule daily channel posts
    eat = pytz.timezone("Africa/Addis_Ababa")
    if app.job_queue:
        app.job_queue.run_daily(post_stupids_of_day, time=dt.time(20, 0,  tzinfo=eat))
        app.job_queue.run_daily(post_daily_fact,     time=dt.time(20, 30, tzinfo=eat))
        logger.info("Daily posts scheduled: 8:00 PM and 8:30 PM EAT")
    else:
        logger.warning("JobQueue unavailable — install python-telegram-bot[job-queue]")

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Dex is online.")
    app.run_polling(drop_pending_updates=True, timeout=30)


if __name__ == "__main__":
    main()
