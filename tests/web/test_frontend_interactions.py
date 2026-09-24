"""Exercise the plain-JavaScript UI handlers without a school API or browser profile."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_venue_preference_and_clear_results_handlers():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for the optional JavaScript smoke test")
    root = Path(__file__).parents[2]
    script = Path(__file__).with_name("frontend_interactions.js")
    app_js = root / "jlu_booking/web/static/app.js"
    subprocess.run([node, str(script), str(app_js)], check=True, capture_output=True, text=True)
