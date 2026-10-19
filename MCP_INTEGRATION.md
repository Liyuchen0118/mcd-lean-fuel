# MCP_INTEGRATION.md - 麦当劳 MCP 集成说明

本文档说明 McLeanFuel 实际使用的麦当劳 MCP Server、Tool、调用流程及业务价值。

## 1. 使用的 MCP Server

| 字段 | 值 |
|---|---|
| Server 名称 | mcd-mcp |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | `Authorization: Bearer <MCP_TOKEN>` |
| 限流 | 600 次/分钟（超过返回 429） |
| Token 来源 | https://open.mcd.cn/mcp 控制台申请 |
| 协议版本 | 2024-11-05 |

配置示例（脱敏，仅环境变量占位符）：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer ${MCD_MCP_TOKEN}"
      }
    }
  }
}
```

## 2. 使用的 MCP Tool 清单

本 Skill 实际调用 **8 个** 麦当劳 MCP Tool（覆盖营养/门店/菜单/券/价格/下单全链路）：

| Tool 名称 | 用途 | 在 Skill 中的调用位置 |
|---|---|---|
| `list-nutrition-foods` | 获取 160 个餐品的营养成分（卡路里/蛋白/脂肪/碳水/钠/钙） | `plan`、`deal`、`party` 启动时拉取营养基线 |
| `query-nearby-stores` | 按城市+关键词查询附近可点餐门店 | `deal`、`party` 找门店 |
| `query-meals` | 获取指定门店的当前菜单（111+ 餐品含 `currentPrice`/`originalPrice`） | `deal`、`party` 拉取含价格的菜单 |
| `query-meal-detail` | 查询餐品详情（套餐组成、可替换选项） | 预留扩展点（当前未深度使用） |
| `query-my-coupons` | 查询用户卡包中已有的优惠券 | `deal`、`party` 收集可用券 |
| `query-store-coupons` | 查询指定门店+订单类型下可使用的券 | `deal`、`party` 收集门店专属券 |
| `auto-bind-coupons` | 一键领取麦麦省所有当前可用券 | `plan`、`deal`、`party` 自动领券步骤 |
| `calculate-price` | 计算商品价格（含优惠），返回实付金额 | `deal`、`party` 验证最终实付金额 |

## 3. 调用流程

### 3.1 单人减脂方案（`mcleanfuel plan [user]`）

```
┌─────────────────────────────────────────────────────────┐
│  1. 读取本地用户画像（profile.py）                       │
│      ↓                                                   │
│  2. Mifflin-St Jeor 公式计算 BMR/TDEE/宏量目标            │
│      ↓                                                   │
│  3. MCP: list-nutrition-fods → 拿到 160 个餐品营养        │
│      ↓                                                   │
│  4. planner.py 组合搜索 4 餐次（早/午/晚/加餐）           │
│      ↓                                                   │
│  5. MCP: auto-bind-coupons → 自动领券（可选）             │
│      ↓                                                   │
│  6. report.py 生成 Markdown/JSON 报告                    │
└─────────────────────────────────────────────────────────┘
```

### 3.2 单人凑单省券（`mcleanfuel deal [user] --city --keyword --budget`）

```
┌─────────────────────────────────────────────────────────┐
│  1. 读取用户画像 + 临时覆盖预算                          │
│      ↓                                                   │
│  2. MCP: query-nearby-stores → 找门店（storeCode）       │
│      ↓                                                   │
│  3. MCP: query-meals → 111+ 餐品（含价格）                │
│  3. MCP: list-nutrition-foods → 160 餐品营养              │
│  3. 按 productName 合并 → 营养+价格双标签                │
│      ↓                                                   │
│  4. MCP: auto-bind-coupons → 一键领券                     │
│  4. MCP: query-my-coupons → 用户已有券                   │
│  4. MCP: query-store-coupons → 门店可用券                 │
│  4. 券类型识别（定价/免费/买一送一/折扣）                  │
│      ↓                                                   │
│  5. coupon_optimizer.py 组合搜索（预算约束 + 蛋白达标）   │
│  5. 应用券 → 计算用券后实付                                │
│      ↓                                                   │
│  6. MCP: calculate-price → 验证最终实付金额              │
│      ↓                                                   │
│  7. report.render_deal_report → Markdown 报告            │
└─────────────────────────────────────────────────────────┘
```

### 3.3 多人组队订餐（`mcleanfuel party alice,bob --budget`）

```
┌─────────────────────────────────────────────────────────┐
│  1. 读取多个用户画像                                     │
│      ↓                                                   │
│  2. MCP: query-nearby-stores → 找门店（共享给全员）      │
│      ↓                                                   │
│  3. 一次拉取菜单+营养+券（共享，避免重复调用）            │
│  3. MCP: query-meals + list-nutrition-foods             │
│  3. MCP: auto-bind-coupons + query-my-coupons           │
│  3. MCP: query-store-coupons                            │
│      ↓                                                   │
│  4. 总预算按蛋白目标比例分配（PartyPlanner._allocate）    │
│      ↓                                                   │
│  5. 对每人生成个人凑单方案（CouponOptimizer.optimize）   │
│      ↓                                                   │
│  6. 合计 + 共享券策略                                     │
│      ↓                                                   │
│  7. 若超预算：自动降级替换更便宜餐品（循环直到达标）      │
│      ↓                                                   │
│  8. MCP: calculate-price → 合并验证全员实付金额          │
│      ↓                                                   │
│  9. report.render_party_report → 多人 Markdown 报告     │
└─────────────────────────────────────────────────────────┘
```

## 4. 业务价值

| 场景 | 传统方式 | McLeanFuel |
|---|---|---|
| 减脂期想吃麦麦 | 凭感觉点，蛋白不够、卡路里超标 | 1 条命令生成全天宏量达标方案 |
| 用优惠券凑单 | 手动翻菜单+查券+算价 | 自动领券+组合搜索+MCP 验证实付 |
| 多人聚餐 AA | 反复协调预算 | 总预算按需求分配，超预算自动降级 |
| 长期跟踪 | 每次重新算 | 用户画像持久化，复用计算结果 |

**核心创新点**：

1. **多 Tool 串联**：一次 `deal` 命令串联 6 个 MCP Tool（nearby-stores → meals → my-coupons → store-coupons → auto-bind → calculate-price），是真正意义上的"Skill"而非单点查询工具
2. **券类型自动识别**：从券标题解析"定价/免费/买一送一/折扣"4 类，提取目标餐品关键词，处理"麦辣鸡腿堡"vs"麦辣鸡腿汉堡"等简称差异
3. **多目标约束优化**：贪心+组合搜索算法，目标函数综合考虑蛋白质达标率、卡路里贴近、脂肪/碳水/钠约束、预算限制
4. **超预算自动降级**：在 party 场景下，循环替换最贵餐品为同分类更便宜选项，直到总价落入预算
5. **医学公式 TDEE**：采用 Mifflin-St Jeor 公式（1990），比 Harris-Benedict 更准，并设有最低 1200 kcal 保护

## 5. 实测验证

本 Skill 已用真实 MCP Token 在麦当劳北京东方银座餐厅（storeCode=1950105）端到端验证：

```
$ mcleanfuel doctor
✅ 已连接: mcd-mcp v1.0.0
✅ 共 35 个 Tool
✅ 获取 160 个餐品营养数据

