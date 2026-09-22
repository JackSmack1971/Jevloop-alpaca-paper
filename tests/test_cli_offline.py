from pathlib import Path
from jevloop.doctor import main as doctor_main
from jevloop.serve import Handler
from jevloop.simulate import main as simulate_main


def test_doctor_offline_is_explicitly_unverified(capsys):
    assert doctor_main(["--offline"])==0
    out=capsys.readouterr().out
    assert "UNVERIFIED_RUNTIME" in out


def test_simulation_runs_without_network(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv("JEV_LOOP_HOME",str(tmp_path))
    assert simulate_main(["--ticks","5","--no-log"])==0
    assert "SIMULATION_OK" in capsys.readouterr().out


def test_dashboard_handler_has_no_generic_translate_path():
    # The improved handler serves an explicit allow-list rather than mapping arbitrary paths.
    assert "translate_path" not in Handler.__dict__


def test_run_cli_defaults_to_dry_and_requires_explicit_paper(monkeypatch):
    import jevloop.loop as loop
    seen = []

    def fake_run(**kwargs):
        seen.append(kwargs)
        return 0

    monkeypatch.setattr(loop, "run", fake_run)
    assert loop.main(["--ticks", "1"]) == 0
    assert seen[-1]["dry_execution"] is True
    assert loop.main(["--ticks", "1", "--paper"]) == 0
    assert seen[-1]["dry_execution"] is False
