"""Exercise the plain-JavaScript UI handlers without a school API or browser profile."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("script_name", ["frontend_interactions.js", "admin_frontend_interactions.js"])
def test_frontend_interactions_without_school_api(script_name):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for the optional JavaScript smoke test")
    root = Path(__file__).parents[2]
    script = Path(__file__).with_name(script_name)
    app_js = root / "jlu_booking/web/static/app.js"
    subprocess.run([node, str(script), str(app_js)], check=True, capture_output=True, text=True)
