# index.py (带排序开关的可配置版)
import akshare as ak
import sys
import time
import json
import pandas as pd

# ================= 配置区域 =================

# 1. 扫描数量
TOP_COUNT = 300 

# 2. 目标形态 (0=跌, 1=涨，左侧为最新日期)
TARGET_PATTERN = "00011111" 

# 3. 【新增开关】 是否开启热门排序功能
# 默认顺序 (False) = 看“短线爆发”（抓取今天最强的基金）。
# 热门排序 (True) = 看“中长线趋势”（抓取过去半年最稳的基金）。

# ENABLE_HOT_SORT = False
ENABLE_HOT_SORT = True


# 4. 排序标准 (仅当 ENABLE_HOT_SORT = True 时生效)
# 可选: "近1周", "近1月", "近3月", "近6月", "近1年", "今年来"
SORT_KEY = "近6月"

# ===========================================

def get_fund_pattern(code):
    """获取形态 (返回 0/1 字符串，左边为最新)"""
    try:
        fund_df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        if len(fund_df) < 20: return None
        
        fund_df = fund_df.tail(20).copy()
        fund_df['diff'] = fund_df['单位净值'].diff()
        fund_df['pattern'] = fund_df['diff'].apply(lambda x: '1' if x > 0 else '0')
        
        p_list = fund_df['pattern'].tolist()
        p_list = p_list[1:]
        p_list.reverse() # 翻转，最新在左
        
        return "".join(p_list)
    except:
        return None

def get_fund_scale(code):
    """获取基金规模"""
    try:
        info_df = ak.fund_individual_basic_info_em(symbol=code)
        row = info_df[info_df['item'] == '资产规模']
        if row.empty:
            row = info_df[info_df['item'] == '基金规模']
        
        if not row.empty:
            return row['value'].values[0]
        else:
            return "规模未知"
    except:
        return "获取失败"

def main():
    print(f"🚀 启动选基程序...")
    print(f"🎯 目标形态: [{TARGET_PATTERN}...] (左侧代表最新)")
    
    if ENABLE_HOT_SORT:
        print(f"🔥 排序模式: 开启 (按 {SORT_KEY} 涨幅筛选 TOP {TOP_COUNT})")
    else:
        print(f"🎲 排序模式: 关闭 (使用默认榜单顺序(日增长率))")

    print("-" * 60)

    try:
        # 1. 获取全量榜单
        rank_df = ak.fund_open_fund_rank_em(symbol="全部")
        
        # 2. 根据配置决定是否排序
        if ENABLE_HOT_SORT:
            # 数据清洗：转数字
            rank_df[SORT_KEY] = pd.to_numeric(rank_df[SORT_KEY], errors='coerce')
            # 降序排列
            rank_df.sort_values(by=SORT_KEY, ascending=False, inplace=True)
            # 重置索引
            rank_df.reset_index(drop=True, inplace=True)
        
        # 3. 截取前 N 名
        top_funds = rank_df.head(TOP_COUNT)
        
    except Exception as e:
        print(f"❌ 获取榜单失败: {e}")
        return

    matches = []
    
    # 4. 循环分析
    for index, row in top_funds.iterrows():
        code = str(row['基金代码'])
        name = row['基金简称']
        
        pattern = get_fund_pattern(code)
        
        if pattern:
            scale = get_fund_scale(code)
            
            fund_data = {
                "code": code,
                "name": name,
                "pattern": pattern,
                "fund_scale": scale
            }
            
            # 如果开启了排序，可以顺便打印一下排名信息
            if ENABLE_HOT_SORT:
                fund_data["hot_rank"] = f"{SORT_KEY}第{index+1}名"

            print(json.dumps(fund_data, ensure_ascii=False))

            if pattern.startswith(TARGET_PATTERN):
                matches.append(fund_data)
        else:
            print(json.dumps({"code": code, "name": name, "error": "数据不足"}, ensure_ascii=False))
        
        time.sleep(0.2)

    print("-" * 60)
    print(f"✅ 扫描结束。")
    
    if matches:
        print(f"🎉 发现 {len(matches)} 个符合条件的基金：\n")
        for m in matches:
            print(f"[{m['code']}] {m['name']} | 规模: {m['fund_scale']}")
    else:
        print(f"⚠️ 未发现符合该走势的基金。")

if __name__ == "__main__":
    main()