import importlib

import pytest


def test_config_module_reads_env_over_yaml(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text(
        'RSS_URL_PREFIX: "https://yaml/"\nPORT: 9000\n',
        encoding="utf-8",
    )
    (tmp_path / "sources.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("B2P_CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("RSS_URL_PREFIX", "https://env/")
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setenv("R2_ACCESS_KEY", "env-ak")
    monkeypatch.setenv("R2_SECRET_KEY", "env-sk")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://r2.example")
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket-env")

    import bilibili_podcast.config as c
    importlib.reload(c)

    assert c.RSS_URL_PREFIX == "https://env/"
    assert c.PORT == 8080
    assert c.ACCESS_KEY == "env-ak"
    assert c.SECRET_KEY == "env-sk"
    assert c.ENDPOINT_URL == "https://r2.example"
    assert c.BUCKET_NAME == "bucket-env"


def test_config_module_falls_back_to_yaml_when_env_unset(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text(
        'RSS_URL_PREFIX: "https://yaml/"\n'
        'PORT: 9000\n'
        'ENDPOINT_URL: "https://r2.yaml"\n'
        'BUCKET_NAME: "bucket-yaml"\n',
        encoding="utf-8",
    )
    (tmp_path / "sources.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("B2P_CONFIG_PATH", str(tmp_path / "config.yaml"))
    for var in ("RSS_URL_PREFIX", "PORT", "R2_ACCESS_KEY", "R2_SECRET_KEY",
                "R2_ENDPOINT_URL", "R2_BUCKET_NAME"):
        monkeypatch.delenv(var, raising=False)

    import bilibili_podcast.config as c
    importlib.reload(c)

    assert c.RSS_URL_PREFIX == "https://yaml/"
    assert c.PORT == 9000
    assert c.ENDPOINT_URL == "https://r2.yaml"
    assert c.BUCKET_NAME == "bucket-yaml"


def test_config_module_works_without_yaml(monkeypatch, tmp_path):
    (tmp_path / "sources.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("B2P_CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("RSS_URL_PREFIX", "https://env/")
    monkeypatch.setenv("R2_ACCESS_KEY", "env-ak")

    import bilibili_podcast.config as c
    importlib.reload(c)

    assert c.RSS_URL_PREFIX == "https://env/"
    assert c.ACCESS_KEY == "env-ak"
    assert c.PORT == 8000
