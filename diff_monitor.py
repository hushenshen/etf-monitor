import requests
import json
import time
from datetime import datetime

# ===================== 配置 =====================
LOF_LIST = [
    "160644",
    "161226",
    "161720",
    "164906",
    "501016",
]

REFRESH_INTERVAL = 10
# ==================================================

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

# ===================== 主程序 =====================
print("✅ LOF折溢价监控【真实干净版】启动成功！")

while True:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n====== {now} ======")

    for code in LOF_LIST:
        fund = get_fund(code)
        price = get_live_price(code)

        # 只有数据完全正常才显示
        if not fund or not price:
            print(f"⏳ {code} 数据暂未更新")
            continue

        nav = fund["nav"]
        estimate = fund["estimate"]
        rate = (price / estimate - 1) * 100

        print(f"[{code}] {fund['name'][:28]:<28} | 现价:{price:>6.3f} | 估值:{estimate:>6.3f} | 净值:{nav:>6.3f} | 折溢价:{rate:>6.2f}%")

    time.sleep(REFRESH_INTERVAL)