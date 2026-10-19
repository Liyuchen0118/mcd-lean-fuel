"""麦当劳 MCP Streamable HTTP 客户端（Python stdlib only）。

支持：
- initialize / tools/list / tools/call 三个核心方法
- 处理 Streamable HTTP（含 SSE 响应）与纯 JSON 响应
- 解析麦当劳 MCP 自定义文本表格格式（``[N]{f1,f2,...}:\\n  v1,v2,...``）
- 401/429 错误分类
- Token 从参数 / 环境变量 / 本地配置文件三处自动读取
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any
from urllib import error, request

DEFAULT_URL = os.environ.get("MCD_MCP_URL", "https://mcp.mcd.cn")
TOKEN_FILE = os.path.expanduser("~/.config/zhanlu/mcd-mcp-token")


class McpError(RuntimeError):
    """MCP 调用通用错误。"""


class McpAuthError(McpError):
    """Token 无效或过期（HTTP 401）。"""


class McpRateLimitError(McpError):
    """触发限流（HTTP 429，>600 次/分钟）。"""


class McpClient:
    """麦当劳 MCP Streamable HTTP 客户端。

    用法::

        client = McpClient()           # 自动从环境变量/配置文件读 Token
        client.initialize()             # 建立会话（也可省略，调用 tool 时自动建立）
        foods = client.list_nutrition_foods()    # 业务方法封装

    Token 来源优先级（高 → 低）：
        1. 构造参数 ``token``
        2. 环境变量 ``MCD_MCP_TOKEN``
        3. 本地文件 ``~/.config/zhanlu/mcd-mcp-token``（权限 600）
    """

    def __init__(self, token: str | None = None, url: str = DEFAULT_URL):
        self.token = token or os.environ.get("MCD_MCP_TOKEN") or self._read_token_file()
        if not self.token:
            raise McpError(
                "MCP Token 未提供。请通过参数、环境变量 MCD_MCP_TOKEN 或 "
                f"配置文件 {TOKEN_FILE} 提供。"
            )
        self.url = url.rstrip("/")
        self._session_id = str(uuid.uuid4())
        self._initialized = False
        self._next_id = 1

    @staticmethod
    def _read_token_file() -> str | None:
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                t = f.read().strip()
                return t or None
        except FileNotFoundError:
            return None
        except OSError:
            return None

    # ------------------------------------------------------------------ HTTP
    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self.token}",
                "Mcp-Session-Id": self._session_id,
            },
        )
        try:
            with request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
        except error.HTTPError as e:
            if e.code == 401:
                raise McpAuthError("MCP Token 无效或已过期，请到 https://open.mcd.cn/mcp 重新申请。") from e
            if e.code == 429:
                raise McpRateLimitError("触发限流（>600 次/分钟），请降低调用频率后重试。") from e
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise McpError(f"HTTP {e.code}: {e.reason} | body={body[:200]}") from e
        except error.URLError as e:
            raise McpError(f"网络错误：{e.reason}") from e

        if body.startswith("event:") or body.startswith("data:"):
            return self._parse_sse(body)
        return json.loads(body)

    @staticmethod
    def _parse_sse(body: str) -> dict[str, Any]:
        result: dict[str, Any] | None = None
        for line in body.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk:
                    parsed = json.loads(chunk)
                    if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
                        result = parsed
        if result is None:
            raise McpError(f"无法解析 SSE 响应：{body[:200]}")
        return result

    # -------------------------------------------------------------- MCP 核心
    def initialize(self) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcleanfuel", "version": "0.1.0"},
            },
        }
        self._next_id += 1
        result = self._post(payload)
        if "result" in result:
            self._initialized = True
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        if not self._initialized:
            self.initialize()
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": "tools/list", "params": {}}
        self._next_id += 1
        result = self._post(payload)
        return result.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._initialized:
            self.initialize()
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        self._next_id += 1
        result = self._post(payload)
        if "error" in result:
            err = result["error"]
            raise McpError(f"Tool '{name}' 调用失败：[{err.get('code')}] {err.get('message')}")
        return result.get("result", {}) or {}

    # ------------------------------------------------------------- 业务封装
    def list_nutrition_foods(self, **kwargs) -> list[dict[str, Any]]:
        """获取餐品营养信息列表。

        返回字段：productName, nutritionDescription, energyKj, energyKcal,
        protein, fat, carbohydrate, sodium, calcium
        """
        raw = self.call_tool("list-nutrition-foods", kwargs)
        return self._extract_data_rows(raw)

    def query_nearby_stores(
        self,
        *,
        city: str | None = None,
        keyword: str | None = None,
        be_type: int = 1,
        search_type: int = 2,
    ) -> list[dict[str, Any]]:
        """查询到店可点餐门店。

        参数：
        - be_type: 1=到店自提(无 beCode), 5=得来速(有 beCode)
        - search_type: 1=搜索收藏餐厅, 2=按位置搜索（需要 city+keyword）
        - city: 城市名（如 "北京"）
        - keyword: 位置关键词（如 "东直门"）

        返回字段：storeCode, storeName, address, businessStartTime/EndTime,
        businessStatus, reservation, reservationTimeOptions, beCode(beType=5时)
        """
        params: dict[str, Any] = {"beType": be_type, "searchType": search_type}
        if city:
            params["city"] = city
        if keyword:
            params["keyword"] = keyword
        raw = self.call_tool("query-nearby-stores", params)
        return self._extract_data_rows(raw)

    def query_meals(
        self,
        *,
        store_code: str,
        order_type: int = 1,
        be_type: int = 1,
        be_code: str | None = None,
        reservation_date: str | None = None,
    ) -> dict[str, Any]:
        """查询门店当前可售卖餐品列表（含价格）。

        参数：
        - store_code: 门店编码（storeCode，来自 query-nearby-stores）
        - order_type: 1=到店(含自取+得来速), 2=外送(含麦乐送+企业团餐)
        - be_type: 1=到店取餐, 2=麦乐送到家, 5=得来速, 6=企业团餐
        - be_code: 业务编码（beType=5/2/6 时必传，从门店数据获取）
        - reservation_date: 预约场景必传 yyyy-MM-dd HH:mm

        返回：``{categories: [...], meals: {productCode: {name, currentPrice, originalPrice, ...}}}``
        """
        params: dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
        }
        if be_code:
            params["beCode"] = be_code
        if reservation_date:
            params["reservationDate"] = reservation_date
        raw = self.call_tool("query-meals", params)
        data = self._extract_data(raw)
        return data if isinstance(data, dict) else {}

    def query_meal_detail(self, *, store_code: str, order_type: int = 1,
                          be_type: int = 1, code: str, be_code: str | None = None,
                          **kwargs) -> dict[str, Any]:
        params: dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
            "code": code,
        }
        if be_code:
            params["beCode"] = be_code
        raw = self.call_tool("query-meal-detail", {**params, **kwargs})
        rows = self._extract_data_rows(raw)
        return rows[0] if rows else {}

    def query_store_coupons(self, *, store_code: str, order_type: int = 1,
                            be_type: int = 1, be_code: str | None = None,
                            reservation_date: str | None = None,
                            **kwargs) -> list[dict[str, Any]]:
        """查询指定门店下可使用的优惠券列表。"""
        params: dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
        }
        if be_code:
            params["beCode"] = be_code
        if reservation_date:
            params["reservationDate"] = reservation_date
        raw = self.call_tool("query-store-coupons", {**params, **kwargs})
        return self._extract_data_rows(raw)

    def available_coupons(self, **kwargs) -> list[dict[str, Any]]:
        raw = self.call_tool("available-coupons", kwargs)
        return self._extract_data_rows(raw)

    def auto_bind_coupons(self, **kwargs) -> dict[str, Any]:
        """一键领取麦麦省所有当前可用优惠券。

        返回形如 ``{successCoupons: [{couponId, couponName, couponCode, ...}, ...]}``。
        """
        raw = self.call_tool("auto-bind-coupons", kwargs)
        data = self._extract_data(raw)
        return data if isinstance(data, dict) else {"raw": raw}

    def query_my_coupons(self, *, page: str = "1", page_size: str = "200",
                         **kwargs) -> dict[str, Any]:
        """查询用户卡包中的优惠券（所有门店通用）。

        返回形如 ``{coupons: [{id, code, title, discountInfo, tradeStartDate, ...}, ...]}``。
        """
        raw = self.call_tool("query-my-coupons", {"page": page, "pageSize": page_size, **kwargs})
        data = self._extract_data(raw)
        return data if isinstance(data, dict) else {"coupons": data if isinstance(data, list) else []}

    def query_my_account(self, **kwargs) -> dict[str, Any]:
        raw = self.call_tool("query-my-account", kwargs)
        rows = self._extract_data_rows(raw)
        return rows[0] if rows else {}

    def calculate_price(self, *, store_code: str, order_type: int = 1,
                         be_type: int = 1, items: list[dict[str, Any]],
                         be_code: str | None = None,
                         coupon_ids: list[str] | None = None,
                         need_tableware: bool | None = None,
                         reservation_date: str | None = None,
                         **kwargs) -> dict[str, Any]:
        """计算商品价格（含优惠）。

        参数：
        - items: ``[{code, num, isCombo?, ...}]`` 商品列表
        - coupon_ids: 优惠券 id 列表
        - need_tableware: 是否需要餐具
        """
        params: dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
            "items": items,
        }
        if be_code:
            params["beCode"] = be_code
        if coupon_ids:
            params["couponIds"] = coupon_ids
        if need_tableware is not None:
            params["needTableware"] = need_tableware
        if reservation_date:
            params["reservationDate"] = reservation_date
        raw = self.call_tool("calculate-price", {**params, **kwargs})
        data = self._extract_data(raw)
        return data if isinstance(data, dict) else ({"data": data} if data else {})

    def create_order(self, *, store_code: str, order_type: int = 1,
                      be_type: int = 1, items: list[dict[str, Any]],
                      be_code: str | None = None, take_way_code: str | None = None,
                      address_id: str | None = None, need_tableware: bool | None = None,
                      coupon_ids: list[str] | None = None,
                      reservation_date: str | None = None,
                      remark: str | None = None, **kwargs) -> dict[str, Any]:
        """创建订单。返回订单详情与支付链接。"""
        params: dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
            "items": items,
        }
        if be_code:
            params["beCode"] = be_code
        if take_way_code:
            params["takeWayCode"] = take_way_code
        if address_id:
            params["addressId"] = address_id
        if need_tableware is not None:
            params["needTableware"] = need_tableware
        if coupon_ids:
            params["couponIds"] = coupon_ids
        if reservation_date:
            params["reservationDate"] = reservation_date
        if remark:
            params["remark"] = remark
        raw = self.call_tool("create-order", {**params, **kwargs})
        data = self._extract_data(raw)
        return data if isinstance(data, dict) else ({"data": data} if data else {})

    def order_list(self, **kwargs) -> list[dict[str, Any]]:
        raw = self.call_tool("order-list", kwargs)
        return self._extract_data_rows(raw)

    def campaign_calendar(self, **kwargs) -> list[dict[str, Any]]:
        raw = self.call_tool("campaign-calendar", kwargs)
        return self._extract_data_rows(raw)

    def now_time_info(self, **kwargs) -> dict[str, Any]:
        raw = self.call_tool("now-time-info", kwargs)
        rows = self._extract_data_rows(raw)
        return rows[0] if rows else {}

    def query_party_city(self, *, spu_id: int, **kwargs) -> list[dict[str, Any]]:
        raw = self.call_tool("query-party-city", {"spuId": spu_id, **kwargs})
        return self._extract_data_rows(raw)

    def query_party_store(self, *, code: str, spu_id: int,
                           latitude: float | None = None, longitude: float | None = None,
                           **kwargs) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"code": code, "spuId": spu_id}
        if latitude is not None:
            params["latitude"] = latitude
        if longitude is not None:
            params["longitude"] = longitude
        raw = self.call_tool("query-party-store", {**params, **kwargs})
        return self._extract_data_rows(raw)

    # ---------------------------------------------- 麦当劳自定义文本格式解析
    @staticmethod
    def _extract_data(result: dict[str, Any]) -> Any:
        """从 tools/call 的 result 中提取业务数据（保留原始结构 dict/list）。"""
        sc = result.get("structuredContent") if isinstance(result, dict) else None
        if sc and isinstance(sc, dict):
            data = sc.get("data")
            if isinstance(data, (list, dict)):
                return data
            if isinstance(data, str):
                return McpClient._parse_table_text(data)

        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list):
            for c in content:
                if not isinstance(c, dict) or c.get("type") != "text":
                    continue
                text = c.get("text", "")
                marker = "Original Response"
                idx = text.find(marker)
                if idx < 0:
                    continue
                json_text = text[idx + len(marker):].strip()
                try:
                    parsed = json.loads(json_text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    data = parsed.get("data")
                    if isinstance(data, (list, dict)):
                        return data
                    if isinstance(data, str):
                        return McpClient._parse_table_text(data)
                if isinstance(parsed, list):
                    return parsed
        return None

    # 常见的"列表型"字段名，用于从 dict 包装结果中提取 list
    _LIST_FIELDS = (
        "coupons", "successCoupons", "list", "data", "items",
        "stores", "meals", "rows", "records",
    )

    @staticmethod
    def _extract_data_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
        """兼容版：强制返回 list。dict 会被自动解包为内嵌的列表字段。"""
        data = McpClient._extract_data(result)
        if data is None:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in McpClient._LIST_FIELDS:
                v = data.get(key)
                if isinstance(v, list):
                    return v
            return [data]
        return []

    @staticmethod
    def _parse_table_text(text: str) -> list[dict[str, Any]]:
        """解析麦当劳自定义文本表格格式。

        格式::

            [N]{field1,field2,...}:
              v1,v2,...
              ...

        解析为 ``[{field1: v1, field2: v2, ...}, ...]``。
        若格式不匹配，返回空列表。
        """
        text = text.strip()
        if not text:
            return []

        # 提取字段声明
        fields: list[str] = []
        bracket_start = text.find("[")
        brace_start = text.find("{", bracket_start if bracket_start >= 0 else 0)
        brace_end = text.find("}", brace_start if brace_start >= 0 else 0)
        colon_pos = text.find(":", brace_end if brace_end >= 0 else 0)

        if brace_start >= 0 and brace_end > brace_start and colon_pos > brace_end:
            fields_str = text[brace_start + 1: brace_end]
            fields = [f.strip() for f in fields_str.split(",") if f.strip()]
        else:
            # 不是表格格式，尝试作为 JSON 解析
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parsed
                if isinstance(parsed, dict):
                    return [parsed]
            except json.JSONDecodeError:
                pass
            return []

        if not fields:
            return []

        # 解析数据行（位于冒号之后，每行以空格开头）
        rows_text = text[colon_pos + 1:]
        result = []
        for line in rows_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            values = [v.strip() for v in _split_csv_preserving_none(stripped)]
            if len(values) != len(fields):
                continue
            row: dict[str, Any] = {}
            for field, raw_val in zip(fields, values):
                row[field] = _coerce_value(raw_val)
            result.append(row)
        return result


def _split_csv_preserving_none(s: str) -> list[str]:
    """简单 CSV 切分（不处理引号转义）。"""
    return s.split(",")


def _coerce_value(raw: str) -> Any:
    """把字符串值转成合适的 Python 类型。"""
    if raw == "null" or raw == "":
        return None
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw
