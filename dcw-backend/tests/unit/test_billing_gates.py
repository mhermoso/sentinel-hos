import pytest
from fastapi import HTTPException
from app.core.billing import SaaSPlanTier, TenantSubscriptionContext, require_tier_capability

@pytest.mark.asyncio
async def test_starter_tier_blocks_voice_ivr():
    tenant = TenantSubscriptionContext(
        tenant_id="tenant_1",
        plan_tier=SaaSPlanTier.STARTER,
        active_vehicle_count=10
    )
    gate = require_tier_capability(lambda caps: caps.allow_voice_ivr)

    with pytest.raises(HTTPException) as exc_info:
        await gate(tenant)

    assert exc_info.value.status_code == 402
