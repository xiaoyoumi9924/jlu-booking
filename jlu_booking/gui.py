import math
import os
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog, ttk

if __package__:
    from .app_runner import build_auto_worker_command
    from .api import VENUES, get_sports_for_venue, resolve_venue_sport, book_place, can_book, extract_available_slots, get_companion_user, query_courts
    from .config import AUTO_CONFIG_FILE, load_auto_config, require_companion_student_number, save_auto_config, validate_auto_config
    from .paths import LOG_DIR
    from .token_store import (
        TokenStoreError,
        clear_saved_token,
        extract_token_input,
        resolve_token,
        save_token,
    )
    from .ui_support import (
        EMBEDDED_LOGO_GIF,
        choose_ui_fonts,
        fit_window_geometry,
        logo_candidate_paths,
    )
else:
    # 兼容 `python jlu_booking/gui.py` 这种按文件运行的方式。
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from jlu_booking.app_runner import build_auto_worker_command
    from jlu_booking.api import VENUES, get_sports_for_venue, resolve_venue_sport, book_place, can_book, extract_available_slots, get_companion_user, query_courts
    from jlu_booking.config import AUTO_CONFIG_FILE, load_auto_config, require_companion_student_number, save_auto_config, validate_auto_config
    from jlu_booking.paths import LOG_DIR
    from jlu_booking.token_store import (
        TokenStoreError,
        clear_saved_token,
        extract_token_input,
        resolve_token,
        save_token,
    )
    from jlu_booking.ui_support import (
        EMBEDDED_LOGO_GIF,
        choose_ui_fonts,
        fit_window_geometry,
        logo_candidate_paths,
    )


class ScrollableFrame(tk.Frame):
    """只在鼠标位于结果区域时响应滚轮的可滚动容器。"""

    def __init__(self, parent, bg="#FFFFFF", **kwargs):
        super().__init__(parent, bg=bg, **kwargs)

        self.canvas = tk.Canvas(
            self,
            bg=bg,
            highlightthickness=0,
            bd=0,
        )
        self.scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
        )
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.window_id = self.canvas.create_window(
            (0, 0),
            window=self.inner,
            anchor="nw",
        )

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _on_inner_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _bind_mousewheel(self, _event):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, event):
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")


