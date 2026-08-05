from collections.abc import Callable
from enum import Enum

from fastapi import HTTPException, status
from pydantic import BaseModel


class SaaSPlanTier(str, Enum):
    STARTER = "STARTER"        # $8 / truck / mo
    PRO = "PRO"                # $18 / truck / mo
    ENTERPRISE = "ENTERPRISE"  # Custom Invoicing

class TierCapabilities(BaseModel):
    max_vehicles: int
    allow_sms_alerts: bool = True
    allow_voice_ivr: bool = False
    allow_pdf_audits: bool = False
    allow_sso_saml: bool = False
    telematics_poll_interval_sec: int = 120

TIER_LIMITS: dict[SaaSPlanTier, TierCapabilities] = {
    SaaSPlanTier.STARTER: TierCapabilities(
        max_vehicles=25,
        allow_sms_alerts=True,
        allow_voice_ivr=False,
        allow_pdf_audits=False,
        allow_sso_saml=False,
        telematics_poll_interval_sec=120,
    ),
    SaaSPlanTier.PRO: TierCapabilities(
        max_vehicles=150,
        allow_sms_alerts=True,
        allow_voice_ivr=True,
        allow_pdf_audits=True,
        allow_sso_saml=False,
        telematics_poll_interval_sec=60,
    ),
    SaaSPlanTier.ENTERPRISE: TierCapabilities(
        max_vehicles=99999,
        allow_sms_alerts=True,
        allow_voice_ivr=True,
        allow_pdf_audits=True,
        allow_sso_saml=True,
        telematics_poll_interval_sec=30,
    ),
}

class TenantSubscriptionContext(BaseModel):
    tenant_id: str
    plan_tier: SaaSPlanTier
    active_vehicle_count: int

def require_tier_capability(capability_check: Callable[[TierCapabilities], bool]):
    async def dependency(tenant: TenantSubscriptionContext) -> TenantSubscriptionContext:
        capabilities = TIER_LIMITS[tenant.plan_tier]
        if not capability_check(capabilities):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "FEATURE_LOCKED",
                    "message": f"Feature requires a higher subscription tier. Current tier: {tenant.plan_tier}",
                    "upgrade_url": "https://app.drivercompliancewatch.com/billing/upgrade",
                },
            )
        return tenant
    return dependency
