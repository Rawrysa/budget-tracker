from pydantic import BaseModel, field_validator
from typing import Optional

SOURCES = ["text", "image", "voice"]


class ExpenseInput(BaseModel):
    amount: float
    category: str
    merchant: Optional[str] = None
    description: Optional[str] = None
    date: str
    source: str = "text"

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        if v not in SOURCES:
            raise ValueError(f"Source must be one of: {', '.join(SOURCES)}")
        return v

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v


class BudgetInput(BaseModel):
    category: str
    monthly_limit: float

    @field_validator("monthly_limit")
    @classmethod
    def validate_limit(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Monthly limit must be positive")
        return v
