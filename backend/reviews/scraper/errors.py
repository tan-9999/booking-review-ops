"""Fetch failure types. The runner reacts differently to each one."""


class FetchError(Exception):
    """Base class for anything that stopped us getting a usable page."""


class TransientFetchError(FetchError):
    """Timeouts, 5xx, 429. Worth retrying with backoff."""


class PermanentFetchError(FetchError):
    """404, unexpected redirect, etc. Retrying the same URL won't help."""


class BlockedError(FetchError):
    """
    Anti-bot challenge / CAPTCHA / access denied.
    We deliberately do NOT try to get around this: the run is marked BLOCKED
    and the next scheduled run tries again later.
    """
