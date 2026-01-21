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

# 1. 扫描数量
TOP_COUNT = 50 

# 2. 目标形态 (0=跌, 1=涨，左侧为最新日期)
TARGET_PATTERN = "00011111" 

# 3. 是否开启热门排序功能
ENABLE_HOT_SORT = True

# 4. 排序标准
SORT_KEY = "近6月"

# 5. 是否开启去重
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
        # 即使失败也尝试发送报错日志
        send_email("\n".join(result_buffer))
        return

    matches = []
    
    for index, row in top_funds.iterrows():
        code = str(row['基金代码'])
        name = row['基金简称']
        pattern = get_fund_pattern(code)
        
        if pattern:
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
    send_email(email_content)

if __name__ == "__main__":
    main()