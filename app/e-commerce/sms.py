"""SMS delivery integration.

Maintained for backwards compatibility. Implementation is located in ``integrations.sms``.
"""

from integrations.sms import _DEV_LAST_CODE, send_otp

__all__ = ["_DEV_LAST_CODE", "send_otp"]
