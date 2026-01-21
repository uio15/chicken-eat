# index.py (自动发送邮件版)
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
# 默认顺序 (False) = 看“短线爆发”（抓取今天最强的基金）。
# 热门排序 (True)  = 看“中长线趋势”（抓取过去半年最稳的基金）。
ENABLE_HOT_SORT = True

# 4. 排序标准 (仅当 ENABLE_HOT_SORT = True 时生效)
SORT_KEY = "近6月"

# 5. 是否开启去重 (同名基金 A/C 只保留一个)
# True  = 开启 (优先保留 C 类)
# False = 关闭 (A和C都显示)
ENABLE_DEDUPLICATE = True

# ===========================================

def get_fund_pattern(code):
    try:
        fund_df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        if len(fund_df) < 20: return None
        fund_df = fund_df.tail(20).copy()
        fund_df['diff'] = fund_df['单位净值'].diff()
        fund_df['pattern'] = fund_df['diff'].apply(lambda x: '1' if x > 0 else '0')
        p_list = fund_df['pattern'].tolist()[1:]
        p_list.reverse() 
        return "".join(p_list)
    except:
        return None

def send_email(content):
    """
    发送邮件函数，读取 GitHub Secrets 注入的环境变量
    """
    sender = os.environ.get('EMAIL_SENDER')
    password = os.environ.get('EMAIL_PASSWORD')
    receivers_str = os.environ.get('EMAIL_RECEIVERS')
    
    if not sender or not password or not receivers_str:
        print("❌ 环境变量缺失，无法发送邮件。请检查 GitHub Secrets。")
        return

    # 处理收件人，支持逗号分隔
    receivers = [r.strip() for r in receivers_str.split(',')]
    
    # 邮件内容设置
    message = MIMEText(content, 'plain', 'utf-8')
    message['From'] = Header(f"基金分析机器人 <{sender}>", 'utf-8')
    message['To'] =  Header("订阅者", 'utf-8')
    
    current_date = time.strftime("%Y-%m-%d", time.localtime())
    subject = f'【基金日报】{current_date} 走势筛选结果'
    message['Subject'] = Header(subject, 'utf-8')

    try:
        # === SMTP 配置 (请根据您的邮箱服务商修改) ===
        # QQ邮箱/163邮箱通常使用 465 端口 (SSL)
        # 如果是 QQ 邮箱: smtp_server = "smtp.qq.com"
        # 如果是 163 邮箱: smtp_server = "smtp.163.com"
        # 这里我默认写 smtp.qq.com，如果您用的是别的，请修改下面这一行：
        smtp_server = "smtp.qq.com" 
        
        server = smtplib.SMTP_SSL(smtp_server, 465) 
        server.login(sender, password)
        server.sendmail(sender, receivers, message.as_string())
        server.quit()
        print("✅ 邮件发送成功！")
    except smtplib.SMTPException as e:
        print(f"❌ 邮件发送失败: {e}")

def main():
    print(f"🚀 启动选基程序...")
    
    # 用于收集输出结果的字符串
    result_buffer = []
    
    def log(text):
        print(text)
        result_buffer.append(text)

    log(f"分析时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")
    log(f"目标形态: [{TARGET_PATTERN}] (左侧代表最新)")
    log("-" * 30)

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
        return

    matches = []
    
    for index, row in top_funds.iterrows():
        code = str(row['基金代码'])
        name = row['基金简称']
        pattern = get_fund_pattern(code)
        
        if pattern:
            # print(f"分析中: {name}") # 减少日志输出，避免邮件过长
            if pattern.startswith(TARGET_PATTERN):
                matches.append({"code": code, "name": name, "pattern": pattern})
        time.sleep(0.2)

    log(f"✅ 扫描结束。")
    
    if matches:
        log(f"\n🎉 发现 {len(matches)} 个符合 [{TARGET_PATTERN}] 走势的基金：\n")
        for m in matches:
            line = f"[{m['code']}] {m['name']} | {m['pattern']}"
            log(line)
    else:
        log(f"\n⚠️ 未发现符合该走势的基金。")

    # === 发送邮件 ===
    email_content = "\n".join(result_buffer)
    # 无论有没有结果，都发邮件通知（如果不希望没结果时发邮件，可以加 if matches: 判断）
    send_email(email_content)

if __name__ == "__main__":
    main()