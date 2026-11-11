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
"""Money: Toman in bandeh, Rial at the gateway. One named function.

Bandeh prices are int Toman. Zibal wants int Rial. The conversion is
``× 10`` — and it lives here, in exactly one place, so no route or
service ever invents its own ``* 10``.
"""


def toman_to_rial(toman: int) -> int:
    """Convert an int Toman amount to int Rial (×10). The only conversion."""
    return toman * 10


__all__ = ["toman_to_rial"]
