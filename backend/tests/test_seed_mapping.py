"""seed_mapping.SEED 一致性护栏。

回归测试：曾因前 3 支宽基 ETF 漏写 listing（5 元组混入 6 元组解包）导致
`for code, name, idx, sectors, cat, listing in SEED` 在首条即抛
`ValueError: not enough values to unpack (expected 6, got 5)`，而主测试套件未覆盖
完整 SEED 遍历，故漏过。此处显式遍历 SEED 校验结构，杜绝此类静默断裂。
"""
from scripts.seed_mapping import SEED


def test_seed_entries_unpack_consistently():
    assert len(SEED) > 0, "SEED 不应为空"
    for entry in SEED:
        assert len(entry) >= 5, f"SEED 条目至少需 5 个元素(etf_code,name,idx,sectors,cat): {entry}"
        code, name, idx, sectors, cat = entry[0], entry[1], entry[2], entry[3], entry[4]
        listing = entry[5] if len(entry) >= 6 else None
        assert isinstance(code, str) and code, f"etf_code 非法: {entry}"
        assert isinstance(name, str) and name, f"etf_name 非法: {entry}"
        assert isinstance(idx, str), f"related_index_code 非法: {entry}"
        assert isinstance(sectors, list), f"related_sector_codes 应为 list: {entry}"
        assert isinstance(cat, str), f"category 非法: {entry}"
        if listing is not None:
            assert listing in ("场内", "场外"), f"listing 非法(应为 场内/场外): {entry}"


def test_seed_codes_unique():
    codes = [e[0] for e in SEED]
    assert len(codes) == len(set(codes)), f"SEED 存在重复 etf_code: {codes}"
