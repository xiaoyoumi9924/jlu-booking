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


def test_web_templates_and_static_assets_are_packaged():
    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"web/templates/*.html"' in pyproject
    assert '"web/templates/admin/*.html"' in pyproject
    assert '"web/static/*.css"' in pyproject
    assert '"web/static/*.js"' in pyproject
    assert '"web/static/*.png"' in pyproject
    assert (root / "jlu_booking/web/static/JLU_LOGO.png").read_bytes() == (
        root / "assets/JLU_LOGO.png"
    ).read_bytes()


def test_windows_bundle_has_icon_and_console_worker():
    root = Path(__file__).resolve().parent.parent
    spec = (root / "packaging/jlu-booking.spec").read_text(encoding="utf-8")

    assert (root / "assets/JLU_LOGO.ico").is_file()
    assert "JLU_LOGO.ico" in spec
    assert 'name="JLU Booking Worker"' in spec
    assert 'worker_options["console"] = True' in spec


def test_windows_installer_creates_a_desktop_shortcut():
    root = Path(__file__).resolve().parent.parent
    installer = (root / "packaging/windows-installer.iss").read_text(
        encoding="utf-8"
    )

    assert "OutputBaseFilename=JLU-Booking-Setup" in installer
    assert "PrivilegesRequired=lowest" in installer
    assert 'Name: "desktopicon"' in installer
    assert 'Name: "{autodesktop}' in installer
    assert 'Tasks: desktopicon' in installer
    assert 'Source: "..\\dist\\JLU Booking\\*"' in installer


def test_windows_version_metadata_matches_package():
    root = Path(__file__).resolve().parent.parent
    version_info = (root / "packaging/windows-version-info.txt").read_text(
        encoding="utf-8"
    )
    installer = (root / "packaging/windows-installer.iss").read_text(
        encoding="utf-8"
    )

    assert f"StringStruct('FileVersion', '{__version__}')" in version_info
    assert f"StringStruct('ProductVersion', '{__version__}')" in version_info
    assert f'#define MyAppVersion "{__version__}"' in installer
