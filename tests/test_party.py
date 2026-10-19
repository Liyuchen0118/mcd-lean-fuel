"""party 模块单元测试。"""

from __future__ import annotations

import pytest

from mcleanfuel.party import PartyPlanner, PartyPlan
from mcleanfuel.profile import Profile


class _FakeClient:
    """最小化的 Mock client，仅返回固定数据。"""

    def list_nutrition_foods(self):
        return [
            {"productName": "双层吉士汉堡", "energyKcal": 429, "protein": 27,
             "fat": 22, "carbohydrate": 31, "sodium": 1017, "calcium": 232},
            {"productName": "麦乐鸡5块", "energyKcal": 213, "protein": 12,
             "fat": 12, "carbohydrate": 13, "sodium": 422, "calcium": 9},
            {"productName": "小杯玉米杯", "energyKcal": 53, "protein": 2,
             "fat": 1, "carbohydrate": 7, "sodium": 1, "calcium": 6},
        ]

    def query_meals(self, **kwargs):
        return {"meals": {
            "001": {"name": "双层吉士汉堡", "currentPrice": "22.5", "originalPrice": "25"},
            "002": {"name": "麦乐鸡5块", "currentPrice": "13.0", "originalPrice": "15"},
            "003": {"name": "小杯玉米杯", "currentPrice": "6.0", "originalPrice": "8"},
        }}

    def auto_bind_coupons(self): return {"successCoupons": []}
    def query_my_coupons(self, **kwargs): return {"coupons": []}
    def query_store_coupons(self, **kwargs): return []
    def calculate_price(self, **kwargs): return {}
    def query_nearby_stores(self, **kwargs):
        return [{"storeCode": "1950105", "storeName": "测试门店",
                  "address": "测试地址", "businessStartTime": "06:00",
                  "businessEndTime": "22:00"}]


def test_party_planner_single_member():
    """单成员组队应等价于单人 deal。"""
    planner = PartyPlanner(
        _FakeClient(),
        store_code="1950105",
        store_info={"storeName": "测试门店"},
    )
    profile = Profile(name="alice", weight_kg=65, height_cm=170, age=28,
                       sex="female", activity="moderate", goal="cut", budget_yuan=80)
    plan = planner.plan([profile], total_budget=80)
    assert isinstance(plan, PartyPlan)
    assert plan.member_count == 1
    assert len(plan.members[0].deal.items) >= 1
    assert plan.total_original_price > 0


def test_party_planner_multi_members():
    """多成员组队，预算按蛋白比例分配。"""
    planner = PartyPlanner(
        _FakeClient(),
        store_code="1950105",
        store_info={"storeName": "测试门店"},
    )
    alice = Profile(name="alice", weight_kg=65, height_cm=170, age=28,
                     sex="female", activity="moderate", goal="cut", budget_yuan=60)
    bob = Profile(name="bob", weight_kg=80, height_cm=180, age=32,
                   sex="male", activity="active", goal="maintain", budget_yuan=80)
    plan = planner.plan([alice, bob], total_budget=150)
    assert plan.member_count == 2
    # 预算分配应按蛋白比例（bob 目标蛋白高于 alice）
    alice_budget = plan.members[0].profile.budget_yuan
    bob_budget = plan.members[1].profile.budget_yuan
    assert alice_budget + bob_budget == pytest.approx(150, rel=1e-3)


def test_party_plan_aggregates_macros():
    planner = PartyPlanner(
        _FakeClient(),
        store_code="1950105",
        store_info={},
    )
    alice = Profile(name="alice", weight_kg=65, height_cm=170, age=28,
                     sex="female", activity="moderate", goal="cut")
    plan = planner.plan([alice], total_budget=100)
    # 合计宏量等于 alice 的 deal.macros
    alice_deal = plan.members[0].deal
    assert plan.total_macros["protein_g"] == pytest.approx(
        alice_deal.macros["protein_g"], rel=1e-3)


def test_party_plan_empty_profiles_raises():
    planner = PartyPlanner(_FakeClient(), store_code="1950105")
    with pytest.raises(ValueError, match="不能为空"):
        planner.plan([], total_budget=100)


def test_party_plan_non_auth_error_continues_and_records():
    """非 McpAuthError 的异常应被记录到 plan.errors，其他成员继续处理。"""
    class FailingClient(_FakeClient):
        """对 alice 的 optimize 抛 McpRateLimitError，对 bob 正常返回。"""
        # 由于 optimize 依赖 fetch + _search_best_combo，我们让 fetch 在第二次
        # 调用时抛限流错误。但实际 fetch 是共享的，只在 PartyPlanner.fetch 时
        # 调一次。所以这里改成让 list_nutrition_foods 在第一次正常、后续失败。
        # 但 PartyPlanner.fetch 只调一次 list_nutrition_foods。
        # 更简单的做法：直接 monkeypatch optimize 抛错。
        pass

    planner = PartyPlanner(FailingClient(), store_code="1950105",
                            store_info={"storeName": "测试门店"})
    alice = Profile(name="alice", weight_kg=65, height_cm=170, age=28,
                     sex="female", activity="moderate", goal="cut")
    bob = Profile(name="bob", weight_kg=80, height_cm=180, age=32,
                   sex="male", activity="active", goal="maintain")

    # monkeypatch optimizer.optimize：对 alice 抛限流错误
    original_optimize = planner._optimizer.optimize

    call_count = {"n": 0}
    def fake_optimize(profile):
        call_count["n"] += 1
        if call_count["n"] == 1:  # 第一个成员（alice）失败
            from mcleanfuel.mcp_client import McpRateLimitError
            raise McpRateLimitError("触发限流")
        return original_optimize(profile)

    planner._optimizer.optimize = fake_optimize

    plan = planner.plan([alice, bob], total_budget=150)
    # alice 失败，bob 正常
    assert len(plan.errors) == 1
    assert "alice" in plan.errors[0]
    assert "限流" in plan.errors[0]
    # alice 的 deal 为空
    assert plan.members[0].deal.items == []
    # bob 的 deal 仍有内容
    assert len(plan.members[1].deal.items) >= 1


def test_party_plan_auth_error_terminates():
    """McpAuthError 应直接传播，终止整个 party（不能静默吞掉）。"""
    planner = PartyPlanner(_FakeClient(), store_code="1950105")
    alice = Profile(name="alice", weight_kg=65, height_cm=170, age=28,
                     sex="female", activity="moderate", goal="cut")

    # monkeypatch optimizer.optimize 抛 McpAuthError
    from mcleanfuel.mcp_client import McpAuthError
    def raise_auth(profile):
        raise McpAuthError("Token 失效")
    planner._optimizer.optimize = raise_auth

    with pytest.raises(McpAuthError, match="Token 失效"):
        planner.plan([alice], total_budget=100)
