"""Allow ``python -m vitrine``."""

import sys

from .cli import main

sys.exit(main())
