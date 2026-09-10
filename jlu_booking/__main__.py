import sys
from pathlib import Path


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--auto-worker"]:
        if not __package__:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from jlu_booking.auto import main as auto_main
        else:
            from .auto import main as auto_main
        return auto_main(args[1:])

    import tkinter as tk

    if not __package__:
        # 兼容直接运行此文件；正常入口仍是 `python -m jlu_booking`。
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from jlu_booking.gui import BookingApp
        from jlu_booking.ui_support import enable_windows_dpi_awareness
    else:
        from .gui import BookingApp
        from .ui_support import enable_windows_dpi_awareness

    enable_windows_dpi_awareness()
    root = tk.Tk()
    BookingApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
