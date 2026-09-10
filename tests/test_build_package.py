import re
from pathlib import Path

from jlu_booking import __version__
from tools import build_package


def test_build_script_output_is_windows_console_safe(monkeypatch, capsys):
    monkeypatch.setattr(build_package.subprocess, "run", lambda *args, **kwargs: None)

    assert build_package.main() == 0
    output = capsys.readouterr()

    assert (output.out + output.err).isascii()


def test_package_versions_match():
    pyproject = (
        Path(__file__).resolve().parent.parent / "pyproject.toml"
    ).read_text(encoding="utf-8")
    version_match = re.search(
        r'^version\s*=\s*"([^"]+)"',
        pyproject,
        flags=re.MULTILINE,
    )

    assert version_match is not None
    assert version_match.group(1) == __version__
