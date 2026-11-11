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
"""Runnable self-check for core/datetime_utils.py. Run from repo root:

    python tests/check_datetime.py

No server, no database: pure functions, fixed instants.
"""

import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

from core.datetime_utils import (
    OTP_EXPIRY_SECONDS,
    SESSION_EXPIRY_DAYS,
    TEHRAN,
    UTC,
    expires_at,
    format_tehran,
    is_expired,
    otp_is_expired,
    session_is_expired,
    to_tehran,
    utcnow,
)


def main() -> None:
    # 1. utcnow is aware and in UTC.
    now = utcnow()
    assert now.tzinfo is UTC, now
    assert now.utcoffset() == datetime.timedelta(0)

    # 2. Tehran is UTC+03:30 (no daylight saving in Iran since 2023).
    noon_utc = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    tehran = to_tehran(noon_utc)
    assert tehran.tzinfo is TEHRAN
    assert (tehran.hour, tehran.minute) == (15, 30), tehran
    assert format_tehran(noon_utc) == "2026-01-01 15:30", format_tehran(noon_utc)

    # 3. expires_at adds exactly the requested seconds.
    base = datetime.datetime(2026, 11, 11, 10, 0, 0, tzinfo=UTC)
    assert expires_at(120, now=base) == base + datetime.timedelta(seconds=120)
    assert expires_at(OTP_EXPIRY_SECONDS, now=base) == base + datetime.timedelta(seconds=120)
    assert expires_at(SESSION_EXPIRY_DAYS * 86400, now=base) == base + datetime.timedelta(days=30)

    # 4. Expiry checks: past is expired, future is not, exactly-now is expired.
    past = base - datetime.timedelta(seconds=1)
    future = base + datetime.timedelta(seconds=60)
    assert is_expired(past, now=base) is True
    assert is_expired(future, now=base) is False
    assert is_expired(base, now=base) is True, "exactly at expiry counts as expired"
    # The wrappers compare against the real clock, so use relative instants.
    assert otp_is_expired(utcnow() - datetime.timedelta(seconds=10)) is True
    assert session_is_expired(utcnow() + datetime.timedelta(hours=1)) is False

    # 5. Naive datetimes are rejected loudly instead of silently miscompared.
    naive = datetime.datetime(2026, 11, 11, 10, 0, 0)
    for call in (
        lambda: to_tehran(naive),
        lambda: is_expired(naive, now=base),
        lambda: is_expired(future, now=naive),
        lambda: expires_at(60, now=naive),
    ):
        try:
            call()
        except ValueError:
            pass
        else:
            raise AssertionError(f"naive datetime was accepted: {call}")

    print("All datetime checks passed.")


if __name__ == "__main__":
    main()
