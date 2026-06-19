"""SQLAlchemy ORM models for users, stores, products, receipts, and line items."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cartlog.db.base import Base


class ReceiptStatus(StrEnum):
    """Lifecycle states for a receipt as it moves through ingestion and review."""

    PENDING = "pending"
    PARSING = "parsing"
    PARSED = "parsed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class JobStatus(StrEnum):
    """Lifecycle states for a queued ingestion job."""

    PENDING = "pending"
    PARSING = "parsing"
    DONE = "done"
    FAILED = "failed"


class JobStep(StrEnum):
    """Sub-step a job is on while its status is 'parsing'."""

    EXTRACTING = "extracting"  # the model is reading the receipt (the long LLM call)
    SAVING = "saving"  # persisting the parsed receipt to the database


class ReviewReasonCode(StrEnum):
    """Why a receipt was flagged for review; one receipt may have several."""

    LOW_CONFIDENCE = "low_confidence"  # overall extraction confidence below threshold
    UNMAPPED_CATEGORY = "unmapped_category"  # one or more line items fell through to Uncategorized
    TOTAL_MISMATCH = "total_mismatch"  # line-item totals diverge from the parsed grand total
    NO_LINE_ITEMS = "no_line_items"  # the parser extracted no line items at all


class User(Base):
    """A person who owns receipts."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    receipts: Mapped[list[Receipt]] = relationship(back_populates="user")


class Store(Base):
    """A store chain at a specific location; comparison queries group by chain."""

    __tablename__ = "stores"
    # A chain at a specific location is one logical store; comparison queries group by chain.
    __table_args__ = (UniqueConstraint("chain_name", "location", name="uq_store_chain_location"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_name: Mapped[str] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    receipts: Mapped[list[Receipt]] = relationship(back_populates="store")


class StoreMerge(Base):
    """A saved transformation rule: receipts parsed as a (chain, location) redirect to a store.

    Recorded when an operator merges one store into another. The normalized source identity is
    matched against every future store creation so the merge keeps applying; deleting the rule
    stops future redirects but never back-dates already-reassigned receipts.
    """

    __tablename__ = "store_merges"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The merged-away store's chain/location, shown verbatim in the admin UI.
    source_chain_name: Mapped[str] = mapped_column(String(255))
    source_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # normalize_store_identity(chain, location); the column future ingests match on. Unique so
    # one rule wins per source identity. Width holds two 255-char parts plus a separator.
    source_identity_normalized: Mapped[str] = mapped_column(String(511), unique=True)
    target_store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    target_store: Mapped[Store] = relationship()


class Category(Base):
    """A product category in a flat, single-level taxonomy."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    # The reserved 'Uncategorized' row is is_system and cannot be edited/deleted in the UI.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default=func.false())

    products: Mapped[list[Product]] = relationship(back_populates="category")


class Product(Base):
    """A normalized product that line items resolve to during ingestion."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The normalization anchor: "eggs", "milk 2%", etc. Get-or-created during ingest.
    canonical_name: Mapped[str] = mapped_column(String(255), unique=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    # How many times the LLM reclassifier has been spent on this product while it stayed
    # Uncategorized; once it hits the cap we stop retrying and leave it for manual review.
    reclassify_attempts: Mapped[int] = mapped_column(default=0, server_default="0")

    category: Mapped[Category | None] = relationship(back_populates="products")
    line_items: Mapped[list[LineItem]] = relationship(back_populates="product")


class ProductMerge(Base):
    """A saved transformation rule: items named `source_name` redirect to a target product.

    Recorded when an operator merges one product into another. The normalized source name is
    matched against every future product creation so the merge keeps applying; deleting the
    rule stops future redirects but never back-dates already-merged line items.
    """

    __tablename__ = "product_merges"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The merged-away product's canonical_name, shown verbatim in the admin UI.
    source_name: Mapped[str] = mapped_column(String(255))
    # normalize_text(source_name); the column future ingests match on. Unique so one
    # rule wins per source term.
    source_name_normalized: Mapped[str] = mapped_column(String(255), unique=True)
    target_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    target_product: Mapped[Product] = relationship()


class Receipt(Base):
    """A single scanned receipt and its parsing metadata."""

    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    purchase_date: Mapped[date] = mapped_column(Date)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    image_path: Mapped[str] = mapped_column(String(1024))
    # Verbatim parser output, retained for re-processing and audit.
    raw_parser_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User | None] = relationship(back_populates="receipts")
    store: Mapped[Store] = relationship(back_populates="receipts")
    line_items: Mapped[list[LineItem]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )
    review_reasons: Mapped[list[ReceiptReviewReason]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )


class LineItem(Base):
    """One purchased line on a receipt, linked to its normalized Product."""

    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    # Kept verbatim from the receipt; normalized fields live on the linked Product.
    raw_description: Mapped[str] = mapped_column(String(512))
    # The parser's verbatim category guess for this line, retained even after the linked
    # Product is (re)categorized; the queryable surface for diagnosing miscategorization.
    original_category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    unit_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    # Normalized measure derived at ingest/edit by cartlog.units; see measure_status.
    measure_quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    measure_dimension: Mapped[str | None] = mapped_column(String(10), nullable=True)
    normalized_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    # resolved | not_applicable | needs_review. Default covers rows inserted before
    # normalization runs (e.g. a bare LineItem in a test or a pending backfill).
    measure_status: Mapped[str] = mapped_column(
        String(16), default="not_applicable", server_default="not_applicable"
    )

    receipt: Mapped[Receipt] = relationship(back_populates="line_items")
    product: Mapped[Product] = relationship(back_populates="line_items")


class ReceiptReviewReason(Base):
    """A structured reason a receipt is flagged needs_review (machine code + detail)."""

    __tablename__ = "receipt_review_reasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id"))
    code: Mapped[str] = mapped_column(String(50))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    receipt: Mapped[Receipt] = relationship(back_populates="review_reasons")


