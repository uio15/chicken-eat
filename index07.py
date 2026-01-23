# index.py
import akshare as ak
import sys
import time
import json
import pandas as pd
import smtplib
import os
from email.mime.text import MIMEText
from email.utils import formataddr

# ================= 配置区域 =================

# 1. 扫描数量（候选池大小：先从榜单取前 N 个，再对这批做“7天策略打分”）
TOP_COUNT = 50 

# 2. 目标形态 (0=跌, 1=涨，左侧为最新日期)
# 1001 或 100111 (黄金 N 字底)
TARGET_PATTERN = "101111" 

# 3. 是否开启热门排序功能
ENABLE_HOT_SORT = True

# 4. 排序标准
SORT_KEY = "近6月"

# 5. 是否开启去重
ENABLE_DEDUPLICATE = True

# 6. 7天策略参数（Confirmed via 寸止）
# - 目标口径：未来 7 日收益 > 0%（无法保证，只能用历史规律与近况做打分筛选）
HOLD_DAYS = 7
# - 最终输出候选基金数量（不是仓位；Confirmed via 寸止）
OUTPUT_TOP_N = 6

# 7. 是否启用“形态过滤”（默认关闭：允许打破当前思路；Confirmed via 寸止）
ENABLE_PATTERN_FILTER = False

# 8. 拉取净值点数（用于计算近7/20日指标）
NAV_LOOKBACK_POINTS = 90

# 9. 7天策略打分权重（可按实盘反馈微调）
SCORE_W_RET_HOLD = 6.0    # 近 HOLD_DAYS 日动量（偏短线）
SCORE_W_RET_20 = 2.0      # 近 20 日趋势（防止纯噪声）
SCORE_W_VOL_20 = 2.5      # 近 20 日波动惩罚（偏稳）
SCORE_W_MDD_20 = 4.0      # 近 20 日最大回撤惩罚（控制回撤）
SCORE_W_POS_20 = 0.8      # 近 20 日上涨天数占比（稳定性）

# 10. 过滤条件（Confirmed via 寸止）
# 只保留最近 HOLD_DAYS（默认7个交易日）收益为正的候选
FILTER_RET_HOLD_POSITIVE = True

# 11. ret7 软上限（Confirmed via 寸止）
# 图片建议的“别追暴涨过头”，这里用“超过阈值就扣分”的方式实现，避免硬过滤错过趋势延续
RET_HOLD_SOFT_CAP = 0.12
SCORE_W_RET_HOLD_CAP = 8.0

# 12. Bias 乖离惩罚（Confirmed via 寸止）
# 以 20 日均线乖离率作为“过热”信号，超过阈值后扣分（防暴涨）
ENABLE_BIAS_20_PENALTY = True
BIAS_20_THRESHOLD = 0.10
SCORE_W_BIAS_20 = 5.0

# 13. 大盘环境过滤（Confirmed via 寸止）
# 用沪深300（csi000300）20日均线判断风险环境；弱势时可选择仅提示或直接停止开仓
ENABLE_MARKET_FILTER = True
MARKET_INDEX_SYMBOL = "sh000300"
MARKET_MA_WINDOW = 20
# mode: "warn"=只提示继续选; "block"=风险OFF时直接不选（提高胜率但减少机会）
MARKET_FILTER_MODE = "warn"

# ===========================================

def fetch_fund_nav_df(code, lookback_points=NAV_LOOKBACK_POINTS):
    """
    拉取基金净值走势数据，并做基础清洗（日期升序、净值转数值）
    """
    try:
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        if df is None or len(df) == 0:
            return None

        df = df.tail(lookback_points).copy()

        if '净值日期' in df.columns:
            df['净值日期'] = pd.to_datetime(df['净值日期'], errors='coerce')
            df = df.dropna(subset=['净值日期']).sort_values('净值日期')

        if '单位净值' in df.columns:
            df['单位净值'] = pd.to_numeric(df['单位净值'], errors='coerce')
            df = df.dropna(subset=['单位净值'])

        if len(df) < max(HOLD_DAYS + 1, 21):
            return None

        return df.reset_index(drop=True)
    except Exception:
        return None


def calc_updown_pattern(fund_df, points=20):
    """
    生成涨跌形态字符串（0=跌, 1=涨，左侧为最新日期）
    """
    try:
        if fund_df is None or len(fund_df) < points:
            return None
        df = fund_df.tail(points).copy()
        df['diff'] = df['单位净值'].diff()
        df['pattern'] = df['diff'].apply(lambda x: '1' if x > 0 else '0')
        p_list = df['pattern'].tolist()[1:]
        p_list.reverse()
        return "".join(p_list)
    except Exception:
        return None


