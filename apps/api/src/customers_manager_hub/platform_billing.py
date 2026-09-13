from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.billing_models import (
    BillingDisplayUnit,
    BusinessWallet,
    BusinessWalletTransaction,
    PlatformAIModelPrice,
    PlatformBillingSettings,
    WalletTransactionKind,
)
from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import AuditEvent, Tenant
from customers_manager_hub.platform_ai_models import PlatformAIProvider
from customers_manager_hub.platform_auth import PlatformOwnerDependency

router = APIRouter(prefix="/api/v1/platform/billing", tags=["platform-billing"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


class ModelPricePut(BaseModel):
    provider: PlatformAIProvider
    model_id: str = Field(min_length=1, max_length=255)
    input_per_million_rial: int = Field(ge=0)
    output_per_million_rial: int = Field(ge=0)
    audio_per_minute_rial: int = Field(ge=0)

    @field_validator("model_id")
    @classmethod
    def normalize_model_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Model ID must not be blank")
        if any(character in normalized for character in ("/", "?", "#", "\x00")):
            raise ValueError("Model ID contains an unsupported path character")
        return normalized


class ModelPriceResponse(BaseModel):
    id: UUID
    provider: PlatformAIProvider
    model_id: str
    input_per_million_rial: int
    output_per_million_rial: int
    audio_per_minute_rial: int
    effective_from: datetime


class WalletCredit(BaseModel):
    amount_rial: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class WalletResponse(BaseModel):
    business_id: UUID
    balance_rial: int
    updated_at: datetime


class WalletTransactionResponse(BaseModel):
    id: UUID
    kind: WalletTransactionKind
    amount_rial: int
    balance_after_rial: int
    message_id: UUID | None
    provider: str | None
    model_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    audio_seconds: float | None
    note: str | None
    created_at: datetime


class BillingSettingsPut(BaseModel):
    display_unit: BillingDisplayUnit


class BillingSettingsResponse(BaseModel):
    display_unit: BillingDisplayUnit


async def _require_business(db: AsyncSession, business_id: UUID) -> Tenant:
    business = await db.get(Tenant, business_id)
    if business is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")
    return business


@router.get("/settings", response_model=BillingSettingsResponse)
async def get_billing_settings(
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> BillingSettingsResponse:
    del owner
    settings_row = await db.get(PlatformBillingSettings, 1)
    display_unit = (
        BillingDisplayUnit(settings_row.display_unit)
        if settings_row is not None
        else BillingDisplayUnit.RIAL
    )
    return BillingSettingsResponse(display_unit=display_unit)


@router.put("/settings", response_model=BillingSettingsResponse)
async def put_billing_settings(
    payload: BillingSettingsPut,
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> BillingSettingsResponse:
    settings_row = await db.get(PlatformBillingSettings, 1)
    if settings_row is None:
        settings_row = PlatformBillingSettings(
            id=1,
            display_unit=payload.display_unit.value,
            updated_by_user_id=owner.id,
        )
        db.add(settings_row)
    else:
        settings_row.display_unit = payload.display_unit.value
        settings_row.updated_by_user_id = owner.id
    db.add(
        AuditEvent(
            tenant_id=None,
            actor_user_id=owner.id,
            action="platform.billing_settings.updated",
            target_type="platform_billing_settings",
            target_id=None,
            details={"display_unit": payload.display_unit.value},
        )
    )
    await db.commit()
    return BillingSettingsResponse(display_unit=payload.display_unit)


def _price_response(price: PlatformAIModelPrice) -> ModelPriceResponse:
    return ModelPriceResponse(
        id=price.id,
        provider=PlatformAIProvider(price.provider),
        model_id=price.model_id,
        input_per_million_rial=price.input_per_million_rial,
        output_per_million_rial=price.output_per_million_rial,
        audio_per_minute_rial=price.audio_per_minute_rial,
        effective_from=price.effective_from,
    )


def _transaction_response(
    transaction: BusinessWalletTransaction,
) -> WalletTransactionResponse:
    return WalletTransactionResponse(
        id=transaction.id,
        kind=WalletTransactionKind(transaction.kind),
        amount_rial=transaction.amount_rial,
        balance_after_rial=transaction.balance_after_rial,
        message_id=transaction.message_id,
        provider=transaction.provider,
        model_id=transaction.model_id,
        input_tokens=transaction.input_tokens,
        output_tokens=transaction.output_tokens,
        total_tokens=transaction.total_tokens,
        audio_seconds=transaction.audio_seconds,
        note=transaction.note,
        created_at=transaction.created_at,
    )


@router.get("/pricing", response_model=list[ModelPriceResponse])
async def list_model_pricing(
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> list[ModelPriceResponse]:
    del owner
    rows = list(
        (
            await db.scalars(
                select(PlatformAIModelPrice).order_by(
                    PlatformAIModelPrice.provider,
                    PlatformAIModelPrice.model_id,
                    PlatformAIModelPrice.effective_from.desc(),
                )
            )
        ).all()
    )
    latest: dict[tuple[str, str], PlatformAIModelPrice] = {}
    for row in rows:
        latest.setdefault((row.provider, row.model_id), row)
    return [_price_response(row) for row in latest.values()]


@router.put("/pricing", response_model=ModelPriceResponse)
async def put_model_pricing(
    payload: ModelPricePut,
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> ModelPriceResponse:
    price = PlatformAIModelPrice(
        provider=payload.provider.value,
        model_id=payload.model_id,
        input_per_million_rial=payload.input_per_million_rial,
        output_per_million_rial=payload.output_per_million_rial,
        audio_per_minute_rial=payload.audio_per_minute_rial,
        effective_from=datetime.now(UTC),
        created_by_user_id=owner.id,
    )
    db.add(price)
    await db.flush()
    db.add(
        AuditEvent(
            tenant_id=None,
            actor_user_id=owner.id,
            action="platform.ai_pricing.created",
            target_type="platform_ai_model_price",
            target_id=price.id,
            details={"provider": payload.provider.value, "model_id": payload.model_id},
        )
    )
    await db.commit()
    await db.refresh(price)
    return _price_response(price)


@router.get("/businesses/{business_id}/wallet", response_model=WalletResponse)
async def get_business_wallet(
    business_id: UUID,
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> WalletResponse:
    del owner
    await _require_business(db, business_id)
    wallet = await db.get(BusinessWallet, business_id)
    if wallet is None:
        wallet = BusinessWallet(tenant_id=business_id, balance_rial=0)
        db.add(wallet)
        await db.commit()
        await db.refresh(wallet)
    return WalletResponse(
        business_id=business_id,
        balance_rial=wallet.balance_rial,
        updated_at=wallet.updated_at,
    )


@router.post(
    "/businesses/{business_id}/wallet/credit",
    response_model=WalletTransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def credit_business_wallet(
    business_id: UUID,
    payload: WalletCredit,
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> WalletTransactionResponse:
    await _require_business(db, business_id)
    wallet = await db.scalar(
        select(BusinessWallet)
        .where(BusinessWallet.tenant_id == business_id)
        .with_for_update()
    )
    if wallet is None:
        wallet = BusinessWallet(tenant_id=business_id, balance_rial=0)
        db.add(wallet)
        await db.flush()
    wallet.balance_rial += payload.amount_rial
    transaction = BusinessWalletTransaction(
        tenant_id=business_id,
        kind=WalletTransactionKind.CREDIT.value,
        amount_rial=payload.amount_rial,
        balance_after_rial=wallet.balance_rial,
        created_by_user_id=owner.id,
        note=payload.note,
    )
    db.add(transaction)
    await db.flush()
    db.add(
        AuditEvent(
            tenant_id=business_id,
            actor_user_id=owner.id,
            action="platform.business_wallet.credited",
            target_type="business_wallet_transaction",
            target_id=transaction.id,
            details={"amount_rial": payload.amount_rial},
        )
    )
    await db.commit()
    await db.refresh(transaction)
    return _transaction_response(transaction)


@router.get(
    "/businesses/{business_id}/wallet/transactions",
    response_model=list[WalletTransactionResponse],
)
async def list_business_wallet_transactions(
    business_id: UUID,
    owner: PlatformOwnerDependency,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[WalletTransactionResponse]:
    del owner
    await _require_business(db, business_id)
    transactions = list(
        (
            await db.scalars(
                select(BusinessWalletTransaction)
                .where(BusinessWalletTransaction.tenant_id == business_id)
                .order_by(
                    BusinessWalletTransaction.created_at.desc(),
                    BusinessWalletTransaction.id.desc(),
                )
                .limit(limit)
            )
        ).all()
    )
    return [_transaction_response(transaction) for transaction in transactions]
