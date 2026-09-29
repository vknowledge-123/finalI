"""Keep isolated pytest runs in the same offline mode as the full suite."""

import os

os.environ.setdefault("APP_TESTING", "1")
