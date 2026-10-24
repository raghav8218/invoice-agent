from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class InvoiceRecord(BaseModel):
    #validate for storing in db

    vendor_name: str = Field(..., min_length=1, description="Vendor or company name")
    invoice_number: str = Field(..., min_length=1, description="Unique invoice identifier")
    invoice_date: date = Field(..., description="Date invoice was issued")
    due_date: Optional[date] = Field(None, description="Payment due date")
    amount: Decimal = Field(..., gt=0, description="Total amount due (positive)")
    currency: str = Field(default="USD", min_length=3, max_length=3, description="3-letter currency code")
    source_filename: str = Field(..., min_length=1, description="PDF file name")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when recorded"
    )

    @field_validator("vendor_name", "invoice_number", "source_filename", mode="before")
    @classmethod
    def strip_whitespace(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Field cannot be empty whitespace")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper() #currency to uppercase 

    @model_validator(mode="after")
    def validate_due_date_after_invoice_date(self) -> "InvoiceRecord":
        if self.due_date and self.due_date < self.invoice_date:
            raise ValueError(f"Due date ({self.due_date}) cannot be before invoice date ({self.invoice_date})")
        return self


class ExtractedInvoice(BaseModel):
    #llm extracted stuff
    vendor_name: Optional[str] = Field(None, description="Name of the vendor/supplier")
    invoice_number: Optional[str] = Field(None, description="Invoice number or ID")
    invoice_date: Optional[str] = Field(None, description="Invoice issue date as found in document")
    due_date: Optional[str] = Field(None, description="Payment due date, if found")
    amount: Optional[str] = Field(None, description="Total invoice amount, e.g. '2037.92' or '$2,037.92'")
    currency: Optional[str] = Field(None, description="Currency code or symbol, e.g. 'USD' or '$'")
