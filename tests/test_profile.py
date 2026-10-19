"""profile 模块单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcleanfuel.profile import Profile, ProfileStore


def test_profile_creation():
    p = Profile(name="alice", weight_kg=65, height_cm=170, age=28,
                sex="female", activity="moderate", goal="cut", budget_yuan=60)
    assert p.name == "alice"
    assert p.sex == "female"
    assert p.activity == "moderate"
    assert p.goal == "cut"
    assert p.budget_yuan == 60


def test_profile_invalid_empty_name():
    with pytest.raises(ValueError, match="不能为空"):
        Profile(name="", weight_kg=65, height_cm=170, age=28, sex="female")


def test_profile_invalid_negative_weight():
    with pytest.raises(ValueError, match="正数"):
        Profile(name="x", weight_kg=-1, height_cm=170, age=28, sex="male")


def test_profile_invalid_sex():
    with pytest.raises(ValueError):
        Profile(name="x", weight_kg=65, height_cm=170, age=28, sex="other")


def test_profile_invalid_activity():
    with pytest.raises(ValueError):
        Profile(name="x", weight_kg=65, height_cm=170, age=28,
                sex="male", activity="invalid")


def test_profile_invalid_negative_budget():
    with pytest.raises(ValueError, match="负"):
        Profile(name="x", weight_kg=65, height_cm=170, age=28,
                sex="male", budget_yuan=-10)


def test_profile_compute_targets():
    p = Profile(name="alice", weight_kg=65, height_cm=170, age=28,
                sex="female", activity="moderate", goal="cut")
    t = p.compute_targets()
    assert t.tdee > 0
    assert t.target_calories < t.tdee  # 减脂有缺口
    assert t.deficit == pytest.approx(500, rel=1e-3)


def test_profile_to_from_dict_roundtrip():
    p = Profile(name="bob", weight_kg=80, height_cm=180, age=32,
                sex="male", activity="active", goal="maintain", budget_yuan=80)
    d = p.to_dict()
    p2 = Profile.from_dict(d)
    assert p2.name == p.name
    assert p2.weight_kg == p.weight_kg
    assert p2.activity == p.activity
    assert p2.goal == p.goal


def test_profile_store_add_get(tmp_path: Path):
    store = ProfileStore(path=tmp_path / "p.json", autoload=False)
    p = Profile(name="alice", weight_kg=65, height_cm=170, age=28, sex="female")
    store.add(p)
    got = store.get("alice")
    assert got is not None
    assert got.name == "alice"
    assert store.default_name == "alice"  # 第一个自动设默认


def test_profile_store_persistence(tmp_path: Path):
    path = tmp_path / "p.json"
    store = ProfileStore(path=path, autoload=False)
    store.add(Profile(name="alice", weight_kg=65, height_cm=170, age=28, sex="female"))
    store.add(Profile(name="bob", weight_kg=80, height_cm=180, age=32, sex="male"))
    store.set_default("bob")

    # 重新加载
    store2 = ProfileStore(path=path, autoload=True)
    assert len(store2) == 2
    assert "alice" in store2
    assert "bob" in store2
    assert store2.default_name == "bob"


def test_profile_store_remove(tmp_path: Path):
    store = ProfileStore(path=tmp_path / "p.json", autoload=False)
    store.add(Profile(name="alice", weight_kg=65, height_cm=170, age=28, sex="female"))
    assert store.remove("alice")
    assert store.get("alice") is None
    assert not store.remove("alice")  # 再删返回 False


def test_profile_store_set_default(tmp_path: Path):
    store = ProfileStore(path=tmp_path / "p.json", autoload=False)
    store.add(Profile(name="alice", weight_kg=65, height_cm=170, age=28, sex="female"))
    store.add(Profile(name="bob", weight_kg=80, height_cm=180, age=32, sex="male"))
    store.set_default("alice")
    assert store.default_name == "alice"
    with pytest.raises(KeyError):
        store.set_default("nonexistent")


def test_profile_store_get_or_default(tmp_path: Path):
    store = ProfileStore(path=tmp_path / "p.json", autoload=False)
    store.add(Profile(name="alice", weight_kg=65, height_cm=170, age=28, sex="female"))
    store.add(Profile(name="bob", weight_kg=80, height_cm=180, age=32, sex="male"))
    store.set_default("bob")
    assert store.get_or_default(None).name == "bob"
    assert store.get_or_default("alice").name == "alice"
    with pytest.raises(KeyError):
        store.get_or_default("nonexistent")


def test_profile_store_empty_raises(tmp_path: Path):
    store = ProfileStore(path=tmp_path / "p.json", autoload=False)
    with pytest.raises(KeyError):
        store.get_or_default(None)


def test_profile_store_file_permissions(tmp_path: Path):
    """保存的文件应为 600 权限。"""
    path = tmp_path / "p.json"
    store = ProfileStore(path=path, autoload=False)
    store.add(Profile(name="alice", weight_kg=65, height_cm=170, age=28, sex="female"))
    import os, stat
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
