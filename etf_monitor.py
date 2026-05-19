#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
场内ETF监控（仅2026交易日交易时间运行）
修复涨跌幅 + 停牌重试 + 现价/监控价百分比 + 无竖线完美对齐 + 涨跌幅彩色打印 + 前面带序号(01/02格式)
"""

import requests
import time
import yaml
import logging
import sys
import re
import os
import datetime

from datetime import datetime, time as dt_time, timedelta
from utils import is_trading_time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "etfs_to_monitor.yml")

# 终端颜色定义
RED = '\033[91m'
GREEN = '\033[92m'
RESET = '\033[0m'

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class ETFMonitor:
    def __init__(self, config):
        self.etfs = config.get("etfs", [])
        self.feishu_webhook = config.get("feishu_webhook")
        self.proxy = config.get("proxy")
        self.interval = config.get("interval", 5)
        self.suspend_retry_min = config.get("suspend_retry_min", 5)

        self.notify_record = {}
        self.fail_count = {}
        self.temp_suspended = {}

        self.session = requests.Session()
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}


    # ---------- 价格获取（修复涨跌幅） ----------
    def get_etf_price(self, code):
        price, name, change_today, open_price, pre_close = None, None, 0.0, 0.0, 0.0

        sh_prefix = ("50", "51", "56", "588")
        is_sh = code.startswith(sh_prefix)

        # 东方财富：f43现价、f58名称、f60昨收、f46开盘
        try:
            market = "1" if is_sh else "0"
            url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "secid": f"{market}.{code}",
                "fields": "f43,f58,f60,f46",
                "ut": "fa5fd1943cd70d26fd9a75910ab0195",
                "fltt": "2",
                "invt": "2",
                "wbp2u": "|0|0|0|web"
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/"
            }

            r = self.session.get(url, params=params, headers=headers, timeout=8)
            r.raise_for_status()
            data = r.json()

            if data.get("data"):
                d = data["data"]
                f43 = d.get("f43")
                f58 = d.get("f58")
                f60 = d.get("f60")
                f46 = d.get("f46")

                if f43 and f58 and f60:
                    price = float(f43) / 100.0
                    pre_close = float(f60) / 100.0
                    name = f58
                    open_price = float(f46) / 100.0 if f46 else 0.0
                    # 自己精确计算日涨跌幅
                    change_today = (price / pre_close - 1) * 100
        except Exception:
            pass

        # 新浪兜底
        if price is None or price < 0.3:
            try:
                market = "sh" if is_sh else "sz"
                url = f"https://hq.sinajs.cn/list={market}{code}"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://finance.sina.com/"
                }
                r = self.session.get(url, headers=headers, timeout=8)
                r.encoding = "gbk"

                match = re.search(r'="([^"]+)"', r.text)
                if match:
                    items = match.group(1).split(",")
                    if len(items) >= 5:
                        name = items[0]
                        open_price = float(items[1])
                        pre_close = float(items[2])
                        price = float(items[3])
                        if pre_close > 0:
                            change_today = (price / pre_close - 1) * 100
            except Exception:
                pass

        if price is None or price <= 0 or price < 0.3 or pre_close <= 0:
            return None, {}

        return price, {
            "code": code,
            "name": name or code,
            "price": round(price, 3),
            "change_today": round(change_today, 2),
            "open_price": round(open_price, 3),
            "pre_close": round(pre_close, 3),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    # ---------- 飞书通知 ----------
    def send_feishu(self, title, info, reasons, code):
        today = datetime.now().date()
        if self.notify_record.get(code) == today:
            return

        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "red"
                },
                "elements": [{
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**基金**：{info['name']}\n"
                            f"**代码**：{code}\n"
                            f"**价格**：{info['price']:.3f}\n"
                            f"**昨收**：{info['pre_close']:.3f}\n"
                            f"**日涨跌幅**：{info['change_today']:.2f}%\n"
                            f"**触发**：\n" + "\n".join(reasons)
                        )
                    }
                }]
            }
        }

        try:
            self.session.post(self.feishu_webhook, json=card, timeout=10)
            self.notify_record[code] = today
        except Exception:
            pass

    # ---------- 检查逻辑 ----------
    def check(self):
        logger.info("=============================================================================")
        now = datetime.now()

        # 带序号循环
        for idx, etf in enumerate(self.etfs, start=1):
            code = etf["code"]
            triggers = etf.get("triggers", {})
            price_below = triggers.get("price_below", 0.0)

            if code in self.temp_suspended:
                elapsed = now - self.temp_suspended[code]
                wait_sec = int(self.suspend_retry_min * 60 - elapsed.total_seconds())
                if elapsed < timedelta(minutes=self.suspend_retry_min):
                    logger.info("[%02d] %s 临时停牌中（%d秒后重试）", idx, code, max(wait_sec, 0))
                    continue

            price, info = self.get_etf_price(code)
            if not price:
                self.fail_count[code] = self.fail_count.get(code, 0) + 1
                if self.fail_count[code] >= 3:
                    if code not in self.temp_suspended:
                        self.temp_suspended[code] = now
                        logger.warning("[%02d] %s 连续3次获取失败，进入临时停牌，%d分钟重试一次", idx, code, self.suspend_retry_min)
                continue

            self.fail_count[code] = 0
            if code in self.temp_suspended:
                del self.temp_suspended[code]
                logger.info("[%02d] %s 已复牌，恢复正常监控", idx, code)

            # 计算相对监控价百分比，固定占位对齐
            if price_below and float(price_below) > 0:
                rel_pct = (price - float(price_below)) / float(price_below) * 100
                rel_str = f"{rel_pct:+.2f}%"
            else:
                rel_str = "   --   "

            # 涨跌幅颜色处理
            change = info["change_today"]
            if change > 0:
                change_color = f"{RED}{change:+5.2f}%{RESET}"
            elif change < 0:
                change_color = f"{GREEN}{change:+5.2f}%{RESET}"
            else:
                change_color = f"{change:+5.2f}%"

            # 前面带序号(01格式) + 彩色涨跌幅 + 完美对齐
            logger.info(
                "[%02d] 监:%5.3f(%7s) 当前:%5.3f %s 昨:%5.3f (%s)%s",
                idx,
                price_below,
                rel_str,
                price,
                change_color,
                info["pre_close"],
                code,
                info["name"].ljust(15)
            )

            reasons = []
            if price_below is not None:
                try:
                    if price <= float(price_below):
                        reasons.append(f"价格 ≤ {price_below}")
                except (ValueError, TypeError):
                    pass

            drop_today_below = triggers.get("drop_today_below")
            if drop_today_below is not None:
                try:
                    if info["change_today"] <= float(drop_today_below):
                        reasons.append(f"日跌幅 {info['change_today']:.2f}%")
                except (ValueError, TypeError):
                    pass

            drop_month_below = triggers.get("drop_month_below")
            if drop_month_below is not None and info.get("open_price"):
                try:
                    drop_month = (price - info["open_price"]) / info["open_price"] * 100
                    if drop_month <= float(drop_month_below):
                        reasons.append(f"月跌幅 {drop_month:.2f}%")
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

            if reasons:
                self.send_feishu(
                    f"【ETF预警】{info['name']}",
                    info,
                    reasons,
                    code
                )

            time.sleep(1)

    # ---------- 主循环 ----------
    def run(self):
        logger.info("ETF监控启动（2026 交易日 9:30-11:30/13:00-15:00）")
        while True:
            if is_trading_time():
                self.check()
                sleep_sec = self.interval
            else:
                self.temp_suspended.clear()
                self.fail_count.clear()
                # logger.info("非交易时间，休眠 60 秒...")
                sleep_sec = 60
            time.sleep(sleep_sec)

# ---------- 配置文件加载 ----------
def load_config():
    if len(sys.argv) < 2:
        print("用法：\n  --create  创建配置\n  --run     启动")
        sys.exit(1)

    if sys.argv[1] == "--create":
        yaml.safe_dump({
            "interval": 5,
            "suspend_retry_min": 5,
            "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
            "proxy": "",
            "etfs": [{
                "code": "510300",
                "name": "沪深300ETF",
                "triggers": {
                    "price_below": 4.75,
                    "drop_today_below": -1.5,
                    "drop_month_below": -5.0
                }
            }]
        }, open(CONFIG_FILE, "w", encoding="utf-8"), allow_unicode=True)
        print("✅ 已生成", CONFIG_FILE)
        sys.exit(0)

    if sys.argv[1] == "--run":
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    sys.exit(1)


if __name__ == "__main__":
    ETFMonitor(load_config()).run()