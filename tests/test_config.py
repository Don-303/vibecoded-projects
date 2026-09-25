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
