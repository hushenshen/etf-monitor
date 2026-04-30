FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y \
    curl vim\
    && rm -rf /var/lib/apt/lists/*

# Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY etf_monitor.py .

# 时区（中国）
ENV TZ=Asia/Shanghai
ENV PYTHONUNBUFFERED=1

# 运行 + 强制实时输出
CMD ["python", "-u", "etf_monitor.py", "--run"]