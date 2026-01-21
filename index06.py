# index.py (去重版 + 恢复详细日志 + 邮件发送)
import akshare as ak
import sys
import time
import json
import pandas as pd
import smtplib
import os
from email.mime.text import MIMEText
from email.header import Header

# ================= 配置区域 =================

# 1. 扫描数量
TOP_COUNT = 50 

# 2. 目标形态 (0=跌, 1=涨，左侧为最新日期)
# 含义：最近3天跌，紧接着前5天是涨 (3跌5涨)
TARGET_PATTERN = "00011111" 

# 3. 是否开启热门排序功能
ENABLE_HOT_SORT = True

# 4. 排序标准
SORT_KEY = "近6月"

# 5. 是否开启去重 (同名基金 A/C 只保留一个)
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

def send_email(content):
    """
    发送邮件函数
    """
    # 从 GitHub Secrets 环境变量中读取配置
    sender = os.environ.get('EMAIL_SENDER')
    password = os.environ.get('EMAIL_PASSWORD')
    receivers_str = os.environ.get('EMAIL_RECEIVERS')
    
    # 如果本地运行没有配置环境变量，则仅打印提示，不报错退出
    if not sender or not password or not receivers_str:
        print("❌ 环境变量缺失 (EMAIL_SENDER/PASSWORD/RECEIVERS)，跳过发送邮件。")
        return

    receivers = [r.strip() for r in receivers_str.split(',')]
    
    message = MIMEText(content, 'plain', 'utf-8')
    message['From'] = Header(f"基金策略机器人 <{sender}>", 'utf-8')
    message['To'] =  Header("订阅者", 'utf-8')
    
    current_date = time.strftime("%Y-%m-%d", time.localtime())
    subject = f'【基金日报】{current_date} 形态筛选结果'
    message['Subject'] = Header(subject, 'utf-8')

    try:
        # SMTP 配置 (默认使用 QQ 邮箱配置，如是用 163 请改为 smtp.163.com)
        smtp_server = "smtp.qq.com" 
        server = smtplib.SMTP_SSL(smtp_server, 465) 
        server.login(sender, password)
        server.sendmail(sender, receivers, message.as_string())
        server.quit()
        print("📧 邮件发送成功！")
    except smtplib.SMTPException as e:
        print(f"❌ 邮件发送失败: {e}")

def main():
    print(f"🚀 启动选基程序...")
    current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    print(f"分析时间: {current_time_str}")
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
        
        # 2. 预处理数据
        if ENABLE_HOT_SORT:
            rank_df[SORT_KEY] = pd.to_numeric(rank_df[SORT_KEY], errors='coerce')

        # === 核心去重逻辑 ===
        if ENABLE_DEDUPLICATE:
            rank_df['base_name'] = rank_df['基金简称'].str.replace(r'[AC]$', '', regex=True)
            rank_df['prio'] = rank_df['基金简称'].apply(lambda x: 0 if x.endswith('C') else 1)
            rank_df.sort_values(by=['base_name', 'prio'], ascending=[True, True], inplace=True)
            rank_df.drop_duplicates(subset=['base_name'], keep='first', inplace=True)
            rank_df.drop(columns=['base_name', 'prio'], inplace=True)

        # === 排序逻辑 ===
        if ENABLE_HOT_SORT:
            rank_df.sort_values(by=SORT_KEY, ascending=False, inplace=True)

        rank_df.reset_index(drop=True, inplace=True)
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
            
            if ENABLE_HOT_SORT:
                fund_data["hot_rank"] = f"{SORT_KEY}第{index+1}名"

            # === 关键点：恢复详细日志打印 ===
            # 这里打印到控制台，您在 GitHub Actions 的 logs 页面能看到详细过程
            print(json.dumps(fund_data, ensure_ascii=False))

            if pattern.startswith(TARGET_PATTERN):
                matches.append(fund_data)
        else:
            print(json.dumps({"code": code, "name": name, "error": "数据不足"}, ensure_ascii=False))
        
        time.sleep(0.2)

    print("-" * 60)
    print(f"✅ 扫描结束。")
    
    # === 准备邮件内容 ===
    # 初始化邮件正文列表
    email_lines = []
    email_lines.append(f"分析时间: {current_time_str}")
    email_lines.append(f"目标形态: [{TARGET_PATTERN}]")
    email_lines.append("-" * 30)

    if matches:
        summary_title = f"🎉 发现 {len(matches)} 个符合条件的基金 (已去重)：\n"
        print(summary_title) # 打印到控制台
        email_lines.append(summary_title) # 添加到邮件

        for m in matches:
            # 格式化输出
            result_line = f"[{m['code']}] {m['name']} | {m['pattern']}"
            print(result_line) # 打印到控制台
            email_lines.append(result_line) # 添加到邮件
    else:
        no_result_msg = f"⚠️ 未发现符合该走势的基金。"
        print(no_result_msg)
        email_lines.append(no_result_msg)

    # === 发送邮件 ===
    # 将列表拼接成字符串发送
    full_email_content = "\n".join(email_lines)
    send_email(full_email_content)

if __name__ == "__main__":
    main()