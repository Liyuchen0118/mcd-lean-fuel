"""McLeanFuel - 麦麦减脂燃料师

基于麦当劳 MCP 的健康减脂餐规划 Skill。

四大核心功能：
1. ``profile``  用户画像录入与持久化存储
2. ``plan``     单人每日推荐摄入卡路里 + 三餐+加餐方案
3. ``deal``     单人凑单省券优化（含门店菜单价格）
4. ``party``    多人组队订餐套餐推荐
"""

from __future__ import annotations

from .tdee import (
    ActivityLevel,
    Goal,
    NutritionTargets,
    Sex,
    calculate_bmr,
    calculate_targets,
)
from .planner import FoodItem, MealPlan, Planner, PlanResult
from .mcp_client import McpClient, McpError, McpAuthError, McpRateLimitError
from .profile import Profile, ProfileStore, PROFILES_FILE
from .coupon_optimizer import Coupon, CouponOptimizer, DealResult, MealItem
from .party import PartyPlanner, PartyPlan, PartyMemberResult
from .report import (
    render_deal_report,
    render_markdown_report,
    render_party_report,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # tdee
    "ActivityLevel", "Goal", "NutritionTargets", "Sex",
    "calculate_bmr", "calculate_targets",
    # planner
    "FoodItem", "MealPlan", "Planner", "PlanResult",
    # mcp_client
    "McpClient", "McpError", "McpAuthError", "McpRateLimitError",
    # profile
    "Profile", "ProfileStore", "PROFILES_FILE",
    # coupon
    "Coupon", "CouponOptimizer", "DealResult", "MealItem",
    # party
    "PartyPlanner", "PartyPlan", "PartyMemberResult",
    # report
    "render_deal_report", "render_markdown_report", "render_party_report",
]
