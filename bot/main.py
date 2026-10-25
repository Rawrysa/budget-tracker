import logging
import os

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

import anthropic
from agent import BudgetAgent, get_or_create_agent_id, get_or_create_vault_id, get_or_create_environment_id
from handlers import handle_text, handle_voice, handle_photo, handle_receipt_confirmation

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
MCP_HEALTH_URL = os.getenv("MCP_HEALTH_URL", "http://localhost:8000/health")
MCP_BASE = os.getenv("MCP_BASE_URL", "http://localhost:8000/mcp")
MCP_TOKEN = os.getenv("BUDGET_MCP_TOKEN", "")


async def wait_for_mcp(retries: int = 15, delay: float = 2.0) -> None:
    """Poll MCP server health endpoint until ready."""
    logger.info("Waiting for MCP server at %s ...", MCP_HEALTH_URL)
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(MCP_HEALTH_URL)
                if resp.status_code == 200:
                    logger.info("MCP server is healthy.")
                    return
        except Exception as e:
            logger.warning("MCP not ready (attempt %d/%d): %s", attempt, retries, e)
        await asyncio.sleep(delay)
    raise RuntimeError(f"MCP server did not become healthy after {retries} attempts.")


def make_handlers(agent: BudgetAgent):
    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get("pending_receipt_items"):
            await handle_receipt_confirmation(update, context, agent)
        else:
            await handle_text(update, context, agent)

    async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_voice(update, context, agent)

    async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_photo(update, context, agent)

    return text_handler, voice_handler, photo_handler


async def post_init(app: Application) -> None:
    await wait_for_mcp()

    anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    vault_id = get_or_create_vault_id(anthropic_client)
    environment_id = get_or_create_environment_id(anthropic_client)
    agent_id = get_or_create_agent_id(anthropic_client)
    agent = BudgetAgent(agent_id, vault_id, environment_id)

    text_handler, voice_handler, photo_handler = make_handlers(agent)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CommandHandler("start", text_handler))
    app.add_handler(CommandHandler("help", text_handler))
    app.add_handler(CommandHandler("clear", text_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    logger.info("Bot ready.")


if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    logger.info("Starting Telegram bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
