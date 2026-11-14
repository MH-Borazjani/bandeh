# Copyright (C) 2026 Mohammadhossein Borazjani
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""SMS delivery integration.

Maintained for backwards compatibility. Implementation is located in ``integrations.sms``.
"""

from integrations.sms import (
    _DEV_LAST_CODE,
    _DEV_SENT_MESSAGES,
    KAVENEGAR_BASE_URL,
    KavenegarClient,
    KavenegarError,
    SmsClientDep,
    get_sms_client,
    send_order_paid,
    send_order_shipped,
    send_otp,
)

__all__ = [
    "KAVENEGAR_BASE_URL",
    "KavenegarClient",
    "KavenegarError",
    "SmsClientDep",
    "_DEV_LAST_CODE",
    "_DEV_SENT_MESSAGES",
    "get_sms_client",
    "send_order_paid",
    "send_order_shipped",
    "send_otp",
]