def calc_7d_score(fund_df, hold_days=HOLD_DAYS):
    """
    规则打分：用近期动量 + 趋势 + 波动/回撤控制，近似筛“未来7天更可能上涨”的候选。
    返回 (score, features)；score 越大越靠前。
    """
    if fund_df is None or '单位净值' not in fund_df.columns:
        return None

    nav = fund_df['单位净值'].astype(float)
    if len(nav) < max(hold_days + 1, 21):
        return None

    ret_hold = nav.iloc[-1] / nav.iloc[-(hold_days + 1)] - 1
    ret_20 = nav.iloc[-1] / nav.iloc[-21] - 1 if len(nav) >= 21 else 0.0

    daily_ret = nav.pct_change().dropna()
    vol_20 = float(daily_ret.tail(20).std()) if len(daily_ret) >= 2 else 0.0

    window_nav = nav.tail(20)
    cummax = window_nav.cummax()
    mdd_20 = float((window_nav / cummax - 1).min()) if len(window_nav) >= 2 else 0.0

    ma_20 = float(window_nav.mean()) if len(window_nav) >= 1 else 0.0
    bias_20 = float((nav.iloc[-1] - ma_20) / ma_20) if ma_20 else 0.0
    over_bias = max(0.0, bias_20 - float(BIAS_20_THRESHOLD)) if ENABLE_BIAS_20_PENALTY else 0.0

    pos_ratio_20 = float((daily_ret.tail(20) > 0).mean()) if len(daily_ret) >= 1 else 0.0

    over_cap = max(0.0, float(ret_hold) - float(RET_HOLD_SOFT_CAP))

    score = (
        SCORE_W_RET_HOLD * float(ret_hold)
        + SCORE_W_RET_20 * float(ret_20)
        - SCORE_W_VOL_20 * vol_20
        - SCORE_W_MDD_20 * abs(mdd_20)
        + SCORE_W_POS_20 * (pos_ratio_20 - 0.5)
        - SCORE_W_RET_HOLD_CAP * over_cap
        - SCORE_W_BIAS_20 * over_bias
    )

    features = {
        "ret_hold": float(ret_hold),
        "ret_hold_over_cap": over_cap,
        "ret_20": float(ret_20),
        "vol_20": vol_20,
        "mdd_20": mdd_20,
        "pos_ratio_20": pos_ratio_20,
        "ma_20": ma_20,
        "bias_20": bias_20,
        "bias_20_over": over_bias,
    }
    return float(score), features


def get_market_regime():
    """
    获取大盘环境：用沪深300（默认 csi000300）收盘价与 MA20 判断风险 ON/OFF
    """
    try:
        if not ENABLE_MARKET_FILTER:
            return None

        df = ak.stock_zh_index_daily_em(symbol=MARKET_INDEX_SYMBOL)
        if df is None or len(df) == 0:
            return None

        df = df.copy()
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            df = df.dropna(subset=['date']).sort_values('date')

        if 'close' not in df.columns:
            return None

        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        if len(df) < int(MARKET_MA_WINDOW):
            return None

        close = float(df['close'].iloc[-1])
        ma = float(df['close'].tail(int(MARKET_MA_WINDOW)).mean())
        bias = float((close - ma) / ma) if ma else 0.0

        return {
            "symbol": MARKET_INDEX_SYMBOL,
            "close": close,
            "ma": ma,
            "bias": bias,
            "risk_on": close >= ma if ma else True,
        }
    except Exception:
        return None

def send_email(content):
    """
    发送邮件函数 (修复 502 Invalid Input 问题)
    """
    sender = os.environ.get('EMAIL_SENDER')
    password = os.environ.get('EMAIL_PASSWORD') # 注意：这里必须是QQ邮箱的授权码，不是QQ密码
    receivers_str = os.environ.get('EMAIL_RECEIVERS')
    
    if not sender or not password or not receivers_str:
        print("❌ 环境变量缺失，无法发送邮件。请检查 GitHub Secrets。")
        return

    receivers = [r.strip() for r in receivers_str.split(',')]
    
    current_date = time.strftime("%Y-%m-%d", time.localtime())
    subject = f'【基金日报】{current_date} 走势筛选结果'

    # === 构造邮件对象 ===
    msg = MIMEText(content, 'plain', 'utf-8')
    
    # 修复 1: 使用 formataddr 标准化发件人写法
    msg['From'] = formataddr(("基金分析机器人", sender))
    
    # 修复 2: 收件人头部必须包含真实邮箱，否则QQ容易报错 502
    # 如果只有一个收件人，直接放；如果有多个，用逗号连接
    msg['To'] = ",".join(receivers)
    
    msg['Subject'] = subject

    try:
        smtp_server = "smtp.qq.com"
        # QQ邮箱 SSL 端口通常是 465
        server = smtplib.SMTP_SSL(smtp_server, 465)
        
        # 打印调试信息 (GitHub Actions 日志中可见)
        print(f"🔄 正在连接 SMTP 服务器... 发送给: {receivers}")
        
        server.login(sender, password)
        server.sendmail(sender, receivers, msg.as_string())
        server.quit()
        print("✅ 邮件发送成功！")
    except smtplib.SMTPException as e:
        print(f"❌ 邮件发送失败: {e}")
    except Exception as e:
        print(f"❌ 发生未知错误: {e}")

