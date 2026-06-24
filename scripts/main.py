#!/usr/bin/env python3
"""
AI 股票分析系统 - 云端版
每天早上 9:00 盘前分析 + 9:30 开盘异动追踪
"""

import os
import sys
import json
import re
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
import argparse

# ============================================================
# 配置
# ============================================================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
PUSHPLUS_API_URL = "https://www.pushplus.plus/send"
DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "recommendations.json")

# 北京时间
BJ_TZ = timezone(timedelta(hours=8))

# A股代码前缀映射
def get_stock_prefix(code):
    """根据股票代码判断交易所前缀"""
    code = code.strip()
    if code.startswith("6"):
        return "sh"
    elif code.startswith("0") or code.startswith("3"):
        return "sz"
    elif code.startswith("4") or code.startswith("8"):
        return "bj"
    return "sz"

# ============================================================
# 数据获取
# ============================================================

def fetch_url(url, headers=None):
    """通用 HTTP GET 请求"""
    if headers is None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"[错误] 请求失败: {e}"


def fetch_index_data():
    """获取主要指数数据"""
    indices = [
        ("sh000001", "上证指数"),
        ("sz399001", "深证成指"),
        ("sz399006", "创业板指"),
        ("sh000688", "科创50"),
    ]
    codes = ",".join([c[0] for c in indices])
    url = f"https://hq.sinajs.cn/list={codes}"

    raw = fetch_url(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0"
    })

    results = []
    for line in raw.strip().split("\n"):
        if not line.strip():
            continue
        try:
            parts = line.split('"')
            if len(parts) < 2:
                continue
            data = parts[1].split(",")
            name = data[0]
            current = data[3] if len(data) > 3 else "N/A"
            change_pct = data[5] if len(data) > 5 else "N/A"
            results.append(f"{name}: {current} ({change_pct}%)")
        except Exception:
            continue
    return results


def fetch_hot_stocks():
    """获取热门股票"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "20", "po": "1", "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2",
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18",
    }
    full_url = url + "?" + urllib.parse.urlencode(params)
    raw = fetch_url(full_url)
    try:
        data = json.loads(raw)
        stocks = data.get("data", {}).get("diff", [])[:10]
        result = []
        for s in stocks:
            name = s.get("f14", "")
            code = s.get("f12", "")
            price = s.get("f2", "N/A")
            chg_pct = s.get("f3", "N/A")
            result.append(f"{code} {name} 现价:{price} 涨幅:{chg_pct}%")
        return result
    except Exception:
        return ["获取热榜失败"]


def fetch_sector_performance():
    """获取板块涨幅排名"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "10", "po": "1", "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2",
        "fid": "f3", "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f14",
    }
    full_url = url + "?" + urllib.parse.urlencode(params)
    raw = fetch_url(full_url)
    try:
        data = json.loads(raw)
        sectors = data.get("data", {}).get("diff", [])[:5]
        result = [f"{s.get('f14','')} {s.get('f3','N/A')}%" for s in sectors]
        return result
    except Exception:
        return []


def fetch_stock_prices(codes):
    """
    批量查询股票实时价格
    codes: list of (股票名称, 股票代码)
    returns: list of "名称(代码): 价格 (涨跌幅%)"
    """
    if not codes:
        return []

    sina_codes = []
    for name, code in codes:
        prefix = get_stock_prefix(code)
        sina_codes.append(f"{prefix}{code}")

    url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
    raw = fetch_url(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0"
    })

    results = []
    lines = raw.strip().split("\n")
    for i, (name, code) in enumerate(codes):
        if i < len(lines):
            try:
                parts = lines[i].split('"')
                if len(parts) < 2:
                    results.append(f"{name}({code}): 无数据")
                    continue
                data = parts[1].split(",")
                price = data[3] if len(data) > 3 else "N/A"
                change_pct = data[5] if len(data) > 5 else "N/A"
                if change_pct and change_pct not in ("N/A", ""):
                    icon = "🟢" if float(change_pct) > 0 else ("🔴" if float(change_pct) < 0 else "⚪")
                    results.append(f"{name}({code}): {price}  {icon} {change_pct}%")
                else:
                    results.append(f"{name}({code}): {price}")
            except Exception:
                results.append(f"{name}({code}): 查询失败")
        else:
            results.append(f"{name}({code}): 无数据")
    return results


# ============================================================
# 推荐记录管理
# ============================================================

