"""用户画像管理 + JSON 持久化。

存储位置：``~/.config/mcleanfuel/profiles.json``（家目录，不进项目 git）
权限 600。

用法::

    from mcleanfuel.profile import ProfileStore, Profile

    store = ProfileStore()
    alice = store.add(Profile(name="alice", weight_kg=65, height_cm=170,
                              age=28, sex="female", activity="moderate",
                              goal="cut", budget_yuan=80))
    store.set_default("alice")
    print(store.get("alice"))
    print(store.list_all())
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .tdee import ActivityLevel, Goal, Sex, calculate_targets, NutritionTargets


CONFIG_DIR = Path(os.environ.get("MCLEANFUEL_HOME") or "~/.config/mcleanfuel").expanduser()
PROFILES_FILE = CONFIG_DIR / "profiles.json"


@dataclass
class Profile:
    """用户画像。"""

    name: str
    weight_kg: float
    height_cm: float
    age: int
    sex: str  # "male" | "female"
    activity: str = "moderate"  # 见 ActivityLevel
    goal: str = "cut"  # 见 Goal
    budget_yuan: float = 80.0  # 全天预算
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def __post_init__(self):
        if not self.name or not self.name.strip():
            raise ValueError("用户名不能为空")
        self.name = self.name.strip()
        # 校验枚举值
        ActivityLevel(self.activity)
        Goal(self.goal)
        Sex(self.sex)
        if self.weight_kg <= 0 or self.height_cm <= 0 or self.age <= 0:
            raise ValueError("weight_kg、height_cm、age 必须为正数")
        if self.budget_yuan < 0:
            raise ValueError("budget_yuan 不能为负")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Profile":
        return cls(
            name=d["name"],
            weight_kg=float(d["weight_kg"]),
            height_cm=float(d["height_cm"]),
            age=int(d["age"]),
            sex=d["sex"],
            activity=d.get("activity", "moderate"),
            goal=d.get("goal", "cut"),
            budget_yuan=float(d.get("budget_yuan", 80.0)),
            created_at=d.get("created_at", datetime.now().isoformat(timespec="seconds")),
            updated_at=d.get("updated_at", datetime.now().isoformat(timespec="seconds")),
        )

    def compute_targets(self) -> NutritionTargets:
        """根据画像计算每日营养目标。"""
        return calculate_targets(
            weight_kg=self.weight_kg,
            height_cm=self.height_cm,
            age=self.age,
            sex=Sex(self.sex),
            activity=ActivityLevel(self.activity),
            goal=Goal(self.goal),
            total_budget_yuan=self.budget_yuan,
        )


class ProfileStore:
    """用户画像仓库（JSON 持久化）。"""

    def __init__(self, path: Path | None = None, *, autoload: bool = True):
        self.path = path or PROFILES_FILE
        self._profiles: dict[str, Profile] = {}
        self._default: str | None = None
        if autoload:
            self.load()

    # ----------------------------------------------------------- 读写文件
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(data, dict):
            return
        self._default = data.get("default")
        for name, d in (data.get("profiles") or {}).items():
            try:
                self._profiles[name] = Profile.from_dict(d)
            except (KeyError, ValueError, TypeError):
                continue

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 设置 600 权限（家目录配置文件）
        old_umask = os.umask(0o077)
        try:
            payload = {
                "version": 1,
                "default": self._default,
                "profiles": {n: p.to_dict() for n, p in self._profiles.items()},
            }
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            os.umask(old_umask)

    # ----------------------------------------------------------- 增删改查
    def add(self, profile: Profile, *, set_default: bool = False) -> Profile:
        """新增/更新用户画像。同名 profile 会被覆盖。"""
        if not isinstance(profile, Profile):
            raise TypeError("profile 必须是 Profile 实例")
        profile.updated_at = datetime.now().isoformat(timespec="seconds")
        self._profiles[profile.name] = profile
        if set_default or self._default is None:
            self._default = profile.name
        self.save()
        return profile

    def get(self, name: str) -> Profile | None:
        return self._profiles.get(name)

    def get_or_default(self, name: str | None = None) -> Profile:
        """获取指定用户或默认用户；都没有则报错。"""
        if name:
            p = self._profiles.get(name)
            if p is None:
                raise KeyError(f"未找到用户画像：{name}")
            return p
        if self._default and self._default in self._profiles:
            return self._profiles[self._default]
        if not self._profiles:
            raise KeyError("未配置任何用户画像，请先 `mcleanfuel profile add` 添加")
        # 没有显式默认，取第一个
        return next(iter(self._profiles.values()))

    def remove(self, name: str) -> bool:
        if name not in self._profiles:
            return False
        del self._profiles[name]
        if self._default == name:
            self._default = next(iter(self._profiles), None)
        self.save()
        return True

    def set_default(self, name: str) -> None:
        if name not in self._profiles:
            raise KeyError(f"未找到用户画像：{name}")
        self._default = name
        self.save()

    def list_all(self) -> list[Profile]:
        return list(self._profiles.values())

    @property
    def default_name(self) -> str | None:
        return self._default

    def __len__(self) -> int:
        return len(self._profiles)

    def __contains__(self, name: str) -> bool:
        return name in self._profiles
