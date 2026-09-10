from tools import build_package


def test_build_script_output_is_windows_console_safe(monkeypatch, capsys):
    monkeypatch.setattr(build_package.subprocess, "run", lambda *args, **kwargs: None)

    assert build_package.main() == 0
    output = capsys.readouterr()

    assert (output.out + output.err).isascii()
