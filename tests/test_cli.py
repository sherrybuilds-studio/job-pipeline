"""python -m pipeline: one entry point that lists every step and fails clearly
when the profile is missing."""
from pipeline.__main__ import COMMANDS, main


def test_help_lists_every_command(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    for name in COMMANDS:
        assert name in out


def test_no_arguments_prints_usage_and_fails(capsys):
    assert main([]) == 1
    assert "usage" in capsys.readouterr().out


def test_unknown_command(capsys):
    assert main(["apply-everywhere"]) == 2
    assert "unknown command" in capsys.readouterr().out


def test_a_missing_profile_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    from pipeline import profile
    monkeypatch.setenv("JOB_PIPELINE_PROFILE", str(tmp_path / "profile.yaml"))
    profile.reset()
    try:
        assert main(["cv", str(tmp_path / "cv.pdf")]) == 2
    finally:
        profile.reset()
    assert "profile.example.yaml" in capsys.readouterr().err


def test_cv_command_renders_the_master_cv(tmp_path, capsys):
    out = tmp_path / "cv.pdf"
    assert main(["cv", str(out)]) == 0
    assert out.exists() and "CV written to" in capsys.readouterr().out
