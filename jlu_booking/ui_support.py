"""Cross-platform helpers for the Tkinter user interface."""

from __future__ import annotations

import os
import sys
import ctypes
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path


EMBEDDED_LOGO_GIF = """
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
""".strip()


COMMON_CJK_FONTS = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Hiragino Sans GB",
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "DengXian",
    "SimHei",
    "Arial Unicode MS",
    "DejaVu Sans",
)

PLATFORM_CJK_FONTS = {
    "windows": (
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "DengXian",
        "SimHei",
    ),
    "macos": (
        "PingFang SC",
        "Hiragino Sans GB",
        "Heiti SC",
        "Songti SC",
    ),
    "linux": (
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "DejaVu Sans",
    ),
}

PLATFORM_LATIN_FONTS = {
    "windows": ("Segoe UI", "Arial"),
    "macos": ("Helvetica Neue", "Helvetica", "Arial"),
    "linux": ("Noto Sans", "DejaVu Sans", "Liberation Sans"),
}

PLATFORM_MONO_FONTS = {
    "windows": ("Cascadia Mono", "Consolas", "Courier New"),
    "macos": ("Menlo", "Monaco", "Courier"),
    "linux": ("DejaVu Sans Mono", "Liberation Mono", "Courier"),
}


def platform_family(platform_name: str | None = None) -> str:
    """Return a stable family name for the current operating system."""

    name = (platform_name or sys.platform).casefold()
    if name.startswith("win"):
        return "windows"
    if name == "darwin":
        return "macos"
    return "linux"


def enable_windows_dpi_awareness(platform_name: str | None = None):
    """Ask Windows for crisp per-monitor rendering before creating Tk."""

    if platform_family(platform_name) != "windows":
        return False

    try:
        user32 = ctypes.windll.user32
        try:
            return bool(
                user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            )
        except (AttributeError, OSError):
            return bool(user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        return False


def choose_font_family(available, candidates, fallback):
    """Choose an installed font case-insensitively."""

    installed = {
        str(font_name).strip().casefold(): str(font_name).strip()
        for font_name in available
        if str(font_name).strip()
    }
    for candidate in candidates:
        match = installed.get(candidate.casefold())
        if match:
            return match
    return fallback


def choose_ui_fonts(
    available,
    platform_name: str | None = None,
    default_font="TkDefaultFont",
    fixed_font="TkFixedFont",
):
    """Select Chinese, Latin, and monospaced fonts for this computer."""

    family = platform_family(platform_name)
    cjk_candidates = PLATFORM_CJK_FONTS[family] + tuple(
        font_name
        for font_name in COMMON_CJK_FONTS
        if font_name not in PLATFORM_CJK_FONTS[family]
    )
    ui_font = choose_font_family(available, cjk_candidates, default_font)
    latin_font = choose_font_family(
        available,
        PLATFORM_LATIN_FONTS[family],
        ui_font,
    )
    mono_font = choose_font_family(
        available,
        PLATFORM_MONO_FONTS[family],
        fixed_font,
    )
    return ui_font, latin_font, mono_font


def logo_candidate_paths(module_file=None, prefix=None):
    """Return possible installed/source logo locations without duplicates."""

    module_path = Path(module_file or __file__).resolve()
    package_dir = module_path.parent
    project_dir = package_dir.parent
    install_prefix = Path(prefix or sys.prefix)
    candidates = []

    override = os.getenv("JLU_BOOKING_LOGO", "").strip()
    if override:
        candidates.append(Path(override).expanduser())

    candidates.extend(
        (
            package_dir / "assets" / "JLU_LOGO.png",
            project_dir / "assets" / "JLU_LOGO.png",
            install_prefix / "share" / "jlu-booking" / "assets" / "JLU_LOGO.png",
        )
    )

    try:
        package_distribution = distribution("jlu-booking")
    except PackageNotFoundError:
        package_distribution = None

    if package_distribution is not None:
        for item in package_distribution.files or ():
            normalized = str(item).replace("\\", "/").casefold()
            if normalized.endswith("share/jlu-booking/assets/jlu_logo.png"):
                candidates.append(Path(package_distribution.locate_file(item)))

    for asset_dir in (project_dir / "assets", package_dir / "assets"):
        if asset_dir.is_dir():
            candidates.extend(
                path
                for path in asset_dir.iterdir()
                if path.is_file()
                and path.suffix.casefold() == ".png"
                and path.stem.casefold() in {"jlu_logo", "jlu_icon", "logo"}
            )

    unique = []
    seen = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key not in seen:
            seen.add(key)
            unique.append(Path(candidate))
    return unique


def fit_window_geometry(
    screen_width,
    screen_height,
    preferred_width,
    preferred_height,
    *,
    horizontal_margin=48,
    vertical_margin=96,
):
    """Fit and center a window while leaving room for desktop chrome."""

    screen_width = max(1, int(screen_width))
    screen_height = max(1, int(screen_height))
    available_width = max(1, screen_width - horizontal_margin)
    available_height = max(1, screen_height - vertical_margin)
    width = max(1, min(int(preferred_width), available_width))
    height = max(1, min(int(preferred_height), available_height))
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2)
    return width, height, x, y