class IngestionJob(Base):
    """A queued unit of ingestion work: one stored receipt file awaiting parsing."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    image_path: Mapped[str] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(50), default=JobStatus.PENDING)
    # The sub-step a parsing job is on (extracting/saving); null unless status == 'parsing'.
    step: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Incremented each time a transient failure re-queues the job.
    retry_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When a re-queued job becomes eligible to claim again; null means immediately. Drives backoff.
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Set once parsing succeeds and a Receipt exists; null while pending/failed.
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("receipts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # One-directional: a Receipt has no back-reference to its source job.
    receipt: Mapped[Receipt | None] = relationship()


class ParseCostEvent(Base):
    """Append-only record of one receipt parse's LLM token usage and estimated USD cost.

    Kept separate from ingestion_jobs (and with no foreign key to it or to receipts) so that
    deleting or reparsing a receipt, which deletes the producing job, never erases the money
    that was actually spent. The monthly parsing-cost figure sums this ledger.
    """

    __tablename__ = "parse_cost_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Which job produced this event, for traceability only. Plain int (no FK) so a deleted
    # job never cascades to the ledger.
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Token counts and models for the two model calls. Null when a call did not happen or
    # genai-prices had no pricing data; estimated_cost_usd is a snapshot priced when it ran.
    parse_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classify_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classify_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    classify_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)


class FolderIngestConfig(Base):
    """Singleton configuration for the watch-folder ingestion channel (one row, id=1)."""

    __tablename__ = "folder_ingest_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=func.false())
    watch_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    processed_subdir: Mapped[str] = mapped_column(
        String(255), default="processed", server_default="processed"
    )
    failed_subdir: Mapped[str] = mapped_column(
        String(255), default="failed", server_default="failed"
    )
    poll_interval: Mapped[float] = mapped_column(Float, default=10.0, server_default="10.0")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
