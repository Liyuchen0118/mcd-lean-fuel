"""planner 模块单元测试。"""

from __future__ import annotations

import pytest

from mcleanfuel.planner import (
    FoodItem,
    MealPlan,
    Planner,
    classify_food,
    MEAL_CATEGORY_RULES,
)
from mcleanfuel.tdee import ActivityLevel, Goal, MealTarget, Sex, calculate_targets


SAMPLE_ROWS = [
    {"productName": "猪柳麦满分", "energyKcal": 308, "protein": 16, "fat": 16,
     "carbohydrate": 24, "sodium": 781, "calcium": 213},
    {"productName": "双层原味板烧鸡腿麦满分", "energyKcal": 355, "protein": 23,
     "fat": 17, "carbohydrate": 27, "sodium": 984, "calcium": 138},
    {"productName": "麦乐鸡5块", "energyKcal": 213, "protein": 12, "fat": 12,
     "carbohydrate": 13, "sodium": 422, "calcium": 9},
    {"productName": "小杯玉米杯", "energyKcal": 53, "protein": 2, "fat": 1,
     "carbohydrate": 7, "sodium": 1, "calcium": 6},
    {"productName": "冰美式中杯", "energyKcal": 13, "protein": 1, "fat": 0,
     "carbohydrate": 2, "sodium": 0, "calcium": 7},
    {"productName": "大薯条", "energyKcal": 379, "protein": 6, "fat": 16,
     "carbohydrate": 50, "sodium": 216, "calcium": 23},
    {"productName": "朱古力新地", "energyKcal": 282, "protein": 5, "fat": 9,
     "carbohydrate": 44, "sodium": 126, "calcium": 182},
]


def test_food_item_from_mcp_row():
    row = SAMPLE_ROWS[0]
    f = FoodItem.from_mcp_row(row)
    assert f.product_name == "猪柳麦满分"
    assert f.energy_kcal == 308
    assert f.protein_g == 16
    assert f.fat_g == 16
    assert f.carb_g == 24


def test_food_item_from_mcp_row_missing_name():
    with pytest.raises(ValueError, match="productName"):
        FoodItem.from_mcp_row({"energyKcal": 100})


def test_food_item_protein_density():
    f = FoodItem(product_name="test", energy_kcal=100, protein_g=20, fat_g=5, carb_g=10)
    assert f.protein_density == pytest.approx(20, rel=1e-3)


def test_classify_food():
    assert classify_food("双层吉士汉堡", 429, 27) == "mains"
    assert classify_food("中薯条", 289, 4) == "sides"
    assert classify_food("冰美式中杯", 13, 1) == "drinks_low_cal"
    assert classify_food("热燕麦奶中杯", 157, 3) == "drinks_milk"
    assert classify_food("朱古力新地", 282, 5) == "desserts"


def test_meal_category_rules_complete():
    assert set(MEAL_CATEGORY_RULES.keys()) == {"breakfast", "lunch", "dinner", "snack"}


def test_planner_from_mcp_rows():
    planner = Planner.from_mcp_rows(SAMPLE_ROWS)
    assert len(planner.foods) == len(SAMPLE_ROWS)


def test_planner_plan_meal_returns_items():
    planner = Planner.from_mcp_rows(SAMPLE_ROWS)
    targets = calculate_targets(75, 175, 30, Sex.MALE, ActivityLevel.MODERATE, Goal.CUT)
    breakfast_target = targets.meals["breakfast"]

    plan = planner.plan_meal(breakfast_target)
    assert isinstance(plan, MealPlan)
    assert len(plan.items) >= 1
    assert all(isinstance(i, FoodItem) for i in plan.items)
    # 选出的餐品应在该餐允许类型内
    allowed = MEAL_CATEGORY_RULES["breakfast"]
    for it in plan.items:
        assert it.category in allowed


def test_planner_plan_meal_skips_banned_items():
    planner = Planner.from_mcp_rows(SAMPLE_ROWS)
    targets = calculate_targets(75, 175, 30, Sex.MALE, ActivityLevel.MODERATE, Goal.CUT)
    plan = planner.plan_meal(targets.meals["breakfast"])
    # 大薯条和朱古力新地应在默认 ban 列表
    names = {it.product_name for it in plan.items}
    assert "大薯条" not in names
    assert "朱古力新地" not in names


def test_planner_plan_meal_respects_calorie_ceiling():
    planner = Planner.from_mcp_rows(SAMPLE_ROWS)
    targets = calculate_targets(75, 175, 30, Sex.MALE, ActivityLevel.MODERATE, Goal.CUT)
    # 加餐目标最小
    snack_target = targets.meals["snack"]
    plan = planner.plan_meal(snack_target)
    # 单品不应超过餐次目标 1.5 倍
    for it in plan.items:
        assert it.energy_kcal <= snack_target.calories * 1.5


def test_planner_plan_day():
    planner = Planner.from_mcp_rows(SAMPLE_ROWS)
    targets = calculate_targets(75, 175, 30, Sex.MALE, ActivityLevel.MODERATE, Goal.CUT, total_budget_yuan=100)
    plan = planner.plan_day(targets)
    assert set(plan.meals.keys()) == {"breakfast", "lunch", "dinner", "snack"}
    for name, mp in plan.meals.items():
        assert len(mp.items) >= 1, f"{name} 未选到任何餐品"
    # 全天总能量不应严重超标
    assert plan.total_kcal <= targets.target_calories * 1.5


def test_planner_with_empty_rows():
    planner = Planner(foods=[])
    targets = calculate_targets(75, 175, 30, Sex.MALE, ActivityLevel.MODERATE, Goal.CUT)
    plan = planner.plan_meal(targets.meals["breakfast"])
    assert plan.items == []
    assert plan.total_kcal == 0
