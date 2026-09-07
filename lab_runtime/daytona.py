"""Bounded Harbor/Daytona integration, pinned to Harbor 0.22.0."""

from harbor.environments.daytona import DaytonaEnvironment
from tenacity import stop_after_attempt


class BoundedDaytonaEnvironment(DaytonaEnvironment):
    """Project-local extension; installed upstream code stays untouched."""

    async def _create_sandbox(self, params, daytona=None):
        # Apply at the final creation boundary, including snapshot paths.
        params.ttl_minutes = 5
        params.public = False
        create_once = DaytonaEnvironment._create_sandbox.retry_with(
            stop=stop_after_attempt(1)
        )
        return await create_once(self, params, daytona)
