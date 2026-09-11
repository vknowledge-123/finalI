"""Keep authentication query strings out of Uvicorn access logs."""

import logging
from urllib.parse import urlsplit, urlunsplit


class AccessQueryFilter(logging.Filter):
    def filter(self, record):
        # Uvicorn's access arguments: client, method, URL, HTTP version, status.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            try:
                url = urlsplit(str(args[2]))
                args[2] = urlunsplit((url.scheme, url.netloc, url.path, "", ""))
            except ValueError:
                args[2] = "[invalid request URL]"
            record.args = tuple(args)
        return True


def install_access_log_filter():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessQueryFilter) for f in logger.filters):
        logger.addFilter(AccessQueryFilter())
