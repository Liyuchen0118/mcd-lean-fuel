# 🍔 McLeanFuel 麦麦减脂燃料师

> 基于麦当劳 MCP 的健康减脂餐规划 Skill —— 录入用户画像 → 计算每日推荐卡路里 → 凑单省券优化 → 多人组队订餐

<p align="center">
  <a href="https://open.mcd.cn/mcp" target="_blank">🔗 麦当劳 MCP 开放平台</a> ·
  <a href="https://github.com/M-China/mcd-developer-innovation-challenge">🏆 麦当劳程序员创意开发大赛</a>
</p>

---

## ✨ 核心特色

| 功能 | 子命令 | 一句话 |
|---|---|---|
| 👤 用户画像录入与持久化 | `profile add/list/show/remove` | 一次录入体征，永久复用 |
| 🎯 每日推荐摄入卡路里 | `plan [user]` | Mifflin-St Jeor 公式计算 BMR/TDEE + 宏量目标 + 三餐+加餐方案 |
| 💰 凑单省券优化 | `deal [user] --city --keyword --budget` | 自动领券 + 餐品组合搜索 + MCP `calculate-price` 验证实付 |
| 👥 多人组队订餐 | `party alice,bob --budget` | 总预算按蛋白比例分配 + 共享券策略 + 超预算自动降级 |

## 🎯 解决的现实问题

减脂期"想吃麦麦但不知道怎么吃"的痛点：
- 不知道一天该吃多少卡路里、蛋白/脂肪/碳水怎么分
- 单点套餐营养不均衡（高脂高钠）
- 有券不会用、凑单总超预算
- 多人聚餐预算难协调

**McLeanFuel 把这些问题一次解决**：
1. 用医学公式计算你专属的 TDEE 和宏量目标
2. 从麦当劳 160+ 个餐品中搜索最优三餐组合（蛋白达标 + 卡路里贴近 + 约束不超）
3. 自动领券 + 识别券类型（定价/免费/买一送一）+ 应用到组合 + MCP 验证实付金额
4. 多人组队时按蛋白需求分配预算、超预算自动降级替换更便宜餐品

## 📦 安装

```bash
# 从源码安装（推荐）
git clone https://github.com/Liyuchen0118/mcd-lean-fuel.git
cd mcd-lean-fuel
python3 -m pip install --user -e .
```

依赖：**Python ≥ 3.9**，零第三方运行时依赖（仅用 stdlib）。

## 🚀 快速开始

### 1️⃣ 申请麦当劳 MCP Token

访问 https://open.mcd.cn/mcp ，手机号登录 → 控制台 → 激活 → 复制 MCP Token。

把 Token 安全存到家目录（不进 git）：

```bash
mkdir -p ~/.config/zhanlu
echo -n 'YOUR_MCP_TOKEN' > ~/.config/zhanlu/mcd-mcp-token
chmod 600 ~/.config/zhanlu/mcd-mcp-token
```

或用环境变量：

```bash
export MCD_MCP_TOKEN=your_token
```

### 2️⃣ 检查环境

```bash
mcleanfuel doctor
```

### 3️⃣ 录入用户画像

```bash
# 录入减脂期女性 alice（65kg, 170cm, 28 岁, 中度活动）
mcleanfuel profile add alice -w 65 -H 170 -a 28 --sex female \
    --activity moderate --goal cut --budget 60

# 录入维持期男性 bob
mcleanfuel profile add bob -w 80 -H 180 -a 32 --sex male \
    --activity active --goal maintain --budget 80 --set-default

# 查看所有用户
mcleanfuel profile list
```

### 4️⃣ 生成单人减脂方案

```bash
# 基于 alice 的画像生成全天三餐+加餐方案（Markdown 报告）
mcleanfuel plan alice -o alice-plan.md

# 也可以输出 JSON 给其他 AI Agent 调用
mcleanfuel plan alice --json -o alice-plan.json
```

输出包含：BMR / TDEE / 目标卡路里 / 三餐+加餐分配 / 每餐餐品组合 / 营养达标进度条 / 自动领券结果 / 健康提示。

### 5️⃣ 单人凑单省券

```bash
# 在北京东方银座餐厅，预算 ¥50 给 alice 凑单
mcleanfuel deal alice --city 北京 --keyword 东直门 --budget 50 -o alice-deal.md
```

调用链：`query-nearby-stores` → `query-meals`（111 个含价格餐品） → `query-my-coupons` + `query-store-coupons`（券） → `auto-bind-coupons`（一键领券） → 组合优化 → `calculate-price`（验证实付）。

### 6️⃣ 多人组队订餐

```bash
# alice + bob 在北京东直门组队，总预算 ¥150
mcleanfuel party alice,bob --city 北京 --keyword 东直门 --budget 150 -o party.md
```

特性：
- 总预算按蛋白需求比例分配（bob 蛋白目标高，分得更多预算）
- 每人独立凑单 + 共享券策略
- 超预算自动降级：替换最贵餐品为同分类更便宜选项
- MCP `calculate-price` 合并验证

