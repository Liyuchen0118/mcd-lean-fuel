"""Markdown 报告生成。

包含：
- ``render_markdown_report``：单人全天减脂方案报告
- ``render_deal_report``：单人凑单省券报告
- ``render_party_report``：多人组队订餐报告
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .coupon_optimizer import Coupon, DealResult, MealItem
from .mcp_client import McpClient
from .party import PartyMemberResult, PartyPlan, PartyPlanner
from .planner import FoodItem, PlanResult
from .profile import Profile
from .tdee import ActivityLevel, Goal, NutritionTargets, Sex


def _fmt(v: float, suffix: str = "", digits: int = 1) -> str:
    if v is None:
        return "-"
    return f"{v:.{digits}f}{suffix}"


def _achievement_bar(ratio: float) -> str:
    """进度条：0-150% 映射 0-10 格。"""
    pct = max(0.0, min(1.5, ratio)) / 1.5
    filled = int(pct * 10)
    return "█" * filled + "░" * (10 - filled)


def render_markdown_report(
    *,
    targets: NutritionTargets,
    plan: PlanResult,
    user_profile: dict,
    coupons_bound: list[dict] | None = None,
    store_info: dict | None = None,
    generated_at: _dt.datetime | None = None,
) -> str:
    """渲染全天减脂餐方案 Markdown 报告。"""
    now = generated_at or _dt.datetime.now()
    lines: list[str] = []
    p = lines.append

    p("# 🍔 麦麦减脂燃料方案\n")
    p(f"> _由 McLeanFuel 于 {now:%Y-%m-%d %H:%M:%S} 生成 | 仅供参考，不构成医疗建议_")
    p("")

    # ---------- 用户画像 ----------
    p("## 👤 用户画像\n")
    p(f"| 项目 | 数值 |")
    p(f"|---|---|")
    p(f"| 性别 | {user_profile.get('sex', '-')} |")
    p(f"| 年龄 | {user_profile.get('age', '-')} 岁 |")
    p(f"| 身高 | {user_profile.get('height_cm', '-')} cm |")
    p(f"| 体重 | {user_profile.get('weight_kg', '-')} kg |")
    p(f"| 活动水平 | {ActivityLevel(user_profile.get('activity', 'moderate')).name} |")
    p(f"| 目标 | {Goal(user_profile.get('goal', 'cut')).name} |")
    p(f"| 预算 | ¥{user_profile.get('budget_yuan', 0):.1f} |")
    p("")

    # ---------- TDEE 与目标 ----------
    p("## 🎯 TDEE 与营养目标\n")
    p(f"- **BMR** (基础代谢率): {_fmt(targets.bmr, ' kcal/天')}")
    p(f"- **TDEE** (每日总能量消耗): {_fmt(targets.tdee, ' kcal/天')}")
    p(f"- **目标摄入**: {_fmt(targets.target_calories, ' kcal/天')}"
      f" (缺口 {_fmt(targets.deficit, ' kcal/天')}, 约 {_fmt(targets.estimated_weekly_weight_change_kg, ' kg/周', 3)})")
    p("")
    p("**宏量营养目标**\n")
    p("| 营养素 | 每日目标 | 占比 |")
    p("|---|---:|---:|")
    protein_kcal = targets.protein_g * 4
    fat_kcal = targets.fat_g * 9
    carb_kcal = targets.carb_g * 4
    total_kcal = protein_kcal + fat_kcal + carb_kcal
    p(f"| 蛋白质 | {_fmt(targets.protein_g, ' g')} | {_fmt(protein_kcal / total_kcal * 100, '%', 0)} |")
    p(f"| 脂肪 | {_fmt(targets.fat_g, ' g')} | {_fmt(fat_kcal / total_kcal * 100, '%', 0)} |")
    p(f"| 碳水 | {_fmt(targets.carb_g, ' g')} | {_fmt(carb_kcal / total_kcal * 100, '%', 0)} |")
    p("")

    # ---------- 三餐方案 ----------
    p("## 🍽️ 全天餐品方案\n")
    meal_order = ["breakfast", "lunch", "dinner", "snack"]
    for name in meal_order:
        mp = plan.meals.get(name)
        if not mp or not mp.items:
            continue
        target = mp.meal
        p(f"### {_meal_label_zh(name)} — 目标 {_fmt(target.calories, ' kcal', 0)} | "
          f"蛋白 {_fmt(target.protein_g, ' g', 0)} | 预算 ¥{_fmt(target.budget_yuan, '', 0)}")
        p("")
        p("| 餐品 | 能量(kcal) | 蛋白(g) | 脂肪(g) | 碳水(g) | 钠(mg) |")
        p("|---|---:|---:|---:|---:|---:|")
        for it in mp.items:
            p(f"| {it.product_name} | {_fmt(it.energy_kcal, '', 0)} | "
              f"{_fmt(it.protein_g, '', 0)} | {_fmt(it.fat_g, '', 0)} | "
              f"{_fmt(it.carb_g, '', 0)} | {_fmt(it.sodium_mg, '', 0)} |")
        p(f"| **小计** | **{_fmt(mp.total_kcal, '', 0)}** | "
          f"**{_fmt(mp.total_protein_g, '', 0)}** | "
          f"**{_fmt(mp.total_fat_g, '', 0)}** | "
          f"**{_fmt(mp.total_carb_g, '', 0)}** | "
          f"**{_fmt(mp.total_sodium_mg, '', 0)}** |")
        p("")
        # 达标可视化
        p(f"- 卡路里达标率: {_fmt(mp.total_kcal / max(target.calories, 1) * 100, '%', 0)} "
          f"{_achievement_bar(mp.total_kcal / max(target.calories, 1))}")
        p(f"- 蛋白达标率: {_fmt(mp.protein_achievement_ratio * 100, '%', 0)} "
          f"{_achievement_bar(mp.protein_achievement_ratio)}")
        compliance_mark = "✅" if mp.compliance else "⚠️"
        p(f"- {compliance_mark} 该餐合规: {mp.compliance}")
        p("")

    # ---------- 全天合计 ----------
    p("## 📊 全天营养合计\n")
    p("| 营养素 | 实际 | 目标 | 达标率 |")
    p("|---|---:|---:|---:|")
    p(f"| 能量 (kcal) | {_fmt(plan.total_kcal, '', 0)} | "
      f"{_fmt(targets.target_calories, '', 0)} | "
      f"{_fmt(plan.total_kcal / targets.target_calories * 100, '%', 0)} |")
    p(f"| 蛋白质 (g) | {_fmt(plan.total_protein_g, '', 0)} | "
      f"{_fmt(targets.protein_g, '', 0)} | "
      f"{_fmt(plan.protein_achievement_ratio * 100, '%', 0)} |")
    p(f"| 脂肪 (g) | {_fmt(plan.total_fat_g, '', 0)} | "
      f"{_fmt(targets.fat_g, '', 0)} | "
      f"{_fmt(plan.total_fat_g / targets.fat_g * 100, '%', 0)} |")
    p(f"| 碳水 (g) | {_fmt(plan.total_carb_g, '', 0)} | "
      f"{_fmt(targets.carb_g, '', 0)} | "
      f"{_fmt(plan.total_carb_g / targets.carb_g * 100, '%', 0)} |")
    p(f"| 钠 (mg) | {_fmt(plan.total_sodium_mg, '', 0)} | 2300 | "
      f"{_fmt(plan.total_sodium_mg / 2300 * 100, '%', 0)} |")
    p("")

    # ---------- 省钱明细 ----------
    if coupons_bound:
        p("## 💰 自动领券明细\n")
        p(f"本次通过 `auto-bind-coupons` 工具自动领取 **{len(coupons_bound)}** 张麦麦省优惠券：\n")
        p("| # | 优惠券名称 |")
        p("|---|---|")
        for i, c in enumerate(coupons_bound, 1):
            name = (c.get("couponName") or c.get("name") or
                    c.get("title") or f"coupon-{c.get('couponId', i)}")
            p(f"| {i} | {name} |")
        p("")
        p("> 💡 这些券可在下单时自动应用，进一步降低减脂餐成本。\n")

    # ---------- 门店信息 ----------
    if store_info:
        p("## 🏪 推荐门店\n")
        p(f"- 名称: {store_info.get('storeName') or store_info.get('name', '-')}")
        p(f"- 地址: {store_info.get('address', '-')}")
        p(f"- 营业时间: {store_info.get('businessHours', '-')}")
        p("")

    # ---------- 健康提示 ----------
    p("## 💡 健康提示\n")
    if plan.total_sodium_mg > 2300:
        p(f"- ⚠️ 全天钠摄入 **{_fmt(plan.total_sodium_mg, ' mg', 0)}** 超过推荐上限 2300 mg，"
          f"建议多喝水、减少高钠餐品频率。")
    if plan.total_kcal > targets.target_calories * 1.1:
        p(f"- ⚠️ 全天能量 **超出目标 { _fmt(plan.calorie_overrun_ratio * 100, '%', 0)}**，"
          f"可考虑替换低卡选项（如美式咖啡替代拿铁、玉米杯替代薯条）。")
    if plan.protein_achievement_ratio < 0.85:
        p(f"- ⚠️ 蛋白质达标率仅 {_fmt(plan.protein_achievement_ratio * 100, '%', 0)}，"
          f"建议增加高蛋白餐品（如双层吉士汉堡、麦乐鸡、鸡翅）。")
    if plan.protein_achievement_ratio >= 0.95 and plan.total_kcal <= targets.target_calories * 1.1:
        p("- ✅ 当前方案蛋白质充足且能量控制在目标范围内，可持续执行。")
    p("- 🥗 搭配建议：可额外补充蔬菜（非麦麦产品）以增加膳食纤维摄入。")
    p("- 💧 每日饮水量建议 1.5-2 L，运动量大时相应增加。")
    p("")

    # ---------- 免责声明 ----------
    p("---\n")
    p("**免责声明**：本方案由 McLeanFuel 基于麦当劳 MCP 餐品营养数据自动生成，")
    p("仅供参考，不构成医疗、营养或其他专业建议。")
    p("餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。")
    p("减脂需结合个体差异、运动量与饮食习惯综合考虑，如有健康问题请咨询专业医师或营养师。\n")
    p(f"_McLeanFuel v0.1.0 · 麦当劳 MCP Skill_")

    return "\n".join(lines)


def _meal_label_zh(name: str) -> str:
    return {
        "breakfast": "🌅 早餐",
        "lunch": "☀️ 午餐",
        "dinner": "🌙 晚餐",
        "snack": "🍎 加餐",
    }.get(name, name)


def render_coupon_summary(coupons: list[dict]) -> str:
    """简版优惠券清单（用于 CLI 输出）。"""
    if not coupons:
        return "（无）"
    out = [f"自动领取 {len(coupons)} 张优惠券："]
    for i, c in enumerate(coupons, 1):
        name = (c.get("couponName") or c.get("name") or
                c.get("title") or f"coupon-{c.get('couponId', i)}")
        out.append(f"  {i}. {name}")
    return "\n".join(out)


# =========================================================================
# 凑单报告
# =========================================================================
def render_deal_report(
    *,
    profile: Profile,
    deal: DealResult,
    store: dict,
    budget: float,
    generated_at: _dt.datetime | None = None,
) -> str:
    """渲染单人凑单省券报告。"""
    now = generated_at or _dt.datetime.now()
    lines: list[str] = []
    p = lines.append

    p("# 🍔 麦麦凑单省券方案\n")
    p(f"> _由 McLeanFuel 于 {now:%Y-%m-%d %H:%M:%S} 生成 | "
      f"仅供参考，不构成医疗、营养建议_")
    p("")

    # ---------- 用户画像摘要 ----------
    targets = profile.compute_targets()
    p("## 👤 用户与目标\n")
    p(f"- 用户: **{profile.name}**")
    p(f"- 性别/年龄: {profile.sex} / {profile.age}")
    p(f"- 身高/体重: {profile.height_cm:.0f}cm / {profile.weight_kg:.1f}kg")
    p(f"- 活动/目标: {profile.activity} / {profile.goal}")
    p(f"- **每日推荐卡路里**: {_fmt(targets.target_calories, ' kcal', 0)} "
      f"(TDEE {_fmt(targets.tdee, ' kcal', 0)}, "
      f"{'缺口' if targets.deficit > 0 else '盈余'} {_fmt(abs(targets.deficit), ' kcal', 0)})")
    p(f"- 蛋白/脂肪/碳水: {_fmt(targets.protein_g, 'g', 0)} / "
      f"{_fmt(targets.fat_g, 'g', 0)} / {_fmt(targets.carb_g, 'g', 0)}")
    p(f"- **凑单预算**: ¥{budget:.1f}")
    p("")

    # ---------- 门店信息 ----------
    p("## 🏪 门店\n")
    p(f"- 名称: {store.get('storeName', '-')}")
    p(f"- 地址: {store.get('address', '-')}")
    p(f"- 营业时间: {store.get('businessStartTime', '?')}-{store.get('businessEndTime', '?')}")
    p(f"- 门店编码: {store.get('storeCode', '-')}")
    p("")

    # ---------- 餐品组合 ----------
    p("## 🍽️ 凑单方案\n")
    if not deal.items:
        p("（未找到合适的餐品组合，请放宽预算或调整目标）\n")
        return "\n".join(lines)

    p("| # | 餐品 | 数量 | 原价 | 用券价 | 营养(kcal/P/F/C) | 钠(mg) |")
    p("|---|---|---:|---:|---:|---|---:|")
    for i, m in enumerate(deal.items, 1):
        qty = deal.item_num(i - 1)  # DealResult.item_num 用 0-based 索引
        if m.nutrition:
            # 营养按数量显示（bogo ×2 反映实际获得份数）
            n_str = (f"{m.nutrition.energy_kcal * qty:.0f}/"
                     f"{m.nutrition.protein_g * qty:.0f}/"
                     f"{m.nutrition.fat_g * qty:.0f}/"
                     f"{m.nutrition.carb_g * qty:.0f}")
            na = m.nutrition.sodium_mg * qty
        else:
            n_str = "-"
            na = 0
        # 原价按数量显示，用券价是实付（bogo 时 = 1×单价）
        orig = m.original_price * qty
        cur = m.current_price * (1 if qty == 2 else qty)  # bogo 实付 1 份钱
        p(f"| {i} | {m.name} | ×{qty} | ¥{orig:.1f} | ¥{cur:.1f} | "
          f"{n_str} | {na:.0f} |")
    p(f"| **合计** |  | **¥{_fmt(deal.items_without_coupon_price, '', 2)}** | "
      f"**¥{_fmt(deal.items_with_coupon_price, '', 2)}** | "
      f"{_fmt(deal.macros['kcal'], '', 0)}/{_fmt(deal.macros['protein_g'], '', 0)}/"
      f"{_fmt(deal.macros['fat_g'], '', 0)}/{_fmt(deal.macros['carb_g'], '', 0)} | "
      f"{_fmt(deal.macros['sodium_mg'], '', 0)} |")
    p("")

    # ---------- 营养达标 ----------
    p("## 📊 营养达标率\n")
    cr = deal.target_achievement.get("calorie_ratio", 0)
    pr = deal.target_achievement.get("protein_ratio", 0)
    p(f"- 能量: {_fmt(deal.macros['kcal'], ' kcal', 0)} / "
      f"{_fmt(targets.target_calories, ' kcal', 0)} "
      f"= **{_fmt(cr * 100, '%', 0)}** {_achievement_bar(cr)}")
    p(f"- 蛋白: {_fmt(deal.macros['protein_g'], ' g', 0)} / "
      f"{_fmt(targets.protein_g, ' g', 0)} "
      f"= **{_fmt(pr * 100, '%', 0)}** {_achievement_bar(pr)}")
    p(f"- 脂肪: {_fmt(deal.macros['fat_g'], ' g', 0)} / "
      f"{_fmt(targets.fat_g, ' g', 0)} "
      f"= **{_fmt(deal.macros['fat_g'] / max(targets.fat_g, 1) * 100, '%', 0)}**")
    p(f"- 碳水: {_fmt(deal.macros['carb_g'], ' g', 0)} / "
      f"{_fmt(targets.carb_g, ' g', 0)} "
      f"= **{_fmt(deal.macros['carb_g'] / max(targets.carb_g, 1) * 100, '%', 0)}**")
    p("")

    # ---------- 券策略 ----------
    p("## 💰 优惠券策略\n")
    if deal.coupons_used:
        p(f"本次应用 **{len(deal.coupons_used)}** 张优惠券：\n")
        p("| # | 券标题 | 类型 | 优惠详情 | 目标餐品 |")
        p("|---|---|---|---|---|")
        for i, c in enumerate(deal.coupons_used, 1):
            type_label = {
                "fixed_price": "定价券", "free": "免费券",
                "bogo": "买一送一", "discount": "折扣券",
                "unknown": "其它",
            }.get(c.discount_type, c.discount_type)
            detail = (f"¥{c.discount_value}" if c.discount_value is not None
                      and c.discount_type == "fixed_price" else
                      "免费" if c.discount_type == "free" else
                      "买1送1" if c.discount_type == "bogo" else "-")
            p(f"| {i} | {c.title} | {type_label} | {detail} | {c.target_keyword} |")
        p("")
    else:
        p("（本次未应用任何优惠券）\n")

    p(f"**价格汇总**")
    p(f"- 原价合计: ¥{_fmt(deal.items_without_coupon_price, '', 2)}")
    p(f"- 用券后: **¥{_fmt(deal.items_with_coupon_price, '', 2)}**")
    p(f"- **节省: ¥{_fmt(deal.saved_price, '', 2)}**")
    p(f"- 预算: ¥{budget:.2f} ({'✅ 在预算内' if deal.items_with_coupon_price <= budget else '⚠️ 超预算'})")
    if deal.mcp_calculated:
        p(f"- ✅ 已通过麦当劳 MCP `calculate-price` 验证实付金额")
    else:
        p(f"- ⚠ 实付金额为本地估算，未通过 MCP `calculate-price` 验证")
    p("")

    # ---------- 健康提示 ----------
    p("## 💡 健康提示\n")
    if pr < 0.85:
        p(f"- ⚠️ 蛋白质达标率仅 {_fmt(pr * 100, '%', 0)}，"
          f"建议增加高蛋白餐品或降低预算约束")
    if deal.macros["sodium_mg"] > 2300:
        p(f"- ⚠️ 全天钠摄入 {_fmt(deal.macros['sodium_mg'], ' mg', 0)} 超过推荐上限 2300 mg")
    if cr > 1.15:
        p(f"- ⚠️ 能量超标 {_fmt((cr - 1) * 100, '%', 0)}，可考虑替换低卡选项")
    if pr >= 0.95 and cr <= 1.1:
        p("- ✅ 当前方案蛋白质充足且能量达标")
    p("- 🥗 可额外补充蔬菜以增加膳食纤维")
    p("")

    p("---\n")
    p("**免责声明**：本方案由 McLeanFuel 基于麦当劳 MCP 餐品营养/价格/券数据自动生成，")
    p("仅供参考，不构成医疗、营养建议。")
    p("餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。\n")
    p(f"_McLeanFuel v0.1.0 · 麦当劳 MCP Skill_")
    return "\n".join(lines)


# =========================================================================
# 多人组队报告
# =========================================================================
def render_party_report(
    *,
    plan: PartyPlan,
    total_budget: float,
    generated_at: _dt.datetime | None = None,
) -> str:
    """渲染多人组队订餐报告。"""
    now = generated_at or _dt.datetime.now()
    lines: list[str] = []
    p = lines.append

    p("# 🍔 麦麦多人组队订餐方案\n")
    p(f"> _由 McLeanFuel 于 {now:%Y-%m-%d %H:%M:%S} 生成 | "
      f"仅供参考，不构成医疗、营养建议_")
    p("")

    # ---------- 团队概览 ----------
    p("## 👥 团队概览\n")
    p(f"- 成员数: **{plan.member_count}**")
    p(f"- 门店: {plan.store.get('storeName', '-')} ({plan.store.get('storeCode', '-')})")
    p(f"- 地址: {plan.store.get('address', '-')}")
    p(f"- 总预算: **¥{total_budget:.2f}**")
    p(f"- 用券后实付: **¥{plan.total_coupon_price:.2f}**")
    p(f"- 节省: ¥{plan.total_saved:.2f}")
    if plan.within_budget:
        p(f"- 状态: ✅ 在预算内（剩余 ¥{total_budget - plan.total_coupon_price:.2f}）")
    else:
        p(f"- 状态: ⚠️ 超预算 ¥{plan.total_coupon_price - total_budget:.2f}")
    if plan.mcp_verified:
        p(f"- ✅ 已通过 MCP `calculate-price` 验证")
    if plan.errors:
        p(f"- ⚠️ 有 {len(plan.errors)} 个成员级别的错误（详见下方各成员方案）")
    p("")

    # ---------- 成员方案 ----------
    p("## 🍽️ 成员餐品方案\n")
    for i, m in enumerate(plan.members, 1):
        p(f"### 成员 {i}: {m.profile.name} ({m.profile.sex}, {m.profile.age}岁)")
        p(f"目标: {_fmt(m.targets.target_calories, ' kcal', 0)} | "
          f"蛋白 {_fmt(m.targets.protein_g, 'g', 0)} | "
          f"预算 ¥{m.profile.budget_yuan:.0f}\n")
        if not m.deal.items:
            # 若 plan.errors 中有该成员的错误，显示出来
            member_errors = [e for e in plan.errors if m.profile.name in e]
            if member_errors:
                for e in member_errors:
                    p(f"⚠️ {e}")
            p("（未生成凑单方案，请检查上方错误信息）\n")
            continue
        p("| 餐品 | 数量 | 原价 | 用券价 | 营养(kcal/P/F/C) |")
        p("|---|---:|---:|---:|---|")
        for idx, it in enumerate(m.deal.items):
            qty = m.deal.item_num(idx)
            if it.nutrition:
                n_str = (f"{it.nutrition.energy_kcal * qty:.0f}/"
                         f"{it.nutrition.protein_g * qty:.0f}/"
                         f"{it.nutrition.fat_g * qty:.0f}/"
                         f"{it.nutrition.carb_g * qty:.0f}")
            else:
                n_str = "-"
            orig = it.original_price * qty
            cur = it.current_price * (1 if qty == 2 else qty)
            p(f"| {it.name} | ×{qty} | ¥{orig:.1f} | ¥{cur:.1f} | {n_str} |")
        p(f"| **小计** | **¥{_fmt(m.deal.items_without_coupon_price, '', 2)}** | "
          f"**¥{_fmt(m.deal.items_with_coupon_price, '', 2)}** | "
          f"{_fmt(m.deal.macros['kcal'], '', 0)}/{_fmt(m.deal.macros['protein_g'], '', 0)}/"
          f"{_fmt(m.deal.macros['fat_g'], '', 0)}/{_fmt(m.deal.macros['carb_g'], '', 0)} |")
        p("")
        pr = m.deal.target_achievement.get("protein_ratio", 0)
        cr = m.deal.target_achievement.get("calorie_ratio", 0)
        p(f"- 蛋白达标率: {_fmt(pr * 100, '%', 0)} {_achievement_bar(pr)}")
        p(f"- 能量达标率: {_fmt(cr * 100, '%', 0)} {_achievement_bar(cr)}")
        if m.deal.coupons_used:
            coupons_str = ", ".join(c.title for c in m.deal.coupons_used)
            p(f"- 用券: {coupons_str}")
        p("")

    # ---------- 团队合计 ----------
    p("## 📊 团队营养合计\n")
    p("| 营养素 | 全员合计 |")
    p("|---|---:|")
    p(f"| 能量 (kcal) | {_fmt(plan.total_macros['kcal'], '', 0)} |")
    p(f"| 蛋白质 (g) | {_fmt(plan.total_macros['protein_g'], '', 0)} |")
    p(f"| 脂肪 (g) | {_fmt(plan.total_macros['fat_g'], '', 0)} |")
    p(f"| 碳水 (g) | {_fmt(plan.total_macros['carb_g'], '', 0)} |")
    p(f"| 钠 (mg) | {_fmt(plan.total_macros['sodium_mg'], '', 0)} |")
    p("")
    p(f"**平均蛋白达标率**: {_fmt(plan.avg_protein_ratio * 100, '%', 0)}")
    if plan.all_protein_compliant:
        p("✅ 全员蛋白质达标率均 ≥ 85%")
    else:
        p("⚠️ 有成员蛋白质未达标")

    p("\n## 💰 价格汇总\n")
    p(f"- 原价合计: ¥{_fmt(plan.total_original_price, '', 2)}")
    p(f"- 用券后: **¥{_fmt(plan.total_coupon_price, '', 2)}**")
    p(f"- 总节省: **¥{_fmt(plan.total_saved, '', 2)}**")

    # ---------- 健康提示 ----------
    p("\n## 💡 健康提示\n")
    if not plan.within_budget:
        p("- ⚠️ 总价超出预算，已自动降级替换更便宜餐品")
    p("- 🥗 鼓励每人额外补充蔬菜水果")
    p("- 💧 多人聚餐注意饮水量，避免高钠餐品同时点")

    p("\n---\n")
    p("**免责声明**：本方案由 McLeanFuel 自动生成，仅供参考。")
    p("餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。\n")
    p(f"_McLeanFuel v0.1.0 · 麦当劳 MCP Skill_")
    return "\n".join(lines)
