from __future__ import annotations

import re


QUICK_CAPTURE_SECTION_TITLE = "## QuickCaptures"
QUICK_CAPTURE_ATTACHMENT_PLACEHOLDER_RE = re.compile(r"<(?:clipboard|file)-Image-[^>]+>")
