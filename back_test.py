# backtest.py
import akshare as ak
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ================= 复用你的配置参数 =================
HOLD_DAYS = 7
# 权重参数 (直接复用 index.py)
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

# ================= 回测设置 =================
# 选一个波动大的标的来测试效果
# 比如：半导体ETF (512480) 或者 纳指ETF (513100)
# 注意：这里用 ETF 代码测试，因为场外基金手续费太高，无法做短线回测
TARGET_CODE = "512480" 
START_DATE = "20240101"
END_DATE = "20251231"

def get_data(code, start, end):
    print(f"⏳ 正在拉取 {code} 的历史数据...")
    try:
        # 使用 ETF 接口，数据更全
        df = ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start, end_date=end, adjust="hfq")
        df.rename(columns={'日期': 'date', '收盘': 'close'}, inplace=True)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df = df[['close']].astype(float)
        return df
    except Exception as e:
        print(f"❌ 数据获取失败: {e}")
        return None

def calc_score_for_row(current_idx, full_df):
    """
    模拟站在 current_idx 这一天，利用过去的数据计算分数
    """
    # 切片获取“过去”的数据（包含今天）
    # 我们需要至少 21 天数据来计算指标
    if current_idx < 21:
        return None
    
    # 截取直到当前行的数据
    history = full_df.iloc[:current_idx+1]['close']
    
    nav = history
    
    # 1. 计算 Ret Hold (近7日涨幅)
    ret_hold = nav.iloc[-1] / nav.iloc[-(HOLD_DAYS + 1)] - 1
    
    # 2. 计算 Ret 20 (近20日涨幅)
    ret_20 = nav.iloc[-1] / nav.iloc[-21] - 1
    
    # 3. 计算 Vol 20 & MDD 20
    daily_ret = nav.pct_change().dropna().tail(20)
    if len(daily_ret) < 2: return None
    
    vol_20 = float(daily_ret.std())
    
    window_nav = nav.tail(20)
    cummax = window_nav.cummax()
    mdd_20 = float((window_nav / cummax - 1).min())
    
    # 4. 均线与乖离
    ma_20 = float(window_nav.mean())
    bias_20 = float((nav.iloc[-1] - ma_20) / ma_20) if ma_20 else 0.0
    over_bias = max(0.0, bias_20 - BIAS_20_THRESHOLD) if ENABLE_BIAS_20_PENALTY else 0.0
    
    # 5. 上涨天数占比
    pos_ratio_20 = float((daily_ret > 0).mean())
    
    # 6. 软上限惩罚
    over_cap = max(0.0, float(ret_hold) - RET_HOLD_SOFT_CAP)

    # === 核心打分公式 ===
    score = (
        SCORE_W_RET_HOLD * float(ret_hold)
        + SCORE_W_RET_20 * float(ret_20)
        - SCORE_W_VOL_20 * vol_20
        - SCORE_W_MDD_20 * abs(mdd_20)
        + SCORE_W_POS_20 * (pos_ratio_20 - 0.5)
        - SCORE_W_RET_HOLD_CAP * over_cap
        - SCORE_W_BIAS_20 * over_bias
    )
    
    return score

def run_backtest():
    # 1. 获取数据
    df = get_data(TARGET_CODE, START_DATE, END_DATE)
    if df is None: return

    # 2. 逐日计算分数
    scores = []
    print("🔄 开始逐日计算策略分数...")
    
    # 从第 22 天开始算，因为前面数据不够算指标
    for i in range(len(df)):
        s = calc_score_for_row(i, df)
        scores.append(s if s is not None else np.nan)
        
    df['score'] = scores
    
    # 3. 计算“未来7日真实收益”（用于验证预测能力）
    # shift(-7) 表示把未来的数据拉到今天，让我们知道今天如果买入，7天后赚多少
    df['future_7d_ret'] = df['close'].shift(-HOLD_DAYS) / df['close'] - 1
    
    # 清洗数据
    df.dropna(inplace=True)
    
    # 4. 分析结果
    print("-" * 30)
    print(f"📊 回测统计 ({TARGET_CODE})")
    print(f"样本天数: {len(df)}")
    
    # 计算 IC (Information Coefficient): 分数和未来收益的相关性
    # 如果 > 0.05 说明因子有效；如果 < 0 说明是反向指标
    ic = df['score'].corr(df['future_7d_ret'])
    print(f"💡 IC值 (分数与未来7日涨跌的相关性): {ic:.4f}")
    if ic > 0.1: print("   ✅ 这是一个非常强的预测指标！")
    elif ic > 0.02: print("   ✅ 指标有效，有一定的预测能力。")
    elif ic < -0.02: print("   ⚠️ 指标失效，甚至可能是反向指标（分越高越跌）。")
    else: print("   ⚠️ 指标与未来涨跌基本无关（随机）。")

    # 5. 可视化
    plot_results(df)

def plot_results(df):
    # 设置中文字体
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS'] 
    plt.rcParams['axes.unicode_minus'] = False
    
    fig, ax1 = plt.subplots(figsize=(12, 6))

    # 画净值曲线
    color = 'tab:blue'
    ax1.set_xlabel('日期')
    ax1.set_ylabel('基金净值', color=color)
    ax1.plot(df.index, df['close'], color=color, label='净值', alpha=0.6)
    ax1.tick_params(axis='y', labelcolor=color)

    # 画分数值
    ax2 = ax1.twinx()  
    color = 'tab:orange'
    ax2.set_ylabel('策略打分', color=color)
    ax2.plot(df.index, df['score'], color=color, label='打分', linewidth=1.5)
    ax2.tick_params(axis='y', labelcolor=color)
    
    # 画一条 0 分线
    ax2.axhline(0, color='gray', linestyle='--', alpha=0.5)

    # 标记高分时刻 (买点) 和 低分时刻 (卖点/风险点)
    # 假设 score > 0.5 为高分区间 (Top candidates usually have high scores)
    high_score_mask = df['score'] > df['score'].quantile(0.90) # 前10%的高分
    low_score_mask = df['score'] < df['score'].quantile(0.10)  # 后10%的低分
    
    ax1.scatter(df.index[high_score_mask], df['close'][high_score_mask], 
                color='red', marker='^', s=50, label='高分时刻(前10%)', zorder=10)
    
    plt.title(f'策略打分 vs 基金走势 ({TARGET_CODE})')
    fig.tight_layout()
    plt.show()

if __name__ == "__main__":
    run_backtest()