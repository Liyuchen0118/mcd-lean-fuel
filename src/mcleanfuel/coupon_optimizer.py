"""凑单省券优化器。

给定用户画像（每日营养目标）+ 门店 + 预算 + 已领优惠券，
自动从门店菜单中搜索满足营养约束且总价最低的餐品组合，
并通过优惠券进一步降低实付金额。

算法：
1. 拉取门店菜单（query-meals，含价格）+ 餐品营养（list-nutrition-foods）
2. 按 productName 合并得到"营养+价格"双标签餐品池
3. 拉取用户已领券（query-my-coupons）+ 门店可用券（query-store-coupons）
4. 券类型识别：定价券 / 免费券 / 买一送一 / 折扣券
5. 贪心组合搜索：在预算内最大化蛋白质达标率，最小化总价
6. 券优化：对组合中命中券的餐品应用优惠
7. 调用 calculate-price 验证最终实付金额
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .mcp_client import McpClient, McpError
from .planner import FoodItem
from .profile import Profile
from .tdee import NutritionTargets


# ----------------------------------------------------------------- 数据模型
@dataclass
class MealItem:
    """门店菜单项（含价格 + 营养）。"""

    product_code: str
    name: str
    current_price: float
    original_price: float
    image: str = ""
    can_with_order: bool = False
    nutrition: FoodItem | None = None

    @property
    def effective_price(self) -> float:
        return self.current_price if self.current_price > 0 else self.original_price


@dataclass
class Coupon:
    """优惠券（标准化结构）。"""

    coupon_id: str
    code: str
    title: str
    raw: dict = field(default_factory=dict)
    discount_type: str = "unknown"  # fixed_price | free | bogo | discount | unknown
    discount_value: float | None = None  # 定价券的新价格
    target_keyword: str = ""  # 从 title 提取的目标餐品关键词

    @classmethod
    def from_my_coupons(cls, row: dict) -> "Coupon":
        cid = str(row.get("id") or row.get("couponId") or "")
        code = str(row.get("code") or "")
        title = str(row.get("title") or row.get("couponName") or "")
        di = row.get("discountInfo", {}) or {}
        kw = _extract_coupon_keyword(title)
        dtype, dval = _classify_coupon(title, di)
        return cls(
            coupon_id=cid, code=code, title=title,
            raw=row, discount_type=dtype, discount_value=dval,
            target_keyword=kw,
        )

    @classmethod
    def from_auto_bind(cls, row: dict) -> "Coupon":
        return cls.from_my_coupons({
            "id": row.get("couponId"),
            "code": row.get("couponCode"),
            "title": row.get("couponName", ""),
            "discountInfo": row.get("discountInfo", {}),
        })

    def matches(self, meal: MealItem) -> bool:
        """该券能否用于这个餐品。

        匹配规则（按优先级）：
        1. 严格包含：券关键词 ⊆ 餐品名 或反之
        2. 规范化匹配：把"汉堡"↔"堡"、"麦满分"等后缀统一后再比较
        3. 核心子串匹配：3 字符以上的共同子串（应对"麦辣鸡腿堡" vs "麦辣鸡腿汉堡"）
        """
        if not self.target_keyword:
            return False
        kw = self.target_keyword
        name = meal.name
        if not name:
            return False
        if kw in name or name in kw:
            return True
        # 规范化后缀：汉堡→堡、麦满分→空、汉堡包→堡
        norm_map = [("汉堡包", "堡"), ("汉堡", "堡"), ("麦满分", ""), ("套餐", "")]
        kw_norm = kw
        name_norm = name
        for src, dst in norm_map:
            kw_norm = kw_norm.replace(src, dst)
            name_norm = name_norm.replace(src, dst)
        if kw_norm and name_norm and (kw_norm in name_norm or name_norm in kw_norm):
            return True
        # 核心子串：3 字符共同子串
        if len(kw) >= 3 and len(name) >= 3:
            for i in range(len(kw) - 2):
                sub = kw[i:i + 3]
                if sub in name_norm:
                    return True
        return False


@dataclass
class DealResult:
    """凑单结果。"""

    items: list[MealItem] = field(default_factory=list)
    coupons_used: list[Coupon] = field(default_factory=list)
    # 每个餐品对应的实际数量（key=items 索引，value=数量）。
    # bogo（买一送一）券应用的餐品 num=2（付 1 份钱得 2 份），其余默认 1。
    # _verify_with_mcp 会把此字段同步到 calculate-price 的 items[].num。
    item_nums: dict[int, int] = field(default_factory=dict)
    items_without_coupon_price: float = 0.0  # 不用券时的总价（按 num 累加原价）
    items_with_coupon_price: float = 0.0  # 用券后的实付
    saved_price: float = 0.0
    macros: dict[str, float] = field(default_factory=lambda: {
        "kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0, "sodium_mg": 0.0,
    })
    target_achievement: dict[str, float] = field(default_factory=lambda: {
        "calorie_ratio": 0.0, "protein_ratio": 0.0,
    })
    mcp_calculated: bool = False  # 是否调用了 calculate-price 验证

    def item_num(self, idx: int) -> int:
        """获取第 idx 个餐品的数量（默认 1）。"""
        return self.item_nums.get(idx, 1)


# ----------------------------------------------------------------- 工具函数
_BAN_WORDS = ("元", "免费", "买一送一", "半价", "立减", "立享", "打折",
              "立省", "限时", "人气", "爆款", "热卖", "新品",
              "中杯", "大杯", "小杯", "迷你", "任选", "同款")


def _extract_coupon_keyword(title: str) -> str:
    """从券标题提取目标餐品关键词。

    例：
      "11.9元麦辣鸡腿堡" → "麦辣鸡腿堡"
      "免费脆薯饼" → "脆薯饼"
      "人气麦旋风买一送一" → "麦旋风"
      "9.9元中杯冰美式" → "冰美式"
    """
    if not title:
        return ""
    s = title
    # 去掉数字和小数
    s = re.sub(r"\d+(\.\d+)?", "", s)
    # 去掉停用词
    for w in _BAN_WORDS:
        s = s.replace(w, "")
    # 去掉非中文字符
    s = re.sub(r"[^\u4e00-\u9fa5]+", "", s)
    return s.strip()


def _classify_coupon(title: str, discount_info: dict) -> tuple[str, float | None]:
    """分类券类型。

    返回 (discount_type, discount_value)
    - fixed_price: 定价券（discount_value 是新价格）
    - free: 免费券（discount_value = 0）
    - bogo: 买一送一
    - discount: 折扣券（discount_value 是折扣率/金额）
    - unknown: 未识别
    """
    t = title or ""
    di = discount_info or {}
    val_str = str(di.get("discountValue", "")).strip()
    desc = str(di.get("discountDesc", "")).strip()

    if "买一送一" in t or "买1送1" in t or "第二份" in t and "0元" in t:
        return "bogo", None

    if "免费" in t or val_str == "0":
        return "free", 0.0

    if val_str:
        try:
            v = float(val_str)
            if "折" in desc or "折扣" in desc:
                return "discount", v
            return "fixed_price", v
        except ValueError:
            pass

    return "unknown", None


# ----------------------------------------------------------------- 优化器
class CouponOptimizer:
    """凑单省券优化器。"""

    def __init__(
        self,
        client: McpClient,
        *,
        store_code: str,
        order_type: int = 1,
        be_type: int = 1,
        be_code: str | None = None,
        reservation_date: str | None = None,
    ):
        self.client = client
        self.store_code = store_code
        self.order_type = order_type
        self.be_type = be_type
        self.be_code = be_code
        self.reservation_date = reservation_date

        # 拉取后填充
        self.meals: list[MealItem] = []
        self.coupons: list[Coupon] = []

    # ----------------------------------------------------------- 数据拉取
    def fetch(self, *, auto_bind: bool = True) -> None:
        """拉取菜单 + 营养数据 + 优惠券。"""
        # 1. 营养数据
        nutrition_rows = self.client.list_nutrition_foods()
        # name → FoodItem
        nut_map: dict[str, FoodItem] = {}
        for row in nutrition_rows:
            try:
                fi = FoodItem.from_mcp_row(row)
                nut_map[fi.product_name] = fi
            except (ValueError, TypeError):
                continue

        # 2. 门店菜单（含价格）
        meals_data = self.client.query_meals(
            store_code=self.store_code,
            order_type=self.order_type,
            be_type=self.be_type,
            be_code=self.be_code,
            reservation_date=self.reservation_date,
        )
        meal_dict = meals_data.get("meals", {}) if isinstance(meals_data, dict) else {}
        for code, info in meal_dict.items():
            if not isinstance(info, dict):
                continue
            name = str(info.get("name", "")).strip()
            if not name:
                continue
            try:
                cur = float(info.get("currentPrice", 0) or 0)
            except (TypeError, ValueError):
                cur = 0.0
            try:
                orig = float(info.get("originalPrice", 0) or 0)
            except (TypeError, ValueError):
                orig = 0.0
            # 按 name 模糊匹配营养数据
            nutrition = _match_nutrition(name, nut_map)
            self.meals.append(MealItem(
                product_code=str(code),
                name=name,
                current_price=cur,
                original_price=orig,
                image=str(info.get("image", "")),
                can_with_order=bool(info.get("canWithOrder", False)),
                nutrition=nutrition,
            ))

        # 3. 自动领券（可选）
        if auto_bind:
            try:
                self.client.auto_bind_coupons()
            except McpError:
                pass

        # 4. 用户已领券
        try:
            my_data = self.client.query_my_coupons()
            for c in (my_data.get("coupons") or []):
                try:
                    self.coupons.append(Coupon.from_my_coupons(c))
                except Exception:
                    continue
        except McpError:
            pass

        # 5. 门店可用券
        try:
            store_rows = self.client.query_store_coupons(
                store_code=self.store_code,
                order_type=self.order_type,
                be_type=self.be_type,
                be_code=self.be_code,
                reservation_date=self.reservation_date,
            )
            for r in store_rows:
                if not isinstance(r, dict):
                    continue
                try:
                    self.coupons.append(Coupon.from_my_coupons(r))
                except Exception:
                    continue
        except McpError:
            pass

    # ----------------------------------------------------- 凑单核心算法
    def optimize(self, profile: Profile) -> DealResult:
        """为单个用户做凑单优化。"""
        if not self.meals:
            raise RuntimeError("未拉取门店菜单数据，请先 fetch()")

        targets = profile.compute_targets()

        # 过滤候选餐品（有营养数据的）
        candidates = [
            m for m in self.meals
            if m.nutrition is not None and m.nutrition.energy_kcal > 0
        ]
        if not candidates:
            # 没有营养匹配，直接用所有餐品做价格优化
            candidates = list(self.meals)
            for m in candidates:
                if m.nutrition is None:
                    m.nutrition = FoodItem(
                        product_name=m.name, energy_kcal=0,
                        protein_g=0, fat_g=0, carb_g=0,
                    )

        # 用 planner 思路：按蛋白质密度排序，组合搜索
        # 但 planner.FoodItem 没有 price；这里直接内联算法
        best = self._search_best_combo(candidates, targets, profile.budget_yuan)

        # 应用券优化
        result = self._apply_coupons(best, targets)

        # 调用 calculate-price 验证（若可能）
        try:
            self._verify_with_mcp(result)
            result.mcp_calculated = True
        except McpError:
            result.mcp_calculated = False

        return result

    # ----------------------------------------------------------- 组合搜索
    def _search_best_combo(
        self,
        candidates: list[MealItem],
        targets: NutritionTargets,
        budget: float,
        *,
        max_items: int = 4,
    ) -> list[MealItem]:
        """贪心+组合搜索：在预算内最大化蛋白达标率 + 卡路里贴近。"""
        # 按蛋白/价格比降序
        def key(m: MealItem) -> float:
            if not m.nutrition or m.effective_price <= 0:
                return 0
            return m.nutrition.protein_g / m.effective_price
        candidates = sorted(candidates, key=key, reverse=True)
        # 截断到前 40 个高性价比候选
        top = candidates[:40]
        if not top:
            return []

        best: list[MealItem] = [top[0]]
        best_score = self._score(top[:1], targets, budget)

        for n in (1, 2, 3, 4):
            if n > max_items:
                break
            if n > len(top):
                break
            for combo in _combinations(top, n):
                s = self._score(combo, targets, budget)
                if s < best_score:
                    best = list(combo)
                    best_score = s
        return best

    @staticmethod
    def _score(combo: list[MealItem], targets: NutritionTargets,
               budget: float) -> float:
        if not combo:
            return float("inf")
        total_k = sum(m.nutrition.energy_kcal for m in combo if m.nutrition)
        total_p = sum(m.nutrition.protein_g for m in combo if m.nutrition)
        total_f = sum(m.nutrition.fat_g for m in combo if m.nutrition)
        total_c = sum(m.nutrition.carb_g for m in combo if m.nutrition)
        total_price = sum(m.effective_price for m in combo)

        # 主目标：蛋白达标 + 卡路里贴近 + 价格最低
        score = 0.0
        # 蛋白不足重罚
        if total_p < targets.protein_g:
            score += (targets.protein_g - total_p) ** 2 * 5.0
        else:
            score -= min(total_p - targets.protein_g, targets.protein_g * 0.3) * 3.0
        # 卡路里贴近
        score += (total_k - targets.target_calories) ** 2
        # 约束：超标加罚
        if total_k > targets.target_calories * 1.15:
            score += (total_k - targets.target_calories * 1.15) ** 2 * 2.0
        if total_f > targets.fat_g * 1.3:
            score += (total_f - targets.fat_g * 1.3) ** 2 * 8.0
        if total_c > targets.carb_g * 1.3:
            score += (total_c - targets.carb_g * 1.3) ** 2 * 4.0
        # 预算约束
        if total_price > budget:
            score += (total_price - budget) ** 2 * 10.0  # 超预算重罚
        # 多样性鼓励
        score -= len(combo) * 5.0
        return score

    # ----------------------------------------------------------- 券优化
    def _apply_coupons(self, items: list[MealItem],
                        targets: NutritionTargets) -> DealResult:
        """对组合应用券，降低实付金额。

        bogo（买一送一）券的处理：
        - 实付 = 1 × 单价（不变，因为买 1 送 1）
        - 数量 = 2（得到 2 份）
        - 营养按 num=2 累加
        - _verify_with_mcp 同步传 num=2，让 MCP calculate-price 正确返回实付
        """
        original_total = sum(m.effective_price for m in items)

        used: list[Coupon] = []
        new_prices: dict[int, float] = {}  # items 索引 → 用券后单价（1 份的价格）
        item_nums: dict[int, int] = {}  # items 索引 → 数量

        for ci, coupon in enumerate(self.coupons):
            for ii, m in enumerate(items):
                if ii in new_prices:
                    continue  # 已被别的券覆盖
                if not coupon.matches(m):
                    continue
                # 应用券
                if coupon.discount_type == "fixed_price" and coupon.discount_value is not None:
                    new_prices[ii] = coupon.discount_value
                    used.append(coupon)
                    break
                elif coupon.discount_type == "free":
                    new_prices[ii] = 0.0
                    used.append(coupon)
                    break
                elif coupon.discount_type == "bogo":
                    # 买一送一：付 1 份的钱，得 2 份
                    # 实付单价不变（仍是原价），但 num=2
                    new_prices[ii] = m.effective_price
                    item_nums[ii] = 2
                    used.append(coupon)
                    break

        # 实付金额 = sum(每项用券后单价 × 数量)
        # - 普通券：单价 = 券后价，num = 1
        # - bogo：单价 = 原价，num = 2（实付 = 原价 × 1，因为 num=2 但只付 1 份钱）
        #   但 calculate-price 对 bogo 会自动算 "num=2 实付=1×单价"，所以本地也按此规则
        def _qty(i: int) -> int:
            return item_nums.get(i, 1)

        def _unit_price(i: int, m: MealItem) -> float:
            """该餐品每份实付单价（bogo 时是原价，让 num=2 拼出实付=1×原价）。"""
            return new_prices.get(i, m.effective_price)

        # bogo 券：实付 = 单价 × 1（不是 × 2，因为"买一送一"只付 1 份钱）
        # 普通券：实付 = 券后单价 × 1
        coupon_total = 0.0
        for i, m in enumerate(items):
            qty = _qty(i)
            if item_nums.get(i) == 2:  # bogo: 买一送一
                coupon_total += _unit_price(i, m) * 1  # 只付 1 份
            else:
                coupon_total += _unit_price(i, m) * qty

        # 原价合计也按 num 累加（bogo 得 2 份，原价也是 2 份）
        original_total_with_qty = sum(
            m.effective_price * _qty(i) for i, m in enumerate(items)
        )

        # 计算宏量合计（按实际数量累加）
        macros = {
            "kcal": sum(m.nutrition.energy_kcal * _qty(i) for i, m in enumerate(items) if m.nutrition),
            "protein_g": sum(m.nutrition.protein_g * _qty(i) for i, m in enumerate(items) if m.nutrition),
            "fat_g": sum(m.nutrition.fat_g * _qty(i) for i, m in enumerate(items) if m.nutrition),
            "carb_g": sum(m.nutrition.carb_g * _qty(i) for i, m in enumerate(items) if m.nutrition),
            "sodium_mg": sum(m.nutrition.sodium_mg * _qty(i) for i, m in enumerate(items) if m.nutrition),
        }

        return DealResult(
            items=items,
            coupons_used=used,
            item_nums=item_nums,
            items_without_coupon_price=round(original_total_with_qty, 2),
            items_with_coupon_price=round(coupon_total, 2),
            saved_price=round(original_total_with_qty - coupon_total, 2),
            macros=macros,
            target_achievement={
                "calorie_ratio": round(macros["kcal"] / max(targets.target_calories, 1), 3),
                "protein_ratio": round(macros["protein_g"] / max(targets.protein_g, 1), 3),
            },
        )

    # ----------------------------------------------------------- MCP 验证
    def _verify_with_mcp(self, result: DealResult) -> None:
        """调用 calculate-price 验证实际价格。

        items 的 num 字段来自 result.item_nums：
        - 普通券：num=1
        - bogo（买一送一）券：num=2，MCP 会自动算"买一送一"实付 = 1 × 单价
        """
        if not result.items:
            return
        items_payload = [
            {"code": m.product_code, "num": result.item_num(i)}
            for i, m in enumerate(result.items)
        ]
        coupon_ids = [c.coupon_id for c in result.coupons_used if c.coupon_id]
        calc = self.client.calculate_price(
            store_code=self.store_code,
            order_type=self.order_type,
            be_type=self.be_type,
            items=items_payload,
            be_code=self.be_code,
            coupon_ids=coupon_ids or None,
            reservation_date=self.reservation_date,
        )
        # 提取实付金额（字段名可能不同）
        for k in ("payAmount", "totalPrice", "actualPrice", "amount"):
            v = calc.get(k)
            if isinstance(v, (int, float)):
                result.items_with_coupon_price = round(float(v), 2)
                result.saved_price = round(result.items_without_coupon_price - float(v), 2)
                return
        # 解析嵌套
        data = calc.get("data") if isinstance(calc, dict) else None
        if isinstance(data, dict):
            for k in ("payAmount", "totalPrice", "actualPrice", "amount"):
                v = data.get(k)
                if isinstance(v, (int, float)):
                    result.items_with_coupon_price = round(float(v), 2)
                    result.saved_price = round(result.items_without_coupon_price - float(v), 2)
                    return


# ----------------------------------------------------------------- 辅助
def _match_nutrition(name: str, nut_map: dict[str, FoodItem]) -> FoodItem | None:
    """按名称匹配营养数据。先精确，再包含匹配。"""
    if name in nut_map:
        return nut_map[name]
    # 包含匹配（短名→长名）
    for k, v in nut_map.items():
        if k in name or name in k:
            return v
    return None


def _combinations(items: list, r: int):
    """itertools.combinations 的轻量包装（避免 import overhead 不大，主要是为了限流）。"""
    from itertools import combinations
    return combinations(items, r)
