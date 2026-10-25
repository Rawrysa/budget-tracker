import base64
import json
import logging
import os
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

from agent import BudgetAgent

logger = logging.getLogger(__name__)

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        _whisper_model = whisper.load_model(WHISPER_MODEL)
        logger.info("Loaded Whisper model: %s", WHISPER_MODEL)
    return _whisper_model


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: BudgetAgent) -> None:
    user_id = update.effective_user.id
    text = update.message.text.strip()
    logger.info("Text from user %d: %s", user_id, text[:100])

    if text.lower() in ("/start", "/help"):
        await update.message.reply_text(
            "Hi! I'm your budget tracker bot.\n\n"
            "Just tell me what you spent, e.g.:\n"
            "• R85 Uber home\n"
            "• Spent 450 at Woolworths\n"
            "• Lunch R120 Nandos\n\n"
            "Or ask about your spending:\n"
            "• How much did I spend this month?\n"
            "• Show me my April summary\n"
            "• What's my grocery budget status?\n\n"
            "Type /clear to reset conversation history."
        )
        return

    if text.lower() == "/clear":
        agent.clear_history(user_id)
        await update.message.reply_text("Conversation history cleared.")
        return

    await update.message.chat.send_action("typing")
    response = await agent.process_message(text, user_id)
    await update.message.reply_text(response)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: BudgetAgent) -> None:
    user_id = update.effective_user.id
    logger.info("Voice message from user %d", user_id)
    await update.message.chat.send_action("typing")

    voice = update.message.voice
    voice_file = await context.bot.get_file(voice.file_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        ogg_path = os.path.join(tmpdir, "voice.ogg")
        wav_path = os.path.join(tmpdir, "voice.wav")

        await voice_file.download_to_drive(ogg_path)

        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_ogg(ogg_path)
            audio.export(wav_path, format="wav")
        except Exception as e:
            logger.error("Audio conversion failed: %s", e)
            await update.message.reply_text("Sorry, I couldn't process the audio file.")
            return

        try:
            model = _get_whisper()
            result = model.transcribe(wav_path)
            transcript = result.get("text", "").strip()
        except Exception as e:
            logger.error("Transcription failed: %s", e)
            await update.message.reply_text("Sorry, I couldn't transcribe the voice message.")
            return

    if not transcript:
        await update.message.reply_text("I couldn't make out any speech. Please try again.")
        return

    logger.info("Transcribed for user %d: %s", user_id, transcript)
    agent_response = await agent.process_message(transcript, user_id)
    await update.message.reply_text(f'🎙 I heard: "{transcript}"\n\n{agent_response}')


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: BudgetAgent) -> None:
    import anthropic

    user_id = update.effective_user.id
    logger.info("Photo from user %d", user_id)
    await update.message.chat.send_action("typing")

    photo = update.message.photo[-1]
    photo_file = await context.bot.get_file(photo.file_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        img_path = os.path.join(tmpdir, "receipt.jpg")
        await photo_file.download_to_drive(img_path)
        with open(img_path, "rb") as f:
            img_b64 = base64.standard_b64encode(f.read()).decode()

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    client = anthropic.AsyncAnthropic(api_key=api_key)

    try:
        extract_resp = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Extract all expense items from this receipt or screenshot. "
                                "Return a JSON list of objects only, with keys: amount (number), "
                                "merchant (string), description (string). "
                                "No explanation, just the JSON array."
                            ),
                        },
                    ],
                }
            ],
        )
        raw = extract_resp.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        items = json.loads(raw)
    except Exception as e:
        logger.error("Receipt extraction failed: %s", e)
        await update.message.reply_text("Sorry, I couldn't extract expenses from that image.")
        return

    if not items:
        await update.message.reply_text("No expense items found in the image.")
        return

    summary_lines = []
    for i, item in enumerate(items, 1):
        summary_lines.append(
            f"{i}. R{item.get('amount', '?')} — {item.get('merchant', 'Unknown')} ({item.get('description', '')})"
        )

    prompt = (
        f"I found these items in your receipt:\n"
        + "\n".join(summary_lines)
        + "\n\nShould I save all of these? Reply 'yes' to save all, or tell me which ones to skip."
    )

    # Store pending items in context for confirmation flow
    context.user_data["pending_receipt_items"] = items
    await update.message.reply_text(prompt)


async def handle_receipt_confirmation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, agent: BudgetAgent
) -> None:
    """Handle yes/no reply after receipt photo extraction."""
    text = update.message.text.strip().lower()
    items = context.user_data.pop("pending_receipt_items", None)

    if not items:
        await handle_text(update, context, agent)
        return

    if text in ("yes", "y", "yeah", "yep", "sure", "ok", "okay", "all"):
        from datetime import date as date_cls
        today = date_cls.today().isoformat()
        saved = []
        for item in items:
            desc = f"{item.get('merchant', '')} {item.get('description', '')}".strip()
            result = await agent.process_message(
                f"Save expense: R{item.get('amount')} at {item.get('merchant', 'Unknown')} for {item.get('description', 'receipt item')} today",
                update.effective_user.id,
            )
            saved.append(result)
        await update.message.reply_text(f"Saved {len(items)} expense(s) from your receipt.")
    else:
        await update.message.reply_text(
            "No problem! Just tell me which items you'd like to save and I'll log them for you."
        )
