"""One creation request, final-boundary lifetime limits, durable create events."""
import time
from harbor.environments.daytona import DaytonaEnvironment
from tenacity import stop_after_attempt

EVENT_SINK = None


class ControlsDaytonaEnvironment(DaytonaEnvironment):
    async def _create_sandbox(self, params, daytona=None):
        if EVENT_SINK is None:
            raise RuntimeError('v2 durable event sink missing')
        params.ttl_minutes = 60
        params.public = False
        params.auto_delete_interval = 0
        params.network_block_all = False
        event = dict(status='creating', sandbox_id=None, time=time.time())
        self.v2_create_events = [event]
        EVENT_SINK(dict(event))
        try:
            result = await DaytonaEnvironment._create_sandbox.retry_with(stop=stop_after_attempt(1))(self, params, daytona)
            event.update(status='created', sandbox_id=getattr(self._sandbox, 'id', None))
            return result
        except BaseException as exc:
            definite = type(exc).__name__ in {'DaytonaBadRequestError', 'DaytonaValidationError', 'DaytonaUnauthorizedError', 'DaytonaForbiddenError'}
            event.update(status='rejected' if definite else 'uncertain', error_type=type(exc).__name__,
                         sandbox_id=getattr(getattr(self, '_sandbox', None), 'id', None))
            raise
        finally:
            EVENT_SINK(dict(event))