def load_recommendations():
    """加载历史推荐记录"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_recommendations(data):
    """保存推荐记录"""
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[数据] 推荐记录已保存到 {DATA_FILE}")


def extract_stock_codes(text):
    """
    从AI输出文本中提取股票名称和代码
    匹配模式: "股票名(代码)" 或 "股票名（代码）" 或 "代码 股票名"
    """
    stocks = []

    # 模式1: "名称(代码)" 或 "名称（代码）" — 代码为6位数字
    pattern1 = re.findall(r'([一-龥A-Za-z]+)[\(（](\d{6})[\)）]', text)
    for name, code in pattern1:
        if name and code:
            stocks.append((name.strip(), code))

    # 模式2: 行首的 "代码 名称" 格式
    pattern2 = re.findall(r'(?:^|\n)\s*(\d{6})\s+([一-龥]+)', text)
    for code, name in pattern2:
        # 去重
        if not any(s[1] == code for s in stocks):
            stocks.append((name.strip(), code))

    return stocks


def get_yesterday_str(today_str):
    """获取昨天的日期字符串"""
    today = datetime.strptime(today_str, "%Y-%m-%d").replace(tzinfo=BJ_TZ)
    yesterday = today - timedelta(days=1)
    # 如果是周一，往前跳到周五
    while yesterday.weekday() >= 5:  # 周六=5, 周日=6
        yesterday -= timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def build_yesterday_review(today_str):
    """
    构建昨日推荐股票的涨跌幅回顾
    """
    data = load_recommendations()

    yesterday = get_yesterday_str(today_str)
    print(f"[回顾] 查找 {yesterday} 的推荐记录")

    review_parts = []

    for period in ["morning", "opening"]:
        label = "盘前关注" if period == "morning" else "开盘异动"
        record = data.get(yesterday, {}).get(period, {})

        if not record:
            continue

        raw_text = record.get("raw_text", "")
        stocks = extract_stock_codes(raw_text)

        if not stocks:
            # 如果没解析出代码，尝试从保存的stock_list读取
            stocks = [(s.get("name",""), s.get("code","")) for s in record.get("stock_list", [])]

        if stocks:
            review_parts.append(f"\n【{label}】")
            prices = fetch_stock_prices(stocks)
            review_parts.extend([f"  {p}" for p in prices])

    if review_parts:
        return "昨日推荐回顾：" + "".join(review_parts)
    return ""


def save_today_recommendations(today_str, mode, analysis_text):
    """
    保存今日推荐记录
    """
    data = load_recommendations()
    if today_str not in data:
        data[today_str] = {}

    # 提取股票代码
    stocks = extract_stock_codes(analysis_text)

    data[today_str][mode] = {
        "raw_text": analysis_text,
        "stock_list": [{"name": s[0], "code": s[1]} for s in stocks]
    }

    save_recommendations(data)


# ============================================================
# AI 分析
# ============================================================

def call_deepseek(system_prompt, user_message):
    """调用 DeepSeek API 进行深度分析"""
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7,
        "max_tokens": 2048,
        "stream": False
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "User-Agent": "stock-analysis/1.0"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"]
            else:
                return f"[API返回异常] {json.dumps(result, ensure_ascii=False)[:500]}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"[API请求失败 HTTP {e.code}] {body[:500]}"
    except Exception as e:
        return f"[API请求异常] {str(e)}"


def morning_analysis():
    """9:00 盘前分析"""
    index_data = fetch_index_data()
    hot_stocks = fetch_hot_stocks()
    sectors = fetch_sector_performance()

    today = datetime.now(BJ_TZ)
    today_str = today.strftime("%Y年%m月%d日")
    date_str = today.strftime("%Y-%m-%d")

    # 获取昨日回顾
    yesterday_review = build_yesterday_review(date_str)

    system_prompt = """你是职业A股分析师，擅长多维度分析。

核心规则：
1. 所有分析必须基于提供的真实数据，严禁编造
2. 每只推荐股票附上明确的关注逻辑和风险提示
3. 输出格式必须严格遵循要求

分析维度：技术面、消息面、资金面、基本面"""

    user_message = f"""今天是{today_str}，请基于以下数据做盘前分析，选出3只今日值得关注的股票。

【昨日指数收盘】
{chr(10).join(index_data) if index_data else "暂无数据"}

【热门个股】
{chr(10).join(hot_stocks) if hot_stocks else "暂无数据"}

【板块表现】
{chr(10).join(sectors) if sectors else "暂无数据"}

{f"【昨日推荐回顾】\n{yesterday_review}\n" if yesterday_review else ""}
请严格按照以下格式输出：

📋 今日盘前关注（3只）

