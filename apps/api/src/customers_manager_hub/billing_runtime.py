from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.ai_models import AIExecutionTrace
from customers_manager_hub.billing_models import (
    BusinessWallet,
    BusinessWalletTransaction,
    PlatformAIModelPrice,
    WalletTransactionKind,
)
from customers_manager_hub.database import AsyncSessionFactory

_MILLION = Decimal(1_000_000)
_MINUTE_SECONDS = Decimal(60)


class BillingError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def _resolve_price(
    db: AsyncSession,
    provider: str,
    model_id: str,
    effective_at: datetime,
) -> PlatformAIModelPrice | None:
    return await db.scalar(
        select(PlatformAIModelPrice)
        .where(
            PlatformAIModelPrice.provider == provider,
            PlatformAIModelPrice.model_id == model_id,
            PlatformAIModelPrice.effective_from <= effective_at,
        )
        .order_by(PlatformAIModelPrice.effective_from.desc(), PlatformAIModelPrice.id.desc())
        .limit(1)
    )


def calculate_charge_rial(
    price: PlatformAIModelPrice,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    audio_seconds: float | None,
) -> int:
    input_cost = Decimal(input_tokens or 0) / _MILLION * price.input_per_million_rial
    output_cost = Decimal(output_tokens or 0) / _MILLION * price.output_per_million_rial
    audio_cost = (
        Decimal(str(audio_seconds or 0.0))
        / _MINUTE_SECONDS
        * price.audio_per_minute_rial
    )
    return int((input_cost + output_cost + audio_cost).to_integral_value(rounding=ROUND_CEILING))


class AIBillingService:
    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def ensure_can_execute(
        self,
        tenant_id: UUID,
        provider: str,
        model_id: str,
        *,
        require_price: bool = False,
    ) -> None:
        async with self._session_factory() as db:
            price = await _resolve_price(db, provider, model_id, datetime.now(UTC))
            if price is None:
                if require_price:
                    raise BillingError("model_pricing_not_configured")
                return
            wallet = await db.get(BusinessWallet, tenant_id)
            if wallet is None or wallet.balance_rial <= 0:
                raise BillingError("insufficient_business_balance")

    async def record_charge(
        self,
        db: AsyncSession,
        trace: AIExecutionTrace,
    ) -> int | None:
        if trace.status != "succeeded":
            return None
        existing = await db.scalar(
            select(BusinessWalletTransaction.id).where(
                BusinessWalletTransaction.ai_execution_trace_id == trace.id
            )
        )
        if existing is not None:
            return None
        price = await _resolve_price(
            db,
            trace.provider,
            trace.model_id,
            trace.created_at,
        )
        if price is None:
            return None
        wallet = await db.scalar(
            select(BusinessWallet)
            .where(BusinessWallet.tenant_id == trace.tenant_id)
            .with_for_update()
        )
        if wallet is None:
            raise BillingError("business_wallet_missing")
        amount = calculate_charge_rial(
            price,
            input_tokens=trace.input_tokens,
            output_tokens=trace.output_tokens,
            audio_seconds=trace.audio_seconds,
        )
        wallet.balance_rial -= amount
        db.add(
            BusinessWalletTransaction(
                tenant_id=trace.tenant_id,
                kind=WalletTransactionKind.AI_DEBIT.value,
                amount_rial=amount,
                balance_after_rial=wallet.balance_rial,
                ai_execution_trace_id=trace.id,
                message_id=trace.message_id,
                provider=trace.provider,
                model_id=trace.model_id,
                input_tokens=trace.input_tokens,
                output_tokens=trace.output_tokens,
                total_tokens=trace.total_tokens,
                audio_seconds=trace.audio_seconds,
            )
        )
        return amount
