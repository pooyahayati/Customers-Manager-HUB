from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.billing_models import (
    BillingDisplayUnit,
    BusinessWallet,
    PlatformBillingSettings,
)
from customers_manager_hub.database import get_db_session
from customers_manager_hub.tenants import TenantContextDependency

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/billing", tags=["billing"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


class BusinessBillingSummary(BaseModel):
    balance_rial: int
    display_unit: BillingDisplayUnit


@router.get("/summary", response_model=BusinessBillingSummary)
async def get_business_billing_summary(
    context: TenantContextDependency,
    db: DbSession,
) -> BusinessBillingSummary:
    wallet = await db.get(BusinessWallet, context.tenant.id)
    settings_row = await db.get(PlatformBillingSettings, 1)
    return BusinessBillingSummary(
        balance_rial=wallet.balance_rial if wallet is not None else 0,
        display_unit=(
            BillingDisplayUnit(settings_row.display_unit)
            if settings_row is not None
            else BillingDisplayUnit.RIAL
        ),
    )
