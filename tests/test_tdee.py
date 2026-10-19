"""tdee 模块单元测试。"""

from __future__ import annotations

import pytest

from mcleanfuel.tdee import (
    ActivityLevel,
    Goal,
    MealTarget,
    NutritionTargets,
    Sex,
    calculate_bmr,
    calculate_targets,
)


def test_calculate_bmr_male():
    # 75 kg, 175 cm, 30 岁 男性
    bmr = calculate_bmr(75, 175, 30, Sex.MALE)
    expected = 10 * 75 + 6.25 * 175 - 5 * 30 + 5
    assert bmr == pytest.approx(expected, rel=1e-3)


def test_calculate_bmr_female():
    bmr = calculate_bmr(60, 165, 28, Sex.FEMALE)
    expected = 10 * 60 + 6.25 * 165 - 5 * 28 - 161
    assert bmr == pytest.approx(expected, rel=1e-3)


def test_calculate_bmr_invalid_inputs():
    with pytest.raises(ValueError, match="正数"):
        calculate_bmr(0, 175, 30, Sex.MALE)
    with pytest.raises(ValueError, match="正数"):
        calculate_bmr(75, -1, 30, Sex.MALE)
    with pytest.raises(ValueError, match="超出"):
        calculate_bmr(75, 175, 10, Sex.MALE)
    with pytest.raises(ValueError, match="超出"):
        calculate_bmr(75, 175, 150, Sex.MALE)


def test_calculate_targets_cut():
    t = calculate_targets(
        weight_kg=75, height_cm=175, age=30,
        sex=Sex.MALE, activity=ActivityLevel.MODERATE,
        goal=Goal.CUT, total_budget_yuan=80.0,
    )
    # 减脂期热量应低于 TDEE 500
    assert t.target_calories < t.tdee
    assert t.deficit == pytest.approx(500, rel=1e-3)
    # 蛋白质 2.0 g/kg
    assert t.protein_g == pytest.approx(150, rel=1e-3)
    # 脂肪 0.8 g/kg
    assert t.fat_g == pytest.approx(60, rel=1e-3)
    # 四餐都有
    assert set(t.meals.keys()) == {"breakfast", "lunch", "dinner", "snack"}


def test_calculate_targets_min_floor():
    # 极小体重 + 减脂，仍不低于 1200 kcal
    t = calculate_targets(
        weight_kg=40, height_cm=150, age=18,
        sex=Sex.FEMALE, activity=ActivityLevel.SEDENTARY,
        goal=Goal.CUT,
    )
    assert t.target_calories >= 1200


def test_calculate_targets_meal_split():
    t = calculate_targets(
        weight_kg=75, height_cm=175, age=30,
        sex=Sex.MALE, activity=ActivityLevel.MODERATE,
        goal=Goal.MAINTAIN, total_budget_yuan=100,
    )
    # 4 餐合计 = 全天目标
    total_calories = sum(m.calories for m in t.meals.values())
    assert total_calories == pytest.approx(t.target_calories, rel=1e-3)

    # 比例正确：30/40/25/5
    ratios = {n: m.calories / t.target_calories for n, m in t.meals.items()}
    assert ratios["breakfast"] == pytest.approx(0.30, rel=1e-3)
    assert ratios["lunch"] == pytest.approx(0.40, rel=1e-3)
    assert ratios["dinner"] == pytest.approx(0.25, rel=1e-3)
    assert ratios["snack"] == pytest.approx(0.05, rel=1e-3)


def test_calculate_targets_budget_split():
    t = calculate_targets(
        weight_kg=75, height_cm=175, age=30,
        sex=Sex.MALE, activity=ActivityLevel.MODERATE,
        goal=Goal.CUT, total_budget_yuan=100.0,
    )
    assert t.meals["breakfast"].budget_yuan == pytest.approx(30, rel=1e-3)
    assert t.meals["lunch"].budget_yuan == pytest.approx(40, rel=1e-3)


def test_calculate_targets_weekly_weight_change():
    t = calculate_targets(
        weight_kg=75, height_cm=175, age=30,
        sex=Sex.MALE, activity=ActivityLevel.MODERATE,
        goal=Goal.CUT,
    )
    # 减脂缺口 500 kcal/天 → 约 0.45 kg/周减重
    assert t.estimated_weekly_weight_change_kg < 0
    assert t.estimated_weekly_weight_change_kg == pytest.approx(-500 * 7 / 7700, rel=1e-3)
