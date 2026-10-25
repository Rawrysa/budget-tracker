# Budget Tracker

A personal monthly budget tracker with three components:

- **MCP Server** — FastMCP HTTP server owning all database operations (SQLite)
- **Telegram Bot** — Claude-powered agent that logs and queries expenses via natural language
- **Streamlit Dashboard** — Visual overview of spending with charts and filters

---

## Prerequisites

- Python 3.11+
- `ffmpeg` (required for voice note transcription)
  - macOS: `brew install ffmpeg`
  - Ubuntu: `sudo apt install ffmpeg`
  - Windows: download from https://ffmpeg.org/download.html and add to PATH

---

## Setup

### 1. Clone and create your `.env`

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather on Telegram |
| `BUDGET_MCP_TOKEN` | Any random secret string (e.g. `openssl rand -hex 32`) |
| `WHISPER_MODEL` | Whisper model size: `tiny`, `base`, `small`, `medium` |
| `DB_PATH` | Path to SQLite file (default: `./budget.db`) |
| `BUDGET_AGENT_ID` | Managed Agent ID (auto-created on first bot run — copy from logs and set here to reuse) |

### 2. Install dependencies

```bash
make install
```

Or install per component:

```bash
make install-mcp
make install-bot
make install-dashboard
```

### 3. Run the MCP server (terminal 1)

```bash
make mcp
```

The server starts at `http://localhost:8000/mcp`.

### 4. Run the Telegram bot (terminal 2)

```bash
make bot
```

The bot will wait for the MCP server to be healthy before starting.

### 5. Run the dashboard (terminal 3)

```bash
make dashboard
```

Opens at `http://localhost:8501`.

---

## Add to Claude Code

Use the MCP server directly from Claude Code's CLI:

```bash
claude mcp add-json budget-db '{"type":"http","url":"http://localhost:8000/mcp","headers":{"Authorization":"Bearer YOUR_TOKEN"}}'
```

Replace `YOUR_TOKEN` with the value of `BUDGET_MCP_TOKEN` from your `.env`.

The `.mcp.json` in the project root is pre-configured and will be picked up automatically when you open Claude Code in this directory.

---

## Example Telegram Messages

**Logging expenses:**
- `R85 Uber home`
- `Spent 450 at Woolworths`
- `Lunch R120 at Nandos`
- `Netflix subscription R199`

**Querying spending:**
- `How much did I spend this month?`
- `Show me my April summary`
- `What's my grocery budget status?`
- `How much have I spent on transport?`

**Managing budgets:**
- `Set my groceries budget to R3000`
- `What are my budget limits?`

**Voice notes:** Record a voice note describing an expense — it will be transcribed and processed automatically.

**Photos/receipts:** Send a photo of a receipt and the bot will extract items and ask for confirmation before saving.

---

## Project Structure

```
budget-tracker/
├── mcp-server/
│   ├── server.py          # FastMCP HTTP server + tool definitions
│   ├── database.py        # Async SQLite operations (aiosqlite)
│   ├── models.py          # Pydantic models + category list
│   └── requirements.txt
├── bot/
│   ├── main.py            # Telegram bot entry point + health check
│   ├── agent.py           # Claude tool-use agent loop + MCP client
│   ├── handlers.py        # Text, voice, photo message handlers
│   └── requirements.txt
├── dashboard/
│   ├── app.py             # Streamlit dashboard (4 pages)
│   ├── models_ref.py      # Shared category list for dashboard
│   └── requirements.txt
├── .mcp.json              # Claude Code MCP configuration
├── .env.example           # Environment variable template
├── Makefile               # make mcp / make bot / make dashboard
└── README.md
```

---

## MCP Tools Reference

| Tool | Description |
|---|---|
| `get_categories` | List all expense categories |
| `add_category` | Add a new expense category |
| `save_expense` | Save a new expense |
| `get_expenses` | List expenses for a month/year |
| `get_monthly_summary` | Total per category + budget status |
| `delete_expense` | Remove an expense by ID |
| `set_budget` | Create or update a category budget limit |
| `get_budgets` | List all budget limits |
| `get_budget_status` | Spend vs budget per category |
| `export_to_excel` | Export a month to `.xlsx` |

---

## Expense Categories

Default categories (seeded on first run): `Groceries` · `Transport` · `Eating Out` · `Utilities` · `Entertainment` · `Health` · `Shopping` · `Subscriptions` · `Fuel` · `Other`

Categories are stored in the database and can be extended at runtime — either by telling the bot ("add a Pet Supplies category") or by calling the `add_category` MCP tool directly.