class BookingApp:
    SIDEBAR_WIDTH = 320
    MAIN_WINDOW_WIDTH = 1400
    MAIN_WINDOW_HEIGHT = 900

    def __init__(self, root):
        self.root = root
        self.root.title("JLU Booking · 吉林大学体育场馆")

        # 吉林大学校徽标准色为 PANTONE 7455C；以下为屏幕端近似配色。
        self.COLOR_BLUE = "#144399"
        self.COLOR_BLUE_DARK = "#0B2F70"
        self.COLOR_BLUE_DEEP = "#08275D"
        self.COLOR_BLUE_MID = "#2D61B7"
        self.COLOR_BLUE_TINT = "#EAF1FF"
        self.COLOR_BLUE_PALE = "#F4F7FE"

        self.COLOR_BG = "#F2F5FA"
        self.COLOR_CARD = "#FFFFFF"
        self.COLOR_BORDER = "#E3E9F2"
        self.COLOR_CONTROL = "#F3F6FA"
        self.COLOR_TEXT = "#172033"
        # 正文、辅助文字均保持足够对比度，避免 Windows 不同显示器上发灰。
        self.COLOR_SUBTEXT = "#526078"
        self.COLOR_MUTED = "#647188"

        self.COLOR_GREEN = "#147A55"
        self.COLOR_GREEN_BG = "#E8F7F0"
        self.COLOR_RED = "#B42318"
        self.COLOR_RED_BG = "#FFF0EE"

        available_fonts = tkfont.families(self.root)
        default_font = tkfont.nametofont(
            "TkDefaultFont",
            root=self.root,
        ).actual("family")
        fixed_font = tkfont.nametofont(
            "TkFixedFont",
            root=self.root,
        ).actual("family")
        self.FONT, self.FONT_LATIN, self.FONT_MONO = choose_ui_fonts(
            available_fonts,
            default_font=default_font,
            fixed_font=fixed_font,
        )
        self.configure_named_fonts()
        try:
            self.token, self.token_source = resolve_token()
        except TokenStoreError:
            # A damaged or unreadable token file must not prevent the GUI from
            # opening. The user can still enter a replacement when first needed.
            self.token = ""
            self.token_source = "none"
        # 无论 Token 来自环境变量还是本机文件，本次打开 GUI 后第一次使用前
        # 都做一次只读接口验证。验证成功后在本次进程内复用结果，避免重复请求。
        self.token_validated = False
        self.root.configure(bg=self.COLOR_BG)

        # 场馆与项目统一从 api.VENUES 生成。
        # GUI 只负责展示与选择，不再把 shopNum / shortName 写死在界面层。
        try:
            startup_config = load_auto_config(AUTO_CONFIG_FILE, create_if_missing=True)
        except (OSError, ValueError):
            first_venue = next(iter(VENUES))
            startup_config = {
                "venue": first_venue,
                "sport": next(iter(get_sports_for_venue(first_venue))),
                "target_day": "今天",
            }

        self.venue_options = tuple(
            {
                "key": venue_name,
                "name": venue_name,
                "sports": tuple(venue_info["sports"].keys()),
            }
            for venue_name, venue_info in VENUES.items()
        )
        self.current_venue_name = startup_config["venue"]
        self.startup_config = startup_config

        self.logo_source = None
        self.logo_image = None
        self.icon_image = None
        self.current_view = "query"
        self.nav_section = None
        self.main_title_var = None
        self.main_subtitle_var = None
        self.content_host = None
        self.query_page = None
        self.auto_page = None
        self.venue_section = None
        self.query_venue_badge_var = None
        self.venue_var = None
        self.sport_choices = None
        self.sport_buttons = {}
        self.date_buttons = {}
        self.is_loading = False
        self.selected_slot = None
        self.last_query_date = None
        self.last_query_venue = None
        self.last_query_shop_num = None
        self.booking_dialog = None
        self.companion_entry = None
        self.companion_status_var = None
        self.verify_companion_button = None
        self.validated_companion = None
        self.is_verifying_companion = False
        self.booking_check_button = None
        self.booking_check_status_var = None
        self.booking_check_status_label = None
        self.is_checking_booking = False
        self.booking_check_passed = False
        self.real_booking_button = None
        self.is_submitting_booking = False
        self.booking_submitted = False

        self.auto_settings_dialog = None
        self.auto_venue_var = None
        self.auto_venue_badge_var = None
        self.auto_venue_buttons = {}
        self.auto_sport_var = None
        self.auto_sport_row = None
        self.auto_day_var = None
        self.auto_sport_buttons = {}
        self.auto_day_buttons = {}
        self.auto_selection_summary_var = None
        self.auto_companion_entry = None
        self.auto_companion_var = None
        self.auto_companion_status_var = None
        self.auto_companion_status_label = None
        self.auto_validated_companion_number = None
        self.auto_validated_companion_name = None
        self.auto_court_var = None
        self.auto_court_spinbox = None
        self.auto_time_text = None
        self.auto_real_booking_var = None
        self.auto_mode_buttons = {}
        self.auto_mode_note_var = None
        self.auto_mode_note_label = None
        self.auto_save_button = None
        self.auto_start_button = None
        self.auto_stop_button = None
        self.auto_status_var = tk.StringVar(value="未运行")
        self.auto_output_lines = []
        self.auto_log_window = None
        self.auto_log_text = None
        self.auto_log_mode = "live"
        self.auto_log_title_var = None
        self.auto_log_buttons = {}
        self.auto_process = None
        self.auto_process_reader = None
        self.auto_stop_requested = False
        self.is_saving_auto_config = False
        self.is_closing = False

        self.center_window(self.MAIN_WINDOW_WIDTH, self.MAIN_WINDOW_HEIGHT)
        self.setup_styles()
        self.load_logo()
        self.build_ui()

        self.root.bind(
            "<Return>",
            lambda _event: self.start_query() if self.current_view == "query" else None,
        )
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if not self.token:
            self.root.after(350, self.show_first_run_guide)

    def setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Vertical.TScrollbar",
            background="#D3DBE8",
            troughcolor=self.COLOR_CARD,
            bordercolor=self.COLOR_CARD,
            arrowcolor=self.COLOR_SUBTEXT,
            width=9,
        )

    def configure_named_fonts(self):
        """Give Tk dialogs and controls a readable font on each platform."""

        for name in (
            "TkDefaultFont",
            "TkTextFont",
            "TkMenuFont",
            "TkHeadingFont",
            "TkCaptionFont",
            "TkSmallCaptionFont",
            "TkIconFont",
            "TkTooltipFont",
        ):
            try:
                tkfont.nametofont(name, root=self.root).configure(family=self.FONT)
            except tk.TclError:
                continue

        self.root.option_add("*selectBackground", self.COLOR_BLUE_MID)
        self.root.option_add("*selectForeground", "#FFFFFF")

    def validate_token(self, token):
        """Use a read-only venue query to verify a Token with the school service."""

        venue_name = getattr(self, "current_venue_name", "") or next(iter(VENUES))
        sports = get_sports_for_venue(venue_name)
        sport_name = next(iter(sports))

        # 尽量使用用户当前正在查看的项目；若界面尚未建立，则退回该场馆首项。
        for variable_name in ("auto_sport_var", "sport_var"):
            variable = getattr(self, variable_name, None)
            if variable is None:
                continue
            try:
                selected = variable.get()
            except (AttributeError, tk.TclError):
                continue
            if selected in sports:
                sport_name = selected
                break

        shop_num, sport_short_name = resolve_venue_sport(venue_name, sport_name)
        query_courts(
            query_date=date.today().isoformat(),
            sport_short_name=sport_short_name,
            shop_num=shop_num,
            token=token,
        )

    def get_token(self, parent=None, prompt=True):
        """Return a validated Token and remember newly entered values locally."""

        dialog_parent = parent or self.root
        candidate = str(getattr(self, "token", "") or "").strip()
        if candidate and getattr(self, "token_validated", False):
            return candidate
        if not candidate and not prompt:
            return None

        entered_now = False
        while True:
            if not candidate:
                if not prompt:
                    return None
                raw_token = simpledialog.askstring(
                    "设置登录 Token",
                    (
                        "请粘贴 Token，或者直接粘贴包含 token=... 的完整请求地址。\n\n"
                        "获取方法：登录学校场馆系统 → 打开浏览器开发者工具 "
                        "Network → 查询一次场地 → 找到 easyserpClient 请求。\n\n"
                        "提交后程序会先向学校系统验证；只有有效 Token 才会保存到"
                        "当前用户的本机配置目录，下次打开会自动读取。"
                    ),
                    show="*",
                    parent=dialog_parent,
                )
                if raw_token is None:
                    return None
                try:
                    candidate = extract_token_input(raw_token)
                except ValueError as exc:
                    messagebox.showwarning(
                        "Token 格式不正确",
                        f"{exc}\n\n请重新输入有效的 Token。",
                        parent=dialog_parent,
                    )
                    candidate = ""
                    continue
                entered_now = True
            try:
                self.validate_token(candidate)
            except Exception as exc:
                previous_source = getattr(self, "token_source", "none")
                self.token = ""
                self.token_source = "none"
                self.token_validated = False
                if previous_source == "saved":
                    try:
                        clear_saved_token()
                    except TokenStoreError:
                        pass
                messagebox.showerror(
                    "Token 验证未通过",
                    (
                        "学校系统没有接受这个 Token，请重新获取并输入有效 Token。\n"
                        "如果 Token 刚刚获取，也请检查网络后重试。\n\n"
                        f"服务器提示：{exc}"
                    ),
                    parent=dialog_parent,
                )
                candidate = ""
                entered_now = False
                continue

            self.token = candidate
            self.token_validated = True
            if not entered_now:
                return candidate

            self.token_source = "session"
            try:
                save_token(candidate)
                self.token_source = "saved"
            except (TokenStoreError, ValueError) as exc:
                messagebox.showwarning(
                    "Token 未能保存",
                    (
                        "Token 已验证且本次可以使用，但未能保存到本机；"
                        "关闭程序后需要重新输入。\n\n"
                        f"{exc}"
                    ),
                    parent=dialog_parent,
                )
            return candidate

    def show_first_run_guide(self):
        """Offer a short first-run path without requiring terminal commands."""

        if self.is_closing or self.token:
            return
        should_setup = messagebox.askyesno(
            "欢迎使用 JLU Booking",
            (
                "第一次使用只需完成两件事：\n\n"
                "1. 粘贴自己的 Token 或完整请求地址\n"
                "2. 在“自动预约”中选择目标并点击“保存并启动”\n\n"
                "验证成功后，Token 和同行人学工号会保存在当前用户的本机配置中，"
                "下次打开会自动读取。它们不会被打包进程序或上传到 GitHub。\n\n"
                "是否现在设置 Token？"
            ),
            parent=self.root,
        )
        if not should_setup:
            return
        if self.get_token(parent=self.root, prompt=True):
            self.open_auto_settings_dialog()

    def load_logo(self):
        """
        加载吉林大学校徽。

        优先使用项目或安装目录中的高清图片；找不到或解码失败时，
        使用程序内置的小尺寸校徽，因此 Windows 安装后也不会丢失。
        """
        failures = []
        logo_origin = None

        for path in logo_candidate_paths(__file__):
            if not path.is_file():
                continue
            try:
                self.logo_source = tk.PhotoImage(file=str(path))
                logo_origin = str(path)
                break
            except tk.TclError as exc:
                failures.append(f"{path}: {exc}")

        if self.logo_source is None:
            try:
                self.logo_source = tk.PhotoImage(
                    data=EMBEDDED_LOGO_GIF,
                    format="gif",
                )
                logo_origin = "程序内置校徽"
            except tk.TclError as exc:
                failures.append(f"程序内置校徽: {exc}")

        if self.logo_source is None:
            print("[GUI] 校徽加载失败，将显示 JLU 文字。")
            if failures:
                print("\n".join(f"[GUI] {failure}" for failure in failures))
            return

        logo_factor = max(
            1,
            math.ceil(
                max(self.logo_source.width(), self.logo_source.height()) / 96
            ),
        )
        icon_factor = max(
            1,
            math.ceil(
                max(self.logo_source.width(), self.logo_source.height()) / 48
            ),
        )
        self.logo_image = self.logo_source.subsample(logo_factor, logo_factor)
        self.icon_image = self.logo_source.subsample(icon_factor, icon_factor)

        try:
            self.root.iconphoto(True, self.icon_image)
        except tk.TclError as exc:
            # 某些 Linux 窗口管理器不支持 iconphoto，不影响界面中的校徽。
            failures.append(f"窗口图标: {exc}")

        print(f"[GUI] 已加载校徽：{logo_origin}")

    def build_ui(self):
        shell = tk.Frame(self.root, bg=self.COLOR_BG)
        shell.pack(fill="both", expand=True)

        self.build_sidebar(shell)
        self.build_main_area(shell)

    def build_sidebar(self, parent):
        # Windows 高 DPI 会放大字体，但固定像素侧栏不会同步变宽。
        # 320 px 可完整容纳场馆名、导航说明和三个运动项目标签。
        sidebar = tk.Frame(
            parent,
            bg=self.COLOR_BLUE,
            width=self.SIDEBAR_WIDTH,
        )
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=self.COLOR_BLUE)
        brand.pack(fill="x", padx=28, pady=(24, 18))

        logo_badge = tk.Frame(
            brand,
            bg=self.COLOR_CARD,
            width=86,
            height=86,
            highlightbackground="#7698D1",
            highlightthickness=1,
        )
        logo_badge.pack(anchor="w")
        logo_badge.pack_propagate(False)

        if self.logo_image:
            tk.Label(
                logo_badge,
                image=self.logo_image,
                bg=self.COLOR_CARD,
                bd=0,
            ).pack(expand=True)
        else:
            tk.Label(
                logo_badge,
                text="JLU",
                font=(self.FONT, 24, "bold"),
                fg=self.COLOR_BLUE,
                bg=self.COLOR_CARD,
            ).pack(expand=True)

        tk.Label(
            brand,
            text="吉林大学",
            font=(self.FONT, 21, "bold"),
            fg="#FFFFFF",
            bg=self.COLOR_BLUE,
        ).pack(anchor="w", pady=(12, 1))
        tk.Label(
            brand,
            text="JILIN UNIVERSITY",
            font=(self.FONT_LATIN, 9, "bold"),
            fg="#C9D8F3",
            bg=self.COLOR_BLUE,
        ).pack(anchor="w")

        separator = tk.Frame(sidebar, bg="#3B65AB", height=1)
        separator.pack(fill="x", padx=28, pady=(0, 18))

        self.nav_section = tk.Frame(sidebar, bg=self.COLOR_BLUE)
        self.nav_section.pack(fill="x", padx=20)
        self.refresh_sidebar_nav_items()

        self.venue_section = tk.Frame(sidebar, bg=self.COLOR_BLUE)
        self.venue_section.pack(fill="x", padx=20, pady=(20, 0))
        self.refresh_sidebar_venue_items()

        sidebar_footer = tk.Frame(sidebar, bg=self.COLOR_BLUE)
        sidebar_footer.pack(side="bottom", fill="x", padx=28, pady=28)

        tk.Label(
            sidebar_footer,
            text="求实创新 · 励志图强",
            font=(self.FONT, 10, "bold"),
            fg="#D5E1F5",
            bg=self.COLOR_BLUE,
        ).pack(anchor="w")
        tk.Label(
            sidebar_footer,
            text="场地数据以学校系统为准",
            font=(self.FONT, 9),
            fg="#BED0EC",
            bg=self.COLOR_BLUE,
        ).pack(anchor="w", pady=(7, 0))

    def create_sidebar_section_header(self, parent, title, meta):
        row = tk.Frame(parent, bg=self.COLOR_BLUE)
        tk.Label(
            row,
            text=title,
            font=(self.FONT, 9, "bold"),
            fg="#A9C0E6",
            bg=self.COLOR_BLUE,
        ).pack(side="left")
        tk.Label(
            row,
            text=meta,
            font=(self.FONT, 8, "bold"),
            fg="#B7CBEA",
            bg=self.COLOR_BLUE,
        ).pack(side="right")
        return row

    def refresh_sidebar_nav_items(self):
        """Render both function choices with an explicit current selection."""

        if self.nav_section is None:
            return
        for child in self.nav_section.winfo_children():
            child.destroy()

        self.create_sidebar_section_header(
            self.nav_section,
            title="功能导航",
            meta="2 项",
        ).pack(fill="x", padx=2, pady=(0, 9))

        items = (
            (
                "query",
                "查",
                "场地查询",
                "查看空闲场地与时段",
                self.show_query_page,
            ),
            (
                "auto",
                "约",
                "自动预约",
                "配置、启动与查看状态",
                self.open_auto_settings_dialog,
            ),
        )
        for index, (key, icon, title, subtitle, command) in enumerate(items):
            item = self.create_sidebar_nav_item(
                self.nav_section,
                icon=icon,
                title=title,
                subtitle=(
                    f"✓ 当前功能 · {subtitle}"
                    if key == self.current_view
                    else subtitle
                ),
                active=key == self.current_view,
                command=command,
            )
            item.pack(fill="x", pady=(8 if index else 0, 0))

    def create_sidebar_nav_item(
        self,
        parent,
        icon,
        title,
        subtitle,
        active=False,
        command=None,
    ):
        item_bg = "#103A78"
        selected_green = "#1F9D68"
        border_color = selected_green if active else "#315DAA"
        item = tk.Frame(
            parent,
            bg=item_bg,
            takefocus=command is not None,
            cursor="hand2" if command else "arrow",
            highlightbackground=border_color,
            highlightthickness=2 if active else 1,
        )

        indicator = tk.Frame(
            item,
            width=4,
            bg=selected_green if active else item_bg,
        )
        indicator.pack(side="left", fill="y")
        indicator.pack_propagate(False)

        content = tk.Frame(item, bg=item_bg, padx=11, pady=10)
        content.pack(side="left", fill="both", expand=True)

        icon_badge = tk.Label(
            content,
            text="✓" if active else icon,
            width=3,
            height=2,
            font=(self.FONT, 9, "bold"),
            fg="#FFFFFF" if active else "#D6E2F6",
            bg=selected_green if active else "#24539A",
        )
        icon_badge.pack(side="left", padx=(0, 11))

        text_group = tk.Frame(content, bg=item_bg)
        text_group.pack(side="left", fill="x", expand=True)
        title_label = tk.Label(
            text_group,
            text=title,
            font=(self.FONT, 11, "bold"),
            fg="#FFFFFF",
            bg=item_bg,
        )
        title_label.pack(anchor="w")
        subtitle_label = tk.Label(
            text_group,
            text=subtitle,
            font=(self.FONT, 8, "bold" if active else "normal"),
            fg="#FFFFFF" if active else "#A9BFDF",
            bg=selected_green if active else item_bg,
            padx=7 if active else 0,
            pady=2 if active else 0,
        )
        subtitle_label.pack(anchor="w", pady=(3, 0))

        if command:
            self.bind_sidebar_action(item, command)
        return item

    def bind_sidebar_action(self, widget, command):
        def run_keyboard_action(_event):
            command()
            return "break"

        widget.configure(cursor="hand2")
        widget.bind("<Button-1>", lambda _event: command())
        for child in widget.winfo_children():
            self.bind_sidebar_action(child, command)
        widget.bind("<Return>", run_keyboard_action)
        widget.bind("<space>", run_keyboard_action)

    def create_sidebar_venue_item(self, parent, venue, selected=False):
        card_bg = "#103A78"
        selected_green = "#1F9D68"
        card = tk.Frame(
            parent,
            bg=card_bg,
            padx=12,
            pady=10,
            highlightbackground=selected_green if selected else "#315DAA",
            highlightthickness=2 if selected else 1,
        )

        heading = tk.Frame(card, bg=card_bg)
        heading.pack(fill="x")
        tk.Label(
            heading,
            text="✓" if selected else "馆",
            width=3,
            height=2,
            font=(self.FONT, 9, "bold"),
            fg="#FFFFFF",
            bg=selected_green if selected else "#2A5DAA",
        ).pack(side="left", padx=(0, 10))

        title_group = tk.Frame(heading, bg=card_bg)
        title_group.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_group,
            text=venue["name"],
            font=(self.FONT, 11, "bold"),
            fg="#FFFFFF",
            bg=card_bg,
        ).pack(anchor="w")
        venue_status = tk.Label(
            title_group,
            text="✓ 当前选中" if selected else "点击切换到此场馆",
            font=(self.FONT, 8, "bold" if selected else "normal"),
            fg="#FFFFFF" if selected else "#B9CCEA",
            bg=selected_green if selected else card_bg,
            padx=7 if selected else 0,
            pady=2 if selected else 0,
        )
        venue_status.pack(anchor="w", pady=(3, 0))

        sports_row = tk.Frame(card, bg=card_bg)
        sports_row.pack(fill="x", pady=(7, 0))
        for sport_name in venue["sports"]:
            tk.Label(
                sports_row,
                text=sport_name,
                font=(self.FONT, 8),
                fg="#C8D8F1",
                bg="#1A4787",
                padx=7,
                pady=3,
            ).pack(side="left", padx=(0, 5))

        # 整张场馆卡都可点击，切换后查询项目会自动刷新。
        self.bind_sidebar_action(
            card,
            lambda name=venue["name"]: self.select_venue(name),
        )
        return card

    def refresh_sidebar_venue_items(self):
        if self.venue_section is None:
            return

        for child in self.venue_section.winfo_children():
            child.destroy()

        self.create_sidebar_section_header(
            self.venue_section,
            title="服务场馆",
            meta=f"{len(self.venue_options)} 个",
        ).pack(fill="x", padx=2, pady=(0, 9))

        for venue in self.venue_options:
            venue_item = self.create_sidebar_venue_item(
                self.venue_section,
                venue=venue,
                selected=venue["name"] == self.current_venue_name,
            )
            venue_item.pack(fill="x", pady=(0, 8))

    def build_main_area(self, parent):
        main = tk.Frame(parent, bg=self.COLOR_BG)
        main.pack(side="left", fill="both", expand=True)

        # 不写死高度：Windows 中文字体在高 DPI 下行高更大，让内容自行撑开，
        # 避免第二行标题的下半部分被容器裁掉。
        topbar = tk.Frame(main, bg=self.COLOR_CARD)
        topbar.pack(fill="x")

        title_wrap = tk.Frame(topbar, bg=self.COLOR_CARD)
        title_wrap.pack(side="left", padx=30, pady=(17, 15))
        self.main_subtitle_var = tk.StringVar(value="体育场馆")
        tk.Label(
            title_wrap,
            textvariable=self.main_subtitle_var,
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_BLUE_MID,
            bg=self.COLOR_CARD,
        ).pack(anchor="w")
        self.main_title_var = tk.StringVar(value="场地预约查询")
        tk.Label(
            title_wrap,
            textvariable=self.main_title_var,
            font=(self.FONT, 20, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w", pady=(2, 0))

        self.top_status_var = tk.StringVar(value="●  系统就绪")
        self.top_status_label = tk.Label(
            topbar,
            textvariable=self.top_status_var,
            font=(self.FONT, 9, "bold"),
            fg=self.COLOR_GREEN,
            bg=self.COLOR_GREEN_BG,
            padx=12,
            pady=7,
        )
        self.top_status_label.pack(side="right", padx=30)

        self.content_host = tk.Frame(main, bg=self.COLOR_BG)
        self.content_host.pack(fill="both", expand=True)

        content = tk.Frame(self.content_host, bg=self.COLOR_BG, padx=28, pady=22)
        self.query_page = content
        content.pack(fill="both", expand=True)

        self.build_query_card(content)
        self.build_summary_row(content)
        self.build_results_card(content)

        footer = tk.Frame(content, bg=self.COLOR_BG)
        footer.pack(fill="x", pady=(10, 0))

        self.bottom_status_var = tk.StringVar(value="准备就绪")
        tk.Label(
            footer,
            textvariable=self.bottom_status_var,
            font=(self.FONT, 9),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_BG,
        ).pack(side="left")
        tk.Label(
            footer,
            text="JLU VENUE · 场地查询与预约",
            font=(self.FONT_LATIN, 8, "bold"),
            fg=self.COLOR_MUTED,
            bg=self.COLOR_BG,
        ).pack(side="right")

    def show_query_page(self):
        if self.current_view == "query":
            return
        if self.auto_page is not None:
            self.auto_page.pack_forget()
        self.query_page.pack(fill="both", expand=True)
        self.current_view = "query"
        self.main_subtitle_var.set("体育场馆")
        self.main_title_var.set("场地预约查询")
        self.refresh_sidebar_nav_items()

    def build_query_card(self, parent):
        card = self.create_card(parent, padx=21, pady=18)
        card.pack(fill="x", pady=(0, 14))

        heading = tk.Frame(card, bg=self.COLOR_CARD)
        heading.pack(fill="x", pady=(0, 16))
        tk.Label(
            heading,
            text="快速查询",
            font=(self.FONT, 14, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(side="left")
        self.query_venue_badge_var = tk.StringVar(value=self.current_venue_name)
        tk.Label(
            heading,
            textvariable=self.query_venue_badge_var,
            font=(self.FONT, 9, "bold"),
            fg=self.COLOR_BLUE,
            bg=self.COLOR_BLUE_TINT,
            padx=10,
            pady=5,
        ).pack(side="right")

        row = tk.Frame(card, bg=self.COLOR_CARD)
        row.pack(fill="x")
        row.grid_columnconfigure(2, weight=1)

        sport_group = tk.Frame(row, bg=self.COLOR_CARD)
        sport_group.grid(row=0, column=0, sticky="w", padx=(0, 26))
        tk.Label(
            sport_group,
            text="运动项目",
            font=(self.FONT, 9, "bold"),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w", pady=(0, 7))

        initial_auto_config = self.startup_config
        self.venue_var = tk.StringVar(value=initial_auto_config["venue"])
        self.current_venue_name = initial_auto_config["venue"]
        if self.query_venue_badge_var is not None:
            self.query_venue_badge_var.set(self.current_venue_name)

        self.sport_var = tk.StringVar(value=initial_auto_config["sport"])
        self.sport_choices = tk.Frame(sport_group, bg=self.COLOR_CARD)
        self.sport_choices.pack(anchor="w")
        self.rebuild_sport_buttons()

        date_group = tk.Frame(row, bg=self.COLOR_CARD)
        date_group.grid(row=0, column=1, sticky="w")
        tk.Label(
            date_group,
            text="查询日期",
            font=(self.FONT, 9, "bold"),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w", pady=(0, 7))

        self.date_var = tk.StringVar(
            value="today" if initial_auto_config["target_day"] == "今天" else "tomorrow"
        )
        date_choices = tk.Frame(date_group, bg=self.COLOR_CARD)
        date_choices.pack(anchor="w")
        today = date.today()
        date_labels = {
            "today": f"今天 · {today.strftime('%m月%d日')}",
            "tomorrow": f"明天 · {(today + timedelta(days=1)).strftime('%m月%d日')}",
        }
        for date_key, label in date_labels.items():
            button = self.create_select_button(
                date_choices,
                label,
                lambda key=date_key: self.select_date(key),
            )
            button.pack(side="left", padx=(0, 7))
            self.date_buttons[date_key] = button

        self.query_button = tk.Label(
            row,
            text="查询可预约场地  →",
            font=(self.FONT, 11, "bold"),
            fg="#FFFFFF",
            bg=self.COLOR_BLUE,
            bd=0,
            padx=20,
            pady=13,
            cursor="hand2",
        )
        self.query_button.grid(row=0, column=3, sticky="se")
        self.query_button.bind("<Button-1>", lambda _event: self.start_query())

        self.update_select_buttons()

    def build_summary_row(self, parent):
        self.summary_sport_var = tk.StringVar(value="未查询")
        self.summary_date_var = tk.StringVar(value="未查询")
        self.summary_court_count_var = tk.StringVar(value="0")
        self.summary_slot_count_var = tk.StringVar(value="0")
        self.summary_status_var = tk.StringVar(value="准备就绪")

        row = tk.Frame(parent, bg=self.COLOR_BG)
        row.pack(fill="x", pady=(0, 14))
        for column in range(4):
            row.grid_columnconfigure(column, weight=1)

        items = [
            ("场馆 / 项目", self.summary_sport_var, "项"),
            ("查询日期", self.summary_date_var, "日"),
            ("可预约场地", self.summary_court_count_var, "馆"),
            ("可预约时段", self.summary_slot_count_var, "时"),
        ]
        for index, (title, value_var, icon_text) in enumerate(items):
            card = self.create_summary_item(row, title, value_var, icon_text)
            card.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0 if index == 0 else 5, 0 if index == 3 else 5),
            )

    def build_results_card(self, parent):
        card = self.create_card(parent, padx=20, pady=17)
        card.pack(fill="both", expand=True)

        header = tk.Frame(card, bg=self.COLOR_CARD)
        header.pack(fill="x", pady=(0, 13))

        title_wrap = tk.Frame(header, bg=self.COLOR_CARD)
        title_wrap.pack(side="left")
        tk.Label(
            title_wrap,
            text="可预约场地",
            font=(self.FONT, 14, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w")
        self.result_subtitle_var = tk.StringVar(value="查询后将在这里展示空闲时段")
        tk.Label(
            title_wrap,
            textvariable=self.result_subtitle_var,
            font=(self.FONT, 9),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w", pady=(4, 0))

        self.clear_button = tk.Label(
            header,
            text="清空结果",
            font=(self.FONT, 9, "bold"),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CONTROL,
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
        )
        self.clear_button.pack(side="right")
        self.clear_button.bind("<Button-1>", lambda _event: self.clear_results())

        self.result_status_label = tk.Label(
            header,
            textvariable=self.summary_status_var,
            font=(self.FONT, 9, "bold"),
            fg=self.COLOR_GREEN,
            bg=self.COLOR_GREEN_BG,
            padx=10,
            pady=6,
        )
        self.result_status_label.pack(side="right", padx=(0, 9))

        tk.Frame(card, bg=self.COLOR_BORDER, height=1).pack(fill="x")

        self.result_scroll = ScrollableFrame(card, bg=self.COLOR_CARD)
        self.result_scroll.pack(fill="both", expand=True, pady=(12, 0))

        self.show_empty_state(
            "等待查询",
            "选择运动项目和日期，点击查询即可查看可预约时段",
        )

    def create_card(self, parent, **kwargs):
        return tk.Frame(
            parent,
            bg=self.COLOR_CARD,
            highlightbackground=self.COLOR_BORDER,
            highlightthickness=1,
            bd=0,
            **kwargs,
        )

    def create_select_button(self, parent, text, command):
        button = tk.Label(
            parent,
            text=text,
            font=(self.FONT, 10),
            bd=0,
            padx=14,
            pady=9,
            cursor="hand2",
        )
        button.bind("<Button-1>", lambda _event: command())
        return button

    def create_summary_item(self, parent, title, value_var, icon_text):
        card = self.create_card(parent, padx=14, pady=13)

        badge = tk.Label(
            card,
            text=icon_text,
            font=(self.FONT, 9, "bold"),
            fg=self.COLOR_BLUE,
            bg=self.COLOR_BLUE_TINT,
            width=3,
            height=2,
        )
        badge.pack(side="left", padx=(0, 12))

        text_wrap = tk.Frame(card, bg=self.COLOR_CARD)
        text_wrap.pack(side="left", fill="y")
        tk.Label(
            text_wrap,
            text=title,
            font=(self.FONT, 9),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w")
        tk.Label(
            text_wrap,
            textvariable=value_var,
            font=(self.FONT, 14, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w", pady=(4, 0))

        return card

    def select_venue(self, venue_name):
        if venue_name not in VENUES:
            return
        if self.current_view == "auto" and self.is_saving_auto_config:
            return

        changed = venue_name != self.current_venue_name
        self.current_venue_name = venue_name
        if self.venue_var is not None:
            self.venue_var.set(venue_name)
        if self.query_venue_badge_var is not None:
            self.query_venue_badge_var.set(venue_name)

        self.rebuild_sport_buttons()
        if self.auto_venue_var is not None:
            self.auto_venue_var.set(venue_name)
            if self.auto_venue_badge_var is not None:
                self.auto_venue_badge_var.set(venue_name)
            self.rebuild_auto_sport_buttons()
            self.update_auto_setting_controls()
        self.refresh_sidebar_venue_items()

        if changed and hasattr(self, "result_scroll"):
            self.clear_results()
            self.bottom_status_var.set(f"已切换至 {venue_name}")

    def rebuild_sport_buttons(self):
        if self.sport_choices is None or self.sport_var is None or self.venue_var is None:
            return

        sports = get_sports_for_venue(self.venue_var.get())
        if self.sport_var.get() not in sports:
            self.sport_var.set(next(iter(sports)))

        for child in self.sport_choices.winfo_children():
            child.destroy()
        self.sport_buttons = {}

        for sport_name in sports:
            button = self.create_select_button(
                self.sport_choices,
                sport_name,
                lambda name=sport_name: self.select_sport(name),
            )
            button.pack(side="left", padx=(0, 7))
            self.sport_buttons[sport_name] = button

        self.update_select_buttons()

    def select_sport(self, sport_name):
        if self.venue_var is None:
            return
        if sport_name not in get_sports_for_venue(self.venue_var.get()):
            return
        self.sport_var.set(sport_name)
        self.update_select_buttons()

    def select_date(self, date_key):
        self.date_var.set(date_key)
        self.update_select_buttons()

    def update_select_buttons(self):
        for name, button in self.sport_buttons.items():
            self.style_select_button(button, name == self.sport_var.get())
        for key, button in self.date_buttons.items():
            self.style_select_button(button, key == self.date_var.get())

    def style_select_button(self, button, selected):
        if selected:
            button.configure(
                font=(self.FONT, 10, "bold"),
                fg=self.COLOR_BLUE,
                bg=self.COLOR_BLUE_TINT,
            )
        else:
            button.configure(
                font=(self.FONT, 10),
                fg=self.COLOR_SUBTEXT,
                bg=self.COLOR_CONTROL,
            )

    def get_selected_date(self):
        today = date.today()
        if self.date_var.get() == "today":
            return today.isoformat()
        return (today + timedelta(days=1)).isoformat()

    def start_query(self):
        if self.is_loading:
            return

        token = self.get_token()
        if not token:
            return

        venue_name = self.venue_var.get()
        sport_name = self.sport_var.get()
        try:
            shop_num, sport_short_name = resolve_venue_sport(venue_name, sport_name)
        except ValueError as exc:
            messagebox.showerror("场馆 / 项目配置错误", str(exc))
            return
        query_date = self.get_selected_date()

        self.summary_sport_var.set(f"{venue_name} · {sport_name}")
        self.summary_date_var.set(query_date)
        self.summary_court_count_var.set("—")
        self.summary_slot_count_var.set("—")
        self.summary_status_var.set("查询中")
        self.result_subtitle_var.set("正在获取学校场馆系统的最新数据")
        self.bottom_status_var.set(f"正在查询 · {venue_name} · {sport_name} · {query_date}")
        self.set_status("loading")
        self.set_loading(True)

        self.clear_result_rows()
        self.show_empty_state(
            "正在查询",
            "正在连接吉林大学场馆服务，请稍候…",
            accent=self.COLOR_BLUE,
        )

        threading.Thread(
            target=self.run_query,
            args=(query_date, venue_name, sport_name, shop_num, sport_short_name, token),
            daemon=True,
        ).start()

    def run_query(self, query_date, venue_name, sport_name, shop_num, sport_short_name, token):
        try:
            data = query_courts(
                query_date=query_date,
                sport_short_name=sport_short_name,
                shop_num=shop_num,
                token=token,
            )
            available_slots = extract_available_slots(data)
            self.root.after(
                0,
                self.show_result,
                query_date,
                venue_name,
                sport_name,
                shop_num,
                available_slots,
            )
        except Exception as exc:
            self.root.after(0, self.show_error, str(exc))

    @staticmethod
    def group_slots_by_court(available_slots):
        grouped = {}
        sorted_slots = sorted(
            available_slots,
            key=lambda item: (item["court_name"], item["start"]),
        )
        for slot in sorted_slots:
            # 保留完整 slot，而不是只留下 start/end。
            # 后续预约需要 place_short_name，例如 ppq4 / ymq3 / pkq2。
            grouped.setdefault(slot["court_name"], []).append(slot)

        return grouped

    def show_result(self, query_date, venue_name, sport_name, shop_num, available_slots):
        self.clear_result_rows()

        # 保存“这批查询结果”对应的日期。
        # 即使用户之后切换了“今天/明天”，点击旧结果时仍使用原来的日期。
        self.last_query_date = query_date
        self.last_query_venue = venue_name
        self.last_query_shop_num = shop_num

        grouped = self.group_slots_by_court(available_slots)

        if not grouped:
            self.show_empty_state(
                "暂无可预约时段",
                "当前日期的场地可能已约满，可以切换项目或日期再试试",
            )
        else:
            for index, (court_name, slots) in enumerate(grouped.items()):
                self.create_court_row(court_name, slots, index)

        self.summary_sport_var.set(f"{venue_name} · {sport_name}")
        self.summary_date_var.set(query_date)
        self.summary_court_count_var.set(str(len(grouped)))
        self.summary_slot_count_var.set(str(len(available_slots)))
        self.summary_status_var.set("查询完成")
        self.result_subtitle_var.set(
            f"{venue_name} · {sport_name} · {query_date} · 共 {len(available_slots)} 个空闲时段"
        )
        self.bottom_status_var.set("查询完成，数据已更新")
        self.set_status("success")
        self.set_loading(False)

    def create_court_row(self, court_name, slots, index):
        row_bg = self.COLOR_BLUE_PALE if index % 2 == 0 else "#F8FAFD"
        row = tk.Frame(
            self.result_scroll.inner,
            bg=row_bg,
            highlightbackground=self.COLOR_BORDER,
            highlightthickness=1,
            padx=15,
            pady=13,
        )
        row.pack(fill="x", pady=(0, 9))
        row.grid_columnconfigure(1, weight=0, minsize=155)
        row.grid_columnconfigure(2, weight=1)

        tk.Label(
            row,
            text=f"{index + 1:02d}",
            font=(self.FONT_LATIN, 10, "bold"),
            fg=self.COLOR_BLUE,
            bg=self.COLOR_BLUE_TINT,
            width=4,
            height=2,
        ).grid(row=0, column=0, sticky="nw", padx=(0, 13))

        name_wrap = tk.Frame(row, bg=row_bg)
        name_wrap.grid(row=0, column=1, sticky="nw", padx=(0, 16))
        tk.Label(
            name_wrap,
            text=court_name,
            font=(self.FONT, 12, "bold"),
            fg=self.COLOR_TEXT,
            bg=row_bg,
        ).pack(anchor="w")
        tk.Label(
            name_wrap,
            text=f"{len(slots)} 个可预约时段",
            font=(self.FONT, 9),
            fg=self.COLOR_SUBTEXT,
            bg=row_bg,
        ).pack(anchor="w", pady=(5, 0))

        time_area = tk.Frame(row, bg=row_bg)
        time_area.grid(row=0, column=2, sticky="ew")
        columns_per_row = 4

        for slot_index, slot in enumerate(slots):
            start = slot["start"]
            end = slot["end"]

            slot_row = slot_index // columns_per_row
            slot_column = slot_index % columns_per_row

            # 时间段现在是“可点击”的标签。
            chip = tk.Label(
                time_area,
                text=f"●  {start} – {end}",
                font=(self.FONT_LATIN, 9, "bold"),
                fg=self.COLOR_GREEN,
                bg=self.COLOR_GREEN_BG,
                padx=11,
                pady=8,
                cursor="hand2",
            )
            chip.grid(
                row=slot_row,
                column=slot_column,
                sticky="w",
                padx=(0, 7),
                pady=3,
            )

            # 默认参数 s=slot 用来固定当前循环里的 slot，
            # 避免 lambda 全部指向最后一个时间段。
            chip.bind(
                "<Button-1>",
                lambda _event, s=slot: self.on_slot_clicked(s),
            )
            chip.bind(
                "<Enter>",
                lambda _event, c=chip: c.configure(
                    bg="#D9F2E7",
                    fg="#0F6847",
                ),
            )
            chip.bind(
                "<Leave>",
                lambda _event, c=chip: c.configure(
                    bg=self.COLOR_GREEN_BG,
                    fg=self.COLOR_GREEN,
                ),
            )

    def on_slot_clicked(self, slot):
        """
        点击可预约时段后打开预约信息窗口。

        当前阶段只做：
        选择时段 -> 输入同行人学工号 -> 查询并验证同行人

        不会调用 can_book()，也不会调用 book_place()。
        """

        if not self.last_query_date or not self.last_query_venue or not self.last_query_shop_num:
            messagebox.showwarning(
                "无法选择",
                "当前查询结果缺少日期信息，请重新查询一次。",
            )
            return

        place_short_name = slot.get("place_short_name")

        if not place_short_name:
            messagebox.showerror(
                "缺少场地参数",
                "当前 slot 中没有 place_short_name。\n\n"
                "请确认 jlu_booking/api.py 的 extract_available_slots() "
                "已经返回 place_short_name。",
            )
            return

        self.selected_slot = {
            "venue": self.last_query_venue,
            "shop_num": self.last_query_shop_num,
            "day": self.last_query_date,
            "court_name": slot["court_name"],
            "place_short_name": place_short_name,
            "start": slot["start"],
            "end": slot["end"],
        }

        self.open_booking_dialog()

    def open_booking_dialog(self):
        """打开预约信息窗口，完成同行人验证、预约前检查和人工确认预约。"""

        if not self.selected_slot:
            return

        # 如果窗口已经存在，就把它提到最前面。
        if self.booking_dialog and self.booking_dialog.winfo_exists():
            self.booking_dialog.lift()
            self.booking_dialog.focus_force()
            return

        self.validated_companion = None
        self.is_verifying_companion = False

        dialog = tk.Toplevel(self.root)
        self.booking_dialog = dialog

        dialog.title("预约场地")
        dialog.configure(bg=self.COLOR_BG)
        dialog.resizable(False, True)
        dialog.transient(self.root)
        dialog.grab_set()

        width, height, x, y = self.dialog_geometry(560, 690)
        dialog.geometry(f"{width}x{height}+{x}+{y}")

        dialog.protocol("WM_DELETE_WINDOW", self.close_booking_dialog)

        shell = tk.Frame(
            dialog,
            bg=self.COLOR_CARD,
            highlightbackground=self.COLOR_BORDER,
            highlightthickness=1,
        )
        shell.pack(fill="both", expand=True, padx=18, pady=18)

        # 顶部标题
        header = tk.Frame(shell, bg=self.COLOR_CARD)
        header.pack(fill="x", padx=26, pady=(24, 18))

        tk.Label(
            header,
            text="预约场地",
            font=(self.FONT, 19, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w")

        tk.Label(
            header,
            text="确认场地信息，并验证同行人身份",
            font=(self.FONT, 9),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w", pady=(5, 0))

        tk.Frame(
            shell,
            bg=self.COLOR_BORDER,
            height=1,
        ).pack(fill="x", padx=26)

        # 场地信息卡
        slot_card = tk.Frame(
            shell,
            bg=self.COLOR_BLUE_PALE,
            highlightbackground="#D9E4F6",
            highlightthickness=1,
            padx=18,
            pady=16,
        )
        slot_card.pack(fill="x", padx=26, pady=(20, 18))

        tk.Label(
            slot_card,
            text=self.selected_slot.get("venue", "未知场馆"),
            font=(self.FONT, 9, "bold"),
            fg=self.COLOR_BLUE_MID,
            bg=self.COLOR_BLUE_PALE,
        ).pack(anchor="w", pady=(0, 5))

        tk.Label(
            slot_card,
            text=self.selected_slot["court_name"],
            font=(self.FONT, 15, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_BLUE_PALE,
        ).pack(anchor="w")

        tk.Label(
            slot_card,
            text=(
                f"{self.selected_slot['day']}    "
                f"{self.selected_slot['start']} – "
                f"{self.selected_slot['end']}"
            ),
            font=(self.FONT_LATIN, 10, "bold"),
            fg=self.COLOR_BLUE,
            bg=self.COLOR_BLUE_PALE,
        ).pack(anchor="w", pady=(7, 0))

        # 同行人区域
        companion_wrap = tk.Frame(shell, bg=self.COLOR_CARD)
        companion_wrap.pack(fill="x", padx=26)

        tk.Label(
            companion_wrap,
            text="同行人学工号",
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w")

        tk.Label(
            companion_wrap,
            text="输入同行人的学工号后，程序会向学校系统查询并核验身份。",
            font=(self.FONT, 9),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w", pady=(5, 9))

        input_row = tk.Frame(companion_wrap, bg=self.COLOR_CARD)
        input_row.pack(fill="x")

        self.companion_entry = tk.Entry(
            input_row,
            font=(self.FONT_LATIN, 11),
            fg=self.COLOR_TEXT,
            bg="#F8FAFD",
            insertbackground=self.COLOR_TEXT,
            relief="flat",
            highlightbackground=self.COLOR_BORDER,
            highlightcolor=self.COLOR_BLUE_MID,
            highlightthickness=1,
        )
        self.companion_entry.pack(
            side="left",
            fill="x",
            expand=True,
            ipady=10,
        )
        try:
            auto_config = load_auto_config(AUTO_CONFIG_FILE, create_if_missing=True)
            self.companion_entry.insert(0, auto_config["companion_student_number"])
        except (OSError, ValueError):
            pass
        self.companion_entry.focus_set()
        self.companion_entry.bind(
            "<Return>",
            lambda _event: self.start_verify_companion(),
        )
        self.companion_entry.bind(
            "<KeyRelease>",
            self.on_companion_input_changed,
        )

        self.verify_companion_button = tk.Label(
            input_row,
            text="验证同行人",
            font=(self.FONT, 10, "bold"),
            fg="#FFFFFF",
            bg=self.COLOR_BLUE,
            padx=16,
            pady=11,
            cursor="hand2",
        )
        self.verify_companion_button.pack(side="left", padx=(10, 0))
        self.verify_companion_button.bind(
            "<Button-1>",
            lambda _event: self.start_verify_companion(),
        )

        # 验证结果
        self.companion_status_var = tk.StringVar(
            value="尚未验证同行人"
        )

        self.companion_status_label = tk.Label(
            companion_wrap,
            textvariable=self.companion_status_var,
            font=(self.FONT, 9, "bold"),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CONTROL,
            anchor="w",
            padx=12,
            pady=10,
        )
        self.companion_status_label.pack(fill="x", pady=(12, 0))

        # 预约前检查状态
        self.booking_check_status_var = tk.StringVar(
            value="验证同行人后，可执行预约前检查"
        )
        self.booking_check_status_label = tk.Label(
            shell,
            textvariable=self.booking_check_status_var,
            font=(self.FONT, 9, "bold"),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CONTROL,
            anchor="w",
            padx=14,
            pady=10,
        )
        self.booking_check_status_label.pack(
            fill="x",
            padx=26,
            pady=(18, 0),
        )

        # 当前阶段说明
        notice = tk.Frame(
            shell,
            bg="#FFF8E8",
            padx=14,
            pady=12,
        )
        notice.pack(fill="x", padx=26, pady=(14, 0))

        tk.Label(
            notice,
            text="当前阶段不会提交真实预约",
            font=(self.FONT, 9, "bold"),
            fg="#8A5A00",
            bg="#FFF8E8",
        ).pack(anchor="w")

        tk.Label(
            notice,
            text=(
                "“检查能否预约”只调用 canBook 预约前检查接口，"
                "不会调用 freeBuyPlace 创建预约。"
            ),
            font=(self.FONT, 9),
            fg="#8A5A00",
            bg="#FFF8E8",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        # 底部按钮
        footer = tk.Frame(shell, bg=self.COLOR_CARD)
        footer.pack(fill="x", padx=26, pady=(20, 24))

        close_button = tk.Label(
            footer,
            text="关闭",
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CONTROL,
            padx=18,
            pady=10,
            cursor="hand2",
        )
        close_button.pack(side="right")
        close_button.bind(
            "<Button-1>",
            lambda _event: self.close_booking_dialog(),
        )

        self.booking_check_button = tk.Label(
            footer,
            text="检查能否预约",
            font=(self.FONT, 10, "bold"),
            fg="#A7B0BF",
            bg="#E9EDF3",
            padx=20,
            pady=10,
            cursor="arrow",
        )
        self.booking_check_button.pack(side="right", padx=(0, 10))
        self.booking_check_button.bind(
            "<Button-1>",
            lambda _event: self.start_booking_check(),
        )

        self.real_booking_button = tk.Label(
            footer,
            text="确认真实预约",
            font=(self.FONT, 10, "bold"),
            fg="#A7B0BF",
            bg="#E9EDF3",
            padx=20,
            pady=10,
            cursor="arrow",
        )
        self.real_booking_button.pack(side="right", padx=(0, 10))
        self.real_booking_button.bind(
            "<Button-1>",
            lambda _event: self.confirm_real_booking(),
        )

        self.update_real_booking_button()

    def close_booking_dialog(self):
        """关闭预约窗口并清理临时状态。"""

        if self.booking_dialog and self.booking_dialog.winfo_exists():
            try:
                self.booking_dialog.grab_release()
            except tk.TclError:
                pass
            self.booking_dialog.destroy()

        self.booking_dialog = None
        self.companion_entry = None
        self.companion_status_var = None
        self.verify_companion_button = None
        self.validated_companion = None
        self.is_verifying_companion = False
        self.booking_check_button = None
        self.booking_check_status_var = None
        self.booking_check_status_label = None
        self.is_checking_booking = False
        self.booking_check_passed = False
        self.real_booking_button = None
        self.is_submitting_booking = False
        self.booking_submitted = False

    def on_companion_input_changed(self, _event=None):
        """
        用户修改学工号后，使之前的验证结果失效。
        防止以后预约时误用旧的同行人 ID。
        """

        if self.validated_companion is not None:
            self.validated_companion = None

        self.booking_check_passed = False
        self.booking_submitted = False
        self.update_real_booking_button()

        if self.booking_check_status_var is not None:
            self.booking_check_status_var.set(
                "验证同行人后，可执行预约前检查"
            )

        if self.booking_check_status_label is not None:
            self.booking_check_status_label.configure(
                fg=self.COLOR_SUBTEXT,
                bg=self.COLOR_CONTROL,
            )

        self.update_booking_check_button()

        if (
            self.companion_status_var is not None
            and not self.is_verifying_companion
        ):
            self.companion_status_var.set("尚未验证同行人")
            self.companion_status_label.configure(
                fg=self.COLOR_SUBTEXT,
                bg=self.COLOR_CONTROL,
            )

    def set_companion_verifying(self, verifying):
        self.is_verifying_companion = verifying

        if not self.verify_companion_button:
            return

        if verifying:
            self.verify_companion_button.configure(
                text="正在验证…",
                fg="#D8E2F2",
                bg="#7893C2",
                cursor="arrow",
            )
        else:
            self.verify_companion_button.configure(
                text="验证同行人",
                fg="#FFFFFF",
                bg=self.COLOR_BLUE,
                cursor="hand2",
            )

    def start_verify_companion(self):
        """开始后台验证同行人。"""

        if self.is_verifying_companion:
            return

        if not self.companion_entry:
            return

        student_number = self.companion_entry.get().strip()

        if not student_number:
            messagebox.showwarning(
                "请输入学工号",
                "请先输入同行人的学工号。",
                parent=self.booking_dialog,
            )
            self.companion_entry.focus_set()
            return

        token = self.get_token(parent=self.booking_dialog)

        if not token:
            return

        self.validated_companion = None
        self.companion_status_var.set("正在向学校系统验证同行人…")
        self.companion_status_label.configure(
            fg=self.COLOR_BLUE,
            bg=self.COLOR_BLUE_TINT,
        )
        self.set_companion_verifying(True)

        threading.Thread(
            target=self.run_verify_companion,
            args=(student_number, token),
            daemon=True,
        ).start()

    def run_verify_companion(self, student_number, token):
        """在线程中请求 getUserInfo，避免界面卡住。"""

        try:
            user = get_companion_user(
                student_number=student_number,
                token=token,
            )

            self.root.after(
                0,
                self.show_companion_verified,
                student_number,
                user,
            )

        except Exception as exc:
            self.root.after(
                0,
                self.show_companion_verify_error,
                str(exc),
            )

    def show_companion_verified(self, student_number, user):
        """显示同行人验证成功结果。"""

        if not (
            self.booking_dialog
            and self.booking_dialog.winfo_exists()
            and self.companion_entry
        ):
            return

        # 验证返回期间如果用户已经改了输入框，则丢弃旧请求结果。
        if self.companion_entry.get().strip() != student_number:
            self.set_companion_verifying(False)
            return

        self.validated_companion = {
            "student_number": student_number,
            "name": user.get("name", "未知"),
            "id": user.get("id"),
        }

        self.companion_status_var.set(
            f"✓ 验证通过：{self.validated_companion['name']}"
        )
        self.companion_status_label.configure(
            fg=self.COLOR_GREEN,
            bg=self.COLOR_GREEN_BG,
        )

        self.booking_check_passed = False
        if self.booking_check_status_var is not None:
            self.booking_check_status_var.set(
                "同行人已验证，可以执行预约前检查"
            )
        if self.booking_check_status_label is not None:
            self.booking_check_status_label.configure(
                fg=self.COLOR_BLUE,
                bg=self.COLOR_BLUE_TINT,
            )

        self.set_companion_verifying(False)
        self.update_booking_check_button()
        self.update_real_booking_button()

    def show_companion_verify_error(self, error_message):
        """显示同行人验证失败结果。"""

        if not (
            self.booking_dialog
            and self.booking_dialog.winfo_exists()
            and self.companion_status_var
        ):
            return

        self.validated_companion = None
        self.booking_check_passed = False
        self.booking_submitted = False
        self.update_booking_check_button()
        self.update_real_booking_button()

        if self.booking_check_status_var is not None:
            self.booking_check_status_var.set(
                "同行人验证失败，暂不能执行预约前检查"
            )
        if self.booking_check_status_label is not None:
            self.booking_check_status_label.configure(
                fg=self.COLOR_SUBTEXT,
                bg=self.COLOR_CONTROL,
            )

        self.companion_status_var.set(
            "验证失败，请检查学工号后重试"
        )
        self.companion_status_label.configure(
            fg=self.COLOR_RED,
            bg=self.COLOR_RED_BG,
        )

        self.set_companion_verifying(False)

        messagebox.showerror(
            "同行人验证失败",
            error_message,
            parent=self.booking_dialog,
        )

    def update_booking_check_button(self):
        """根据当前状态更新“检查能否预约”按钮。"""

        if not self.booking_check_button:
            return

        enabled = (
            self.validated_companion is not None
            and not self.is_verifying_companion
            and not self.is_checking_booking
        )

        if self.is_checking_booking:
            self.booking_check_button.configure(
                text="正在检查…",
                fg="#D8E2F2",
                bg="#7893C2",
                cursor="arrow",
            )
        elif enabled:
            self.booking_check_button.configure(
                text="检查能否预约",
                fg="#FFFFFF",
                bg=self.COLOR_BLUE,
                cursor="hand2",
            )
        else:
            self.booking_check_button.configure(
                text="检查能否预约",
                fg="#A7B0BF",
                bg="#E9EDF3",
                cursor="arrow",
            )

    def start_booking_check(self):
        """
        调用 canBook 做预约前检查。

        重要：
        这个函数本身不会调用 book_place()。
        只有用户后续明确点击“确认真实预约”时才会提交。
        """

        if self.is_checking_booking:
            return

        if self.validated_companion is None:
            messagebox.showwarning(
                "请先验证同行人",
                "请先输入同行人学工号并完成验证。",
                parent=self.booking_dialog,
            )
            return

        if not self.selected_slot:
            messagebox.showerror(
                "缺少场地信息",
                "没有找到当前选择的预约时段，请关闭窗口后重新选择。",
                parent=self.booking_dialog,
            )
            return

        token = self.get_token(parent=self.booking_dialog)
        if not token:
            return

        self.is_checking_booking = True
        self.booking_check_passed = False

        self.booking_check_status_var.set(
            "正在向学校系统检查当前场地是否仍可预约…"
        )
        self.booking_check_status_label.configure(
            fg=self.COLOR_BLUE,
            bg=self.COLOR_BLUE_TINT,
        )
        self.update_booking_check_button()

        threading.Thread(
            target=self.run_booking_check,
            args=(token,),
            daemon=True,
        ).start()

    def run_booking_check(self, token):
        """后台执行 canBook，避免阻塞 Tkinter 主线程。"""

        try:
            result = can_book(
                query_date=self.selected_slot["day"],
                start_time=self.selected_slot["start"],
                end_time=self.selected_slot["end"],
                place_short_name=self.selected_slot["place_short_name"],
                shop_num=self.selected_slot["shop_num"],
                token=token,
            )

            self.root.after(
                0,
                self.show_booking_check_success,
                result,
            )

        except Exception as exc:
            self.root.after(
                0,
                self.show_booking_check_error,
                str(exc),
            )

    def show_booking_check_success(self, _result):
        """显示 canBook 检查通过。"""

        if not (
            self.booking_dialog
            and self.booking_dialog.winfo_exists()
        ):
            return

        self.is_checking_booking = False
        self.booking_check_passed = True

        self.booking_check_status_var.set(
            "✓ 预约前检查通过：当前时段允许继续预约"
        )
        self.booking_check_status_label.configure(
            fg=self.COLOR_GREEN,
            bg=self.COLOR_GREEN_BG,
        )

        self.update_booking_check_button()
        self.update_real_booking_button()

        messagebox.showinfo(
            "预约前检查通过",
            (
                f"场馆：{self.selected_slot.get('venue', '未知场馆')}\n"
                f"场地：{self.selected_slot['court_name']}\n"
                f"日期：{self.selected_slot['day']}\n"
                f"时间：{self.selected_slot['start']} – "
                f"{self.selected_slot['end']}\n\n"
                "学校系统的 canBook 检查已返回 success。\n"
                "此步骤尚未提交真实预约；如需继续，请点击“确认真实预约”。"
            ),
            parent=self.booking_dialog,
        )

    def show_booking_check_error(self, error_message):
        """显示 canBook 检查失败。"""

        if not (
            self.booking_dialog
            and self.booking_dialog.winfo_exists()
        ):
            return

        self.is_checking_booking = False
        self.booking_check_passed = False
        self.update_real_booking_button()

        self.booking_check_status_var.set(
            "预约前检查未通过，请重新查询场地后再试"
        )
        self.booking_check_status_label.configure(
            fg=self.COLOR_RED,
            bg=self.COLOR_RED_BG,
        )

        self.update_booking_check_button()

        messagebox.showerror(
            "预约前检查失败",
            error_message,
            parent=self.booking_dialog,
        )

    def update_real_booking_button(self):
        """根据当前状态更新“确认真实预约”按钮。"""

        if not self.real_booking_button:
            return

        enabled = (
            self.booking_check_passed
            and self.validated_companion is not None
            and not self.is_checking_booking
            and not self.is_submitting_booking
            and not self.booking_submitted
        )

        if self.is_submitting_booking:
            self.real_booking_button.configure(
                text="正在提交…",
                fg="#D8E2F2",
                bg="#7893C2",
                cursor="arrow",
            )
        elif self.booking_submitted:
            self.real_booking_button.configure(
                text="预约已提交",
                fg=self.COLOR_GREEN,
                bg=self.COLOR_GREEN_BG,
                cursor="arrow",
            )
        elif enabled:
            self.real_booking_button.configure(
                text="确认真实预约",
                fg="#FFFFFF",
                bg=self.COLOR_GREEN,
                cursor="hand2",
            )
        else:
            self.real_booking_button.configure(
                text="确认真实预约",
                fg="#A7B0BF",
                bg="#E9EDF3",
                cursor="arrow",
            )

    def confirm_real_booking(self):
        """
        最终人工确认。

        只有：
        1. 同行人已验证
        2. canBook 已通过
        3. 用户再次明确点击“是”
        才会进入真实预约提交。
        """

        if self.is_submitting_booking or self.booking_submitted:
            return

        if not self.booking_check_passed:
            messagebox.showwarning(
                "请先检查能否预约",
                "请先完成“检查能否预约”，并确认 canBook 检查通过。",
                parent=self.booking_dialog,
            )
            return

        if self.validated_companion is None:
            messagebox.showwarning(
                "请先验证同行人",
                "同行人信息当前无有效验证结果。",
                parent=self.booking_dialog,
            )
            return

        if not self.selected_slot:
            messagebox.showerror(
                "缺少场地信息",
                "没有找到当前选择的预约时段，请重新选择。",
                parent=self.booking_dialog,
            )
            return

        companion_name = self.validated_companion.get("name", "未知")

        confirmed = messagebox.askyesno(
            "确认真实预约",
            (
                "即将向吉林大学场馆系统提交真实预约：\n\n"
                f"场馆：{self.selected_slot.get('venue', '未知场馆')}\n"
                f"场地：{self.selected_slot['court_name']}\n"
                f"日期：{self.selected_slot['day']}\n"
                f"时间：{self.selected_slot['start']} – "
                f"{self.selected_slot['end']}\n"
                f"同行人：{companion_name}\n\n"
                "提交成功后会产生真实预约记录。\n"
                "确定继续吗？"
            ),
            parent=self.booking_dialog,
            icon="warning",
        )

        if not confirmed:
            return

        token = self.get_token(parent=self.booking_dialog)

        if not token:
            return

        self.is_submitting_booking = True
        self.booking_check_status_var.set(
            "正在进行最终检查并提交真实预约，请勿重复点击…"
        )
        self.booking_check_status_label.configure(
            fg=self.COLOR_BLUE,
            bg=self.COLOR_BLUE_TINT,
        )

        self.update_booking_check_button()
        self.update_real_booking_button()

        threading.Thread(
            target=self.run_real_booking,
            args=(token,),
            daemon=True,
        ).start()

    def run_real_booking(self, token):
        """
        真正提交预约。

        为降低“检查通过后场地瞬间被占用”的概率，
        提交前再执行一次 canBook，然后立即调用 book_place。
        """

        try:
            # 最终提交前再次检查。
            can_book(
                query_date=self.selected_slot["day"],
                start_time=self.selected_slot["start"],
                end_time=self.selected_slot["end"],
                place_short_name=self.selected_slot["place_short_name"],
                shop_num=self.selected_slot["shop_num"],
                token=token,
            )

            result = book_place(
                query_date=self.selected_slot["day"],
                start_time=self.selected_slot["start"],
                end_time=self.selected_slot["end"],
                place_short_name=self.selected_slot["place_short_name"],
                court_name=self.selected_slot["court_name"],
                companion_user_ids=[
                    self.validated_companion["id"]
                ],
                shop_num=self.selected_slot["shop_num"],
                token=token,
            )

            self.root.after(
                0,
                self.show_real_booking_success,
                result,
            )

        except Exception as exc:
            self.root.after(
                0,
                self.show_real_booking_error,
                str(exc),
            )

    def show_real_booking_success(self, result):
        """显示真实预约成功。"""

        if not (
            self.booking_dialog
            and self.booking_dialog.winfo_exists()
        ):
            return

        self.is_submitting_booking = False
        self.booking_submitted = True
        self.booking_check_passed = False

        data = result.get("data", {}) if isinstance(result, dict) else {}
        money = data.get("money", "")
        pay_type = data.get("type", "")

        self.booking_check_status_var.set(
            "✓ 预约成功：学校系统已确认创建预约"
        )
        self.booking_check_status_label.configure(
            fg=self.COLOR_GREEN,
            bg=self.COLOR_GREEN_BG,
        )

        self.update_booking_check_button()
        self.update_real_booking_button()

        detail_lines = [
            "学校系统返回预约成功。",
            "",
            f"场馆：{self.selected_slot.get('venue', '未知场馆')}",
            f"场地：{self.selected_slot['court_name']}",
            f"日期：{self.selected_slot['day']}",
            (
                f"时间：{self.selected_slot['start']} – "
                f"{self.selected_slot['end']}"
            ),
            f"同行人：{self.validated_companion.get('name', '未知')}",
        ]

        if money != "":
            detail_lines.append(f"金额：{money}")
        if pay_type:
            detail_lines.append(f"类型：{pay_type}")

        messagebox.showinfo(
            "预约成功",
            "\n".join(detail_lines),
            parent=self.booking_dialog,
        )

        self.bottom_status_var.set(
            f"预约成功 · {self.selected_slot['court_name']} · "
            f"{self.selected_slot['day']} "
            f"{self.selected_slot['start']}-{self.selected_slot['end']}"
        )

    def show_real_booking_error(self, error_message):
        """显示真实预约失败。"""

        if not (
            self.booking_dialog
            and self.booking_dialog.winfo_exists()
        ):
            return

        self.is_submitting_booking = False
        self.booking_submitted = False

        # 失败后要求重新 canBook，避免直接重复提交。
        self.booking_check_passed = False

        self.booking_check_status_var.set(
            "预约提交失败，请重新查询并再次执行预约前检查"
        )
        self.booking_check_status_label.configure(
            fg=self.COLOR_RED,
            bg=self.COLOR_RED_BG,
        )

        self.update_booking_check_button()
        self.update_real_booking_button()

        messagebox.showerror(
            "预约失败",
            error_message,
            parent=self.booking_dialog,
        )

    def open_auto_settings_dialog(self):
        """Show automatic booking as a first-class page in the main area."""
        if self.auto_page is not None and self.auto_page.winfo_exists():
            self.query_page.pack_forget()
            self.auto_page.pack(fill="both", expand=True)
            self.current_view = "auto"
            self.main_subtitle_var.set("自动任务")
            self.main_title_var.set("自动预约")
            self.auto_venue_var.set(self.current_venue_name)
            self.auto_venue_badge_var.set(self.current_venue_name)
            self.rebuild_auto_sport_buttons()
            self.update_auto_setting_controls()
            self.refresh_sidebar_nav_items()
            return

        try:
            config = load_auto_config(AUTO_CONFIG_FILE, create_if_missing=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("配置读取失败", str(exc), parent=self.root)
            return

        self.query_page.pack_forget()
        shell = tk.Frame(self.content_host, bg=self.COLOR_BG, padx=28, pady=22)
        self.auto_page = shell
        self.auto_settings_dialog = shell
        shell.pack(fill="both", expand=True)
        self.current_view = "auto"
        self.main_subtitle_var.set("自动任务")
        self.main_title_var.set("自动预约")
        self.refresh_sidebar_nav_items()

        settings_scroll = ScrollableFrame(shell, bg=self.COLOR_BG)
        settings_scroll.pack(fill="both", expand=True)

        card = self.create_card(settings_scroll.inner, padx=18, pady=14)
        card.pack(fill="both", expand=True)

        venue_heading = tk.Frame(card, bg=self.COLOR_CARD)
        venue_heading.pack(fill="x", pady=(0, 12))
        tk.Label(
            venue_heading,
            text="当前场馆",
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(side="left")
        self.auto_venue_var = tk.StringVar(value=self.current_venue_name)
        self.auto_venue_badge_var = tk.StringVar(value=self.current_venue_name)
        tk.Label(
            venue_heading,
            textvariable=self.auto_venue_badge_var,
            font=(self.FONT, 9, "bold"),
            fg="#FFFFFF",
            bg=self.COLOR_BLUE,
            padx=12,
            pady=6,
        ).pack(side="right")
        tk.Label(
            card,
            text="场馆由左侧统一选择，切换后下方项目会自动更新。",
            font=(self.FONT, 8),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w", pady=(0, 10))

        # 运动项目（随场馆动态变化）
        tk.Label(
            card,
            text="预约项目",
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w")
        available_sports = get_sports_for_venue(self.current_venue_name)
        initial_sport = (
            config["sport"]
            if config["venue"] == self.current_venue_name
            and config["sport"] in available_sports
            else next(iter(available_sports))
        )
        self.auto_sport_var = tk.StringVar(value=initial_sport)
        self.auto_sport_buttons = {}
        self.auto_sport_row = tk.Frame(card, bg=self.COLOR_CARD)
        self.auto_sport_row.pack(fill="x", pady=(6, 10))
        self.rebuild_auto_sport_buttons()

        # 预约日期
        tk.Label(
            card,
            text="预约日期",
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w")
        self.auto_day_var = tk.StringVar(value=config["target_day"])
        self.auto_day_buttons = {}
        day_row = tk.Frame(card, bg=self.COLOR_CARD)
        day_row.pack(fill="x", pady=(6, 10))
        for day_name in ("今天", "明天"):
            button = self.create_auto_select_button(
                day_row,
                day_name,
                lambda name=day_name: self.select_auto_day(name),
                width=9,
            )
            button.pack(side="left", padx=(0, 8))
            self.auto_day_buttons[day_name] = button

        self.auto_selection_summary_var = tk.StringVar()
        tk.Label(
            card,
            textvariable=self.auto_selection_summary_var,
            font=(self.FONT, 8, "bold"),
            fg=self.COLOR_BLUE,
            bg=self.COLOR_CARD,
        ).pack(anchor="w", pady=(0, 10))

        # 同行人 + 首选场地
        two_col = tk.Frame(card, bg=self.COLOR_CARD)
        two_col.pack(fill="x", pady=(0, 10))
        two_col.grid_columnconfigure(0, weight=3)
        two_col.grid_columnconfigure(1, weight=1)

        companion_col = tk.Frame(two_col, bg=self.COLOR_CARD)
        companion_col.grid(row=0, column=0, sticky="ew", padx=(0, 14))
        required_label = tk.Label(
            companion_col,
            text="同行人学工号  ·  必填",
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_RED,
            bg=self.COLOR_CARD,
        )
        required_label.pack(anchor="w")
        self.auto_companion_var = tk.StringVar(
            value=config["companion_student_number"]
        )
        self.auto_companion_entry = tk.Entry(
            companion_col,
            textvariable=self.auto_companion_var,
            font=(self.FONT, 11),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CONTROL,
            insertbackground=self.COLOR_TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.COLOR_BORDER,
            highlightcolor=self.COLOR_BLUE_MID,
        )
        self.auto_companion_entry.pack(fill="x", pady=(8, 5), ipady=8)
        self.auto_companion_status_var = tk.StringVar(
            value=(
                "已读取本机保存值；保存前会重新验证"
                if config["companion_student_number"]
                else "必填；验证成功后会记住，下次打开自动填写"
            )
        )
        self.auto_companion_status_label = tk.Label(
            companion_col,
            textvariable=self.auto_companion_status_var,
            font=(self.FONT, 8),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        )
        self.auto_companion_status_label.pack(anchor="w")
        self.auto_companion_var.trace_add(
            "write",
            lambda *_args: self.on_auto_companion_changed(),
        )

        court_col = tk.Frame(two_col, bg=self.COLOR_CARD)
        court_col.grid(row=0, column=1, sticky="nsew")
        tk.Label(
            court_col,
            text="首选场地",
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w")
        self.auto_court_var = tk.IntVar(value=config["preferred_court_number"])
        self.auto_court_spinbox = tk.Spinbox(
            court_col,
            from_=1,
            to=99,
            textvariable=self.auto_court_var,
            width=7,
            font=(self.FONT, 11),
            relief="flat",
            bg=self.COLOR_CONTROL,
            fg=self.COLOR_TEXT,
            buttonbackground=self.COLOR_CONTROL,
            highlightthickness=1,
            highlightbackground=self.COLOR_BORDER,
            highlightcolor=self.COLOR_BLUE_MID,
        )
        self.auto_court_spinbox.pack(anchor="w", pady=(8, 5), ipady=7)
        tk.Label(
            court_col,
            text="例如 3 = 3号场",
            font=(self.FONT, 8),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w")

        # 时间优先级
        tk.Label(
            card,
            text="重点时间（从上到下优先）",
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w")
        tk.Label(
            card,
            text="每行填写一个时间段，例如 17:30-19:30。重点时间都没有时，仍会接受其他可预约场次。",
            font=(self.FONT, 8),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w", pady=(4, 6))
        self.auto_time_text = tk.Text(
            card,
            height=4,
            font=(self.FONT_MONO, 10),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CONTROL,
            insertbackground=self.COLOR_TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.COLOR_BORDER,
            highlightcolor=self.COLOR_BLUE_MID,
            padx=10,
            pady=8,
        )
        self.auto_time_text.pack(fill="x", pady=(0, 10))
        self.auto_time_text.insert(
            "1.0",
            "\n".join(f"{start}-{end}" for start, end in config["time_priority"]),
        )

        tk.Label(
            card,
            text="运行模式",
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w")
        tk.Label(
            card,
            text="请选择一种模式；真实预约命中目标后会直接向学校系统提交。",
            font=(self.FONT, 8),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        ).pack(anchor="w", pady=(4, 6))
        self.auto_real_booking_var = tk.BooleanVar(value=config["real_booking_enabled"])
        self.auto_mode_buttons = {}
        mode_row = tk.Frame(card, bg=self.COLOR_CARD)
        mode_row.pack(fill="x")
        for mode_text, real_booking_enabled in (
            ("仅扫描", False),
            ("真实预约", True),
        ):
            button = self.create_auto_select_button(
                mode_row,
                mode_text,
                lambda enabled=real_booking_enabled: self.select_auto_booking_mode(
                    enabled
                ),
                width=10,
            )
            button.pack(side="left", padx=(0, 8))
            self.auto_mode_buttons[real_booking_enabled] = button
        self.auto_mode_note_var = tk.StringVar()
        self.auto_mode_note_label = tk.Label(
            card,
            textvariable=self.auto_mode_note_var,
            font=(self.FONT, 8, "bold"),
            fg=self.COLOR_GREEN,
            bg=self.COLOR_CARD,
        )
        self.auto_mode_note_label.pack(anchor="w", pady=(6, 0))

        path_text = str(AUTO_CONFIG_FILE)
        tk.Label(
            card,
            text=(
                f"本机配置文件：{path_text}（Token 单独保存在当前用户目录）"
            ),
            font=(self.FONT, 8),
            fg=self.COLOR_MUTED,
            bg=self.COLOR_CARD,
            wraplength=670,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        run_panel = tk.Frame(
            card,
            bg=self.COLOR_BLUE_PALE,
            padx=12,
            pady=10,
            highlightbackground=self.COLOR_BORDER,
            highlightthickness=1,
        )
        run_panel.pack(fill="x", pady=(12, 0))
        status_row = tk.Frame(run_panel, bg=self.COLOR_BLUE_PALE)
        status_row.pack(fill="x")
        tk.Label(
            status_row,
            text="自动任务状态",
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_BLUE_PALE,
        ).pack(side="left")
        tk.Label(
            status_row,
            textvariable=self.auto_status_var,
            font=(self.FONT, 9, "bold"),
            fg=self.COLOR_BLUE,
            bg=self.COLOR_BLUE_PALE,
        ).pack(side="right")

        log_access_row = tk.Frame(run_panel, bg=self.COLOR_BLUE_PALE)
        log_access_row.pack(fill="x", pady=(9, 0))
        tk.Label(
            log_access_row,
            text="运行输出不再挤在设置页中；启动任务时会自动打开独立日志窗口。",
            font=(self.FONT, 8),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_BLUE_PALE,
        ).pack(side="left")
        open_log_button = tk.Label(
            log_access_row,
            text="打开日志窗口",
            font=(self.FONT, 8, "bold"),
            fg="#FFFFFF",
            bg=self.COLOR_BLUE,
            cursor="hand2",
            padx=11,
            pady=6,
        )
        open_log_button.pack(side="right")
        open_log_button.bind(
            "<Button-1>",
            lambda _event: self.open_auto_log_window("live"),
        )

        action_row = tk.Frame(shell, bg=self.COLOR_BG)
        action_row.pack(fill="x", pady=(10, 0))
        self.auto_save_button = tk.Label(
            action_row,
            text="保存配置",
            font=(self.FONT, 10, "bold"),
            fg="#FFFFFF",
            bg=self.COLOR_BLUE,
            padx=20,
            pady=10,
            cursor="hand2",
        )
        self.auto_save_button.pack(side="right", padx=(0, 10))
        self.auto_save_button.bind(
            "<Button-1>",
            lambda _event: self.start_save_auto_settings(),
        )

        self.auto_start_button = tk.Label(
            action_row,
            text="保存并启动",
            font=(self.FONT, 10, "bold"),
            fg="#FFFFFF",
            bg=self.COLOR_GREEN,
            padx=20,
            pady=10,
            cursor="hand2",
        )
        self.auto_start_button.pack(side="right", padx=(0, 10))
        self.auto_start_button.bind(
            "<Button-1>",
            lambda _event: self.start_save_auto_settings(start_after_save=True),
        )

        self.auto_stop_button = tk.Label(
            action_row,
            text="停止任务",
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CONTROL,
            padx=16,
            pady=10,
            cursor="hand2",
        )
        self.auto_stop_button.pack(side="right", padx=(0, 10))
        self.auto_stop_button.bind(
            "<Button-1>",
            lambda _event: self.stop_auto_booking(),
        )

        self.update_auto_setting_controls()
        self.refresh_auto_process_controls()
        shell.after_idle(lambda: settings_scroll.canvas.yview_moveto(0))

    def create_auto_select_button(self, parent, text, command, width):
        button = tk.Label(
            parent,
            text=text,
            width=width,
            font=(self.FONT, 10, "bold"),
            bd=0,
            padx=8,
            pady=9,
            cursor="hand2",
            takefocus=True,
        )
        button.bind("<Button-1>", lambda _event: command())
        button.bind("<Return>", lambda _event: command())
        button.bind("<space>", lambda _event: command())
        return button

    def select_auto_venue(self, venue_name):
        self.select_venue(venue_name)

    def rebuild_auto_sport_buttons(self):
        if self.auto_sport_row is None or self.auto_venue_var is None or self.auto_sport_var is None:
            return

        sports = get_sports_for_venue(self.auto_venue_var.get())
        if self.auto_sport_var.get() not in sports:
            self.auto_sport_var.set(next(iter(sports)))

        for child in self.auto_sport_row.winfo_children():
            child.destroy()
        self.auto_sport_buttons = {}

        for sport_name in sports:
            button = self.create_auto_select_button(
                self.auto_sport_row,
                sport_name,
                lambda name=sport_name: self.select_auto_sport(name),
                width=9,
            )
            button.pack(side="left", padx=(0, 8))
            self.auto_sport_buttons[sport_name] = button

    def select_auto_sport(self, sport_name):
        if self.auto_sport_var is None or self.is_saving_auto_config:
            return
        self.auto_sport_var.set(sport_name)
        self.update_auto_setting_controls()

    def select_auto_day(self, day_name):
        if self.auto_day_var is None or self.is_saving_auto_config:
            return
        self.auto_day_var.set(day_name)
        self.update_auto_setting_controls()

    def select_auto_booking_mode(self, real_booking_enabled):
        if self.auto_real_booking_var is None or self.is_saving_auto_config:
            return
        self.auto_real_booking_var.set(bool(real_booking_enabled))
        self.update_auto_setting_controls()

    def update_auto_setting_controls(self):
        if self.auto_venue_var is not None:
            selected_venue = self.auto_venue_var.get()
            for name, button in self.auto_venue_buttons.items():
                self.style_auto_select_button(button, name, name == selected_venue)
        else:
            selected_venue = ""

        if self.auto_sport_var is not None:
            selected_sport = self.auto_sport_var.get()
            for name, button in self.auto_sport_buttons.items():
                self.style_auto_select_button(button, name, name == selected_sport)
        else:
            selected_sport = ""

        if self.auto_day_var is not None:
            selected_day = self.auto_day_var.get()
            for name, button in self.auto_day_buttons.items():
                self.style_auto_select_button(button, name, name == selected_day)
        else:
            selected_day = ""

        if self.auto_selection_summary_var is not None:
            self.auto_selection_summary_var.set(
                f"当前选择：{selected_venue} · {selected_sport} · {selected_day}"
            )
        if self.auto_venue_badge_var is not None:
            self.auto_venue_badge_var.set(selected_venue)

        if self.auto_real_booking_var is not None:
            real_booking_enabled = bool(self.auto_real_booking_var.get())
            for mode_value, button in self.auto_mode_buttons.items():
                mode_text = "真实预约" if mode_value else "仅扫描"
                self.style_auto_select_button(
                    button,
                    mode_text,
                    mode_value == real_booking_enabled,
                )
            if self.auto_mode_note_var is not None:
                if real_booking_enabled:
                    self.auto_mode_note_var.set(
                        "注意：发现符合条件的场次后会直接提交真实预约。"
                    )
                    self.auto_mode_note_label.configure(fg=self.COLOR_RED)
                else:
                    self.auto_mode_note_var.set(
                        "仅扫描只观察可预约场次，不会向学校系统提交预约。"
                    )
                    self.auto_mode_note_label.configure(fg=self.COLOR_GREEN)
        self.refresh_auto_process_controls()

    def style_auto_select_button(self, button, text, selected):
        if selected:
            button.configure(
                text=f"✓ {text}",
                fg="#FFFFFF",
                bg=self.COLOR_BLUE,
            )
        else:
            button.configure(
                text=text,
                fg=self.COLOR_SUBTEXT,
                bg=self.COLOR_CONTROL,
            )

    def close_auto_settings_dialog(self):
        """Compatibility alias retained for older callbacks."""

        self.show_query_page()

    def on_auto_companion_changed(self):
        self.auto_validated_companion_number = None
        self.auto_validated_companion_name = None
        if self.auto_companion_status_var is not None and not self.is_saving_auto_config:
            if self.auto_companion_var and self.auto_companion_var.get().strip():
                self.auto_companion_status_var.set("尚未验证；保存时将在线验证同行人")
                self.auto_companion_status_label.configure(fg=self.COLOR_SUBTEXT)
            else:
                self.auto_companion_status_var.set("同行人为必填项，未填写不能保存")
                self.auto_companion_status_label.configure(fg=self.COLOR_RED)
        self.refresh_auto_process_controls()

    def collect_auto_settings(self):
        if not self.auto_time_text or not self.auto_companion_entry:
            raise ValueError("自动预约设置窗口尚未准备完成。")

        raw_lines = self.auto_time_text.get("1.0", "end").splitlines()
        time_priority = []
        for index, raw in enumerate(raw_lines, start=1):
            text = raw.strip()
            if not text:
                continue
            if "-" not in text:
                raise ValueError(
                    f"重点时间第 {index} 行格式错误：{text!r}。请使用 HH:MM-HH:MM。"
                )
            start, end = (part.strip() for part in text.split("-", 1))
            time_priority.append([start, end])

        companion_number = require_companion_student_number(
            self.auto_companion_entry.get()
        )

        config = {
            "venue": self.auto_venue_var.get(),
            "sport": self.auto_sport_var.get(),
            "target_day": self.auto_day_var.get(),
            "companion_student_number": companion_number,
            "preferred_court_number": self.auto_court_var.get(),
            "real_booking_enabled": bool(self.auto_real_booking_var.get()),
            "time_priority": time_priority,
        }
        return validate_auto_config(config)

    def set_auto_config_saving(self, saving):
        self.is_saving_auto_config = saving
        widget_state = "disabled" if saving else "normal"
        for widget in (
            self.auto_companion_entry,
            self.auto_court_spinbox,
            self.auto_time_text,
        ):
            if widget is not None:
                widget.configure(state=widget_state)
        if self.auto_save_button:
            if saving:
                self.auto_save_button.configure(
                    text="正在验证并保存…",
                    bg="#7893C2",
                    fg="#D8E2F2",
                    cursor="arrow",
                )
        self.refresh_auto_process_controls()

    def start_save_auto_settings(self, start_after_save=False):
        if self.is_saving_auto_config:
            return
        if start_after_save and self.auto_process_is_running():
            messagebox.showinfo(
                "任务正在运行",
                "请先停止当前自动任务，再使用新配置重新启动。",
                parent=self.auto_settings_dialog,
            )
            return

        try:
            config = self.collect_auto_settings()
        except (TypeError, ValueError) as exc:
            messagebox.showerror(
                "配置不完整",
                str(exc),
                parent=self.auto_settings_dialog,
            )
            return

        token = self.get_token(parent=self.auto_settings_dialog, prompt=True)
        if not token:
            return

        self.auto_companion_status_var.set("正在验证同行人…")
        self.auto_companion_status_label.configure(fg=self.COLOR_BLUE)
        self.set_auto_config_saving(True)
        threading.Thread(
            target=self.run_save_auto_settings,
            args=(config, token, start_after_save),
            daemon=True,
        ).start()

    def run_save_auto_settings(self, config, token, start_after_save=False):
        try:
            companion = get_companion_user(
                student_number=config["companion_student_number"],
                token=token,
            )
            companion_name = companion.get("name", "未知")
            self.root.after(
                0,
                self.finish_save_auto_settings,
                config,
                companion_name,
                start_after_save,
            )
        except Exception as exc:
            self.root.after(
                0,
                self.show_auto_companion_validation_error,
                str(exc),
            )

    def finish_save_auto_settings(
        self,
        config,
        companion_name=None,
        start_after_save=False,
    ):
        if not companion_name:
            self.show_auto_companion_validation_error(
                "学校系统没有返回有效的同行人信息。"
            )
            return

        self.auto_validated_companion_number = config["companion_student_number"]
        self.auto_validated_companion_name = companion_name
        try:
            saved = save_auto_config(config, AUTO_CONFIG_FILE)
        except (OSError, ValueError) as exc:
            self.show_auto_config_persistence_error(str(exc))
            return

        self.set_auto_config_saving(False)
        if self.auto_companion_status_var is not None:
            self.auto_companion_status_var.set(
                f"✓ 同行人验证通过并已记住：{companion_name}"
            )
            self.auto_companion_status_label.configure(fg=self.COLOR_GREEN)

        # 查询页同步到刚保存的场馆、项目与日期，方便立即手动查看。
        self.current_venue_name = saved["venue"]
        self.venue_var.set(saved["venue"])
        self.sport_var.set(saved["sport"])
        if self.query_venue_badge_var is not None:
            self.query_venue_badge_var.set(saved["venue"])
        self.rebuild_sport_buttons()
        self.refresh_sidebar_venue_items()
        self.date_var.set("today" if saved["target_day"] == "今天" else "tomorrow")
        self.update_select_buttons()

        if start_after_save:
            self.start_auto_booking(saved)
            return
        # 验证与保存成功只更新页面状态，不再用弹窗打断用户。
        self.auto_status_var.set("配置已保存；Token 与同行人下次自动读取")

    def auto_process_is_running(self):
        return self.auto_process is not None and self.auto_process.poll() is None

    def refresh_auto_process_controls(self):
        running = self.auto_process_is_running()
        companion_present = bool(
            self.auto_companion_var
            and self.auto_companion_var.get().strip()
        )
        config_actions_enabled = companion_present and not self.is_saving_auto_config
        if self.auto_save_button:
            self.auto_save_button.configure(
                text="正在验证并保存…" if self.is_saving_auto_config else "保存配置",
                fg="#FFFFFF" if config_actions_enabled else "#D8E2F2",
                bg=self.COLOR_BLUE if config_actions_enabled else "#7893C2",
                cursor="hand2" if config_actions_enabled else "arrow",
            )
        if self.auto_start_button:
            start_enabled = config_actions_enabled and not running
            self.auto_start_button.configure(
                text="任务运行中" if running else "保存并启动",
                fg="#FFFFFF" if start_enabled else "#D8E2F2",
                bg=self.COLOR_GREEN if start_enabled else "#7893C2",
                cursor="hand2" if start_enabled else "arrow",
            )
        if self.auto_stop_button:
            self.auto_stop_button.configure(
                fg="#FFFFFF" if running else self.COLOR_MUTED,
                bg=self.COLOR_RED if running else self.COLOR_CONTROL,
                cursor="hand2" if running else "arrow",
            )

    def start_auto_booking(self, config):
        """Launch the automatic task in a child process controlled by the GUI."""

        if self.auto_process_is_running():
            return

        command = build_auto_worker_command(
            real_booking_enabled=config["real_booking_enabled"],
        )
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        if self.token:
            environment["JLU_BOOKING_TOKEN"] = self.token
        companion_number = str(config.get("companion_student_number", "")).strip()
        if companion_number:
            environment["JLU_BOOKING_COMPANION"] = companion_number

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
                creationflags=creationflags,
            )
        except OSError as exc:
            self.auto_status_var.set("启动失败")
            messagebox.showerror(
                "无法启动自动任务",
                str(exc),
                parent=self.auto_settings_dialog or self.root,
            )
            return

        self.auto_process = process
        self.auto_stop_requested = False
        self.auto_status_var.set(
            "运行中 · 真实预约" if config["real_booking_enabled"] else "运行中 · 仅扫描"
        )
        self.append_auto_output(
            f"[{datetime.now().strftime('%H:%M:%S')}] GUI 已启动自动任务\n"
        )
        self.refresh_auto_process_controls()
        self.auto_process_reader = threading.Thread(
            target=self.read_auto_process_output,
            args=(process,),
            daemon=True,
        )
        self.auto_process_reader.start()
        self.open_auto_log_window("live")

    def read_auto_process_output(self, process):
        if process.stdout is not None:
            for line in process.stdout:
                if self.is_closing:
                    break
                try:
                    self.root.after(0, self.handle_auto_output, process, line)
                except (RuntimeError, tk.TclError):
                    break
        return_code = process.wait()
        if not self.is_closing:
            try:
                self.root.after(0, self.finish_auto_process, process, return_code)
            except (RuntimeError, tk.TclError):
                pass

    def handle_auto_output(self, process, line):
        if process is not self.auto_process:
            return
        self.append_auto_output(line)
        text = line.strip()
        if "进入阶段：" in text:
            self.auto_status_var.set(text.split("进入阶段：", 1)[1])
        elif "已锁定目标" in text or "| LOCKED |" in text:
            self.auto_status_var.set("已锁定目标 · 正在尝试")
        elif "自动预约成功" in text or "预约成功 |" in text:
            self.auto_status_var.set("预约成功")

    def append_auto_output(self, line, *, remember=True):
        text = str(line)
        if remember:
            self.auto_output_lines.append(text)
            self.auto_output_lines = self.auto_output_lines[-2000:]
        if self.auto_log_mode != "live":
            return
        if not self.auto_log_text or not self.auto_log_text.winfo_exists():
            return
        self.auto_log_text.configure(state="normal")
        self.auto_log_text.insert("end", text)
        self.auto_log_text.see("end")
        self.auto_log_text.configure(state="disabled")

    def finish_auto_process(self, process, return_code):
        if process is not self.auto_process:
            return
        previous_status = self.auto_status_var.get()
        self.auto_process = None
        if previous_status == "预约成功":
            pass
        elif self.auto_stop_requested:
            self.auto_status_var.set("已停止")
        elif return_code == 0:
            self.auto_status_var.set("已结束")
        else:
            self.auto_status_var.set(f"异常结束（{return_code}）")
        self.auto_stop_requested = False
        self.append_auto_output(
            f"[{datetime.now().strftime('%H:%M:%S')}] 自动任务已结束\n"
        )
        self.refresh_auto_process_controls()

    def stop_auto_booking(self):
        if not self.auto_process_is_running():
            return
        process = self.auto_process
        self.auto_stop_requested = True
        self.auto_status_var.set("正在停止…")
        self.append_auto_output(
            f"[{datetime.now().strftime('%H:%M:%S')}] 正在停止自动任务\n"
        )
        try:
            process.terminate()
        except OSError as exc:
            messagebox.showerror(
                "停止失败",
                str(exc),
                parent=self.auto_settings_dialog or self.root,
            )

    def open_auto_log_window(self, initial_view="live"):
        """Open a large, non-modal window for live output and persisted logs."""

        if initial_view not in {"live", "event", "timing"}:
            initial_view = "live"
        if self.auto_log_window is not None:
            try:
                if self.auto_log_window.winfo_exists():
                    self.auto_log_window.deiconify()
                    self.auto_log_window.lift()
                    self.show_auto_log_view(initial_view)
                    return
            except tk.TclError:
                pass

        window = tk.Toplevel(self.root)
        self.auto_log_window = window
        window.title("JLU Booking · 自动预约日志")
        window.configure(bg=self.COLOR_BG)
        window.minsize(680, 440)
        width, height, x, y = self.dialog_geometry(920, 620)
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.protocol("WM_DELETE_WINDOW", self.close_auto_log_window)
        if self.icon_image is not None:
            try:
                window.iconphoto(False, self.icon_image)
            except tk.TclError:
                pass

        shell = tk.Frame(window, bg=self.COLOR_BG, padx=22, pady=18)
        shell.pack(fill="both", expand=True)

        heading = tk.Frame(shell, bg=self.COLOR_BG)
        heading.pack(fill="x")
        title_group = tk.Frame(heading, bg=self.COLOR_BG)
        title_group.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_group,
            text="自动预约日志",
            font=(self.FONT, 18, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_BG,
        ).pack(anchor="w")
        self.auto_log_title_var = tk.StringVar(value="本次任务实时输出")
        tk.Label(
            title_group,
            textvariable=self.auto_log_title_var,
            font=(self.FONT, 10),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_BG,
        ).pack(anchor="w", pady=(3, 0))
        tk.Label(
            heading,
            textvariable=self.auto_status_var,
            font=(self.FONT, 10, "bold"),
            fg=self.COLOR_GREEN,
            bg=self.COLOR_GREEN_BG,
            padx=11,
            pady=6,
        ).pack(side="right")

        navigation = tk.Frame(shell, bg=self.COLOR_BG)
        navigation.pack(fill="x", pady=(14, 9))
        self.auto_log_buttons = {}
        for text_value, view_kind in (
            ("实时输出", "live"),
            ("事件日志", "event"),
            ("请求耗时日志", "timing"),
        ):
            button = tk.Label(
                navigation,
                text=text_value,
                font=(self.FONT, 10, "bold"),
                cursor="hand2",
                padx=13,
                pady=7,
            )
            button.pack(side="left", padx=(0, 8))
            button.bind(
                "<Button-1>",
                lambda _event, kind=view_kind: self.show_auto_log_view(kind),
            )
            self.auto_log_buttons[view_kind] = button

        viewer = tk.Frame(
            shell,
            bg="#FFFFFF",
            highlightbackground=self.COLOR_BORDER,
            highlightthickness=1,
        )
        viewer.pack(fill="both", expand=True)
        viewer.grid_rowconfigure(0, weight=1)
        viewer.grid_columnconfigure(0, weight=1)
        self.auto_log_text = tk.Text(
            viewer,
            font=(self.FONT_MONO, 11),
            fg=self.COLOR_TEXT,
            bg="#FFFFFF",
            relief="flat",
            bd=0,
            padx=12,
            pady=10,
            spacing1=2,
            spacing3=2,
            state="disabled",
            wrap="word",
        )
        vertical = ttk.Scrollbar(
            viewer,
            orient="vertical",
            command=self.auto_log_text.yview,
        )
        self.auto_log_text.configure(yscrollcommand=vertical.set)
        self.auto_log_text.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")

        footer = tk.Frame(shell, bg=self.COLOR_BG)
        footer.pack(fill="x", pady=(12, 0))
        button_group = tk.Frame(footer, bg=self.COLOR_BG)
        button_group.pack(side="right")

        def add_footer_button(text, command, *, style="secondary"):
            palettes = {
                "secondary": (
                    self.COLOR_BLUE,
                    self.COLOR_BLUE_TINT,
                    "#C8D8F2",
                    "#DCE8FA",
                ),
                "primary": (
                    "#FFFFFF",
                    self.COLOR_BLUE,
                    self.COLOR_BLUE,
                    self.COLOR_BLUE_MID,
                ),
                "neutral": (
                    self.COLOR_SUBTEXT,
                    "#E7ECF4",
                    "#D5DDE9",
                    "#DDE4EE",
                ),
            }
            foreground, background, border, hover = palettes[style]
            button = tk.Label(
                button_group,
                text=text,
                font=(self.FONT, 9, "bold"),
                fg=foreground,
                bg=background,
                cursor="hand2",
                padx=14,
                pady=8,
                takefocus=True,
                highlightbackground=border,
                highlightcolor=border,
                highlightthickness=1,
            )
            button.pack(side="left", padx=(8, 0))
            button.bind("<Button-1>", lambda _event: command())
            button.bind("<Return>", lambda _event: command())
            button.bind("<space>", lambda _event: command())
            button.bind("<Enter>", lambda _event: button.configure(bg=hover))
            button.bind("<Leave>", lambda _event: button.configure(bg=background))
            return button

        add_footer_button("刷新日志", self.refresh_auto_log_view)
        add_footer_button(
            "打开日志文件夹",
            lambda: self.open_auto_log("directory"),
            style="primary",
        )
        add_footer_button(
            "关闭",
            self.close_auto_log_window,
            style="neutral",
        )

        self.show_auto_log_view(initial_view)
        window.after_idle(window.focus_set)

    def close_auto_log_window(self):
        window = self.auto_log_window
        self.auto_log_window = None
        self.auto_log_text = None
        self.auto_log_title_var = None
        self.auto_log_buttons = {}
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def set_auto_log_text(self, content, *, scroll_to_end=False):
        if self.auto_log_text is None or not self.auto_log_text.winfo_exists():
            return
        self.auto_log_text.configure(state="normal")
        self.auto_log_text.delete("1.0", "end")
        self.auto_log_text.insert("1.0", content)
        if scroll_to_end:
            self.auto_log_text.see("end")
        else:
            self.auto_log_text.see("1.0")
        self.auto_log_text.configure(state="disabled")

    def get_auto_log_path(self, kind):
        try:
            config = load_auto_config(AUTO_CONFIG_FILE, create_if_missing=True)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"无法读取自动预约配置：{exc}") from exc

        suffix = "auto.log" if kind == "event" else "request_timing.log"
        return LOG_DIR / f"{config['venue']}_{config['sport']}_{suffix}"

    def show_auto_log_view(self, kind):
        if kind not in {"live", "event", "timing"}:
            return
        self.auto_log_mode = kind
        titles = {
            "live": "本次任务实时输出",
            "event": "事件日志 · 阶段、锁定与预约结果",
            "timing": "请求耗时日志 · 接口与响应时间",
        }
        if self.auto_log_title_var is not None:
            self.auto_log_title_var.set(titles[kind])
        for button_kind, button in self.auto_log_buttons.items():
            selected = button_kind == kind
            button.configure(
                fg="#FFFFFF" if selected else self.COLOR_SUBTEXT,
                bg=self.COLOR_GREEN if selected else self.COLOR_CONTROL,
            )

        if kind == "live":
            content = "".join(self.auto_output_lines)
            if not content:
                content = "任务尚未启动。点击“保存并启动”后，运行输出会显示在这里。\n"
            self.set_auto_log_text(content, scroll_to_end=True)
            return

        try:
            path = self.get_auto_log_path(kind)
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="replace")
                if not content:
                    content = f"日志文件目前为空：\n{path}\n"
            else:
                content = f"日志尚未生成。启动一次自动任务后会自动创建：\n{path}\n"
        except (OSError, RuntimeError) as exc:
            content = f"无法读取日志：\n{exc}\n"
        self.set_auto_log_text(content)

    def refresh_auto_log_view(self):
        self.show_auto_log_view(self.auto_log_mode)

    def open_auto_log(self, kind):
        if kind != "directory":
            self.open_auto_log_window(kind)
            return

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.open_local_path(LOG_DIR)

    def open_local_path(self, path):
        try:
            if os.name == "nt":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror(
                "无法打开",
                f"{path}\n\n{exc}",
                parent=self.auto_settings_dialog or self.root,
            )

    def on_close(self):
        if self.auto_process_is_running():
            confirmed = messagebox.askyesno(
                "自动任务正在运行",
                "关闭窗口会同时停止自动任务。确认关闭吗？",
                parent=self.root,
            )
            if not confirmed:
                return
            try:
                self.auto_process.terminate()
            except OSError:
                pass
        self.is_closing = True
        self.root.destroy()

    def show_auto_companion_validation_error(self, error_message):
        self.auto_validated_companion_number = None
        self.auto_validated_companion_name = None
        self.set_auto_config_saving(False)
        if self.auto_companion_status_var is not None:
            self.auto_companion_status_var.set("同行人无效，请重新填写并保存")
            self.auto_companion_status_label.configure(fg=self.COLOR_RED)
        messagebox.showerror(
            "同行人验证未通过",
            (
                "学校系统没有找到或没有接受这个同行人，请重新填写有效的学工号。\n\n"
                f"服务器提示：{error_message}"
            ),
            parent=self.auto_settings_dialog,
        )
        if self.auto_companion_entry is not None:
            try:
                self.auto_companion_entry.focus_set()
                self.auto_companion_entry.selection_range(0, "end")
            except tk.TclError:
                pass

    def show_auto_config_persistence_error(self, error_message):
        self.set_auto_config_saving(False)
        messagebox.showerror(
            "配置保存失败",
            error_message,
            parent=self.auto_settings_dialog,
        )

    def show_empty_state(self, title, detail, accent=None):
        accent = accent or self.COLOR_MUTED
        empty = tk.Frame(self.result_scroll.inner, bg=self.COLOR_CARD, pady=28)
        empty.pack(fill="both", expand=True)

        tk.Label(
            empty,
            text="◎",
            font=(self.FONT_LATIN, 28),
            fg=accent,
            bg=self.COLOR_CARD,
        ).pack()
        tk.Label(
            empty,
            text=title,
            font=(self.FONT, 12, "bold"),
            fg=self.COLOR_TEXT,
            bg=self.COLOR_CARD,
        ).pack(pady=(7, 4))
        tk.Label(
            empty,
            text=detail,
            font=(self.FONT, 9),
            fg=self.COLOR_SUBTEXT,
            bg=self.COLOR_CARD,
        ).pack()

    def clear_result_rows(self):
        for widget in self.result_scroll.inner.winfo_children():
            widget.destroy()

    def clear_results(self):
        self.last_query_date = None
        self.last_query_venue = None
        self.last_query_shop_num = None
        self.clear_result_rows()
        self.show_empty_state(
            "等待查询",
            "选择运动项目和日期，点击查询即可查看可预约时段",
        )
        self.summary_sport_var.set("未查询")
        self.summary_date_var.set("未查询")
        self.summary_court_count_var.set("0")
        self.summary_slot_count_var.set("0")
        self.summary_status_var.set("准备就绪")
        self.result_subtitle_var.set("查询后将在这里展示空闲时段")
        self.bottom_status_var.set("已清空查询结果")
        self.set_status("ready")

    def show_error(self, error_message):
        self.clear_result_rows()

        error_box = tk.Frame(
            self.result_scroll.inner,
            bg=self.COLOR_RED_BG,
            padx=20,
            pady=22,
        )
        error_box.pack(fill="x", pady=4)
        tk.Label(
            error_box,
            text="查询失败",
            font=(self.FONT, 12, "bold"),
            fg=self.COLOR_RED,
            bg=self.COLOR_RED_BG,
        ).pack(anchor="w")
        tk.Label(
            error_box,
            text=error_message,
            font=(self.FONT, 9),
            fg=self.COLOR_RED,
            bg=self.COLOR_RED_BG,
            justify="left",
            wraplength=650,
        ).pack(anchor="w", pady=(7, 0))

        self.summary_court_count_var.set("0")
        self.summary_slot_count_var.set("0")
        self.summary_status_var.set("查询失败")
        self.result_subtitle_var.set("未能获取场馆数据，请检查网络或 Token")
        self.bottom_status_var.set("查询失败")
        self.set_status("error")
        self.set_loading(False)

        messagebox.showerror("查询失败", error_message)

    def set_loading(self, loading):
        self.is_loading = loading
        if loading:
            self.query_button.configure(
                text="正在查询…",
                fg="#D8E2F2",
                bg="#7893C2",
                cursor="arrow",
            )
        else:
            self.query_button.configure(
                text="查询可预约场地  →",
                fg="#FFFFFF",
                bg=self.COLOR_BLUE,
                cursor="hand2",
            )

    def set_status(self, status):
        styles = {
            "ready": ("●  系统就绪", self.COLOR_GREEN, self.COLOR_GREEN_BG),
            "loading": ("●  正在查询", self.COLOR_BLUE, self.COLOR_BLUE_TINT),
            "success": ("●  数据已更新", self.COLOR_GREEN, self.COLOR_GREEN_BG),
            "error": ("●  查询异常", self.COLOR_RED, self.COLOR_RED_BG),
        }
        text, foreground, background = styles[status]
        self.top_status_var.set(text)
        self.top_status_label.configure(fg=foreground, bg=background)
        self.result_status_label.configure(fg=foreground, bg=background)

    def center_window(self, width, height):
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width, height, x, y = fit_window_geometry(
            screen_width,
            screen_height,
            width,
            height,
        )
        self.root.minsize(min(1020, width), min(700, height))
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def dialog_geometry(self, preferred_width, preferred_height):
        """Fit a dialog to the current screen and center it over the app."""

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width, height, _x, _y = fit_window_geometry(
            screen_width,
            screen_height,
            preferred_width,
            preferred_height,
            horizontal_margin=40,
            vertical_margin=80,
        )
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2
        x = max(0, min(x, screen_width - width))
        y = max(0, min(y, screen_height - height))
        return width, height, x, y
