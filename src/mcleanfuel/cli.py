"""命令行入口。

子命令：
  profile     用户画像管理（add/list/show/remove/set-default）
  plan        生成单人全天减脂方案（基于已存储画像）
  deal        单人凑单省券优化（含门店菜单价格）
  party       多人组队订餐推荐
  doctor      检查 Token + MCP 连通性

用法示例::

  # 1. 录入两个用户画像
  mcleanfuel profile add alice -w 65 -H 170 -a 28 --sex female \\
      --activity moderate --goal cut --budget 60
  mcleanfuel profile add bob -w 80 -H 180 -a 32 --sex male \\
      --activity active --goal maintain --budget 80

  # 2. 单人每日方案（含 TDEE + 三餐+加餐 + 营养达标可视化）
  mcleanfuel plan alice --output alice-plan.md

  # 3. 单人凑单（指定门店，自动领券 + 价格最优）
  mcleanfuel deal alice --city 北京 --keyword 东直门 --budget 50

  # 4. 多人组队（总预算 200 元）
  mcleanfuel party alice,bob --city 北京 --keyword 东直门 \\
      --budget 200 --output party.md

  # 5. 检查环境
  mcleanfuel doctor
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from typing import Any

from . import __version__
from .coupon_optimizer import CouponOptimizer, DealResult
from .mcp_client import McpAuthError, McpClient, McpError, McpRateLimitError, TOKEN_FILE
from .party import PartyPlanner, PartyPlan
from .planner import Planner
from .profile import Profile, ProfileStore, PROFILES_FILE
from .report import (
    render_deal_report,
    render_markdown_report,
    render_party_report,
)
from .tdee import ActivityLevel, Goal, Sex, calculate_targets


# ----------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    handlers = {
        "profile": _cmd_profile,
        "plan": _cmd_plan,
        "deal": _cmd_deal,
        "party": _cmd_party,
        "doctor": _cmd_doctor,
    }
    handler = handlers.get(args.command)
    if not handler:
        parser.error(f"未知命令: {args.command}")
        return 2
    return handler(args)


def plan_cmd(args: argparse.Namespace) -> int:
    """兼容旧入口：`mcleanfuel plan` 子命令。"""
    return _cmd_plan(args)


# ----------------------------------------------------------------- parser
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcleanfuel",
        description="麦麦减脂燃料师 - 基于麦当劳 MCP 的健康减脂餐规划 Skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-V", "--version", action="version", version=f"mcleanfuel {__version__}")
    sub = parser.add_subparsers(dest="command")

    _build_profile_parser(sub)
    _build_plan_parser(sub)
    _build_deal_parser(sub)
    _build_party_parser(sub)
    _build_doctor_parser(sub)
    return parser


def _build_profile_parser(sub) -> None:
    p = sub.add_parser("profile", help="用户画像管理")
    ps = p.add_subparsers(dest="sub", required=True)

    add = ps.add_parser("add", help="新增/更新用户画像")
    add.add_argument("name", help="用户名（如 alice）")
    _add_profile_args(add)
    add.add_argument("--set-default", action="store_true", help="设为默认用户")

    list_p = ps.add_parser("list", help="列出所有用户画像")
    list_p.add_argument("--json", action="store_true")

    show = ps.add_parser("show", help="显示用户画像详情")
    show.add_argument("name", nargs="?", default=None)

    rm = ps.add_parser("remove", help="删除用户画像")
    rm.add_argument("name")

    sd = ps.add_parser("set-default", help="设置默认用户")
    sd.add_argument("name")


def _add_profile_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--weight", "-w", type=float, required=True, help="体重 (kg)")
    p.add_argument("--height", "-H", type=float, required=True, help="身高 (cm)")
    p.add_argument("--age", "-a", type=int, required=True, help="年龄")
    p.add_argument("--sex", type=str, choices=[s.value for s in Sex], required=True, help="性别")
    p.add_argument(
        "--activity", type=str, choices=[a.value for a in ActivityLevel],
        default=ActivityLevel.MODERATE.value, help="活动水平（默认 moderate）",
    )
    p.add_argument(
        "--goal", type=str, choices=[g.value for g in Goal],
        default=Goal.CUT.value, help="目标（默认 cut 减脂）",
    )
    p.add_argument("--budget", type=float, default=80.0, help="全天预算（元，默认 80）")


def _build_plan_parser(sub) -> None:
    p = sub.add_parser("plan", help="生成单人全天减脂方案")
    p.add_argument("user", nargs="?", default=None,
                   help="用户画像名（缺省则用默认用户）")
    p.add_argument("--output", "-o", default=None, help="输出到文件（默认 stdout）")
    p.add_argument("--json", action="store_true", help="输出 JSON（默认 Markdown）")
    p.add_argument("--no-coupons", action="store_true", help="跳过自动领券")
    p.add_argument("--token", default=None, help="MCP Token")


def _build_deal_parser(sub) -> None:
    p = sub.add_parser("deal", help="单人凑单省券优化")
    p.add_argument("user", help="用户画像名")
    p.add_argument("--city", required=True, help="门店城市（如 北京）")
    p.add_argument("--keyword", required=True, help="门店位置关键词（如 东直门）")
    p.add_argument("--budget", type=float, default=None,
                   help="凑单预算（默认使用用户画像的 budget_yuan）")
    p.add_argument("--be-type", type=int, choices=[1, 5], default=1,
                   help="1=到店自提(默认), 5=得来速")
    p.add_argument("--output", "-o", default=None, help="输出到文件（默认 stdout）")
    p.add_argument("--no-auto-bind", action="store_true", help="跳过自动领券步骤")
    p.add_argument("--token", default=None, help="MCP Token")


def _build_party_parser(sub) -> None:
    p = sub.add_parser("party", help="多人组队订餐推荐")
    p.add_argument("users", help="用户画像名列表（逗号分隔，如 alice,bob）")
    p.add_argument("--city", required=True, help="门店城市")
    p.add_argument("--keyword", required=True, help="门店位置关键词")
    p.add_argument("--budget", type=float, required=True, help="总预算（元）")
    p.add_argument("--be-type", type=int, choices=[1, 5], default=1)
    p.add_argument("--output", "-o", default=None, help="输出到文件（默认 stdout）")
    p.add_argument("--no-auto-bind", action="store_true")
    p.add_argument("--token", default=None, help="MCP Token")


def _build_doctor_parser(sub) -> None:
    p = sub.add_parser("doctor", help="检查 Token 与 MCP 连通性")
    p.add_argument("--token", default=None)


# ----------------------------------------------------------------- profile
def _cmd_profile(args: argparse.Namespace) -> int:
    sub = args.sub

    if sub == "add":
        return _profile_add(args)
    if sub == "list":
        return _profile_list(args)
    if sub == "show":
        return _profile_show(args)
    if sub == "remove":
        return _profile_remove(args)
    if sub == "set-default":
        return _profile_set_default(args)
    return 2


def _profile_add(args: argparse.Namespace) -> int:
    store = ProfileStore()
    try:
        profile = Profile(
            name=args.name,
            weight_kg=args.weight,
            height_cm=args.height,
            age=args.age,
            sex=args.sex,
            activity=args.activity,
            goal=args.goal,
            budget_yuan=args.budget,
        )
    except (ValueError, TypeError) as e:
        print(f"❌ 画像参数错误: {e}", file=sys.stderr)
        return 1
    store.add(profile, set_default=args.set_default or len(store) == 0)
    targets = profile.compute_targets()
    print(f"✅ 用户画像已保存: {profile.name}")
    print(f"   存储: {PROFILES_FILE}")
    print(f"   TDEE: {targets.tdee:.0f} kcal/天 | 目标: {targets.target_calories:.0f} kcal")
    print(f"   宏量: 蛋白 {targets.protein_g:.0f}g / 脂肪 {targets.fat_g:.0f}g / 碳水 {targets.carb_g:.0f}g")
    if store.default_name == profile.name:
        print(f"   已设为默认用户")
    return 0


def _profile_list(args: argparse.Namespace) -> int:
    store = ProfileStore()
    profiles = store.list_all()
    if not profiles:
        print("（暂无用户画像，请先 `mcleanfuel profile add` 添加）")
        return 0
    if args.json:
        out = []
        for p in profiles:
            t = p.compute_targets()
            out.append({
                "name": p.name, "sex": p.sex, "age": p.age,
                "height_cm": p.height_cm, "weight_kg": p.weight_kg,
                "activity": p.activity, "goal": p.goal, "budget_yuan": p.budget_yuan,
                "is_default": store.default_name == p.name,
                "tdee": t.tdee, "target_calories": t.target_calories,
                "protein_g": t.protein_g, "fat_g": t.fat_g, "carb_g": t.carb_g,
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"{'用户名':<12} {'性别':<6} {'年龄':<5} {'身高':<6} {'体重':<6} {'活动':<10} {'目标':<9} {'预算':<7} {'TDEE':<8} 默认")
    print("-" * 90)
    for p in profiles:
        t = p.compute_targets()
        default = "★" if store.default_name == p.name else ""
        print(f"{p.name:<12} {p.sex:<6} {p.age:<5} {p.height_cm:<6.0f} {p.weight_kg:<6.0f} "
              f"{p.activity:<10} {p.goal:<9} ¥{p.budget_yuan:<6.0f} {t.tdee:<8.0f} {default}")
    return 0


def _profile_show(args: argparse.Namespace) -> int:
    store = ProfileStore()
    try:
        p = store.get_or_default(args.name)
    except KeyError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    t = p.compute_targets()
    print(f"# 用户画像: {p.name}\n")
    print(f"性别: {p.sex} | 年龄: {p.age} | 身高: {p.height_cm:.0f}cm | 体重: {p.weight_kg:.1f}kg")
    print(f"活动水平: {p.activity} | 目标: {p.goal} | 全天预算: ¥{p.budget_yuan:.1f}")
    print(f"创建时间: {p.created_at} | 更新时间: {p.updated_at}\n")
    print(f"## 每日推荐摄入卡路里与宏量\n")
    print(f"- BMR (基础代谢率): {t.bmr:.0f} kcal/天")
    print(f"- TDEE (每日总能量消耗): {t.tdee:.0f} kcal/天")
    print(f"- 目标摄入: {t.target_calories:.0f} kcal/天"
          f" ({'缺口' if t.deficit > 0 else '盈余'} {abs(t.deficit):.0f} kcal/天)")
    print(f"- 估算周体重变化: {t.estimated_weekly_weight_change_kg:+.3f} kg/周\n")
    print(f"宏量营养目标:")
    print(f"- 蛋白质: {t.protein_g:.0f} g ({t.protein_g * 4 / t.target_calories * 100:.0f}%)")
    print(f"- 脂肪:   {t.fat_g:.0f} g ({t.fat_g * 9 / t.target_calories * 100:.0f}%)")
    print(f"- 碳水:   {t.carb_g:.0f} g ({t.carb_g * 4 / t.target_calories * 100:.0f}%)\n")
    print("三餐+加餐分配:")
    for name in ("breakfast", "lunch", "dinner", "snack"):
        m = t.meals[name]
        print(f"- {m.label_zh} ({m.ratio * 100:.0f}%): "
              f"{m.calories:.0f} kcal / P{m.protein_g:.0f} F{m.fat_g:.0f} C{m.carb_g:.0f} / ¥{m.budget_yuan:.0f}")
    return 0


def _profile_remove(args: argparse.Namespace) -> int:
    store = ProfileStore()
    if store.remove(args.name):
        print(f"✅ 已删除用户画像: {args.name}")
        return 0
    print(f"❌ 未找到: {args.name}", file=sys.stderr)
    return 1


def _profile_set_default(args: argparse.Namespace) -> int:
    store = ProfileStore()
    try:
        store.set_default(args.name)
        print(f"✅ 默认用户已设为: {args.name}")
        return 0
    except KeyError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1


# ----------------------------------------------------------------- plan
def _cmd_plan(args: argparse.Namespace) -> int:
    """单人全天减脂方案（基于存储画像）。"""
    store = ProfileStore()
    try:
        profile = store.get_or_default(args.user)
    except KeyError as e:
        print(f"❌ {e}", file=sys.stderr)
        print("   请先 `mcleanfuel profile add <name> -w ... -H ... -a ... --sex ...`", file=sys.stderr)
        return 1

    targets = profile.compute_targets()
    user_profile = {
        "sex": profile.sex, "age": profile.age,
        "height_cm": profile.height_cm, "weight_kg": profile.weight_kg,
        "activity": profile.activity, "goal": profile.goal,
        "budget_yuan": profile.budget_yuan,
    }

    # 连接 MCP
    try:
        client = McpClient(token=args.token)
    except McpError as e:
        print(f"❌ {e}", file=sys.stderr)
        print(f"   Token 配置方式：环境变量 MCD_MCP_TOKEN 或文件 {TOKEN_FILE}", file=sys.stderr)
        return 2

    # 拉营养数据
    try:
        print(f"→ 用户 {profile.name}：拉取麦当劳餐品营养数据...", file=sys.stderr)
        rows = client.list_nutrition_foods()
    except McpAuthError as e:
        print(f"❌ Token 无效：{e}", file=sys.stderr)
        return 3
    except (McpRateLimitError, McpError) as e:
        print(f"⚠️ MCP 调用失败：{e}", file=sys.stderr)
        rows = []
    print(f"  ✓ 获取 {len(rows)} 个餐品", file=sys.stderr)

    # 规划
    planner = Planner.from_mcp_rows(rows)
    plan = planner.plan_day(targets)

    # 可选领券
    coupons: list[dict] = []
    if not args.no_coupons:
        try:
            print("→ 自动领取麦麦省优惠券...", file=sys.stderr)
            result = client.auto_bind_coupons()
            sc_list = result.get("successCoupons", []) if isinstance(result, dict) else []
            coupons = sc_list
            print(f"  ✓ 领取 {len(coupons)} 张", file=sys.stderr)
        except McpError as e:
            print(f"  ⚠ 自动领券失败: {e}", file=sys.stderr)

    # 输出
    if args.json:
        output = _render_plan_json(targets, plan, user_profile, coupons)
    else:
        output = render_markdown_report(
            targets=targets, plan=plan, user_profile=user_profile,
            coupons_bound=coupons,
        )
    _write_output(output, args.output)
    return 0


def _render_plan_json(targets, plan, user_profile: dict, coupons: list[dict]) -> str:
    payload = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "user_profile": user_profile,
        "targets": {
            "bmr": targets.bmr, "tdee": targets.tdee,
            "target_calories": targets.target_calories,
            "deficit": targets.deficit,
            "weekly_weight_change_kg": round(targets.estimated_weekly_weight_change_kg, 3),
            "macros": {"protein_g": targets.protein_g, "fat_g": targets.fat_g, "carb_g": targets.carb_g},
        },
        "plan": {
            name: {
                "meal_target": {
                    "calories": mp.meal.calories, "protein_g": mp.meal.protein_g,
                    "fat_g": mp.meal.fat_g, "carb_g": mp.meal.carb_g,
                    "budget_yuan": mp.meal.budget_yuan,
                },
                "items": [
                    {"name": it.product_name, "energy_kcal": it.energy_kcal,
                     "protein_g": it.protein_g, "fat_g": it.fat_g, "carb_g": it.carb_g,
                     "sodium_mg": it.sodium_mg, "category": it.category}
                    for it in mp.items
                ],
                "totals": {
                    "kcal": mp.total_kcal, "protein_g": mp.total_protein_g,
                    "fat_g": mp.total_fat_g, "carb_g": mp.total_carb_g,
                    "sodium_mg": mp.total_sodium_mg, "price_yuan": mp.total_price_yuan,
                },
                "compliance": mp.compliance,
            }
            for name, mp in plan.meals.items()
        },
        "daily_totals": {
            "kcal": plan.total_kcal, "protein_g": plan.total_protein_g,
            "fat_g": plan.total_fat_g, "carb_g": plan.total_carb_g,
            "sodium_mg": plan.total_sodium_mg, "price_yuan": plan.total_price_yuan,
        },
        "coupons_bound": len(coupons),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------- deal
def _cmd_deal(args: argparse.Namespace) -> int:
    """单人凑单省券优化。"""
    store = ProfileStore()
    try:
        profile = store.get(args.user)
        if profile is None:
            print(f"❌ 未找到用户画像: {args.user}", file=sys.stderr)
            return 1
    except KeyError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    budget = args.budget if args.budget is not None else profile.budget_yuan
    # 临时覆盖画像预算
    profile = Profile(
        name=profile.name, weight_kg=profile.weight_kg, height_cm=profile.height_cm,
        age=profile.age, sex=profile.sex, activity=profile.activity, goal=profile.goal,
        budget_yuan=budget, created_at=profile.created_at, updated_at=profile.updated_at,
    )

    try:
        client = McpClient(token=args.token)
    except McpError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    # 找门店
    try:
        print(f"→ 查询门店: {args.city} {args.keyword}...", file=sys.stderr)
        stores = client.query_nearby_stores(
            city=args.city, keyword=args.keyword,
            be_type=args.be_type, search_type=2,
        )
    except (McpAuthError, McpRateLimitError, McpError) as e:
        print(f"❌ 门店查询失败: {e}", file=sys.stderr)
        return 3
    if not stores:
        print(f"❌ 未找到门店（city={args.city}, keyword={args.keyword}）", file=sys.stderr)
        return 4
    store = stores[0]
    store_code = str(store.get("storeCode", ""))
    be_code = store.get("beCode") if args.be_type == 5 else None
    print(f"  ✓ 门店: {store.get('storeName', '?')} ({store_code}) - {store.get('address', '')}",
          file=sys.stderr)

    # 凑单优化
    optimizer = CouponOptimizer(
        client, store_code=store_code, order_type=1,
        be_type=args.be_type, be_code=be_code,
    )
    try:
        print("→ 拉取门店菜单 + 营养数据 + 券...", file=sys.stderr)
        optimizer.fetch(auto_bind=not args.no_auto_bind)
        print(f"  ✓ {len(optimizer.meals)} 个菜单项 | {len(optimizer.coupons)} 张可用券",
              file=sys.stderr)
        print(f"→ 对 {profile.name} 做凑单优化（预算 ¥{budget:.1f}）...", file=sys.stderr)
        deal = optimizer.optimize(profile)
    except (McpAuthError, McpRateLimitError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 5
    except McpError as e:
        print(f"❌ MCP 调用失败: {e}", file=sys.stderr)
        return 6

    output = render_deal_report(
        profile=profile, deal=deal, store=store, budget=budget,
    )
    _write_output(output, args.output)
    return 0


# ----------------------------------------------------------------- party
def _cmd_party(args: argparse.Namespace) -> int:
    """多人组队订餐推荐。"""
    names = [n.strip() for n in args.users.split(",") if n.strip()]
    if not names:
        print("❌ 请至少指定 1 个用户（逗号分隔）", file=sys.stderr)
        return 1
    store = ProfileStore()
    profiles: list[Profile] = []
    for n in names:
        p = store.get(n)
        if p is None:
            print(f"❌ 未找到用户画像: {n}", file=sys.stderr)
            return 1
        profiles.append(p)

    try:
        client = McpClient(token=args.token)
    except McpError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    # 找门店
    try:
        print(f"→ 查询门店: {args.city} {args.keyword}...", file=sys.stderr)
        stores = client.query_nearby_stores(
            city=args.city, keyword=args.keyword,
            be_type=args.be_type, search_type=2,
        )
    except (McpAuthError, McpRateLimitError, McpError) as e:
        print(f"❌ 门店查询失败: {e}", file=sys.stderr)
        return 3
    if not stores:
        print(f"❌ 未找到门店", file=sys.stderr)
        return 4
    store = stores[0]
    store_code = str(store.get("storeCode", ""))
    be_code = store.get("beCode") if args.be_type == 5 else None
    print(f"  ✓ 门店: {store.get('storeName')} - {store.get('address')}", file=sys.stderr)

    # 多人规划
    planner = PartyPlanner(
        client, store_code=store_code, order_type=1,
        be_type=args.be_type, be_code=be_code,
        store_info=store,
    )
    try:
        print(f"→ 拉取门店菜单 + 营养数据 + 券（共享给 {len(profiles)} 人）...",
              file=sys.stderr)
        planner.fetch(auto_bind=not args.no_auto_bind)
        print(f"→ 对 {len(profiles)} 人做组队凑单优化（总预算 ¥{args.budget:.1f}）...",
              file=sys.stderr)
        plan = planner.plan(profiles, total_budget=args.budget)
    except (McpAuthError, McpRateLimitError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 5
    except McpError as e:
        print(f"❌ MCP 调用失败: {e}", file=sys.stderr)
        return 6

    output = render_party_report(plan=plan, total_budget=args.budget)
    _write_output(output, args.output)
    return 0


# ----------------------------------------------------------------- doctor
def _cmd_doctor(args: argparse.Namespace) -> int:
    print("McLeanFuel Doctor\n" + "=" * 40)

    try:
        client = McpClient(token=args.token)
    except McpError as e:
        print(f"❌ Token 未配置：{e}")
        print(f"   Token 文件路径: {TOKEN_FILE}")
        print("   配置方式：")
        print("     1) export MCD_MCP_TOKEN=your_token")
        print(f"     2) 写入文件 {TOKEN_FILE} (chmod 600)")
        print("     3) mcleanfuel <cmd> --token your_token")
        return 1

    import os
    if args.token:
        src = "命令行参数"
    elif os.environ.get("MCD_MCP_TOKEN"):
        src = "环境变量 MCD_MCP_TOKEN"
    else:
        src = f"本地文件 {TOKEN_FILE}"
    print(f"✅ Token 来源: {src}")

    print("\n→ 初始化 MCP 会话...")
    try:
        info = client.initialize()
        si = info.get("result", {}).get("serverInfo", {})
        print(f"✅ 已连接: {si.get('name', '?')} v{si.get('version', '?')}")
    except McpError as e:
        print(f"❌ 初始化失败: {e}")
        return 2

    print("\n→ 列出 Tools...")
    try:
        tools = client.list_tools()
        print(f"✅ 共 {len(tools)} 个 Tool")
        for t in tools[:8]:
            print(f"   - {t.get('name', '?')}")
        if len(tools) > 8:
            print(f"   ... 还有 {len(tools) - 8} 个")
    except McpError as e:
        print(f"❌ 列出工具失败: {e}")
        return 3

    print("\n→ 实测 list-nutrition-foods...")
    try:
        rows = client.list_nutrition_foods()
        print(f"✅ 获取 {len(rows)} 个餐品营养数据")
    except McpError as e:
        print(f"❌ 业务调用失败: {e}")
        return 4

    # 检查用户画像存储
    store = ProfileStore()
    if len(store) == 0:
        print(f"\n⚠️ 暂无用户画像（存储位置: {PROFILES_FILE}）")
        print("   建议执行: mcleanfuel profile add alice -w 65 -H 170 -a 28 --sex female")
    else:
        print(f"\n✅ 已配置 {len(store)} 个用户画像")
        for p in store.list_all():
            mark = "★" if store.default_name == p.name else " "
            print(f"   {mark} {p.name} ({p.sex}, {p.age}岁, {p.weight_kg}kg)")

    print("\n✅ 所有检查通过。")
    print("   下一步:")
    print("   mcleanfuel profile add alice -w 65 -H 170 -a 28 --sex female")
    print("   mcleanfuel plan alice")
    print("   mcleanfuel deal alice --city 北京 --keyword 东直门 --budget 50")
    print("   mcleanfuel party alice,bob --city 北京 --keyword 东直门 --budget 200")
    return 0


# ----------------------------------------------------------------- 辅助
def _write_output(content: str, path: str | None) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"\n✅ 报告已保存: {path}", file=sys.stderr)
    else:
        print(content)


if __name__ == "__main__":
    sys.exit(main())
