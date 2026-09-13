from typing import Annotated

from fastapi import Depends, HTTPException, status

from customers_manager_hub.auth import CurrentAuthDependency
from customers_manager_hub.models import PlatformUser


def require_platform_owner(current: CurrentAuthDependency) -> PlatformUser:
    if not current.user.is_platform_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform Owner permission required",
        )
    return current.user


PlatformOwnerDependency = Annotated[PlatformUser, Depends(require_platform_owner)]
