import logging
import os
from datetime import date

import anthropic

logger = logging.getLogger(__name__)

MCP_BASE = os.getenv("MCP_BASE_URL", "http://localhost:8000/mcp")
MCP_TOKEN = os.getenv("BUDGET_MCP_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AGENT_ID_ENV = "BUDGET_AGENT_ID"
VAULT_ID_ENV = "BUDGET_VAULT_ID"
ENVIRONMENT_ID_ENV = "BUDGET_ENVIRONMENT_ID"

SYSTEM_PROMPT = """You are a personal budget tracking assistant. Your job is to help the user
log expenses and understand their spending. You have access to a set of tools
to save and query expenses and budgets.

When the user describes an expense (e.g. "R85 Uber home", "spent 450 at
Woolworths", "lunch R120 Nandos"), extract the amount, merchant, and
category, then call save_expense. Always confirm what you saved.

When the user asks about their spending (e.g. "how much did I spend on food
this month?", "show me my April summary"), call the appropriate query tool
and present the results clearly.

If you encounter an expense that does not fit any existing category, first
call get_categories to see the full list. If none match, propose a new
category name and call add_category before saving the expense.

If the amount or category is ambiguous, ask a single clarifying question
before saving.

Today's date is {today}. Infer the month/year from relative phrases like
"this month", "last month", "in March".

Always respond in a friendly, concise tone. When showing summaries, use
simple formatted lists."""


def _make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _provision_vault(client: anthropic.Anthropic) -> str:
    """Create a vault and register the MCP server bearer token in it.

    Returns the vault_id. Call once and persist in BUDGET_VAULT_ID.
    """
    vault = client.beta.vaults.create(
        display_name="Budget Tracker MCP",
        betas=["managed-agents-2026-04-01"],
    )
    client.beta.vaults.credentials.create(
        vault_id=vault.id,
        display_name="budget-db bearer",
        auth={
            "type": "static_bearer",
            "mcp_server_url": MCP_BASE,
            "token": MCP_TOKEN,
        },
        betas=["managed-agents-2026-04-01"],
    )
    logger.info("Created vault %s with MCP bearer credential", vault.id)
    return vault.id


def get_or_create_vault_id(client: anthropic.Anthropic) -> str:
    vault_id = os.getenv(VAULT_ID_ENV, "")
    if vault_id:
        return vault_id
    vault_id = _provision_vault(client)
    logger.warning(
        "No %s set. Created vault %s. Add %s=%s to your .env to reuse it.",
        VAULT_ID_ENV, vault_id, VAULT_ID_ENV, vault_id,
    )
    return vault_id


def get_or_create_environment_id(client: anthropic.Anthropic) -> str:
    environment_id = os.getenv(ENVIRONMENT_ID_ENV, "")
    if environment_id:
        return environment_id
    environment = client.beta.environments.create(
        name="budget-tracker",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
        betas=["managed-agents-2026-04-01"],
    )
    logger.warning(
        "No %s set. Created environment %s. Add %s=%s to your .env to reuse it.",
        ENVIRONMENT_ID_ENV, environment.id, ENVIRONMENT_ID_ENV, environment.id,
    )
    return environment.id


def create_agent(client: anthropic.Anthropic) -> str:
    """Create a persistent Managed Agent and return its ID.

    Call this once and persist the returned ID in the BUDGET_AGENT_ID env var.
    """
    agent = client.beta.agents.create(
        name="Budget Tracker",
        model="claude-opus-4-7",
        system=SYSTEM_PROMPT.format(today=date.today().isoformat()),
        mcp_servers=[{"type": "url", "url": MCP_BASE, "name": "budget-db"}],
        tools=[{"type": "mcp_toolset", "mcp_server_name": "budget-db", "default_config": {
                "permission_policy": {"type": "always_allow"},
            },}],
        betas=["managed-agents-2026-04-01"],
    )
    logger.info("Created Managed Agent: %s", agent.id)
    return agent.id


def get_or_create_agent_id(client: anthropic.Anthropic) -> str:
    """Return the existing agent ID from env, creating a new agent if absent."""
    agent_id = os.getenv(AGENT_ID_ENV, "")
    if agent_id:
        return agent_id
    agent_id = create_agent(client)
    logger.warning(
        "No %s set. Created agent %s. Add %s=%s to your .env to reuse it.",
        AGENT_ID_ENV, agent_id, AGENT_ID_ENV, agent_id,
    )
    return agent_id


class BudgetAgent:
    """Managed-Agent-backed budget assistant.

    Each Telegram user gets their own session so conversation history is
    isolated. Sessions are created lazily on first message and reused for
    subsequent turns within the same bot process lifetime.
    """

    def __init__(self, agent_id: str, vault_id: str, environment_id: str):
        self._agent_id = agent_id
        self._vault_id = vault_id
        self._environment_id = environment_id
        self._client = _make_client()
        # user_id -> session_id
        self._sessions: dict[int, str] = {}

    def _get_session(self, user_id: int) -> str | None:
        return self._sessions.get(user_id)

    def _create_session(self, user_id: int) -> str:
        session = self._client.beta.sessions.create(
            agent=self._agent_id,
            environment_id=self._environment_id,
            vault_ids=[self._vault_id],
            betas=["managed-agents-2026-04-01"],
        )
        self._sessions[user_id] = session.id
        logger.info("Created session %s for user %d", session.id, user_id)
        return session.id

    async def process_message(self, text: str, user_id: int) -> str:
        session_id = self._get_session(user_id)
        if session_id is None:
            session_id = self._create_session(user_id)

        response_text = ""
        try:
            with self._client.beta.sessions.events.stream(
                session_id,
                betas=["managed-agents-2026-04-01"],
            ) as stream:
                self._client.beta.sessions.events.send(
                    session_id,
                    events=[{
                        "type": "user.message",
                        "content": [{"type": "text", "text": text}],
                    }],
                    betas=["managed-agents-2026-04-01"],
                )
                for event in stream:
                    if event.type == "agent.message":
                        for block in event.content:
                            if block.type == "text":
                                response_text += block.text
                    elif event.type == "session.status_idle":
                        break
                    elif event.type == "session.error":
                        msg = event.error.message if event.error else "unknown"
                        logger.error("Session %s error event: %s", session_id, msg)
                        return f"Sorry, something went wrong: {msg}"
        except anthropic.APIError as e:
            logger.error("Session %s API error: %s", session_id, e)
            if "not found" in str(e).lower() or "expired" in str(e).lower():
                self._sessions.pop(user_id, None)
            return "Sorry, I ran into an error. Please try again."

        return response_text.strip() or "Done."

    def clear_history(self, user_id: int) -> None:
        """Drop the session so the next message starts a fresh conversation."""
        session_id = self._sessions.pop(user_id, None)
        if session_id:
            logger.info("Cleared session %s for user %d", session_id, user_id)
