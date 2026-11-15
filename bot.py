import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    Defaults,
    MessageHandler,
    filters,
)

import config
import db
import scheduler as deadlines
from handlers import extras, onboarding, submissions, tasks

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("mc_club_bot")


async def post_init(app: Application) -> None:
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(
        deadlines.check_deadlines, "interval", seconds=30, args=[app],
        max_instances=1, coalesce=True,
    )
    sched.start()
    app.bot_data["scheduler"] = sched


async def post_shutdown(app: Application) -> None:
    sched = app.bot_data.get("scheduler")
    if sched:
        sched.shutdown(wait=False)


async def on_error(update, context) -> None:
    log.error("Handler xatosi", exc_info=context.error)


def build_application() -> Application:
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .defaults(Defaults(
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        ))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("id", extras.show_ids))

    if not config.GROUP_ID:
        log.warning("GROUP_ID sozlanmagan: faqat /id ishlaydi. Guruhda /id yozib, ID ni .env ga qo'ying.")
        return app

    group = filters.Chat(config.GROUP_ID)
    private = filters.ChatType.PRIVATE

    # Guruhga qo'shilish / chiqish
    app.add_handler(MessageHandler(group & filters.StatusUpdate.NEW_CHAT_MEMBERS, onboarding.on_new_members))
    app.add_handler(MessageHandler(group & filters.StatusUpdate.LEFT_CHAT_MEMBER, onboarding.on_left_member))
    app.add_handler(CallbackQueryHandler(onboarding.on_agree, pattern=r"^agree:\d+$"))

    # Mentor paneli
    app.add_handler(tasks.new_task_conversation())
    app.add_handler(CommandHandler("vazifalar", tasks.list_tasks))
    app.add_handler(CommandHandler("bekor", tasks.cancel_task))

    # /read, /doc va /uzr
    app.add_handler(CommandHandler(["read", "doc"], extras.read_cmd, filters=group))
    app.add_handler(CommandHandler("uzr", extras.uzr_cmd, filters=private))
    app.add_handler(CommandHandler("start", extras.start, filters=private))

    # Tugmalar
    app.add_handler(CallbackQueryHandler(submissions.on_review, pattern=r"^rev:\d+:(ok|no)$"))
    app.add_handler(CallbackQueryHandler(submissions.on_pick, pattern=r"^pick:\d+$"))
    app.add_handler(CallbackQueryHandler(extras.on_excuse_review, pattern=r"^exc:\d+:(ok|no)$"))
    app.add_handler(CallbackQueryHandler(extras.on_uzr_pick, pattern=r"^uzr:\d+$"))

    # Topshiriqlar (suhbatdan keyin ro'yxatdan o'tadi, shuning uchun /yangi paytida xalaqit bermaydi)
    app.add_handler(MessageHandler(private & ~filters.COMMAND, submissions.private_message))
    app.add_handler(
        MessageHandler(group & ~filters.COMMAND & ~filters.StatusUpdate.ALL, submissions.group_message)
    )
    return app


def main() -> None:
    db.init()
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
