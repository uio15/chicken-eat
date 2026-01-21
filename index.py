# index.py (调试模式 + 最新日期在左侧版)
import akshare as ak
import sys
import time
import json # 引入json库以便打印标准格式

# ================= 配置区域 =================
# 1. 抓取数量
TOP_COUNT = 300

# 2. 目标形态 (0=跌, 1=涨)
# 【注意】：现在顺序变了！最左边 = 今天
# 例如 "000" 表示：今天跌、昨天跌、前天跌
TARGET_PATTERN = "000111111"

# ===========================================

def get_fund_pattern(code, name):
    try:
        # 获取历史净值 (参数名 symbol)
        fund_df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        
        if len(fund_df) < 20:
            return None
        
        # 取最近 20 天
        fund_df = fund_df.tail(20).copy()
        
        # 计算涨跌
        fund_df['diff'] = fund_df['单位净值'].diff()
        fund_df['pattern'] = fund_df['diff'].apply(lambda x: '1' if x > 0 else '0')
        
        # === 关键修改：翻转顺序 ===
        # 1. 先转成列表
        p_list = fund_df['pattern'].tolist()
        # 2. 去掉第一个无效值(因为diff产生NaN) - 这个通常在列表最前面(最旧的那天)
        #    但在翻转前，列表是 [旧 -> 新]，所以第一个元素是无效的
        p_list = p_list[1:]
        # 3. 翻转列表：变成 [新 -> 旧]
        p_list.reverse()
        
        # 拼接成字符串 (现在左边是最新日期)
        full_pattern = "".join(p_list)
        
        return full_pattern
        
    except Exception as e:
        # 出错时也可以打印一下，方便看原因
        # print(f"Error {code}: {e}")
        return None

def main():
    print(f"🚀 启动程序 (最新日期在左侧)...")
    print(f"🎯 寻找目标: 开头是 [{TARGET_PATTERN}] 的基金")
    print("-" * 60)

    try:
        rank_df = ak.fund_open_fund_rank_em(symbol="全部")
        top_funds = rank_df.head(TOP_COUNT)
    except Exception as e:
        print(f"❌ 获取榜单失败: {e}")
        return

    matches = []
    
    for index, row in top_funds.iterrows():
        code = str(row['基金代码'])
        name = row['基金简称']
        
        pattern = get_fund_pattern(code, name)
        
        if pattern:
            # === 修改点1：打印每一条数据 ===
            # 构造一个对象
            fund_data = {
                "code": code,
                "name": name,
                "pattern": pattern
            }
            # 打印 JSON 字符串 (ensure_ascii=False 保证中文正常显示)
            print(json.dumps(fund_data, ensure_ascii=False))

            # === 修改点2：匹配逻辑 ===
            # 因为最新日期在左边，所以我们要检查 pattern 是否以 TARGET_PATTERN "开头"
            if pattern.startswith(TARGET_PATTERN):
                matches.append(fund_data)
        else:
            # 如果获取失败，也打印一下以便知道进度
            print(json.dumps({"code": code, "name": name, "error": "获取失败或数据不足"}, ensure_ascii=False))
        
        # 稍微停顿
        time.sleep(0.2)

    print("-" * 60)
    print(f"统计：扫描 {len(top_funds)} 个，匹配到 {len(matches)} 个符合 '{TARGET_PATTERN}...' 走势的基金。")
    
    if matches:
        print("\n✅ 匹配详情:")
        for m in matches:
            print(f"[{m['code']}] {m['name']} | 走势: {m['pattern']}")

if __name__ == "__main__":
    main()