import yaml

from streemcam.__main__ import main


def write_cfg(tmp_path, base_data):
    path = tmp_path / "config.yaml"
    base_data["db_path"] = str(tmp_path / "data" / "db.sqlite")
    path.write_text(yaml.safe_dump(base_data, allow_unicode=True), encoding="utf-8")
    return path


def test_render_go2rtc(tmp_path, base_data):
    cfg_path = write_cfg(tmp_path, base_data)
    out = tmp_path / "go2rtc" / "go2rtc.yaml"
    assert main(["--config", str(cfg_path), "render-go2rtc", "--out", str(out)]) == 0
    assert "yard" in yaml.safe_load(out.read_text(encoding="utf-8"))["streams"]


def test_link(tmp_path, base_data, capsys):
    cfg_path = write_cfg(tmp_path, base_data)
    assert main(["--config", str(cfg_path), "link", "tg:42", "--base", "http://127.0.0.1:8080"]) == 0
    assert capsys.readouterr().out.strip().startswith("http://127.0.0.1:8080/?t=")


def test_bad_config_returns_2(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("public_url: x\n", encoding="utf-8")
    assert main(["--config", str(path), "render-go2rtc", "--out", str(tmp_path / "o.yaml")]) == 2
    assert "invalid config" in capsys.readouterr().err
