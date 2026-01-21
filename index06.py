# index.py (最终修正版 - 专治 QQ 邮箱 502 错误)
import akshare as ak
import sys
import time
import json
import pandas as pd
import smtplib
import os
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

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
    sender = os.environ.get('EMAIL_SENDER')
    password = os.environ.get('EMAIL_PASSWORD')
    receivers_str = os.environ.get('EMAIL_RECEIVERS')
    
    if not sender or not password or not receivers_str:
        print("❌ 环境变量缺失，跳过发送邮件。")
        return

    receivers = [r.strip() for r in receivers_str.split(',')]
    
    # === 关键修改：构建极简邮件对象 ===
    message = MIMEText(content, 'plain', 'utf-8')
    
    # 【重点】QQ邮箱在海外IP环境下，极度反感带有中文别名的 From 头
    # 必须保持 From 和实际发件人完全一致，不要加 "机器人 <xxx>" 这种格式
    message['From'] = sender
    
    # To 头部同理，只放邮箱地址，如果有多个收件人，用逗号连接
    message['To'] = ",".join(receivers)
    
    current_date = time.strftime("%Y-%m-%d", time.localtime())
    subject = f'基金日报 {current_date} 筛选结果'
    message['Subject'] = Header(subject, 'utf-8')

    try:
        # 1. 连接服务器
        smtp_server = "smtp.qq.com"
        server = smtplib.SMTP_SSL(smtp_server, 465)
        
        # 2. 打印调试信息 (可选)
        # server.set_debuglevel(1) 
        
        # 3. 登录并发送
        server.login(sender, password)
        server.sendmail(sender, receivers, message.as_string())
        server.quit()
        print("📧 邮件发送成功！")
    except smtplib.SMTPException as e:
        print(f"❌ 邮件发送失败: {e}")
        # 如果依然报错，那只能建议换 163 邮箱了，网易对 GitHub IP 更友好
        print("💡 建议：如果持续失败，请尝试注册一个 163 邮箱作为发件人。")

def main():
    print(f"🚀 启动选基程序...")
    current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    print(f"分析时间: {current_time_str}")
    
    try:
        rank_df = ak.fund_open_fund_rank_em(symbol="全部")
        if ENABLE_HOT_SORT:
            rank_df[SORT_KEY] = pd.to_numeric(rank_df[SORT_KEY], errors='coerce')
            rank_df.sort_values(by=SORT_KEY, ascending=False, inplace=True)

        if ENABLE_DEDUPLICATE:
            rank_df['base_name'] = rank_df['基金简称'].str.replace(r'[AC]$', '', regex=True)
            rank_df['prio'] = rank_df['基金简称'].apply(lambda x: 0 if x.endswith('C') else 1)
            rank_df.sort_values(by=['base_name', 'prio'], ascending=[True, True], inplace=True)
            rank_df.drop_duplicates(subset=['base_name'], keep='first', inplace=True)
            rank_df.drop(columns=['base_name', 'prio'], inplace=True)

        rank_df.reset_index(drop=True, inplace=True)
        top_funds = rank_df.head(TOP_COUNT)
        
    except Exception as e:
        print(f"❌ 获取榜单失败: {e}")
        return

    matches = []
    
    for index, row in top_funds.iterrows():
        code = str(row['基金代码'])
        name = row['基金简称']
        pattern = get_fund_pattern(code)
        
        if pattern:
            # 简单日志，防止GitHub Actions日志过大
            print(f"分析中: {code} - {name}")
            if pattern.startswith(TARGET_PATTERN):
                matches.append({"code": code, "name": name, "pattern": pattern})
        time.sleep(0.2)

    print("-" * 30)
    print(f"✅ 扫描结束。")
    
    # === 构建邮件内容 ===
    email_lines = []
    email_lines.append(f"分析时间: {current_time_str}")
    email_lines.append(f"目标形态: [{TARGET_PATTERN}]")
    email_lines.append("-" * 30)

    if matches:
        summary_title = f"🎉 发现 {len(matches)} 个符合条件的基金：\n"
        print(summary_title)
        email_lines.append(summary_title)
        for m in matches:
            line = f"[{m['code']}] {m['name']} | {m['pattern']}"
            print(line)
            email_lines.append(line)
    else:
        msg = "⚠️ 未发现符合该走势的基金。"
        print(msg)
        email_lines.append(msg)

    send_email("\n".join(email_lines))

if __name__ == "__main__":
    main()