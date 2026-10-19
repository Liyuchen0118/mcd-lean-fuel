"""TDEE 与宏量营养目标计算。

使用 Mifflin-St Jeor 公式计算基础代谢率（BMR），
乘以活动系数得到每日总能量消耗（TDEE），并按目标
（减脂/维持/增肌）调整总热量，进一步分配宏量营养素。

参考文献：
- Mifflin MD, St Jeor ST, et al. (1990). A new predictive equation
  for resting energy expenditure in healthy individuals.
  Am J Clin Nutr, 51(2):241-247.
- 中国营养学会《中国居民膳食营养素参考摄入量（2023版）》
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"


class ActivityLevel(str, Enum):
    """活动水平（基于 Harris-Benedict 活动系数）。"""

    SEDENTARY = "sedentary"        # 久坐：办公室工作，无规律运动
    LIGHT = "light"                # 轻度：每周 1-3 次轻度运动
    MODERATE = "moderate"          # 中度：每周 3-5 次中等运动
    ACTIVE = "active"              # 高度：每周 6-7 次剧烈运动
    VERY_ACTIVE = "very_active"    # 极度：体力工作 + 每日剧烈训练


class Goal(str, Enum):
    CUT = "cut"            # 减脂：热量缺口 500 kcal/天（约 0.5 kg/周）
    MAINTAIN = "maintain"  # 维持
    BULK = "bulk"          # 增肌：热量盈余 400 kcal/天


ACTIVITY_FACTORS: dict[ActivityLevel, float] = {
    ActivityLevel.SEDENTARY: 1.20,
    ActivityLevel.LIGHT: 1.375,
    ActivityLevel.MODERATE: 1.55,
    ActivityLevel.ACTIVE: 1.725,
    ActivityLevel.VERY_ACTIVE: 1.90,
}

GOAL_ADJUSTMENTS: dict[Goal, int] = {
    Goal.CUT: -500,
    Goal.MAINTAIN: 0,
    Goal.BULK: +400,
}

# 三餐+加餐热量分配比例（合计 1.0）
MEAL_SPLIT: dict[str, float] = {
    "breakfast": 0.30,  # 早餐 30%
    "lunch": 0.40,      # 午餐 40%
    "dinner": 0.25,    # 晚餐 25%
    "snack": 0.05,     # 加餐 5%
}

# 宏量营养素热量系数
KCAL_PER_GRAM = {"protein": 4.0, "carb": 4.0, "fat": 9.0}


@dataclass(frozen=True)
class MealTarget:
    """单餐营养目标。"""

    name: str
    ratio: float
    calories: float
    protein_g: float
    fat_g: float
    carb_g: float
    budget_yuan: float

    @property
    def label_zh(self) -> str:
        return {
            "breakfast": "早餐",
            "lunch": "午餐",
            "dinner": "晚餐",
            "snack": "加餐",
        }.get(self.name, self.name)


@dataclass(frozen=True)
class NutritionTargets:
    """全天营养目标。"""

    bmr: float
    tdee: float
    target_calories: float
    protein_g: float
    fat_g: float
    carb_g: float
    meals: dict[str, MealTarget]

    @property
    def deficit(self) -> float:
        """每日热量缺口（正=缺口，负=盈余）。"""
        return self.tdee - self.target_calories

    @property
    def estimated_weekly_weight_change_kg(self) -> float:
        """估算每周体重变化（kg，负=减重）。"""
        # 1 kg 体脂约 7700 kcal
        return -self.deficit * 7 / 7700


def calculate_bmr(weight_kg: float, height_cm: float, age: int, sex: Sex) -> float:
    """Mifflin-St Jeor 公式计算 BMR（kcal/天）。

    男性: BMR = 10 × 体重(kg) + 6.25 × 身高(cm) - 5 × 年龄 + 5
    女性: BMR = 10 × 体重(kg) + 6.25 × 身高(cm) - 5 × 年龄 - 161
    """
    if weight_kg <= 0 or height_cm <= 0 or age <= 0:
        raise ValueError("weight_kg、height_cm、age 必须为正数")
    if age < 15 or age > 100:
        raise ValueError(f"年龄 {age} 超出适用范围（15-100）")

    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age
    return base + 5.0 if sex == Sex.MALE else base - 161.0


def calculate_targets(
    weight_kg: float,
    height_cm: float,
    age: int,
    sex: Sex,
    activity: ActivityLevel,
    goal: Goal = Goal.CUT,
    total_budget_yuan: float = 100.0,
) -> NutritionTargets:
    """计算每日营养目标 + 各餐分配。"""
    bmr = calculate_bmr(weight_kg, height_cm, age, sex)
    tdee = bmr * ACTIVITY_FACTORS[activity]
    target_calories = max(1200.0, tdee + GOAL_ADJUSTMENTS[goal])  # 最低 1200 kcal 保护

    # 蛋白质：减脂期 2.0 g/kg（保护肌肉），维持 1.6，增肌 1.8
    protein_per_kg = {Goal.CUT: 2.0, Goal.MAINTAIN: 1.6, Goal.BULK: 1.8}[goal]
    protein_g = weight_kg * protein_per_kg
    # 脂肪：0.8 g/kg（保护内分泌）
    fat_g = weight_kg * 0.8
    # 碳水：剩余热量 / 4
    remaining_kcal = max(0.0, target_calories - protein_g * 4.0 - fat_g * 9.0)
    carb_g = remaining_kcal / 4.0

    meals: dict[str, MealTarget] = {}
    for name, ratio in MEAL_SPLIT.items():
        meals[name] = MealTarget(
            name=name,
            ratio=ratio,
            calories=target_calories * ratio,
            protein_g=protein_g * ratio,
            fat_g=fat_g * ratio,
            carb_g=carb_g * ratio,
            budget_yuan=total_budget_yuan * ratio,
        )

    return NutritionTargets(
        bmr=round(bmr, 1),
        tdee=round(tdee, 1),
        target_calories=round(target_calories, 1),
        protein_g=round(protein_g, 1),
        fat_g=round(fat_g, 1),
        carb_g=round(carb_g, 1),
        meals=meals,
    )
