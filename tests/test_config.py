from terminus import config


def test_config_file_and_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "terminus").mkdir()
    (tmp_path / "terminus" / "config.toml").write_text('[todoist]\nproject = "Inbox"\n')
    assert config.Config.from_env({}).todoist_project == "Inbox"
    assert config.Config.from_env({"TODOIST_PROJECT": "Work"}).todoist_project == "Work"


def test_defaults_without_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = config.Config.from_env({})
    assert (cfg.todoist_project, cfg.todoist_token, cfg.todoist_interval) == ("Terminus", None, 60)


def test_planner_hours_from_file(tmp_path, monkeypatch):
    from datetime import time
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "terminus").mkdir()
    (tmp_path / "terminus" / "config.toml").write_text(
        '[planner]\nday_start = "04:00"\nday_end = "22:00"\n'
    )
    cfg = config.Config.from_env({})
    assert (cfg.day_start, cfg.day_end) == (time(4), time(22))
    assert config.Config.from_env({"TERMINUS_DAY_END": "23:00"}).day_end == time(23)
