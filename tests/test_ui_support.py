import base64

from jlu_booking.ui_support import (
    EMBEDDED_LOGO_GIF,
    choose_ui_fonts,
    enable_windows_dpi_awareness,
    fit_window_geometry,
    logo_candidate_paths,
    platform_family,
)


def test_platform_family():
    assert platform_family("win32") == "windows"
    assert platform_family("darwin") == "macos"
    assert platform_family("linux") == "linux"
    assert enable_windows_dpi_awareness("linux") is False


def test_choose_windows_fonts_case_insensitively():
    fonts = ("microsoft yahei ui", "Segoe UI", "Consolas")

    assert choose_ui_fonts(fonts, "win32") == (
        "microsoft yahei ui",
        "Segoe UI",
        "Consolas",
    )


def test_choose_fonts_has_safe_tk_fallbacks():
    assert choose_ui_fonts((), "linux") == (
        "TkDefaultFont",
        "TkDefaultFont",
        "TkFixedFont",
    )

    assert choose_ui_fonts(
        (),
        "linux",
        default_font="System Sans",
        fixed_font="System Mono",
    ) == ("System Sans", "System Sans", "System Mono")


def test_embedded_logo_is_a_gif():
    logo_bytes = base64.b64decode(EMBEDDED_LOGO_GIF)

    assert logo_bytes.startswith((b"GIF87a", b"GIF89a"))
    assert len(logo_bytes) > 1000


def test_logo_override_is_first_candidate(tmp_path, monkeypatch):
    override = tmp_path / "my-logo.png"
    monkeypatch.setenv("JLU_BOOKING_LOGO", str(override))

    candidates = logo_candidate_paths(
        module_file=tmp_path / "project" / "jlu_booking" / "ui_support.py",
        prefix=tmp_path / "python",
    )

    assert candidates[0] == override


def test_window_geometry_fits_small_screen():
    assert fit_window_geometry(1366, 768, 1180, 800) == (
        1180,
        672,
        93,
        48,
    )


def test_window_geometry_keeps_preferred_size_on_large_screen():
    assert fit_window_geometry(1920, 1080, 1180, 800) == (
        1180,
        800,
        370,
        140,
    )


def test_window_geometry_never_exceeds_tiny_screen():
    width, height, x, y = fit_window_geometry(300, 240, 1180, 800)

    assert width <= 300
    assert height <= 240
    assert (x, y) == (24, 48)