$ mcleanfuel profile add alice -w 65 -H 170 -a 28 --sex female
✅ TDEE: 2188 kcal/天 | 目标: 1688 kcal

$ mcleanfuel plan alice
✅ 获取 160 个餐品
✅ 领取 0 张（卡包已有 4 张）

$ mcleanfuel deal alice --city 北京 --keyword 东直门 --budget 50
✅ 门店: 麦当劳北京东方银座餐厅 (1950105)
✅ 111 个菜单项 | 7 张可用券
✅ 已通过麦当劳 MCP `calculate-price` 验证实付金额

$ mcleanfuel party alice,bob --city 北京 --keyword 东直门 --budget 150
✅ 2 人组队，预算按蛋白比例分配
✅ 已通过 MCP `calculate-price` 验证
```

## 6. 限制与注意事项

- **限流**：MCP 每分钟最多 600 次请求，Skill 已通过共享数据拉取（多人共用一次 fetch）最小化调用
- **Token 安全**：本项目代码不含真实 Token，运行时从环境变量 `MCD_MCP_TOKEN` 或本地文件 `~/.config/zhanlu/mcd-mcp-token`（权限 600）读取
- **价格估算**：若 MCP `calculate-price` 调用失败（如限流），Skill 会退化为本地估算价格，并在报告中明确标注"未通过 MCP 验证"
- **营养数据覆盖**：160 个餐品营养数据覆盖菜单的约 70%，未匹配的餐品在凑单时只能用价格、不能用营养约束
- **门店参数**：beType=1（到店自提）不需要 beCode；beType=5（得来速）需要 beCode，本 Skill 已自动处理

## 7. 扩展点

未来可扩展的 MCP Tool 用法：

| 想做什么 | 用的 MCP Tool |
|---|---|
| 自动下单 | `create-order` |
| 历史订单分析（饮食回顾） | `order-list`、`query-order` |
| 积分兑换健康餐 | `query-my-account`、`mall-points-products`、`mall-create-order` |
| 主题活动预约（生日派对） | `query-party-city` → `query-party-store` → `party-order-create` |
| 活动日历提醒 | `campaign-calendar`、`now-time-info` |

---

<p align="center">
  <sub>McLeanFuel v0.1.0 · 1024 程序员节参赛作品</sub>
</p>
