"""多人组队订餐套餐推荐。

给定 N 个用户画像 + 地址 + 总预算，自动：
1. 找附近门店（query-nearby-stores）
2. 拉菜单 + 营养数据 + 用户已领券 + 门店可用券 + 自动领券
3. 对每人生成满足宏量约束的最优凑单方案
4. 共享券策略：把券分配给最受益的人
5. 总预算约束：超额时自动降级（替换为更便宜餐品）
6. 可选：调用 calculate-price 验证实付 + create-order 下单

输出：每人套餐 + 共用券策略 + 总价 + 节省金额 + 营养达标可视化。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from .coupon_optimizer import Coupon, CouponOptimizer, DealResult, MealItem
from .mcp_client import McpAuthError, McpClient, McpError, McpRateLimitError
from .profile import Profile
from .tdee import NutritionTargets


@dataclass
class PartyMemberResult:
    """单个成员的凑单结果。"""

    profile: Profile
    targets: NutritionTargets
    deal: DealResult = field(default_factory=DealResult)


@dataclass
class PartyPlan:
    """多人组队订餐方案。"""

    members: list[PartyMemberResult] = field(default_factory=list)
    store: dict[str, Any] = field(default_factory=dict)
    total_budget: float = 0.0
    total_original_price: float = 0.0
    total_coupon_price: float = 0.0
    total_saved: float = 0.0
    total_macros: dict[str, float] = field(default_factory=lambda: {
        "kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0, "sodium_mg": 0.0,
    })
    within_budget: bool = True
    mcp_verified: bool = False
    # 记录成员级别的错误（如限流、单成员凑单失败）
    errors: list[str] = field(default_factory=list)

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def avg_protein_ratio(self) -> float:
        if not self.members:
            return 0.0
        return sum(m.deal.target_achievement.get("protein_ratio", 0) for m in self.members) / len(self.members)

    @property
    def all_protein_compliant(self) -> bool:
        return all(m.deal.target_achievement.get("protein_ratio", 0) >= 0.85 for m in self.members)


class PartyPlanner:
    """多人组队订餐规划器。"""

    def __init__(
        self,
        client: McpClient,
        *,
        store_code: str,
        order_type: int = 1,
        be_type: int = 1,
        be_code: str | None = None,
        reservation_date: str | None = None,
        store_info: dict[str, Any] | None = None,
    ):
        self.client = client
        self.store_code = store_code
        self.order_type = order_type
        self.be_type = be_type
        self.be_code = be_code
        self.reservation_date = reservation_date
        self.store_info = store_info or {}
        # 共用 CouponOptimizer 实例避免重复拉取数据
        self._optimizer = CouponOptimizer(
            client,
            store_code=store_code,
            order_type=order_type,
            be_type=be_type,
            be_code=be_code,
            reservation_date=reservation_date,
        )
        self._fetched = False

    def fetch(self, *, auto_bind: bool = True) -> None:
        """拉取门店菜单 + 营养数据 + 券（一次拉取，全员共享）。"""
        self._optimizer.fetch(auto_bind=auto_bind)
        self._fetched = True

    def plan(self, profiles: list[Profile], total_budget: float | None = None) -> PartyPlan:
        """为多个用户生成凑单方案。"""
        if not profiles:
            raise ValueError("profiles 不能为空")
        if not self._fetched:
            self.fetch()

        # 每人预算分配：未指定总预算则各用各的 profile.budget_yuan
        if total_budget is None:
            per_budgets = [p.budget_yuan for p in profiles]
        else:
            # 按宏量需求（蛋白质目标）比例分摊预算
            total_p = sum(p.compute_targets().protein_g for p in profiles)
            if total_p <= 0:
                per_budgets = [total_budget / len(profiles)] * len(profiles)
            else:
                per_budgets = [
                    total_budget * (p.compute_targets().protein_g / total_p)
                    for p in profiles
                ]

        plan = PartyPlan(
            store=self.store_info,
            total_budget=total_budget if total_budget is not None else sum(per_budgets),
        )

        # 对每人生成方案（用临时预算）
        for prof, budget in zip(profiles, per_budgets):
            # 临时替换 profile 的预算
            p = Profile(
                name=prof.name, weight_kg=prof.weight_kg, height_cm=prof.height_cm,
                age=prof.age, sex=prof.sex, activity=prof.activity, goal=prof.goal,
                budget_yuan=budget, created_at=prof.created_at, updated_at=prof.updated_at,
            )
            targets = p.compute_targets()
            try:
                deal = self._optimizer.optimize(p)
            except McpAuthError:
                # Token 失效，整个 party 应终止（继续其他成员也会失败）
                raise
            except McpRateLimitError as e:
                # 限流：记录错误，该成员留空方案，继续尝试其他成员
                msg = f"成员 {p.name} 凑单失败（MCP 限流）：{e}"
                print(f"⚠️ {msg}", file=sys.stderr)
                plan.errors.append(msg)
                deal = DealResult()
            except McpError as e:
                # 其他 MCP 错误：记录并继续
                msg = f"成员 {p.name} 凑单失败（MCP 错误）：{e}"
                print(f"⚠️ {msg}", file=sys.stderr)
                plan.errors.append(msg)
                deal = DealResult()
            except Exception as e:
                # 未预期的错误：记录并继续，避免一个成员拖垮整个 party
                msg = f"成员 {p.name} 凑单失败（{type(e).__name__}）：{e}"
                print(f"⚠️ {msg}", file=sys.stderr)
                plan.errors.append(msg)
                deal = DealResult()
            plan.members.append(PartyMemberResult(
                profile=p, targets=targets, deal=deal,
            ))

        # 合计
        plan.total_original_price = round(sum(m.deal.items_without_coupon_price for m in plan.members), 2)
        plan.total_coupon_price = round(sum(m.deal.items_with_coupon_price for m in plan.members), 2)
        plan.total_saved = round(plan.total_original_price - plan.total_coupon_price, 2)
        for k in plan.total_macros:
            plan.total_macros[k] = round(
                sum(m.deal.macros.get(k, 0) for m in plan.members), 1
            )
        plan.within_budget = plan.total_coupon_price <= plan.total_budget

        # 预算超限降级
        if not plan.within_budget:
            self._downgrade_for_budget(plan, total_budget or sum(per_budgets))

        # 可选：MCP 验证
        try:
            self._verify_party_with_mcp(plan)
            plan.mcp_verified = True
        except McpError:
            plan.mcp_verified = False

        return plan

    # ----------------------------------------------------------- 降级策略
    def _downgrade_for_budget(self, plan: PartyPlan, total_budget: float) -> None:
        """超预算时降级：替换最贵餐品为更便宜的同类型。

        降级时考虑 bogo 券应用餐品的 num：原餐品 num=2（bogo）替换为 num=1 的
        普通餐品时，价格和营养差按各自 num 计算。
        """
        # 收集所有成员的所有餐品（带索引，便于查 item_nums）
        all_items: list[tuple[PartyMemberResult, int, MealItem]] = []
        for m in plan.members:
            for idx, it in enumerate(m.deal.items):
                all_items.append((m, idx, it))

        # 按单价 × num（原价总额）降序
        all_items.sort(
            key=lambda x: x[2].effective_price * x[0].deal.item_num(x[1]),
            reverse=True,
        )

        for member, item_idx, item in all_items:
            if plan.total_coupon_price <= total_budget:
                break
            cheaper = self._find_cheaper_alternative(item, member.deal.items)
            if cheaper is None:
                continue

            old_qty = member.deal.item_num(item_idx)  # 原数量（bogo=2，普通=1）
            old_total_price = item.effective_price * old_qty  # 原总价（按 num）
            new_total_price = cheaper.effective_price * 1  # 新餐品 num=1

            # 替换餐品
            member.deal.items = [cheaper if i is item else i for i in member.deal.items]
            # 新餐品不是 bogo，清除该索引的 item_nums（让其回落到默认 1）
            if item_idx in member.deal.item_nums:
                del member.deal.item_nums[item_idx]

            # 更新该成员宏量（按 num 差）
            if cheaper.nutrition and item.nutrition:
                delta_kcal = cheaper.nutrition.energy_kcal * 1 - item.nutrition.energy_kcal * old_qty
                delta_p = cheaper.nutrition.protein_g * 1 - item.nutrition.protein_g * old_qty
                member.deal.macros["kcal"] = round(member.deal.macros["kcal"] + delta_kcal, 1)
                member.deal.macros["protein_g"] = round(member.deal.macros["protein_g"] + delta_p, 1)
                member.deal.target_achievement["protein_ratio"] = round(
                    member.deal.macros["protein_g"] / max(member.targets.protein_g, 1), 3
                )

            # 更新价格（按 num 差）
            member.deal.items_without_coupon_price = round(
                member.deal.items_without_coupon_price - old_total_price + new_total_price, 2)
            # 降级后该餐品已无券应用，实付 = 原价
            member.deal.items_with_coupon_price = member.deal.items_without_coupon_price
            member.deal.saved_price = 0.0
            # 清空该成员的 coupons_used（降级后券不再适用）
            member.deal.coupons_used = []

            plan.total_original_price = round(
                sum(mm.deal.items_without_coupon_price for mm in plan.members), 2)
            plan.total_coupon_price = plan.total_original_price
            plan.total_saved = 0.0

        plan.within_budget = plan.total_coupon_price <= total_budget

    def _find_cheaper_alternative(self, current: MealItem,
                                   current_set: list[MealItem]) -> MealItem | None:
        """从门店菜单找同分类更便宜的替代品。"""
        if not current.nutrition:
            return None
        cur_cat = current.nutrition.category
        cur_price = current.effective_price
        candidates = [
            m for m in self._optimizer.meals
            if m.nutrition and m.nutrition.category == cur_cat
            and m.effective_price < cur_price
            and m.product_code != current.product_code
            and m.product_code not in {x.product_code for x in current_set}
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda x: x.effective_price)

    # ----------------------------------------------------------- MCP 验证
    def _verify_party_with_mcp(self, plan: PartyPlan) -> None:
        """合并调用 calculate-price 验证多人合计实付金额。

        items 的 num 字段来自每个成员 deal.item_nums：
        - 普通餐品：num=1
        - bogo（买一送一）券应用的餐品：num=2，MCP 自动算"买1送1"实付
        """
        items_payload: list[dict] = []
        coupon_ids: list[str] = []
        for m in plan.members:
            for i, it in enumerate(m.deal.items):
                items_payload.append({
                    "code": it.product_code,
                    "num": m.deal.item_num(i),
                })
            coupon_ids.extend(c.coupon_id for c in m.deal.coupons_used if c.coupon_id)
        if not items_payload:
            return
        calc = self.client.calculate_price(
            store_code=self.store_code,
            order_type=self.order_type,
            be_type=self.be_type,
            items=items_payload,
            be_code=self.be_code,
            coupon_ids=coupon_ids or None,
            reservation_date=self.reservation_date,
        )
        for k in ("payAmount", "totalPrice", "actualPrice", "amount"):
            v = calc.get(k) if isinstance(calc, dict) else None
            if isinstance(v, (int, float)):
                plan.total_coupon_price = round(float(v), 2)
                plan.total_saved = round(plan.total_original_price - float(v), 2)
                return
