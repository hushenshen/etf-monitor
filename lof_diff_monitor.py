import requests
import json
import time
import yaml
import sys
import os
from datetime import datetime
from utils import is_trading_time


# ===================== 读取 YML 配置 =====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "lofs_diff_monitor.yml")


def parse_lof_list(raw_list):
    """解析 LOF 列表，兼容新旧两种格式：
    旧格式（纯代码列表）：
      - "160644"
    新格式（含门限）：
      - code: "160644"
        premium_threshold: 3.0
    未设门限的代码默认 999（不触发推送）。
    """
    items = []
    for entry in raw_list:
        if isinstance(entry, dict):
            items.append({
                "code": str(entry["code"]),
                "premium_threshold": float(entry.get("premium_threshold", 999)),
            })
        else:
            items.append({
                "code": str(entry),
                "premium_threshold": 999,
            })
    return items


def create_config():
    """生成配置文件模板"""
    template = {
        "refresh_interval": 60,
        "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/your-hook-token",
        "lof_list": [
            {"code": "160644", "premium_threshold": 3.0},  # 港美互联网
            {"code": "161127", "premium_threshold": 2.0},  # 标普生物
            {"code": "501005", "premium_threshold": 3.0},  # 精准医疗
            {"code": "165520", "premium_threshold": 2.0},  # 有色LOF
            {"code": "161815", "premium_threshold": 3.0},  # 抗通胀LOF
            {"code": "161116", "premium_threshold": 2.0},  # 黄金主题LOF
            {"code": "161725", "premium_threshold": 2.0},  # 白酒LOF
            {"code": "162719", "premium_threshold": 2.0},  # 石油LOF
        ],
    }
    yaml.safe_dump(template, open(CONFIG_FILE, "w", encoding="utf-8"), allow_unicode=True)
    print("✅ 已生成", CONFIG_FILE)


def load_config():
    if len(sys.argv) < 2:
        print("用法：\n  --create  创建配置\n  --run     启动监控")
        sys.exit(1)

    if sys.argv[1] == "--create":
        create_config()
        sys.exit(0)

    if sys.argv[1] == "--run":
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    sys.exit(1)


config = load_config()

LOF_ITEMS = parse_lof_list(config["lof_list"])
REFRESH_INTERVAL = config["refresh_interval"]
FEISHU_WEBHOOK = config.get("feishu_webhook", "")

# 飞书推送去重：同一天同一代码只推送一次
notify_record = {}
# ========================================================


def get_fund(fund_code):
    try:
        url = f"http://fundgz.1234567.com.cn/js/{fund_code}.js"
        resp = requests.get(url, timeout=8)
        text = resp.text.replace("jsonpgz(", "").replace(");", "")
        data = json.loads(text)
        return {
            "name": data["name"],
            "nav": float(data["dwjz"]),
            "estimate": float(data["gsz"]),
        }
    except:
        return None


def get_live_price(code):
    try:
        market = "0" if not code.startswith("50") else "1"
        url = f"http://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=f2&secids={market}.{code}"
        resp = requests.get(url, timeout=3)
        data = resp.json()
        price = data["data"]["diff"][0]["f2"]
        if price != "-" and float(price) > 0:
            return float(price)
    except:
        pass

    try:
        prefix = "sz" if not code.startswith("50") else "sh"
        url = f"http://qt.gtimg.cn/q={prefix}{code}"
        resp = requests.get(url, timeout=3)
        arr = resp.text.split("~")
        return float(arr[3])
    except:
        return None


def send_feishu(code, name, price, estimate, nav, rate, threshold):
    """溢价低于门限时推送飞书卡片通知"""
    today = datetime.now().date()
    if notify_record.get(code) == today:
        return

    if not FEISHU_WEBHOOK:
        return

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"【LOF溢价预警】{name}"},
                "template": "orange",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**基金**：{name}\n"
                            f"**代码**：{code}\n"
                            f"**现价**：{price:.3f}\n"
                            f"**估值**：{estimate:.3f}\n"
                            f"**净值**：{nav:.3f}\n"
                            f"**溢价率**：{rate:+.2f}%\n"
                            f"**预警门限**：<{threshold}%"
                        ),
                    },
                }
            ],
        },
    }

    try:
        requests.post(FEISHU_WEBHOOK, json=card, timeout=10)
        notify_record[code] = today
    except Exception as e:
        print(f"  ⚠️ 飞书推送失败 [{code}]：{e}")


# ===================== 主程序 =====================
print("✅ LOF折溢价监控【飞书预警版】启动成功！")
if FEISHU_WEBHOOK:
    print("📡 飞书推送已启用")
else:
    print("⚠️ 未配置 feishu_webhook，仅打印监控")


def run_check():
    """执行一次完整的折溢价检查"""
    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n====== {now} ======")
    print(f"{'现价':>5}{'估值':>7}{'净值':>8}{'折溢价':>7}{'门限':>6}{'代码':>6}{'名称':>9}")

    for item in LOF_ITEMS:
        code = item["code"]
        threshold = item["premium_threshold"]

        fund = get_fund(code)
        price = get_live_price(code)

        if not fund or not price:
            print(f"⏳ 数据暂未更新                                  {code}")
            continue

        nav = fund["nav"]
        estimate = fund["estimate"]
        rate = (price / estimate - 1) * 100

        # 门限显示：<999 表示有效门限，否则显示 --
        threshold_str = f"<{threshold}%" if threshold < 999 else "--"

        # 溢价低于门限 → 推送飞书（先判断，以便同行显示推送标记）
        will_push = (threshold < 999 and rate < threshold
                     and notify_record.get(code) != datetime.now().date())
        push_indicator = "  📤 已推送" if will_push else ""

        print(
            f"{price:>8.3f} "
            f"{estimate:>8.3f} "
            f"{nav:>8.3f} "
            f"{rate:>+8.2f}% "
            f"{threshold_str:>8}  "
            f"{code}  {fund['name']}{push_indicator}"
        )

        if will_push:
            send_feishu(code, fund["name"], price, estimate, nav, rate, threshold)


# 首次启动：无论是否交易时间，立即执行一次检查
print("首次启动，立即执行一次检查...")
run_check()

while True:
    # ========== 交易时间判断：只有交易时间才执行监控 ==========
    if not is_trading_time():
        time.sleep(60)
        continue

    run_check()
    time.sleep(REFRESH_INTERVAL)
