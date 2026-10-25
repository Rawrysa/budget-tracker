import aiosqlite
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "./budget.db")

CREATE_EXPENSES = """
CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    amount      REAL NOT NULL,
    category    TEXT NOT NULL,
    merchant    TEXT,
    description TEXT,
    date        TEXT NOT NULL,
    source      TEXT DEFAULT 'text',
    created_at  TEXT DEFAULT (datetime('now'))
)
"""

CREATE_BUDGETS = """
CREATE TABLE IF NOT EXISTS budgets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    category      TEXT UNIQUE NOT NULL,
    monthly_limit REAL NOT NULL,
    updated_at    TEXT DEFAULT (datetime('now'))
)
"""

CREATE_CATEGORIES = """
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
)
"""

DEFAULT_CATEGORIES = [
    ("Groceries", "Supermarket and food store purchases"),
    ("Transport", "Public transport, taxis, and ride-hailing"),
    ("Eating Out", "Restaurants, cafes, and takeaways"),
    ("Utilities", "Electricity, water, internet, and phone"),
    ("Entertainment", "Movies, events, and leisure activities"),
    ("Health", "Medical, pharmacy, and fitness"),
    ("Shopping", "Clothing, electronics, and general retail"),
    ("Subscriptions", "Recurring digital or service subscriptions"),
    ("Fuel", "Petrol and diesel"),
    ("Other", "Anything that does not fit another category"),
]


async def run_migrations() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_EXPENSES)
        await db.execute(CREATE_BUDGETS)
        await db.execute(CREATE_CATEGORIES)
        for name, desc in DEFAULT_CATEGORIES:
            await db.execute(
                "INSERT OR IGNORE INTO categories (name, description) VALUES (?, ?)",
                (name, desc),
            )
        await db.commit()
    logger.info("Database migrations complete: %s", DB_PATH)


async def insert_expense(
    amount: float,
    category: str,
    merchant: Optional[str],
    description: Optional[str],
    date: str,
    source: str,
) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            INSERT INTO expenses (amount, category, merchant, description, date, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (amount, category, merchant, description, date, source),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM expenses WHERE id = ?", (cursor.lastrowid,))).fetchone()
        return dict(row)


async def fetch_expenses(month: int, year: int, category: Optional[str] = None) -> list[dict]:
    month_str = f"{year}-{month:02d}"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if category:
            rows = await (
                await db.execute(
                    "SELECT * FROM expenses WHERE strftime('%Y-%m', date) = ? AND category = ? ORDER BY date",
                    (month_str, category),
                )
            ).fetchall()
        else:
            rows = await (
                await db.execute(
                    "SELECT * FROM expenses WHERE strftime('%Y-%m', date) = ? ORDER BY date",
                    (month_str,),
                )
            ).fetchall()
        return [dict(r) for r in rows]


async def fetch_monthly_summary(month: int, year: int) -> dict:
    month_str = f"{year}-{month:02d}"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT category, SUM(amount) as total
                FROM expenses
                WHERE strftime('%Y-%m', date) = ?
                GROUP BY category
                ORDER BY total DESC
                """,
                (month_str,),
            )
        ).fetchall()
        by_category = {r["category"]: r["total"] for r in rows}
        overall = sum(by_category.values())

        budgets = await (
            await db.execute("SELECT category, monthly_limit FROM budgets")
        ).fetchall()
        budget_map = {b["category"]: b["monthly_limit"] for b in budgets}

        budget_status = {}
        for cat, spent in by_category.items():
            limit = budget_map.get(cat)
            budget_status[cat] = {
                "spent": spent,
                "limit": limit,
                "over_budget": (spent > limit) if limit else None,
            }

        return {
            "month": month,
            "year": year,
            "by_category": by_category,
            "total": overall,
            "budget_status": budget_status,
        }


async def remove_expense(expense_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,))
        ).fetchone()
        if not row:
            return {"error": f"Expense {expense_id} not found"}
        await db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        await db.commit()
        return {"deleted": True, "expense": dict(row)}


async def upsert_budget(category: str, monthly_limit: float) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO budgets (category, monthly_limit, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit,
                                                updated_at = excluded.updated_at
            """,
            (category, monthly_limit),
        )
        await db.commit()
        row = await (
            await db.execute("SELECT * FROM budgets WHERE category = ?", (category,))
        ).fetchone()
        return dict(row)


async def fetch_categories() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM categories ORDER BY name")).fetchall()
        return [dict(r) for r in rows]


async def insert_category(name: str, description: Optional[str] = None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        existing = await (
            await db.execute("SELECT * FROM categories WHERE name = ?", (name,))
        ).fetchone()
        if existing:
            return {"error": f"Category '{name}' already exists", "category": dict(existing)}
        await db.execute(
            "INSERT INTO categories (name, description) VALUES (?, ?)",
            (name, description),
        )
        await db.commit()
        row = await (
            await db.execute("SELECT * FROM categories WHERE name = ?", (name,))
        ).fetchone()
        return dict(row)


async def fetch_budgets() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM budgets ORDER BY category")).fetchall()
        return [dict(r) for r in rows]


async def fetch_budget_status(month: int, year: int) -> list[dict]:
    month_str = f"{year}-{month:02d}"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        budgets = await (await db.execute("SELECT * FROM budgets")).fetchall()
        result = []
        for b in budgets:
            cat = b["category"]
            limit = b["monthly_limit"]
            row = await (
                await db.execute(
                    "SELECT COALESCE(SUM(amount), 0) as spent FROM expenses WHERE strftime('%Y-%m', date) = ? AND category = ?",
                    (month_str, cat),
                )
            ).fetchone()
            spent = row["spent"]
            pct = round((spent / limit) * 100, 1) if limit else None
            result.append({
                "category": cat,
                "monthly_limit": limit,
                "spent": spent,
                "remaining": round(limit - spent, 2),
                "percentage_used": pct,
                "over_budget": spent > limit,
            })
        return result
