# index_etf.py
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

# 1. 扫描池逻辑 (ETF 特有)
# 我们不按"6个月排名"初筛，而是按"流动性(成交额)"取前 N 个，保证买卖方便
# 然后再用策略打分筛选出强者
TOP_COUNT_LIQUIDITY = 300  # 先取成交额最大的 300 只 ETF 进入候选池
MIN_TURNOVER = 30000000    # 最小成交额过滤：3000万 (低于此流动性的不看)

# 2. 目标形态
TARGET_PATTERN = "101111" 

# 3. 策略持有参数
HOLD_DAYS = 7     # ETF 也是短线轮动
OUTPUT_TOP_N = 6  # 最终输出数量

# 4. 其他开关
ENABLE_PATTERN_FILTER = False
ENABLE_DEDUPLICATE = True    # ETF 也需要去重(避免名字相似)
ENABLE_DIVERSIFY = True      # 强烈建议开启，避免全买半导体
DIVERSIFY_MAX_PAIR_CORR = 0.80 # 稍微严格一点

# 5. 打分权重 (沿用你的逻辑)
NAV_LOOKBACK_POINTS = 90
SCORE_W_RET_HOLD = 6.0
SCORE_W_RET_20 = 2.0
SCORE_W_VOL_20 = 2.5
SCORE_W_MDD_20 = 4.0
SCORE_W_POS_20 = 0.8
RET_HOLD_SOFT_CAP = 0.12
SCORE_W_RET_HOLD_CAP = 8.0
ENABLE_BIAS_20_PENALTY = True
BIAS_20_THRESHOLD = 0.10
SCORE_W_BIAS_20 = 5.0

# 6. 过滤与大盘
FILTER_RET_HOLD_POSITIVE = True
ENABLE_MARKET_FILTER = True
MARKET_INDEX_SYMBOL = "sh000300"
MARKET_MA_WINDOW = 20
MARKET_FILTER_MODE = "warn"

# 7. 排除列表 (ETF 特有)
# 排除货币ETF、债券ETF(可选)、不知名的小微ETF
EXCLUDE_KEYWORDS = ["货币", "债", "理财", "资金"]

# ===========================================

def fetch_etf_price_df(code, lookback_points=NAV_LOOKBACK_POINTS):
    """
    【修改点】拉取 ETF 历史行情 (前复权)
    """
    try:
        # adjust='qfq' 非常重要！ETF分红如果不复权，K线会断崖下跌，导致策略误判
        df = ak.fund_etf_hist_em(symbol=code, period="daily", start_date="20200101", adjust="qfq")
        if df is None or len(df) == 0:
            return None

        df = df.tail(lookback_points).copy()

        # 标准化列名，适配后续逻辑 (将 '日期'->'净值日期', '收盘'->'单位净值')
        rename_map = {
            '日期': '净值日期',
            '收盘': '单位净值', 
            '成交量': 'vol'
        }
        df.rename(columns=rename_map, inplace=True)

        if '净值日期' in df.columns:
            df['净值日期'] = pd.to_datetime(df['净值日期'], errors='coerce')
            df = df.sort_values('净值日期')

        if '单位净值' in df.columns:
            df['单位净值'] = pd.to_numeric(df['单位净值'], errors='coerce')

        if len(df) < max(HOLD_DAYS + 1, 21):
            return None

        return df.reset_index(drop=True)
    except Exception:
        return None

# 下面这几个函数逻辑通用，直接复制即可，不需要改动
def calc_updown_pattern(fund_df, points=20):
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

def _extract_return_series(fund_df, lookback_days=60):
    if fund_df is None: return None
    df = fund_df.copy().set_index('净值日期')
    ret = df['单位净值'].pct_change().dropna()
    return ret.tail(lookback_days)

def _pair_corr(ret_a, ret_b, min_overlap=30):
    if ret_a is None or ret_b is None: return 1.0
    aligned = pd.concat([ret_a, ret_b], axis=1, join='inner').dropna()
    if len(aligned) < min_overlap: return 1.0
    return float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))

def select_diversified_top(scored_funds, returns_map, top_n=6, max_pair_corr=0.85):
    if not scored_funds: return [], []
    ordered = sorted(scored_funds, key=lambda x: x.get("score", float("-inf")), reverse=True)
    selected = []
    rejected = []

    for f in ordered:
        if len(selected) >= top_n: break
        if not selected:
            selected.append(f)
            continue
        
        corr_list = [_pair_corr(returns_map.get(f['code']), returns_map.get(s['code'])) for s in selected]
        max_corr = max(corr_list) if corr_list else 1.0
        
        if max_corr <= max_pair_corr:
            selected.append(f)
        else:
            rejected.append((f, max_corr))
            
    # 补齐逻辑
    if len(selected) < top_n:
        remaining = [f for f in ordered if f not in selected]
        for f in remaining:
            if len(selected) >= top_n: break
            selected.append(f)
            
    return selected, rejected

def get_market_regime():
    try:
        if not ENABLE_MARKET_FILTER: return None
        df = ak.stock_zh_index_daily_em(symbol=MARKET_INDEX_SYMBOL)
        if df is None: return None
        close = df['close'].iloc[-1]
        ma = df['close'].tail(MARKET_MA_WINDOW).mean()
        return {"symbol": MARKET_INDEX_SYMBOL, "close": close, "ma": ma, "risk_on": close >= ma}
    except: return None

