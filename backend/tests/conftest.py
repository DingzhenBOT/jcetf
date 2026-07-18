"""pytest 公共 fixtures（P0）。

- 每个测试前后清空配置缓存，保证 env 覆盖测试互不串扰。
"""
import pytest

from app.config import clear_cache


@pytest.fixture(autouse=True)
def _reset_config_cache():
    clear_cache()
    yield
    clear_cache()
