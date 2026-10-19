"""coupon_optimizer 模块单元测试。"""

from __future__ import annotations

import pytest

from mcleanfuel.coupon_optimizer import (
    Coupon,
    CouponOptimizer,
    DealResult,
    MealItem,
    _classify_coupon,
    _extract_coupon_keyword,
)
from mcleanfuel.planner import FoodItem


def test_extract_coupon_keyword_fixed_price():
    assert _extract_coupon_keyword("11.9元麦辣鸡腿堡") == "麦辣鸡腿堡"


def test_extract_coupon_keyword_free():
    assert _extract_coupon_keyword("免费脆薯饼") == "脆薯饼"


def test_extract_coupon_keyword_bogo():
    assert _extract_coupon_keyword("人气麦旋风买一送一") == "麦旋风"


def test_extract_coupon_keyword_with_size():
    assert _extract_coupon_keyword("9.9元中杯冰美式") == "冰美式"


def test_extract_coupon_keyword_empty():
    assert _extract_coupon_keyword("") == ""


def test_classify_coupon_fixed_price():
    dtype, val = _classify_coupon("11.9元麦辣鸡腿堡", {"discountValue": "11.9"})
    assert dtype == "fixed_price"
    assert val == pytest.approx(11.9)


def test_classify_coupon_free():
    dtype, val = _classify_coupon("免费脆薯饼", {"discountValue": "0"})
    assert dtype == "free"
    assert val == 0


def test_classify_coupon_bogo():
    dtype, val = _classify_coupon("人气麦旋风买一送一", {"discountValue": "15"})
    assert dtype == "bogo"
    assert val is None


def test_coupon_from_my_coupons():
    c = Coupon.from_my_coupons({
        "id": "abc123",
        "code": "MCD6XXX",
        "title": "11.9元麦辣鸡腿堡",
        "discountInfo": {"discountValue": "11.9", "discountDesc": "用券价格"},
    })
    assert c.coupon_id == "abc123"
    assert c.code == "MCD6XXX"
    assert c.title == "11.9元麦辣鸡腿堡"
    assert c.discount_type == "fixed_price"
    assert c.discount_value == pytest.approx(11.9)
    assert c.target_keyword == "麦辣鸡腿堡"


def test_coupon_matches():
    c = Coupon.from_my_coupons({"title": "11.9元麦辣鸡腿堡",
                                 "discountInfo": {"discountValue": "11.9"}})
    m = MealItem(product_code="1", name="麦辣鸡腿汉堡",
                 current_price=22.5, original_price=25)
    assert c.matches(m)

    m2 = MealItem(product_code="2", name="双层吉士汉堡",
                  current_price=22.5, original_price=25)
    assert not c.matches(m2)


def test_meal_item_effective_price():
    """currentPrice > 0 时用 currentPrice，否则用 originalPrice。"""
    m1 = MealItem(product_code="1", name="A", current_price=11.5, original_price=15)
    assert m1.effective_price == 11.5
    m2 = MealItem(product_code="2", name="B", current_price=0, original_price=15)
    assert m2.effective_price == 15


def test_coupon_optimizer_with_mock_meals(monkeypatch):
    """用 monkeypatch 模拟 MCP client，验证凑单流程能跑通。"""
    class FakeClient:
        def list_nutrition_foods(self):
            return [
                {"productName": "双层吉士汉堡", "energyKcal": 429, "protein": 27,
                 "fat": 22, "carbohydrate": 31, "sodium": 1017, "calcium": 232},
                {"productName": "麦乐鸡5块", "energyKcal": 213, "protein": 12,
                 "fat": 12, "carbohydrate": 13, "sodium": 422, "calcium": 9},
                {"productName": "冰美式中杯", "energyKcal": 13, "protein": 1,
                 "fat": 0, "carbohydrate": 2, "sodium": 0, "calcium": 7},
            ]
        def query_meals(self, **kwargs):
            return {"meals": {
                "001": {"name": "双层吉士汉堡", "currentPrice": "22.5", "originalPrice": "25"},
                "002": {"name": "麦乐鸡5块", "currentPrice": "13.0", "originalPrice": "15"},
                "003": {"name": "冰美式中杯", "currentPrice": "10.0", "originalPrice": "12"},
            }}
        def auto_bind_coupons(self): return {"successCoupons": []}
        def query_my_coupons(self, **kwargs): return {"coupons": []}
        def query_store_coupons(self, **kwargs): return []
        def calculate_price(self, **kwargs): return {}

    opt = CouponOptimizer(FakeClient(), store_code="1950105")
    opt.fetch(auto_bind=False)
    assert len(opt.meals) == 3
    # 双层吉士汉堡应有营养数据匹配
    target_meal = next(m for m in opt.meals if m.name == "双层吉士汉堡")
    assert target_meal.nutrition is not None
    assert target_meal.nutrition.protein_g == pytest.approx(27)
    assert target_meal.effective_price == pytest.approx(22.5)

    from mcleanfuel.profile import Profile
    profile = Profile(name="test", weight_kg=70, height_cm=175, age=30, sex="male",
                       activity="moderate", goal="cut", budget_yuan=50)
    deal = opt.optimize(profile)
    assert isinstance(deal, DealResult)
    assert len(deal.items) >= 1


