# ETF-LOF 监控推送工具

## 一、项目介绍

本项目用于实时监控场内 ETF 和 LOF 基金，满足触发条件时自动通过飞书推送告警消息，实现自动化盯盘、及时提醒。

### ETF 监控 (`etf_monitor.py`)

监控场内 ETF 价格与涨跌幅，触发以下任一条件时推送飞书告警：

- 基金价格低于设定的阈值
- 单日跌幅超过设定的百分比阈值
- 月度跌幅超过设定的百分比阈值

### LOF 折溢价监控 (`lof_diff_monitor.py`)

实时计算 LOF 基金的折溢价率（市价 vs 天天基金估算净值），当**溢价率低于设定门限**时推送飞书告警，帮助你及时发现溢价回落或折价扩大的情况。

## 二、使用方式

### 2.1 核心命令

两个监控脚本统一使用以下命令行参数：

```bash
# 生成配置文件模板（首次使用必执行）
python etf_monitor.py --create       # 生成 etfs_to_monitor.yml
python lof_diff_monitor.py --create  # 生成 lofs_diff_monitor.yml

# 启动监控服务（配置完成后执行）
python etf_monitor.py --run
python lof_diff_monitor.py --run
```

### 2.2 ETF 监控配置

配置文件：`etfs_to_monitor.yml`

```yaml
interval: 60
suspend_retry_min: 5
feishu_webhook: "https://open.feishu.cn/open-apis/bot/v2/hook/XXX"
proxy: ""

etfs:
  - code: "513500"
    name: "标普500ETF"
    triggers:
      price_below: 2.30        # 当前价格低于此值触发
      drop_today_below: -1.0   # 日跌幅低于此百分比触发
      drop_month_below: -5.0   # 月跌幅低于此百分比触发

  - code: "512890"
    name: "红利低波ETF"
    triggers:
      price_below: 1.16
```

### 2.3 LOF 折溢价监控配置

配置文件：`lofs_diff_monitor.yml`

```yaml
refresh_interval: 60
feishu_webhook: "https://open.feishu.cn/open-apis/bot/v2/hook/XXX"

lof_list:
  - code: "160644"             # 港美互联网
    premium_threshold: 3.0     # 溢价率低于 3% 时推送飞书告警
  - code: "161127"             # 标普生物
    premium_threshold: 2.0
  - code: "501005"             # 精准医疗
    premium_threshold: 3.0
  - code: "165520"             # 有色LOF
    premium_threshold: 2.0
  - code: "161815"             # 抗通胀LOF
    premium_threshold: 3.0
  - code: "161116"             # 黄金主题LOF
    premium_threshold: 2.0
  - code: "161725"             # 白酒LOF
    premium_threshold: 2.0
  - code: "162719"             # 石油LOF
    premium_threshold: 2.0
```

> **门限说明**：`premium_threshold` 为溢价率下限，当实时溢价率低于此值时触发飞书推送。不填或设极大值则该 LOF 不启用推送（仅控制台打印）。

## 三、容器部署

### 3.1 docker-compose 快速启动

项目提供 `compose.yml`，同时启动 ETF 监控和 LOF 折溢价监控两个服务：

```yaml
services:
  etf-monitor:
    container_name: etf-monitor
    image: deeplakehss/etf-monitor:latest
    restart: always
    volumes:
      - ./etfs_to_monitor.yml:/app/etfs_to_monitor.yml:ro
    environment:
      - TZ=Asia/Shanghai
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    command: ["python", "-u", "etf_monitor.py", "--run"]

  lof-diff-monitor:
    container_name: lof-diff-monitor
    image: deeplakehss/etf-monitor:latest
    restart: always
    volumes:
      - ./lofs_diff_monitor.yml:/app/lofs_diff_monitor.yml:ro
    environment:
      - TZ=Asia/Shanghai
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    command: ["python", "-u", "lof_diff_monitor.py", "--run"]
```

### 3.2 Docker Hub 镜像

```bash
# 拉取镜像
docker pull deeplakehss/etf-monitor:latest

# 启动 ETF 监控
docker run -d \
  -v $(pwd)/etfs_to_monitor.yml:/app/etfs_to_monitor.yml:ro \
  --name etf-monitor \
  deeplakehss/etf-monitor:latest \
  python -u etf_monitor.py --run

# 启动 LOF 折溢价监控
docker run -d \
  -v $(pwd)/lofs_diff_monitor.yml:/app/lofs_diff_monitor.yml:ro \
  --name lof-diff-monitor \
  deeplakehss/etf-monitor:latest \
  python -u lof_diff_monitor.py --run
```

## 四、注意事项

- 配置文件中的基金代码需正确填写，否则无法正常获取数据；
- 飞书机器人 webhook 地址需提前在飞书群聊中创建，确保机器人拥有群聊发送消息权限；
- 容器部署时，确保挂载的配置文件路径正确，否则容器无法读取配置；
- LOF 监控仅在 A 股交易时段（9:30-11:30 / 13:00-15:00，工作日）运行；
- 同一天同一代码的飞书告警只会推送一次，避免重复打扰。

> （注：文档部分内容可能由 AI 生成）

### 全市场溢价率扫描 (`fund_premium_scanner.py`)

扫描全市场 LOF 基金（**排除封闭式基金**），实时计算溢价率，按溢价从高到低和从低到高分别列出 Top N，并通过飞书推送报告摘要。

- **LOF 溢价率**：基于天天基金实时估算净值（gsz）计算
- 溢价率 = (交易价格 - 估算净值) / 估算净值 × 100%
- **自动排除名称含"封闭"的封闭式基金**，仅保留可交易的开放式 LOF
- 当溢价率超过设定门限时推送飞书预警
- 自动生成 Markdown 报告和 CSV 数据文件

### 2.4 全市场溢价率扫描配置

配置文件：`fund_premium_scanner.yml`

```yaml
refresh_interval: 300          # 扫描间隔（秒）
top_n: 20                      # 溢价/折价排行榜显示数量
lof_top_volume: 200            # LOF 按成交额取前 N 只（已自动排除封闭式基金）
push_premium_threshold: 10.0   # 溢价率超过此值推送预警
push_discount_threshold: -10.0 # 折价率超过此值推送预警
output_dir: "/app/output"      # 报告输出目录
feishu_webhook: "https://open.feishu.cn/open-apis/bot/v2/hook/XXX"
```

```bash
# 生成配置文件模板
python fund_premium_scanner.py --create

# 启动扫描
python fund_premium_scanner.py --run
```

### 3.3 启动全市场溢价率扫描

```bash
# docker-compose 方式（推荐）
docker compose up -d fund-premium-scanner

# docker run 方式
docker run -d \
  -v $(pwd)/fund_premium_scanner.yml:/app/fund_premium_scanner.yml:ro \
  -v $(pwd)/output:/app/output \
  --name fund-premium-scanner \
  deeplakehss/etf-monitor:latest \
  python -u fund_premium_scanner.py --run
```

> **output 目录**：扫描器会在此目录下生成 Markdown 报告和 CSV 数据文件，通过 volume 挂载持久化到宿主机。
