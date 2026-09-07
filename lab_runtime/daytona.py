"""Bounded Harbor/Daytona integration, pinned to Harbor 0.22.0."""

from harbor.environments.daytona import DaytonaEnvironment
from tenacity import stop_after_attempt
import time


class BoundedDaytonaEnvironment(DaytonaEnvironment):
    """Project-local extension; installed upstream code stays untouched."""

    async def _create_sandbox(self, params, daytona=None):
        # Apply at the final creation boundary, including snapshot paths.
        params.ttl_minutes = 5
        params.public = False
        is_m1 = bool(self._user_labels.get('m1_run'))
        if is_m1:
            params.network_block_all = True
            params.auto_stop_interval = 1
            params.auto_delete_interval = 0
        event = {'status': 'creating', 'sandbox_id': None,
                 'start_monotonic': time.monotonic(), 'wall_seconds': None,
                 'error_type': None}
        if is_m1:
            if not hasattr(self, 'm1_create_events'):
                self.m1_create_events = []
            self.m1_create_events.append(event)
        create_once = DaytonaEnvironment._create_sandbox.retry_with(
            stop=stop_after_attempt(1)
        )
        try:
            result = await create_once(self, params, daytona)
            event['status'] = 'created'
            return result
        except BaseException as exc:
            event.update(status='failed', error_type=type(exc).__name__)
            raise
        finally:
            event['wall_seconds'] = time.monotonic()-event['start_monotonic']
            sandbox = getattr(self, '_sandbox', None)
            event['sandbox_id'] = getattr(sandbox, 'id', None)
            if is_m1 and event['status'] == 'failed' and event['sandbox_id'] is None:
                # Timeouts/transport failures do not cancel a server-side create.
                definite_rejections = {'DaytonaBadRequestError', 'DaytonaValidationError',
                                       'DaytonaUnauthorizedError', 'DaytonaForbiddenError'}
                if event['error_type'] not in definite_rejections:
                    event['status'] = 'uncertain'