def send_email(content):
    sender = os.environ.get('EMAIL_SENDER')
    password = os.environ.get('EMAIL_PASSWORD')
    receivers_str = os.environ.get('EMAIL_RECEIVERS')
    if not sender:
        print("❌ 无邮件配置，跳过发送")
        return
    receivers = receivers_str.split(',')
    
    current_date = time.strftime("%Y-%m-%d", time.localtime())
    msg = MIMEText(content, 'plain', 'utf-8')
    msg['From'] = formataddr(("ETF策略机器人", sender))
    msg['To'] = ",".join(receivers)
    msg['Subject'] = f'【ETF日报】{current_date} 轮动筛选结果'

    try:
        server = smtplib.SMTP_SSL("smtp.qq.com", 465)
        server.login(sender, password)
        server.sendmail(sender, receivers, msg.as_string())
        server.quit()
        print("✅ 邮件发送成功")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

# ================= 主程序 =================
def main():
    print(f"🚀 启动 ETF 选基程序...")
    result_buffer = []
    def log(text):
        print(text)
        result_buffer.append(text)

    log(f"分析时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")
    log(f"ETF候选池: 流动性前{TOP_COUNT_LIQUIDITY} | 最小成交额: {MIN_TURNOVER/10000:.0f}万")
    log("-" * 30)

    # 1. 大盘环境
    market = get_market_regime()
    if market:
        status = "风险ON (可开仓)" if market['risk_on'] else "风险OFF (谨慎)"
        log(f"大盘状态: {status} | Close: {market['close']:.2f} | MA{MARKET_MA_WINDOW}: {market['ma']:.2f}")
        if not market['risk_on'] and MARKET_FILTER_MODE == "block":
            log("🚫 触发熔断，停止扫描。")
            send_email("\n".join(result_buffer))
            return

    # 2. 获取 ETF 实时榜单（按成交额排序，作为初筛池）
    try:
        # akshare 获取所有 ETF 实时行情
        spot_df = ak.fund_etf_spot_em()
        # 过滤掉成交额太小的（防止流动性陷阱）
        spot_df = spot_df[spot_df['成交额'] >= MIN_TURNOVER]
        # 过滤掉货币/债券/理财等关键词
        mask = spot_df['名称'].apply(lambda x: not any(k in x for k in EXCLUDE_KEYWORDS))
        spot_df = spot_df[mask]
        
        # 按成交额降序取头部，保证流动性
        spot_df.sort_values(by='成交额', ascending=False, inplace=True)
        candidates = spot_df.head(TOP_COUNT_LIQUIDITY)
        
    except Exception as e:
        log(f"❌ 获取ETF榜单失败: {e}")
        return

    scored_funds = []
    returns_map = {}
    
    # 3. 循环打分
    total = len(candidates)
    for i, (index, row) in enumerate(candidates.iterrows()):
        code = str(row['代码'])
        name = row['名称']
        
        # 简单去重：只看主流宽基和行业，去除联接基金名字干扰(ETF一般不需要这步，但为了保险)
        if "联接" in name: continue

        # 进度条
        print(f"[{i+1}/{total}] 分析: {code} {name} ... ", end="", flush=True)

        # 拉取历史K线
        df = fetch_etf_price_df(code)
        if df is None:
            print("数据不足")
            continue

        # 计算得分
        score_res = calc_7d_score(df)
        if score_res is None:
            print("计算失败")
            continue
            
        score, features = score_res
        
        # 基础过滤：如果7日收益是负的，直接不要（趋势不对）
        if FILTER_RET_HOLD_POSITIVE and features['ret_hold'] <= 0:
            print("动量为负")
            continue

        print(f"得分: {score:.4f}")
        
        # 记录数据
        pattern = calc_updown_pattern(df)
        if ENABLE_DIVERSIFY:
            returns_map[code] = _extract_return_series(df)

        item = {
            "code": code,
            "name": name,
            "score": round(score, 6),
            "pattern": pattern,
            **features
        }
        scored_funds.append(item)
        time.sleep(0.1) # 防封

    # 4. 排序与分散化
    log(f"✅ 扫描结束，合格候选数: {len(scored_funds)}")
    
    if ENABLE_DIVERSIFY:
        final_list, rejected = select_diversified_top(
            scored_funds, returns_map, 
            top_n=OUTPUT_TOP_N, 
            max_pair_corr=DIVERSIFY_MAX_PAIR_CORR
        )
        if rejected:
            log(f"分散化优化: 剔除了 {len(rejected)} 只高相关ETF (如: {rejected[0][0]['name']})")
    else:
        scored_funds.sort(key=lambda x: x['score'], reverse=True)
        final_list = scored_funds[:OUTPUT_TOP_N]

    # 5. 输出结果
    if final_list:
        log(f"\n🎉 ETF 优选 Top {len(final_list)}：\n")
        for idx, f in enumerate(final_list, 1):
            log(f"{idx}. [{f['code']}] {f['name']} | Score: {f['score']:.4f}")
            log(f"   近7日: {f['ret_hold']:.2%} | 近20日: {f['ret_20']:.2%} | 回撤: {f['mdd_20']:.2%}")
    else:
        log("⚠️ 无满足条件的标的。")

    send_email("\n".join(result_buffer))

if __name__ == "__main__":
    main()