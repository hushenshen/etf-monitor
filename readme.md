# ETF\-LOF 监控推送工具 README

# ETF\-LOF 监控推送工具

## 一、项目介绍

本项目用于实时监控场内 ETF（交易型开放式指数基金）或 LOF（上市型开放式基金），当监控标的满足以下任一条件时，会自动通过飞书推送告警消息至指定群聊，实现自动化盯盘、及时提醒：

- 基金价格低于设定的阈值

- 基金单日跌幅超过设定的百分比阈值

- 基金月度跌幅超过设定的百分比阈值

## 二、使用方式

### 2\.1 核心命令

```bash
# 生成监控配置文件（首次使用必执行）
--create

# 启动监控服务（配置完成后执行）
--run
```

### 2\.2 配置说明

1\. 执行 `--create` 命令后，会在当前目录自动生成 `etfs_to_monitor.yml` 配置文件；

2\. 配置文件包含以下核心内容，可手动编辑调整：

- 待监控的 ETF/LOF 基金列表（需填写正确的场内代码）

- 价格告警阈值（低于该价格触发推送）

- 日跌幅告警阈值（超过该百分比触发推送）

- 月跌幅告警阈值（超过该百分比触发推送）

- 飞书群推送相关配置（需自行补充飞书机器人密钥、群聊ID）

3\. 配置示例（etfs\_to\_monitor\.yml 模板）：

```yaml
interval: 60
feishu_webhook: "https://open.feishu.cn/open-apis/bot/v2/hook/XXX"
proxy: ""

etfs:
  - code: "513500"
    name: "标普500ETF"
    triggers:
      price_below: 2.30       # 当前价格低于
      drop_today_below: -1.0   # 当日跌幅低于（%）
      drop_month_below: -5.0   # 当月跌幅低于（%）

  - code: "512890"
    name: "红利低波ETF"
    triggers:
      price_below: 1.16

```

## 三、容器部署

### 3\.1 docker\-compose 快速启动示例

项目提供 `compose.yml` 编排文件，可直接用于启动容器，无需复杂配置，示例如下：

```yaml
version: "3.9"

services:
  etf-monitor:
    container_name: deeplakehss/etf-monitor:latest
    restart: unless-stopped
    volumes:
      - ./etfs_to_monitor.yml:/app/etfs_to_monitor.yml:ro
      - ./logs:/app/logs
    environment:
      - TZ=Asia/Shanghai
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

### 3\.2 Docker Hub 镜像说明

本项目已打包推送至 Docker Hub，可直接拉取镜像部署，无需本地构建，镜像信息如下：

```bash
# 拉取镜像
docker pull deeplakehss/etf-monitor:latest

# 直接运行容器（需提前准备好 etfs_to_monitor.yml 配置文件）
docker run -d -v $(pwd)/etfs_to_monitor.yml:/app/etfs_to_monitor.yml --name etf-monitor deeplakehss/etf-monitor:latest --run
```

## 四、注意事项

- 配置文件 `etfs_to_monitor.yml` 需正确填写基金代码，否则无法正常获取基金数据；

- 飞书机器人 webhook 地址需提前在飞书群聊中创建，确保机器人拥有群聊发送消息权限；

- 容器部署时，需确保挂载的配置文件路径正确，否则容器无法读取配置，导致监控失败；

- 若需调整监控频率、推送格式，可修改项目配置文件或容器启动参数（具体可参考项目源码）。

> （注：文档部分内容可能由 AI 生成）
