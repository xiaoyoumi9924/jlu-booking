from jlu_booking.gui import BookingApp


class FakeVariable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeProcess:
    stdout = None

    def poll(self):
        return None


class FakeThread:
    def __init__(self, *, target, args, daemon):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True


def test_save_and_start_real_booking_launches_without_confirmation(monkeypatch):
    app = BookingApp.__new__(BookingApp)
    app.root = object()
    app.auto_settings_dialog = None
    app.auto_process = None
    app.auto_process_reader = None
    app.auto_stop_requested = False
    app.auto_status_var = FakeVariable("未运行")
    app.token = "validated-token"
    output = []
    opened = []
    processes = []
    process_options = []

    app.append_auto_output = output.append
    app.refresh_auto_process_controls = lambda: None
    app.open_auto_log_window = opened.append

    monkeypatch.setattr(
        "jlu_booking.gui.messagebox.askyesno",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("保存并启动不应再次询问确认")
        ),
    )
    monkeypatch.setattr(
        "jlu_booking.gui.build_auto_worker_command",
        lambda **_kwargs: ["fake-worker"],
    )
    monkeypatch.setattr(
        "jlu_booking.gui.subprocess.Popen",
        lambda *_args, **kwargs: (
            process_options.append(kwargs),
            processes.append(FakeProcess()),
            processes[-1],
        )[-1],
    )
    monkeypatch.setattr("jlu_booking.gui.threading.Thread", FakeThread)

    config = {
        "venue": "宋治平体育馆",
        "sport": "排球",
        "target_day": "明天",
        "companion_student_number": "example-1234",
        "preferred_court_number": 2,
        "time_priority": [["17:30", "19:30"]],
        "real_booking_enabled": True,
    }
    app.start_auto_booking(config)

    assert len(processes) == 1
    assert app.auto_process is processes[0]
    assert app.auto_status_var.get() == "运行中 · 真实预约"
    assert opened == ["live"]
    assert any("GUI 已启动自动任务" in line for line in output)
    assert process_options[0]["env"]["JLU_BOOKING_TOKEN"] == "validated-token"
    assert process_options[0]["env"]["JLU_BOOKING_COMPANION"] == "example-1234"


def test_auto_booking_mode_is_an_explicit_two_choice_selection():
    app = BookingApp.__new__(BookingApp)
    app.auto_real_booking_var = FakeVariable(False)
    app.is_saving_auto_config = False
    updates = []
    app.update_auto_setting_controls = lambda: updates.append(True)

    app.select_auto_booking_mode(True)
    assert app.auto_real_booking_var.get() is True

    app.select_auto_booking_mode(False)
    assert app.auto_real_booking_var.get() is False
    assert updates == [True, True]
