#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场 LOF 溢价率扫描器
============================
扫描全市场 LOF 基金（排除封闭式基金），实时计算溢价率，
按溢价从高到低/从低到高分别列出 Top N，并通过飞书推送报告。

核心逻辑:
1. 东方财富 API 获取 LOF 市场价
2. 天天基金实时估值(gsz) → 计算溢价率（排除名称含"封闭"的封闭式基金）
3. 溢价率 = (交易价格 - 估算净值) / 估算净值 × 100%

用法:
  python fund_premium_scanner.py --create   # 生成配置文件模板
  python fund_premium_scanner.py --run      # 启动扫描
"""

import requests
import json
import time
import yaml
import sys
import os
import logging
import pandas as pd
from datetime import datetime
from utils import is_trading_time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "fund_premium_scanner.yml")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# 终端颜色
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

FUND_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://fund.eastmoney.com/",
}


class FundPremiumScanner:
    """全市场 LOF 溢价率扫描器（排除封闭式基金）"""

    def __init__(self, config):
        self.feishu_webhook = config.get("feishu_webhook", "")
        self.refresh_interval = config.get("refresh_interval", 300)
        self.top_n = config.get("top_n", 20)
        self.lof_top_volume = config.get("lof_top_volume", 200)
        self.push_premium_threshold = config.get("push_premium_threshold", 10.0)
        self.push_discount_threshold = config.get("push_discount_threshold", -10.0)
        self.output_dir = config.get("output_dir", "/app/output")

        self.session = requests.Session()
        self.notify_record = {}  # 当日推送去重

        # 确保输出目录
        os.makedirs(self.output_dir, exist_ok=True)

    # ==================== LOF 数据获取 ====================

    def fetch_lof_spot(self):
        """获取 LOF 实时行情（市场价）- 通过东方财富 API"""
        logger.info("[1/3] 获取 LOF 实时行情...")

        url = "https://88.push2.eastmoney.com/api/qt/clist/get"
        all_data = []
        page = 1

        while True:
            params = {
                "pn": page,
                "pz": 200,
                "po": 1,
                "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2,
                "invt": 2,
                "wbp2u": "|0|0|0|web",
                "fid": "f6",
                "fs": "b:MK0404,b:MK0405,b:MK0406,b:MK0407",
                "fields": "f2,f3,f4,f5,f6,f7,f8,f12,f14,f15,f16,f17,f18,f20,f21",
                "_": int(time.time() * 1000),
            }
            try:
                resp = self.session.get(url, params=params, headers=EM_HEADERS, timeout=15)
                data = resp.json()
                if data.get("data") and data["data"].get("diff"):
                    items = data["data"]["diff"]
                    all_data.extend(items)
                    total = data["data"]["total"]
                    if page * 200 >= total:
                        break
                    page += 1
                else:
                    break
            except Exception as e:
                logger.warning("  LOF 数据获取异常: %s", e)
                break
            time.sleep(0.3)

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        col_map = {
            "f12": "代码", "f14": "名称", "f2": "最新价", "f3": "涨跌幅",
            "f4": "涨跌额", "f5": "成交量", "f6": "成交额",
            "f15": "最高", "f16": "最低", "f17": "今开", "f18": "昨收",
            "f20": "总市值", "f21": "流通市值",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        for col in ["最新价", "涨跌幅", "成交额", "昨收"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 排除封闭式基金（名称含"封闭"）
        if "名称" in df.columns:
            before = len(df)
            df = df[~df["名称"].str.contains("封闭", na=False)]
            logger.info("  排除封闭式基金: %d → %d 只", before, len(df))

        # 按成交额排序，只保留活跃的
        if "成交额" in df.columns:
            df = df.sort_values("成交额", ascending=False).head(self.lof_top_volume).reset_index(drop=True)

        logger.info("  获取到 %d 只 LOF（按成交额前 %d）", len(df), self.lof_top_volume)
        return df

    # ==================== LOF 实时估值 ====================

    def fetch_lof_nav(self, fund_codes):
        """批量获取 LOF 基金昨日净值和实时估值（天天基金接口）"""
        logger.info("[2/3] 获取 LOF 基金实时估值（共 %d 只）...", len(fund_codes))

        nav_dict = {}
        success_count = 0

        for i, code in enumerate(fund_codes):
            try:
                url = f"https://fundgz.1234567.com.cn/js/{code}.js"
                resp = self.session.get(url, headers=FUND_HEADERS, timeout=8)
                text = resp.text

                if "jsonpgz" in text:
                    json_str = text.replace("jsonpgz(", "").rstrip(");")
                    info = json.loads(json_str)
                    nav_dict[str(code)] = {
                        "单位净值": float(info.get("dwjz", 0)),
                        "估算净值": float(info.get("gsz", 0)) if info.get("gsz") else None,
                        "估算涨跌幅": float(info.get("gszzl", 0)) if info.get("gszzl") else None,
                    }
                    if nav_dict[str(code)]["估算净值"] and nav_dict[str(code)]["估算净值"] > 0:
                        success_count += 1
            except Exception:
                nav_dict[str(code)] = None

            if (i + 1) % 50 == 0:
                logger.info("  已处理 %d/%d，成功 %d", i + 1, len(fund_codes), success_count)
                time.sleep(1)
            time.sleep(0.1)

        logger.info("  成功获取 %d 只 LOF 实时估值", success_count)
        return nav_dict

    # ==================== LOF 溢价率计算 ====================

    def calculate_lof_premium(self, lof_df, nav_dict):
        """计算 LOF 溢价率"""
        logger.info("[3/3] 计算 LOF 溢价率...")

        results = []
        no_estimate_codes = []

        for _, row in lof_df.iterrows():
            code = str(row["代码"])
            name = str(row["名称"])
            price = row.get("最新价", 0)
            turnover = row.get("成交额", 0)

            if pd.isna(price) or price <= 0:
                continue

            nav_info = nav_dict.get(code)
            if not nav_info:
                no_estimate_codes.append(code)
                continue

            prev_nav = nav_info.get("单位净值", 0)
            est_nav = nav_info.get("估算净值")

            if est_nav and est_nav > 0 and price > 0:
                premium = (price / est_nav - 1) * 100
                results.append({
                    "代码": code,
                    "名称": name,
                    "类型": "LOF",
                    "最新价": round(price, 3),
                    "昨日净值": round(prev_nav, 4) if prev_nav else None,
                    "估算净值": round(est_nav, 4),
                    "溢价率(%)": round(premium, 2),
                    "估值来源": "实时估值",
                    "成交额": turnover,
                })
            else:
                no_estimate_codes.append(code)

        logger.info("  有实时估值: %d 只，无实时估值: %d 只", len(results), len(no_estimate_codes))
        return pd.DataFrame(results)

    # ==================== 飞书推送 ====================

    def send_feishu(self, title, content_md):
        """发送飞书卡片消息"""
        if not self.feishu_webhook:
            return

        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [{
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": content_md},
                }],
            },
        }

        try:
            self.session.post(self.feishu_webhook, json=card, timeout=10)
        except Exception as e:
            logger.warning("飞书推送失败: %s", e)

    def push_premium_report(self, premium_top, discount_top):
        """推送溢价率报告到飞书（每日去重）"""
        today = datetime.now().date()
        if self.notify_record.get("premium_report") == today:
            return

        # 构建溢价 Top 摘要
        premium_lines = []
        for i, (_, row) in enumerate(premium_top.head(10).iterrows(), 1):
            premium_lines.append(
                f"{i}. {row['代码']} {row['名称']} "
                f"溢价{row['溢价率(%)']:+.2f}%"
            )

        # 构建折价 Top 摘要
        discount_lines = []
        for i, (_, row) in enumerate(discount_top.head(10).iterrows(), 1):
            discount_lines.append(
                f"{i}. {row['代码']} {row['名称']} "
                f"溢价{row['溢价率(%)']:+.2f}%"
            )

        content = (
            f"**🔴 溢价率 Top 10**\n"
            + "\n".join(premium_lines)
            + f"\n\n**🟢 折价率 Top 10**\n"
            + "\n".join(discount_lines)
        )

        self.send_feishu(
            f"【溢价率扫描】{datetime.now().strftime('%m-%d %H:%M')}",
            content,
        )
        self.notify_record["premium_report"] = today

    def push_threshold_alerts(self, df):
        """推送超门限溢价/折价预警（每日去重）"""
        today = datetime.now().date()

        # 高溢价预警
        high_premium = df[df["溢价率(%)"] > self.push_premium_threshold]
        for _, row in high_premium.iterrows():
            key = f"premium_{row['代码']}"
            if self.notify_record.get(key) == today:
                continue
            self.send_feishu(
                f"【高溢价预警】{row['名称']}",
                f"**基金**：{row['名称']}\n"
                f"**代码**：{row['代码']}\n"
                f"**现价**：{row['最新价']:.3f}\n"
                f"**估值**：{row['估算净值']:.4f}\n"
                f"**溢价率**：{row['溢价率(%)']:+.2f}%\n"
                f"**预警门限**：>{self.push_premium_threshold}%",
            )
            self.notify_record[key] = today

        # 深折价预警
        deep_discount = df[df["溢价率(%)"] < self.push_discount_threshold]
        for _, row in deep_discount.iterrows():
            key = f"discount_{row['代码']}"
            if self.notify_record.get(key) == today:
                continue
            self.send_feishu(
                f"【深折价预警】{row['名称']}",
                f"**基金**：{row['名称']}\n"
                f"**代码**：{row['代码']}\n"
                f"**现价**：{row['最新价']:.3f}\n"
                f"**估值**：{row['估算净值']:.4f}\n"
                f"**溢价率**：{row['溢价率(%)']:+.2f}%\n"
                f"**预警门限**：<{self.push_discount_threshold}%",
            )
            self.notify_record[key] = today

    # ==================== 报告生成 ====================

    def generate_report(self, df):
        """生成溢价率 Markdown 报告"""
        now = datetime.now()
        report_path = os.path.join(self.output_dir, f"溢价率报告_{now.strftime('%Y%m%d_%H%M')}.md")

        premium_top = df.sort_values("溢价率(%)", ascending=False).head(self.top_n).reset_index(drop=True)
        discount_top = df.sort_values("溢价率(%)", ascending=True).head(self.top_n).reset_index(drop=True)

        lines = []
        lines.append(f"# LOF 基金溢价率扫描报告")
        lines.append(f"")
        lines.append(f"> 扫描时间: {now.strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"> 数据来源: 东方财富实时行情 + 天天基金实时估值")
        lines.append(f"> 有效基金数: {len(df)}")
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

        # 溢价排行
        lines.append(f"## 一、溢价率 Top {self.top_n}（从高到低）")
        lines.append(f"")
        lines.append(f"| 排名 | 代码 | 名称 | 最新价 | 估算净值 | 溢价率(%) | 成交额(万) |")
        lines.append(f"|:---:|:---:|:---|:---:|:---:|:---:|:---:|")
        for rank, (_, row) in enumerate(premium_top.iterrows(), 1):
            amt = row['成交额'] / 10000 if pd.notna(row['成交额']) else 0
            lines.append(
                f"| {rank} | {row['代码']} | {row['名称']} | "
                f"{row['最新价']} | {row['估算净值']} | **{row['溢价率(%)']:+.2f}%** | "
                f"{amt:.0f} |"
            )
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

        # 折价排行
        lines.append(f"## 二、折价率 Top {self.top_n}（从低到高）")
        lines.append(f"")
        lines.append(f"| 排名 | 代码 | 名称 | 最新价 | 估算净值 | 溢价率(%) | 成交额(万) |")
        lines.append(f"|:---:|:---:|:---|:---:|:---:|:---:|:---:|")
        for rank, (_, row) in enumerate(discount_top.iterrows(), 1):
            amt = row['成交额'] / 10000 if pd.notna(row['成交额']) else 0
            lines.append(
                f"| {rank} | {row['代码']} | {row['名称']} | "
                f"{row['最新价']} | {row['估算净值']} | **{row['溢价率(%)']:+.2f}%** | "
                f"{amt:.0f} |"
            )
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

        # 统计
        lines.append(f"## 三、市场统计")
        lines.append(f"")
        lines.append(f"- 总基金数: {len(df)}")
        lines.append(f"- 平均溢价率: {df['溢价率(%)'].mean():.2f}%")
        lines.append(f"- 溢价基金占比: {(df['溢价率(%)'] > 0).mean()*100:.1f}%")
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"*本报告仅供参考，不构成个人投资建议*")

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        # 保存 CSV
        csv_path = os.path.join(self.output_dir, f"溢价率数据_{now.strftime('%Y%m%d_%H%M')}.csv")
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")

        logger.info("  报告已保存: %s", report_path)
        logger.info("  数据已保存: %s", csv_path)

        return premium_top, discount_top

    # ==================== 打印终端摘要 ====================

    def print_summary(self, df, premium_top, discount_top):
        """终端彩色打印溢价率摘要"""
        print("\n" + "=" * 70)
        print(f"  全市场 LOF 溢价率扫描  {datetime.now().strftime('%H:%M:%S')}")
        print(f"  有效基金: {CYAN}{len(df)}{RESET} 只")
        print("=" * 70)

        print(f"\n  {RED}🔴 溢价率 Top {self.top_n}{RESET}")
        for i, (_, row) in enumerate(premium_top.iterrows(), 1):
            pct = row['溢价率(%)']
            color = RED if pct > 5 else (YELLOW if pct > 2 else RESET)
            print(f"    {i:2d}. {row['代码']} {row['名称'][:10]:<10} "
                  f"价格:{row['最新价']:>7.3f} 估值:{row['估算净值']:>7.4f} "
                  f"溢价:{color}{pct:+7.2f}%{RESET}")

        print(f"\n  {GREEN}🟢 折价率 Top {self.top_n}{RESET}")
        for i, (_, row) in enumerate(discount_top.iterrows(), 1):
            pct = row['溢价率(%)']
            color = GREEN if pct < -5 else (YELLOW if pct < -2 else RESET)
            print(f"    {i:2d}. {row['代码']} {row['名称'][:10]:<10} "
                  f"价格:{row['最新价']:>7.3f} 估值:{row['估算净值']:>7.4f} "
                  f"溢价:{color}{pct:+7.2f}%{RESET}")
        print("=" * 70)

    # ==================== 主扫描流程 ====================

    def scan(self):
        """执行一次完整的溢价率扫描"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("=" * 60)
        logger.info("  溢价率扫描开始 %s", now_str)
        logger.info("=" * 60)

        # 1. 获取 LOF 行情
        lof_df = self.fetch_lof_spot()

        if lof_df.empty:
            logger.warning("未获取到 LOF 行情数据")
            return

        # 2. 获取实时估值 + 3. 计算溢价率
        fund_codes = lof_df["代码"].astype(str).tolist()
        nav_dict = self.fetch_lof_nav(fund_codes)
        all_results = self.calculate_lof_premium(lof_df, nav_dict)

        if all_results.empty:
            logger.warning("未获取到有效数据")
            return

        all_results = all_results.sort_values("溢价率(%)", ascending=False).reset_index(drop=True)
        logger.info("总计有效基金: %d 只", len(all_results))

        # 4. 生成报告
        premium_top, discount_top = self.generate_report(all_results)

        # 5. 终端打印
        self.print_summary(all_results, premium_top, discount_top)

        # 6. 飞书推送
        self.push_premium_report(premium_top, discount_top)
        self.push_threshold_alerts(all_results)

    # ==================== 主循环 ====================

    def run(self):
        logger.info("全市场溢价率扫描器启动")
        logger.info("  扫描间隔: %d 秒", self.refresh_interval)
        logger.info("  Top N: %d", self.top_n)
        if self.feishu_webhook:
            logger.info("  飞书推送: 已启用")
        else:
            logger.info("  飞书推送: 未配置")

        # 首次启动：立即执行一次
        logger.info("首次启动，立即执行一次扫描...")
        self.scan()

        while True:
            if is_trading_time():
                time.sleep(self.refresh_interval)
                self.scan()
            else:
                # 非交易时间，每 60 秒检查一次
                time.sleep(60)


# ==================== 配置文件加载 ====================

def load_config():
    if len(sys.argv) < 2:
        print("用法：\n  --create  生成配置文件模板\n  --run     启动扫描")
        sys.exit(1)

    if sys.argv[1] == "--create":
        template = {
            "refresh_interval": 300,
            "top_n": 20,
            "lof_top_volume": 200,
            "push_premium_threshold": 10.0,
            "push_discount_threshold": -10.0,
            "output_dir": "/app/output",
            "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
        }
        yaml.safe_dump(template, open(CONFIG_FILE, "w", encoding="utf-8"), allow_unicode=True)
        print("已生成配置文件:", CONFIG_FILE)
        sys.exit(0)

    if sys.argv[1] == "--run":
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    sys.exit(1)


if __name__ == "__main__":
    FundPremiumScanner(load_config()).run()