1️⃣ [股票名称/代码]
📌 关注逻辑：[具体原因，说明技术面/消息面/基本面依据]
⚠️ 风险提示：[需要关注的风险点]

2️⃣ [股票名称/代码]
📌 关注逻辑：
⚠️ 风险提示：

3️⃣ [股票名称/代码]
📌 关注逻辑：
⚠️ 风险提示：

📊 大盘研判：[对今日市场整体判断]
🎯 操作策略：[建议的仓位和操作思路]

注意：数据可能不完整，如缺乏关键信息请说明，不要编造。"""

    return call_deepseek(system_prompt, user_message), date_str


def opening_analysis():
    """9:30 开盘异动分析"""
    index_data = fetch_index_data()
    hot_stocks = fetch_hot_stocks()
    sectors = fetch_sector_performance()

    today = datetime.now(BJ_TZ)
    today_str = today.strftime("%Y年%m月%d日")
    date_str = today.strftime("%Y-%m-%d")

    system_prompt = """你是职业A股短线交易分析师，擅长捕捉开盘异动。

核心规则：
1. 所有分析必须基于提供的真实数据，严禁编造
2. 只关注开盘异动（高开、放量、快速拉升）
3. 对每只异动股给出追高风险提示
4. 输出格式必须严格遵循要求"""

    user_message = f"""今天是{today_str}，基于以下开盘数据，分析今日集合竞价阶段的异动股票，选出3只热度高、有潜力的。

【实时行情】
{chr(10).join(index_data) if index_data else "暂无数据"}

【热门个股】
{chr(10).join(hot_stocks) if hot_stocks else "暂无数据"}

【板块表现】
{chr(10).join(sectors) if sectors else "暂无数据"}

请严格按照以下格式输出：

🔥 开盘异动追踪（3只）

1️⃣ [股票名称/代码]
📊 开盘表现：[涨幅/量能特征]
📌 异动原因：[集合竞价放量/消息驱动/板块带动等]
⚠️ 注意：[追高风险提示，包括支撑位压力位]

2️⃣ [股票名称/代码]
📊 开盘表现：
📌 异动原因：
⚠️ 注意：

3️⃣ [股票名称/代码]
📊 开盘表现：
📌 异动原因：
⚠️ 注意：

💡 提示：开盘异动股波动较大，建议观察15-30分钟确认趋势后再做决策。

注意：数据可能不完整，如缺乏关键信息请说明，不要编造。"""

    return call_deepseek(system_prompt, user_message), date_str


# ============================================================
# 微信推送
# ============================================================

def push_to_wechat(title, content):
    """通过 PushPlus 推送到微信"""
    payload = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "txt",
        "channel": "wechat"
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        PUSHPLUS_API_URL,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            code = result.get("code", -1)
            if code == 200:
                print(f"[推送成功] {title}")
                return True
            else:
                print(f"[推送失败] code={code} msg={result.get('msg','')}")
                return False
    except Exception as e:
        print(f"[推送异常] {e}")
        return False


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="AI 股票分析系统")
    parser.add_argument("--mode", required=True,
                        choices=["morning", "opening"],
                        help="分析模式: morning(9:00盘前) / opening(9:30开盘)")
    args = parser.parse_args()

    today = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    now_str = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== [{now_str}] AI 股票分析系统 - {args.mode} 模式 ===")

    # 检查 API 配置
    if not DEEPSEEK_API_KEY:
        print("[错误] 未设置 DEEPSEEK_API_KEY")
        sys.exit(1)
    if not PUSHPLUS_TOKEN:
        print("[错误] 未设置 PUSHPLUS_TOKEN")
        sys.exit(1)

    # 执行分析
    print("[步骤1] 获取市场数据...")
    print("[步骤2] AI 深度分析中（约30秒）...")

    if args.mode == "morning":
        title = f"📋 {today} 盘前关注股票"
        analysis, date_str = morning_analysis()
    else:
        title = f"🔥 {today} 开盘异动追踪"
        analysis, date_str = opening_analysis()

    print(f"[步骤3] 分析完成，保存记录 & 推送...")
    print(f"\n{'='*60}\n{analysis}\n{'='*60}")

    # 保存推荐记录
    save_today_recommendations(date_str, args.mode, analysis)

    # 推送到微信
    success = push_to_wechat(title, analysis)

    if success:
        print("[完成] ✅ 推送成功！请查看微信")
    else:
        print("[完成] ⚠️ 分析已完成，但微信推送失败")


if __name__ == "__main__":
    main()
