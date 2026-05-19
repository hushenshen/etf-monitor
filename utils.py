from datetime import datetime, time as dt_time

def is_trading_time():
    # return True  
    """
    A股精确交易时间判断（含2026节假日）
    周一~周五 9:30-11:30  13:00-15:00
    节假日/周末 不交易
    """
    now = datetime.now()
    weekday = now.weekday()
    current_time = now.time()
    today_str = now.strftime("%Y-%m-%d")

    # 周末休市
    if weekday >= 5:
        return False

    # 2026年节假日休市（完全用你给的列表）
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

    # 交易时间段
    am_start = dt_time(9, 30)
    am_end = dt_time(11, 30)
    pm_start = dt_time(13, 0)
    pm_end = dt_time(15, 0)

    in_am = am_start <= current_time <= am_end
    in_pm = pm_start <= current_time <= pm_end

    return in_am or in_pm