def main():
    print(f"🚀 启动选基程序...")
    result_buffer = []
    
    def log(text):
        print(text)
        result_buffer.append(text)

    log(f"分析时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")
    log(f"候选池: {TOP_COUNT} | 持有周期: {HOLD_DAYS} 天 | 输出 TopN: {OUTPUT_TOP_N} | 形态过滤: {'开启' if ENABLE_PATTERN_FILTER else '关闭'}")
    log("-" * 30)

    market = get_market_regime()
    if market:
        market_status = "风险ON" if market.get("risk_on") else "风险OFF"
        log(
            f"大盘过滤: {market.get('symbol')} | close={market.get('close'):.2f}"
            f" | ma{MARKET_MA_WINDOW}={market.get('ma'):.2f}"
            f" | bias={market.get('bias'):.2%} | {market_status}"
        )
        if (not market.get("risk_on")) and MARKET_FILTER_MODE == "block":
            log("⚠️ 大盘处于 MA 下方：今日停止开仓（MARKET_FILTER_MODE=block）。")
            send_email("\n".join(result_buffer))
            return
    else:
        log("⚠️ 大盘过滤: 获取失败，已跳过。")

    try:
        rank_df = ak.fund_open_fund_rank_em(symbol="全部")
        if ENABLE_HOT_SORT:
            rank_df[SORT_KEY] = pd.to_numeric(rank_df[SORT_KEY], errors='coerce')

        if ENABLE_DEDUPLICATE:
            rank_df['base_name'] = rank_df['基金简称'].str.replace(r'[AC]$', '', regex=True)
            rank_df['prio'] = rank_df['基金简称'].apply(lambda x: 0 if x.endswith('C') else 1)
            rank_df.sort_values(by=['base_name', 'prio'], ascending=[True, True], inplace=True)
            rank_df.drop_duplicates(subset=['base_name'], keep='first', inplace=True)
            rank_df.drop(columns=['base_name', 'prio'], inplace=True)

        if ENABLE_HOT_SORT:
            rank_df.sort_values(by=SORT_KEY, ascending=False, inplace=True)

        rank_df.reset_index(drop=True, inplace=True)
        top_funds = rank_df.head(TOP_COUNT)
        
    except Exception as e:
        log(f"❌ 获取榜单失败: {e}")
        # 即使失败也尝试发送报错日志
        send_email("\n".join(result_buffer))
        return

    scored_funds = []

    for index, row in top_funds.iterrows():
        code = str(row['基金代码'])
        name = row['基金简称']

        fund_df = fetch_fund_nav_df(code)
        if fund_df is None:
            time.sleep(0.2)
            continue

        score_result = calc_7d_score(fund_df)
        if score_result is None:
            time.sleep(0.2)
            continue

        score, features = score_result
        if FILTER_RET_HOLD_POSITIVE and float(features.get("ret_hold", 0.0)) <= 0.0:
            time.sleep(0.2)
            continue
        pattern = calc_updown_pattern(fund_df)

        fund_data = {
            "code": code,
            "name": name,
            "score": round(score, 6),
            **features,
        }
        if pattern:
            fund_data["pattern"] = pattern

        if ENABLE_HOT_SORT:
            fund_data["hot_rank"] = f"{SORT_KEY}第{index+1}名"

        # 打印过程日志
        print(json.dumps(fund_data, ensure_ascii=False))
        scored_funds.append(fund_data)
        time.sleep(0.2)

    log("✅ 扫描结束。")

    if ENABLE_PATTERN_FILTER:
        scored_funds = [
            x for x in scored_funds
            if isinstance(x.get("pattern"), str) and x["pattern"].startswith(TARGET_PATTERN)
        ]

    scored_funds.sort(key=lambda x: x.get("score", float("-inf")), reverse=True)
    top_candidates = scored_funds[:OUTPUT_TOP_N]

    if top_candidates:
        log(f"\n🎉 Top {min(OUTPUT_TOP_N, len(top_candidates))} 候选（规则打分，score 越大越靠前）：\n")
        for i, f in enumerate(top_candidates, start=1):
            line = (
                f"{i}. [{f['code']}] {f['name']} | score={f.get('score')}"
                f" | ret{HOLD_DAYS}={f.get('ret_hold'):.4%}"
                f" | ret20={f.get('ret_20'):.4%}"
                f" | vol20={f.get('vol_20'):.4%}"
                f" | mdd20={f.get('mdd_20'):.4%}"
            )
            if ENABLE_PATTERN_FILTER and f.get("pattern"):
                line += f" | pattern={f.get('pattern')}"
            log(line)
    else:
        log("\n⚠️ 未筛到候选基金（可能是净值数据不足/接口异常/候选池过小）。")

    # === 发送邮件 ===
    email_content = "\n".join(result_buffer)
    send_email(email_content)

if __name__ == "__main__":
    main()
