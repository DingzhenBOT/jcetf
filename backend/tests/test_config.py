"""配置加载测试（P0）。

覆盖：默认加载成功、env 覆盖生效、缺文件 fail-fast、prod 非法组合 fail-fast。
"""
import os

import pytest

from app.config import get_settings, load_settings
from app.errors import ConfigError


def test_default_load():
    s = get_settings(force_reload=True)
    assert s.app.name == "etf-monitor"
    assert s.app.environment in ("dev", "prod")
    assert s.server.host == "127.0.0.1"
    assert s.server.port == 8000
    # 路径应被解析为绝对路径
    assert s.paths.data_dir_abs is not None and s.paths.data_dir_abs.is_absolute()
    assert s.paths.sqlite_path_abs.name == "etf_monitor.db"
    # 时区与数据源默认值
    assert s.scheduler.timezone == "Asia/Shanghai"
    assert s.data_source.preferred == "em"
    assert s.data_source.fallback  # 非空列表


def test_env_override(monkeypatch):
    monkeypatch.setenv("ETF_DATA_SOURCE_MODE", "mock")
    monkeypatch.setenv("ETF_API_PORT", "9000")
    s = get_settings(force_reload=True)
    assert s.data_source.mode == "mock"
    assert s.server.port == 9000


def test_missing_config_fail_fast(monkeypatch, tmp_path):
    missing = tmp_path / "does_not_exist.yaml"
    monkeypatch.setenv("ETF_CONFIG_PATH", str(missing))
    with pytest.raises(ConfigError):
        load_settings(str(missing))


def test_prod_must_bind_loopback(monkeypatch):
    """DESIGN §0：生产必须绑定 127.0.0.1，否则 fail-fast。"""
    monkeypatch.setenv("ETF_ENV", "prod")
    monkeypatch.setenv("ETF_API_HOST", "0.0.0.0")
    with pytest.raises(ConfigError):
        get_settings(force_reload=True)


def test_prod_mock_forbidden(monkeypatch):
    """DESIGN §0：生产禁止 mock 数据源。"""
    monkeypatch.setenv("ETF_ENV", "prod")
    monkeypatch.setenv("ETF_DATA_SOURCE_MODE", "mock")
    monkeypatch.setenv("ETF_API_HOST", "127.0.0.1")
    with pytest.raises(ConfigError):
        get_settings(force_reload=True)
