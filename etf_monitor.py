#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
场内ETF监控（仅2026交易日交易时间运行）—— 支持临时停牌自动重试 + 可配置间隔
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "etfs_to_monitor.yml")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


class ETFMonitor:
    def __init__(self, config):
        self.etfs = config.get("etfs", [])
        self.feishu_webhook = config.get("feishu_webhook")
        self.proxy = config.get("proxy")
        self.interval = config.get("interval", 5)
        # 可配置：临时停牌重试间隔（分钟）
        self.suspend_retry_min = config.get("suspend_retry_min", 5)

        self.notify_record = {}
        self.fail_count = {}
        self.temp_suspended = {}  # {code: 首次失败时间}

        self.session = requests.Session()
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

    # ---------- 2026 交易日 + 交易时间判断 ----------
    def is_trading_time(self):
        """判断当前是否为 2026 年 A 股交易日 + 交易时间"""
        now = datetime.now()
        weekday = now.weekday()  # 0=周一, 6=周日
        current_time = now.time()
        today_str = now.strftime("%Y-%m-%d")

        # 1. 周末直接非交易
        if weekday >= 5:
            return False

        # 2. 2026 年 A 股休市日（交易所官方）
        holiday_list = {
            "2026-01-01", "2026-01-02", "2026-01-03",
            "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18",
            "2026-02-19", "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
            "2026-04-04", "2026-04-05", "2026-04-06",
            "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
            "2026-06-19", "2026-06-20", "2026-06-21",
            "2026-09-25", "2026-09-26", "2026-09-27",
            "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
            "2026-10-05", "2026-10-06", "2026-10-07"
        }
        if today_str in holiday_list:
            return False

        # 3. 交易时段：9:30~11:30、13:00~15:00
        am_start = dt_time(9, 30)
        am_end = dt_time(11, 30)
        pm_start = dt_time(13, 0)
        pm_end = dt_time(15, 0)

        in_am = am_start <= current_time <= am_end
        in_pm = pm_start <= current_time <= pm_end

        return in_am or in_pm

    # ---------- 价格获取 ----------
    def get_etf_price(self, code):
        price, name, change_today, open_price = None, None, 0.0, 0.0

        sh_prefix = ("50", "51", "56", "588")
        is_sh = code.startswith(sh_prefix)

        # 东方财富主源
        try:
            market = "1" if is_sh else "0"
            url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "secid": f"{market}.{code}",
                "fields": "f43,f58,f170,f46,f169",
                "ut": "fa5fd1943cd70d26fd9a75910ab0195",
                "fltt": "2",
                "invt": "2",
                "wbp2u": "|0|0|0|web"
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win664; x64) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/"
            }

            r = self.session.get(url, params=params, headers=headers, timeout=8)
            r.raise_for_status()
            data = r.json()

            if data.get("data"):
                d = data["data"]
                f43 = d.get("f43")
                f58 = d.get("f58")
                f170 = d.get("f170")
                f46 = d.get("f46")

                if f43 and f58:
                    if f43 >= 100:
                        price = f43 / 100.0
                    else:
                        price = float(f43)

                    name = f58
                    change_today = (f170 or 0) / 100.0
                    open_price = (f46 or 0) / 100.0 if f46 else 0.0
        except Exception:
            pass

        # 新浪兜底
        if price is None or price < 0.5:
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
                    if len(items) >= 4 and items[3].replace('.', '').isdigit():
                        name = items[0]
                        last_close = float(items[2])
                        price = float(items[3])
                        open_price = float(items[1])
                        if last_close > 0:
                            change_today = round((price - last_close) / last_close * 100, 2)
            except Exception:
                pass

        if price is None or price <= 0 or price < 0.3:
            return None, {}

        return price, {
            "code": code,
            "name": name or code,
            "price": round(price, 3),
            "change_today": round(change_today, 2),
            "open_price": round(open_price, 3),
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
        logger.info("================================================================")
        now = datetime.now()

        for etf in self.etfs:
            code = etf["code"]
            triggers = etf.get("triggers", {})

            # 临时停牌：按配置时间重试
            if code in self.temp_suspended:
                elapsed = now - self.temp_suspended[code]
                wait_sec = int(self.suspend_retry_min * 60 - elapsed.total_seconds())
                if elapsed < timedelta(minutes=self.suspend_retry_min):
                    logger.info("%s 临时停牌中（%d秒后重试）", code, max(wait_sec, 0))
                    continue

            # 获取价格
            price, info = self.get_etf_price(code)
            if not price:
                self.fail_count[code] = self.fail_count.get(code, 0) + 1
                if self.fail_count[code] >= 3:
                    if code not in self.temp_suspended:
                        self.temp_suspended[code] = now
                        logger.warning("%s 连续3次获取失败，进入临时停牌，%d分钟重试一次", code, self.suspend_retry_min)
                continue

            # 恢复正常
            self.fail_count[code] = 0
            if code in self.temp_suspended:
                del self.temp_suspended[code]
                logger.info("%s 已复牌，恢复正常监控", code)

            # 正常输出
            price_below = triggers.get("price_below")
            logger.info("监控价: %4.3f 当前价: %4.3f (%5.2f%%) (%s)%s ",
                        price_below,
                        price,
                        info["change_today"],
                        code,
                        info["name"].ljust(15))
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
            if self.is_trading_time():
                self.check()
                sleep_sec = self.interval
            else:
                self.temp_suspended.clear()
                self.fail_count.clear()
                logger.info("非交易时间，休眠 60 秒...")
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
            "suspend_retry_min": 5,  # 这里就是临时停牌重试分钟数
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