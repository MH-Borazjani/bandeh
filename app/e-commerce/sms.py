"""Sending the login SMS. Only this module talks to the outside world.

Why a separate module: the auth logic in ``auth.py`` should not care HOW
a text message is delivered -- it just calls ``send_otp(phone, code)``.
Today that prints the code to the server log (development). Tomorrow,
when Bandeh buys an SMS panel, only this file changes: the new provider
(Kavenegar, Ghasedak, ...) plugs in behind the same function, and every
caller keeps working untouched.

DEV ONLY warning: the development backend below stores the last sent
code in ``_DEV_LAST_CODE`` and prints it. That exists so a developer can
log in without a real SMS panel, and so ``check_auth.py`` can read the
code during tests. A real deployment must NEVER do this -- codes must
only travel over the SMS network.
"""

# DEV ONLY. Maps phone -> last code sent. Lets check_auth.py read the
# code during tests and lets a developer log in without an SMS panel.
# Production must not keep this: codes must only go out over SMS.
_DEV_LAST_CODE: dict[str, str] = {}


def send_otp(phone: str, code: str) -> None:
    """Deliver the one-time code to the user's phone.

    ``phone`` is already normalised (``09xxxxxxxxx``), ``code`` is the
    5-digit code as a string. This is the development backend: it prints
    the code to the server log instead of sending a real SMS.

    To plug in a real provider later, replace the body of this function
    with an HTTP call to the provider (Kavenegar, Ghasedak, ...), e.g.::

        httpx.post("https://api.kavenegar.com/v1/{apikey}/sms/send.json",
                   data={"receptor": phone, "message": f"کد ورود بنده: {code}"})

    Nothing else in the codebase needs to change.
    """
    # DEV ONLY: visible in `uvicorn` / `fastapi dev` output. The developer
    # copies the code from the log into the login form.
    print(f"[DEV SMS] code for {phone}: {code}")
    _DEV_LAST_CODE[phone] = code
