from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.core.billing import SaaSPlanTier

class BillingCycleStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELED = "CANCELED"

class TenantSubscriptionSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    plan_tier: SaaSPlanTier = SaaSPlanTier.STARTER
    status: BillingCycleStatus = BillingCycleStatus.ACTIVE
    active_vehicles_metered: int = Field(0, ge=0)
    price_per_vehicle_usd: float = Field(8.00, ge=0.0)
    monthly_recurring_total_usd: float = Field(0.00, ge=0.0)
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
