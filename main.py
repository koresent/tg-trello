import asyncio
import html
import logging
import os
import random

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from dotenv import load_dotenv

load_dotenv()

# ================= Configuration =================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", 0))

TRELLO_API_KEY = os.getenv("TRELLO_API_KEY")
TRELLO_API_TOKEN = os.getenv("TRELLO_API_TOKEN")
TRELLO_TODO_LIST_ID = os.getenv("TRELLO_TODO_LIST_ID")
TRELLO_INBOX_LIST_ID = os.getenv("TRELLO_INBOX_LIST_ID")

if not all(
    [
        TELEGRAM_BOT_TOKEN,
        ALLOWED_USER_ID,
        TRELLO_API_KEY,
        TRELLO_API_TOKEN,
        TRELLO_TODO_LIST_ID,
        TRELLO_INBOX_LIST_ID,
    ]
):
    raise ValueError("Missing required environment variables in .env file.")

TIER_1_LABEL = "Tier 1"
TIER_2_LABEL = "Tier 2"
TIER_3_LABEL = "Tier 3"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

background_tasks = set()


def create_background_task(coro):
    """Wraps asyncio.create_task to maintain a strong reference."""
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task


class TrelloClient:
    def __init__(self, api_key: str, api_token: str):
        self.api_key = api_key
        self.api_token = api_token
        self.base_url = "https://api.trello.com/1"
        self.session: aiohttp.ClientSession | None = None

    async def start(self):
        """Initializes the aiohttp session with a proper timeout."""
        timeout = aiohttp.ClientTimeout(total=10)
        self.session = aiohttp.ClientSession(timeout=timeout)

    async def close(self):
        """Closes the aiohttp session."""
        if self.session:
            await self.session.close()

    async def create_card(self, list_id: str, name: str) -> bool:
        """Creates a new card in the specified list."""
        url = f"{self.base_url}/cards"
        params = {
            "key": self.api_key,
            "token": self.api_token,
            "idList": list_id,
            "name": name,
        }
        try:
            async with self.session.post(url, params=params) as response:
                if response.status != 200:
                    logging.error(
                        f"Failed to create card. Trello API returned: {response.status}"
                    )
                    return False
                return True
        except aiohttp.ClientError as e:
            logging.error(f"Network error while creating card: {e}")
            return False

    async def get_cards_by_tiers(
        self, list_id: str, tier1_label: str, tier2_label: str, tier3_label: str
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Fetches all cards from a list and splits them into Tier 1, 2, and 3 pools."""
        url = f"{self.base_url}/lists/{list_id}/cards"
        params = {"key": self.api_key, "token": self.api_token}
        try:
            async with self.session.get(url, params=params) as response:
                if response.status != 200:
                    logging.error(
                        f"Failed to fetch cards. Trello API returned: {response.status}"
                    )
                    return [], [], []
                cards = await response.json()
        except aiohttp.ClientError as e:
            logging.error(f"Network error while fetching tasks: {e}")
            return [], [], []

        tier1_cards = []
        tier2_cards = []
        tier3_cards = []

        for card in cards:
            labels = {label.get("name") for label in card.get("labels", [])}

            if tier1_label in labels:
                tier1_cards.append(card)
            elif tier2_label in labels:
                tier2_cards.append(card)
            elif tier3_label in labels:
                tier3_cards.append(card)

        return tier1_cards, tier2_cards, tier3_cards


# ================= UI & Utils =================


def get_main_menu():
    """Builds the main reply keyboard with a WebApp button."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="Open Trello",
                    web_app=WebAppInfo(url="https://trello.com/"),
                )
            ]
        ],
        resize_keyboard=True,
    )


async def delete_messages_later(messages: list[Message], delay: int):
    """Sleeps for a specified delay and then deletes the provided messages."""
    await asyncio.sleep(delay)
    for msg in messages:
        try:
            await msg.delete()
        except TelegramBadRequest as e:
            logging.warning(
                f"Message {msg.message_id} already deleted or unavailable: {e}"
            )
        except Exception as e:
            logging.error(f"Unexpected error deleting message {msg.message_id}: {e}")


# ================= Routing & Handlers =================

router = Router()
router.message.filter(F.from_user.id == ALLOWED_USER_ID)


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handles the /start command and sets up the keyboard."""
    await message.answer(
        "Trello bot active. Use /roll to get a task or send text to add to Inbox.",
        reply_markup=get_main_menu(),
    )


@router.message(Command("roll"))
async def cmd_roll(message: Message, trello: TrelloClient):
    """Handles the /roll command, applying probability and formatting output."""
    tier1_cards, tier2_cards, tier3_cards = await trello.get_cards_by_tiers(
        TRELLO_TODO_LIST_ID, TIER_1_LABEL, TIER_2_LABEL, TIER_3_LABEL
    )

    if not tier1_cards and not tier2_cards and not tier3_cards:
        sent_msg = await message.answer(
            "📭 All Tiers (1, 2, 3) are empty. No tasks to roll."
        )
        create_background_task(delete_messages_later([message, sent_msg], 1800))
        return

    roll_result = random.randint(1, 100)

    if roll_result <= 70:
        candidates = tier1_cards or tier2_cards or tier3_cards
    else:
        candidates = tier2_cards or tier1_cards or tier3_cards

    selected_card = random.choice(candidates)

    name = selected_card.get("name", "Untitled Task")
    raw_desc = selected_card.get("desc", "").strip()
    url = selected_card.get("shortUrl", "")

    if len(raw_desc) > 3500:
        raw_desc = raw_desc[:3500] + "\n\n[...Truncated...]"

    name_html = html.escape(name)
    desc_html = html.escape(raw_desc)

    response_text = f'🎯 <b><a href="{url}">{name_html}</a></b>'
    if desc_html:
        response_text += f"\n\n{desc_html}"

    sent_msg = await message.answer(response_text, parse_mode="HTML")

    create_background_task(delete_messages_later([message, sent_msg], 1800))


@router.message(F.text)
async def handle_text_message(message: Message, trello: TrelloClient):
    """Handles plain text messages to create an Inbox task."""
    success = await trello.create_card(TRELLO_INBOX_LIST_ID, message.text)

    if success:
        sent_msg = await message.answer("✅ Card successfully created")
        create_background_task(delete_messages_later([message, sent_msg], 3))
    else:
        await message.answer("❌ Failed to create card")


# ================= Main Entrypoint =================


async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    trello_client = TrelloClient(TRELLO_API_KEY, TRELLO_API_TOKEN)
    await trello_client.start()

    dp["trello"] = trello_client
    dp.include_router(router)

    try:
        logging.info("Starting bot...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await trello_client.close()
        for task in background_tasks:
            task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")
