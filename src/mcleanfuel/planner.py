"""减脂餐组合优化器。

对每个餐次（早/午/晚/加餐），从麦当劳营养数据库中
贪心搜索 1-2 个食物组合，使其宏量营养与预算约束
尽可能满足该餐次目标。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable

from .tdee import MealTarget, NutritionTargets


@dataclass(frozen=True)
class FoodItem:
    """麦当劳餐品营养信息（来自 list-nutrition-foods）。"""

    product_name: str
    energy_kcal: float
    protein_g: float
    fat_g: float
    carb_g: float
    sodium_mg: float = 0.0
    calcium_mg: float = 0.0
    price_yuan: float = 0.0  # 可选，来自 query-meals
    product_code: str = ""

    @classmethod
    def from_mcp_row(cls, row: dict) -> "FoodItem":
        name = str(row.get("productName") or "").strip()
        if not name:
            raise ValueError("餐品缺少 productName")
        return cls(
            product_name=name,
            energy_kcal=_f(row.get("energyKcal")),
            protein_g=_f(row.get("protein")),
            fat_g=_f(row.get("fat")),
            carb_g=_f(row.get("carbohydrate")),
            sodium_mg=_f(row.get("sodium")),
            calcium_mg=_f(row.get("calcium")),
            product_code=str(row.get("productCode") or ""),
        )

    @property
    def protein_density(self) -> float:
        """蛋白质密度：g 蛋白 / 100 kcal。"""
        return self.protein_g / max(self.energy_kcal, 1) * 100

    @property
    def category(self) -> str:
        return classify_food(self.product_name, self.energy_kcal, self.protein_g)


@dataclass
class MealPlan:
    """单餐方案。"""

    meal: MealTarget
    items: list[FoodItem] = field(default_factory=list)

    @property
    def total_kcal(self) -> float:
        return sum(i.energy_kcal for i in self.items)

    @property
    def total_protein_g(self) -> float:
        return sum(i.protein_g for i in self.items)

    @property
    def total_fat_g(self) -> float:
        return sum(i.fat_g for i in self.items)

    @property
    def total_carb_g(self) -> float:
        return sum(i.carb_g for i in self.items)

    @property
    def total_sodium_mg(self) -> float:
        return sum(i.sodium_mg for i in self.items)

    @property
    def total_price_yuan(self) -> float:
        return sum(i.price_yuan for i in self.items)

    @property
    def protein_achievement_ratio(self) -> float:
        return self.total_protein_g / max(self.meal.protein_g, 0.1)

    @property
    def calorie_overrun_ratio(self) -> float:
        return self.total_kcal / max(self.meal.calories, 1) - 1.0

    @property
    def is_calorie_compliant(self) -> bool:
        return self.total_kcal <= self.meal.calories * 1.15

    @property
    def is_protein_compliant(self) -> bool:
        return self.total_protein_g >= self.meal.protein_g * 0.85

    @property
    def is_fat_compliant(self) -> bool:
        return self.total_fat_g <= self.meal.fat_g * 1.30

    @property
    def is_carb_compliant(self) -> bool:
        return self.total_carb_g <= self.meal.carb_g * 1.30

    @property
    def compliance(self) -> bool:
        return (
            self.is_calorie_compliant
            and self.is_protein_compliant
            and self.is_fat_compliant
            and self.is_carb_compliant
        )


@dataclass
class PlanResult:
    """全天方案。"""

    targets: NutritionTargets
    meals: dict[str, MealPlan] = field(default_factory=dict)

    @property
    def total_kcal(self) -> float:
        return sum(m.total_kcal for m in self.meals.values())

    @property
    def total_protein_g(self) -> float:
        return sum(m.total_protein_g for m in self.meals.values())

    @property
    def total_fat_g(self) -> float:
        return sum(m.total_fat_g for m in self.meals.values())

    @property
    def total_carb_g(self) -> float:
        return sum(m.total_carb_g for m in self.meals.values())

    @property
    def total_sodium_mg(self) -> float:
        return sum(m.total_sodium_mg for m in self.meals.values())

    @property
    def total_price_yuan(self) -> float:
        return sum(m.total_price_yuan for m in self.meals.values())

    @property
    def protein_achievement_ratio(self) -> float:
        return self.total_protein_g / max(self.targets.protein_g, 0.1)

    @property
    def calorie_overrun_ratio(self) -> float:
        return self.total_kcal / max(self.targets.target_calories, 1) - 1.0


# ------------------------------------------------------------------ 分类规则
def classify_food(name: str, kcal: float, protein: float) -> str:
    """根据名称和营养粗略分类（用于餐次食物类型约束）。"""
    n = name.lower()
    if any(k in n for k in ("汉堡", "麦满分", "卷", "堡", "炒双蛋")):
        return "mains"
    if any(k in n for k in ("薯条", "鸡翅", "麦乐鸡", "鸡球", "沙拉", "玉米杯")):
        return "sides"
    if any(k in n for k in ("美式", "伯爵红茶")):
        return "drinks_low_cal"
    if any(k in n for k in ("拿铁", "玛奇朵", "卡布奇诺", "燕麦奶", "燕麦奶铁")):
        return "drinks_milk"
    if any(k in n for k in ("新地", "麦旋风", "派", "圆筒", "拉明顿")):
        return "desserts"
    if any(k in n for k in ("可乐", "怡泉", "+c")):
        return "drinks_sweet"
    if "鸡" in n or "汁鸡" in n:
        return "mains"
    return "other"


# 餐次食物类型约束（每餐选什么类型食物）
MEAL_CATEGORY_RULES: dict[str, set[str]] = {
    "breakfast": {"mains", "sides", "drinks_low_cal", "drinks_milk"},
    "lunch": {"mains", "sides", "drinks_low_cal", "drinks_milk"},
    "dinner": {"mains", "sides", "drinks_low_cal"},
    "snack": {"drinks_low_cal", "drinks_milk", "sides", "desserts"},
}


# ------------------------------------------------------------------ 核心规划
class Planner:
    """减脂餐规划器。"""

    # 单餐最大食物数（避免方案过散）
    MAX_ITEMS_PER_MEAL = 3
    # 减脂期默认禁用的餐品（高糖/高脂甜品、大份薯条等）
    DEFAULT_BAN_KEYWORDS = ("朱古力新地", "草莓新地", "麦旋风", "圆筒冰淇淋",
                            "香芋派", "菠萝派", "大薯条", "拉明顿",
                            "迷你朱古力新地", "迷你草莓新地")

    def __init__(self, foods: list[FoodItem], ban_keywords: Iterable[str] | None = None):
        self.foods = foods
        self._ban = tuple(ban_keywords) if ban_keywords else self.DEFAULT_BAN_KEYWORDS

    @classmethod
    def from_mcp_rows(cls, rows: list[dict], **kwargs) -> "Planner":
        foods: list[FoodItem] = []
        for r in rows:
            try:
                foods.append(FoodItem.from_mcp_row(r))
            except (ValueError, TypeError):
                continue
        return cls(foods, **kwargs)

    def _filter_candidates(self, target: MealTarget) -> list[FoodItem]:
        """按餐次类型约束 + 减脂合理性筛候选。"""
        allowed = MEAL_CATEGORY_RULES.get(target.name, set())
        out: list[FoodItem] = []
        for f in self.foods:
            if any(kw in f.product_name for kw in self._ban):
                continue
            if f.energy_kcal <= 0 or f.energy_kcal > target.calories * 1.5:
                # 单品超餐次上限 1.5 倍直接淘汰
                continue
            if f.category not in allowed:
                continue
            out.append(f)
        # 按蛋白质密度降序
        out.sort(key=lambda x: x.protein_density, reverse=True)
        return out

    def plan_meal(self, target: MealTarget) -> MealPlan:
        """为单餐求解最优食物组合。"""
        candidates = self._filter_candidates(target)
        if not candidates:
            return MealPlan(meal=target, items=[])

        # 限制组合搜索规模：取前 N 个高蛋白候选
        top_n = min(20, len(candidates))
        top = candidates[:top_n]

        best: list[FoodItem] = [top[0]]
        best_score = self._score_combo(best, target)

        # 单品
        for combo in top:
            s = self._score_combo([combo], target)
            if s < best_score:
                best, best_score = [combo], s

        # 双品
        for a, b in combinations(top, 2):
            s = self._score_combo([a, b], target)
            if s < best_score:
                best, best_score = [a, b], s

        # 三品（仅当双品仍不达标时展开）
        if not self._is_feasible(best, target) and len(top) >= 3:
            for a, b, c in combinations(top, 3):
                s = self._score_combo([a, b, c], target)
                if s < best_score:
                    best, best_score = [a, b, c], s

        return MealPlan(meal=target, items=best)

    def plan_day(self, targets: NutritionTargets) -> PlanResult:
        """规划全天三餐+加餐方案。"""
        result = PlanResult(targets=targets)
        for meal_name, target in targets.meals.items():
            result.meals[meal_name] = self.plan_meal(target)
        return result

    # ---------------------------------------------------------- 评分与可行性
    @staticmethod
    def _is_feasible(combo: list[FoodItem], target: MealTarget) -> bool:
        if not combo:
            return False
        total_k = sum(i.energy_kcal for i in combo)
        total_p = sum(i.protein_g for i in combo)
        total_f = sum(i.fat_g for i in combo)
        total_c = sum(i.carb_g for i in combo)
        return (
            total_k <= target.calories * 1.15
            and total_p >= target.protein_g * 0.70
            and total_f <= target.fat_g * 1.30
            and total_c <= target.carb_g * 1.30
        )

    @staticmethod
    def _score_combo(combo: list[FoodItem], target: MealTarget) -> float:
        """评分（越低越好）。组合优化目标：蛋白达标 + 卡路里贴近 + 约束不超。"""
        if not combo:
            return float("inf")

        total_k = sum(i.energy_kcal for i in combo)
        total_p = sum(i.protein_g for i in combo)
        total_f = sum(i.fat_g for i in combo)
        total_c = sum(i.carb_g for i in combo)

        # 主目标：卡路里贴近目标（最小化差值平方）
        score = (total_k - target.calories) ** 2

        # 蛋白：不足则重罚；超过则有界奖励（避免鼓励远超）
        if total_p < target.protein_g:
            score += (target.protein_g - total_p) ** 2 * 5.0
        else:
            bonus = min(total_p - target.protein_g, target.protein_g * 0.3)
            score -= bonus * 3.0

        # 约束：超标加罚
        if total_k > target.calories * 1.15:
            score += (total_k - target.calories * 1.15) ** 2 * 2.0
        if total_f > target.fat_g * 1.30:
            score += (total_f - target.fat_g * 1.30) ** 2 * 8.0
        if total_c > target.carb_g * 1.30:
            score += (total_c - target.carb_g * 1.30) ** 2 * 4.0

        # 钠：减脂期不超 800 mg/餐
        total_na = sum(i.sodium_mg for i in combo)
        if total_na > 800:
            score += (total_na - 800) ** 2 * 0.01

        # 多样性：组合越大越多分摊，少量奖励
        score -= len(combo) * 5.0

        return score


def _f(v) -> float:
    """安全转 float。"""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