## 🏗️ 项目结构

```
mcd-lean-fuel/
├── README.md                      # 项目介绍
├── CONTEST_DECLARATION.md         # 参赛声明（官方模板，不可改）
├── MCP_INTEGRATION.md             # MCP Server / Tool / 调用流程说明
├── mcp-config.example.json        # 脱敏 MCP 配置示例（仅环境变量占位符）
├── .env.example                   # 环境变量示例
├── pyproject.toml                 # Python 包元数据
├── setup.py                       # 兼容旧 setuptools 的兜底
├── src/mcleanfuel/
│   ├── __init__.py                # 公共 API 导出
│   ├── tdee.py                    # Mifflin-St Jeor 公式 BMR/TDEE/宏量
│   ├── mcp_client.py              # MCP Streamable HTTP 客户端 + 自定义文本表格解析器
│   ├── profile.py                 # 用户画像 + JSON 持久化（600 权限）
│   ├── planner.py                 # 单人单餐组合优化
│   ├── coupon_optimizer.py        # 凑单省券优化器（券类型识别 + 价格搜索）
│   ├── party.py                   # 多人组队订餐（预算分配 + 共享券 + 降级）
│   ├── report.py                  # Markdown 报告生成（plan/deal/party）
│   └── cli.py                     # argparse 子命令入口
└── tests/                         # 50 个单元测试（pytest）
```

## 🔬 技术亮点

1. **零运行时依赖**：MCP HTTP 客户端、JSON 解析、组合搜索全部用 Python stdlib 实现
2. **麦当劳自定义文本表格解析器**：MCP 的 `list-nutrition-foods` 返回 `[N]{fields}:\n  v1,v2,...` 自定义格式（非 JSON），我们实现了对应解析器（见 `mcp_client._parse_table_text`）
3. **医学公式 TDEE 计算**：采用 Mifflin-St Jeor 公式（1990），比 Harris-Benedict 更准
4. **多 Tool 串联**：一次 `deal` 命令串联 6 个 MCP Tool，符合大赛"Skill"定义
5. **券类型识别**：从券标题自动识别定价/免费/买一送一/折扣 4 类，提取目标餐品关键词
6. **模糊匹配**：处理"麦辣鸡腿堡" vs "麦辣鸡腿汉堡"等简称/全称差异
7. **多目标优化**：贪心+组合搜索，目标函数兼顾蛋白质达标、卡路里贴近、约束不超、预算限制
8. **超预算自动降级**：替换最贵餐品为同分类更便宜选项，循环直到满足预算

## 🎯 目标用户

- 健身/减脂人群：想精确控制宏量营养的麦麦爱好者
- 多人聚餐决策者：需要协调多人预算和口味的组织者
- 麦麦省钱党：想用券但懒得研究规则的"麦门"信徒
- AI Agent 开发者：可作为 Skill 被其他 AI Agent 调用（JSON 输出）

## 📋 活动规则遵守

- ✅ 项目创建时间：2026-10-19（在 2025-12-25 至 2026-10-25 范围内）
- ✅ 公开 GitHub 仓库
- ✅ 包含 `README.md` / `CONTEST_DECLARATION.md`（官方模板未改）/ `MCP_INTEGRATION.md` / `mcp-config.example.json`
- ✅ 真实使用麦当劳 MCP 能力（35 个 Tool 中使用 8 个）
- ✅ 不含真实 Token / 密钥（仅环境变量占位符）
- ✅ 原创开发，不侵犯第三方权益

## 🧪 开发与测试

```bash
# 安装开发依赖
python3 -m pip install --user -e ".[dev]"

# 运行全部测试
python3 -m pytest tests/ -v

# 跑真实端到端
mcleanfuel doctor
mcleanfuel profile add alice -w 65 -H 170 -a 28 --sex female
mcleanfuel plan alice
mcleanfuel deal alice --city 北京 --keyword 东直门 --budget 50
mcleanfuel party alice,bob --city 北京 --keyword 东直门 --budget 150
```

## ⚠️ 免责声明

本项目为麦当劳程序员节创意开发大赛参赛作品，由参赛者独立开发，**非麦当劳官方产品**。

- 输出仅供参考，**不构成医疗、营养或其他专业建议**
- 餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准
- 减脂需结合个体差异、运动量与饮食习惯综合考虑，如有健康问题请咨询专业医师或营养师

## 📄 License

MIT

## 🔗 相关链接

- 麦当劳 MCP 开放平台：https://open.mcd.cn/mcp
- MCP Server 使用指南：https://github.com/M-China/mcd-mcp-server
- 大赛主仓库：https://github.com/M-China/mcd-developer-innovation-challenge
- 本项目 Issue：https://github.com/Liyuchen0118/mcd-lean-fuel/issues

---

<p align="center">
  <sub>Built with ❤️ for 1024 程序员节 · McLeanFuel v0.1.0</sub>
</p>
