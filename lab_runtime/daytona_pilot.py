"""Single-attempt Daytona environment for the bounded real-model pilot."""
import time

from harbor.environments.daytona import DaytonaEnvironment
from tenacity import stop_after_attempt


EVENT_SINK = None
DEFINITE_CREATE_REJECTIONS = {
    'DaytonaAuthenticationError', 'DaytonaAuthorizationError',
    'DaytonaBadRequestError', 'DaytonaValidationError', 'DaytonaForbiddenError'}


class PilotDaytonaEnvironment(DaytonaEnvironment):
    """Apply the registered limits at the final provider creation boundary."""

    async def _create_sandbox(self, params, daytona=None):
        if EVENT_SINK is None:
            raise RuntimeError('pilot durable event sink missing')
        params.ttl_minutes = 5
        params.public = False
        params.network_block_all = True
        params.auto_stop_interval = 1
        params.auto_delete_interval = 0
        event = {'status': 'creating', 'sandbox_id': None, 'time': time.time()}
        EVENT_SINK(dict(event))
        try:
            result = await DaytonaEnvironment._create_sandbox.retry_with(
                stop=stop_after_attempt(1))(self, params, daytona)
            event.update(status='created', sandbox_id=getattr(self._sandbox, 'id', None))
            return result
        except BaseException as exc:
            definite = type(exc).__name__ in DEFINITE_CREATE_REJECTIONS
            event.update(status='rejected' if definite else 'uncertain',
                         error_type=type(exc).__name__,
                         sandbox_id=getattr(getattr(self, '_sandbox', None), 'id', None))
            raise
        finally:
            EVENT_SINK(dict(event))