def test_bogo_coupon_sets_item_num_to_2():
    """bogo（买一送一）券应用后，餐品数量应为 2，实付按 1 份算。"""
    class BogoClient:
        def list_nutrition_foods(self):
            return [{"productName": "麦旋风", "energyKcal": 266, "protein": 5,
                      "fat": 9, "carbohydrate": 40, "sodium": 116, "calcium": 185}]
        def query_meals(self, **kwargs):
            return {"meals": {
                "001": {"name": "麦旋风", "currentPrice": "15.0", "originalPrice": "15.0"},
            }}
        def auto_bind_coupons(self): return {"successCoupons": []}
        def query_my_coupons(self, **kwargs):
            return {"coupons": [{
                "id": "bogo1", "code": "BOGO", "title": "人气麦旋风买一送一",
                "discountInfo": {"discountValue": "15", "discountDesc": "用券价格"},
            }]}
        def query_store_coupons(self, **kwargs): return []
        def calculate_price(self, **kwargs): return {}

    opt = CouponOptimizer(BogoClient(), store_code="1950105")
    opt.fetch(auto_bind=False)
    assert len(opt.coupons) == 1
    coupon = opt.coupons[0]
    assert coupon.discount_type == "bogo"
    assert coupon.target_keyword == "麦旋风"

    from mcleanfuel.profile import Profile
    profile = Profile(name="t", weight_kg=70, height_cm=175, age=30, sex="male",
                       activity="moderate", goal="cut", budget_yuan=50)
    deal = opt.optimize(profile)
    assert len(deal.items) >= 1
    # 第一个餐品应用了 bogo
    assert deal.item_nums.get(0) == 2
    # 营养按 num=2 累加
    assert deal.macros["protein_g"] == pytest.approx(5 * 2, rel=1e-3)
    assert deal.macros["kcal"] == pytest.approx(266 * 2, rel=1e-3)
    # 实付 = 1 × 单价（不是 2 × 单价）
    assert deal.items_with_coupon_price == pytest.approx(15.0, rel=1e-3)
    # 原价 = 2 × 单价
    assert deal.items_without_coupon_price == pytest.approx(30.0, rel=1e-3)
    # 节省 = 原价 - 实付 = 15
    assert deal.saved_price == pytest.approx(15.0, rel=1e-3)
    # 用了 1 张 bogo 券
    assert len(deal.coupons_used) == 1
    assert deal.coupons_used[0].discount_type == "bogo"


def test_bogo_coupon_verify_with_mcp_passes_num_2():
    """_verify_with_mcp 应把 bogo 餐品的 num 传为 2 给 calculate-price。"""
    captured = {}

    class CapturingClient:
        def list_nutrition_foods(self):
            return [{"productName": "麦旋风", "energyKcal": 266, "protein": 5,
                      "fat": 9, "carbohydrate": 40, "sodium": 116, "calcium": 185}]
        def query_meals(self, **kwargs):
            return {"meals": {
                "001": {"name": "麦旋风", "currentPrice": "15.0", "originalPrice": "15.0"},
            }}
        def auto_bind_coupons(self): return {"successCoupons": []}
        def query_my_coupons(self, **kwargs):
            return {"coupons": [{
                "id": "bogo1", "code": "BOGO", "title": "人气麦旋风买一送一",
                "discountInfo": {"discountValue": "15", "discountDesc": "用券价格"},
            }]}
        def query_store_coupons(self, **kwargs): return []
        def calculate_price(self, **kwargs):
            captured.update(kwargs)
            # 模拟 MCP 返回的实付（bogo: 1×单价）
            return {"payAmount": 15.0}

    opt = CapturingClient.__new__  # dummy
    optimizer = CouponOptimizer(CapturingClient(), store_code="1950105")
    optimizer.fetch(auto_bind=False)

    from mcleanfuel.profile import Profile
    profile = Profile(name="t", weight_kg=70, height_cm=175, age=30, sex="male",
                       activity="moderate", goal="cut", budget_yuan=50)
    deal = optimizer.optimize(profile)

    # 验证 calculate-price 收到的 items num=2
    assert "items" in captured
    items = captured["items"]
    assert isinstance(items, list)
    assert items[0]["num"] == 2  # bogo 传 num=2
    # MCP 返回的实付 15.0 应覆盖本地估算
    assert deal.items_with_coupon_price == pytest.approx(15.0, rel=1e-3)
    assert deal.mcp_calculated is True
