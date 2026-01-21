# index.py (去重版 + 优先保留C类)
import akshare as ak
import sys
import time
import json
import pandas as pd

# ================= 配置区域 =================

# 1. 扫描数量
TOP_COUNT = 50 

# 2. 目标形态 (0=跌, 1=涨，左侧为最新日期)
TARGET_PATTERN = "00011111" 

# 3. 是否开启热门排序功能
# 默认顺序 (False) = 看“短线爆发”（抓取今天最强的基金）。
# 热门排序 (True) = 看“中长线趋势”（抓取过去半年最稳的基金）。
ENABLE_HOT_SORT = True

# 4. 排序标准 (仅当 ENABLE_HOT_SORT = True 时生效)
SORT_KEY = "近6月"

# 5. 是否开启去重 (同名基金 A/C 只保留一个)
# True  = 开启 (优先保留 C 类)
# False = 关闭 (A和C都显示)
ENABLE_DEDUPLICATE = True

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

def main():
    print(f"🚀 启动选基程序...")
    print(f"🎯 目标形态: [{TARGET_PATTERN}...] (左侧代表最新)")
    
    # 打印配置状态
    sort_status = f"开启 (按{SORT_KEY})" if ENABLE_HOT_SORT else "关闭"
    dedup_status = "开启 (优先保留C类)" if ENABLE_DEDUPLICATE else "关闭"
    
    print(f"🔥 热门排序: {sort_status}")
    print(f"✂️  同名去重: {dedup_status}")
    print("-" * 60)

    try:
        # 1. 获取全量榜单
        rank_df = ak.fund_open_fund_rank_em(symbol="全部")
        
        # 2. 预处理数据（转数字）
        if ENABLE_HOT_SORT:
            rank_df[SORT_KEY] = pd.to_numeric(rank_df[SORT_KEY], errors='coerce')

        # === 核心去重逻辑 ===
        if ENABLE_DEDUPLICATE:
            # 1. 生成“基础名称”：去掉结尾的 A 或 C
            # 正则逻辑：如果结尾是 A 或 C，就替换为空
            rank_df['base_name'] = rank_df['基金简称'].str.replace(r'[AC]$', '', regex=True)
            
            # 2. 设置优先级：为了让 C 排在 A 前面被保留
            # 我们创建一个临时列 'prio'：如果是 C 结尾，得分 0 (排前面)，否则得分 1
            rank_df['prio'] = rank_df['基金简称'].apply(lambda x: 0 if x.endswith('C') else 1)
            
            # 3. 先按 [基础名称, 优先级] 排序
            # 这样对于同一组，顺序变成了：[某某混合C, 某某混合A]
            rank_df.sort_values(by=['base_name', 'prio'], ascending=[True, True], inplace=True)
            
            # 4. 执行去重：对 base_name 相同的，只保留第一条 (也就是 C)
            rank_df.drop_duplicates(subset=['base_name'], keep='first', inplace=True)
            
            # 5. 清理临时列
            rank_df.drop(columns=['base_name', 'prio'], inplace=True)

        # === 排序逻辑 ===
        if ENABLE_HOT_SORT:
            # 按涨幅降序
            rank_df.sort_values(by=SORT_KEY, ascending=False, inplace=True)
        else:
            # 如果没开热门排序，但开了去重，顺序可能乱了，这里尽量保持原样或按日增长率
            # 简单起见，如果不开热门排序，这里就不做额外操作，保留去重后的自然顺序
            pass

        # 重置索引
        rank_df.reset_index(drop=True, inplace=True)
        
        # 3. 截取前 N 名
        # 注意：现在截取的 TOP 50 是“去重后的”前 50，含金量更高
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
            fund_data = {
                "code": code,
                "name": name,
                "pattern": pattern
            }
            
            # 如果开启了排序，打印排名信息
            if ENABLE_HOT_SORT:
                fund_data["hot_rank"] = f"{SORT_KEY}第{index+1}名"

            print(json.dumps(fund_data, ensure_ascii=False))

            if pattern.startswith(TARGET_PATTERN):
                matches.append(fund_data)
        else:
            print(json.dumps({"code": code, "name": name, "error": "数据不足"}, ensure_ascii=False))
        
        # 0.2秒停顿是必须的，防止被封IP
        time.sleep(0.2)

    print("-" * 60)
    print(f"✅ 扫描结束。")
    
    if matches:
        print(f"🎉 发现 {len(matches)} 个符合条件的基金 (已去重)：\n")
        for m in matches:
            # 简洁输出
            print(f"[{m['code']}] {m['name']}")
    else:
        print(f"⚠️ 未发现符合该走势的基金。")

if __name__ == "__main__":
    main()