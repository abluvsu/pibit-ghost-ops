import os
import glob
import re
import sqlite3
import csv
from datetime import datetime

# ==========================================
# CONFIGURATION & BUSINESS RULES (v2)
# ==========================================
# Use relative paths so this runs anywhere when cloned from GitHub
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INBOX_DIR = os.path.join(BASE_DIR, "inbox")
DB_PATH = os.path.join(BASE_DIR, "triage_mis.db")
REPORT_PATH = os.path.join(BASE_DIR, "EOD_Reconciliation.csv")

# Rule 1: Red Pincodes
RED_PINCODES = ['400001', '600001', '700001']

# Rule 2: Maximum acceptable claims
MAX_PRIOR_CLAIMS = 2

# Rule 3: Blacklisted Brokers
BLACKLISTED_BROKERS = ['apex risk solutions', 'shadow creek brokers']

# Rule 4: High-Risk Industries Requiring Supplementals
HIGH_RISK_INDUSTRIES = ['construction', 'transportation', 'manufacturing']

# ==========================================
# DATABASE SETUP (MIS SCHEMA)
# ==========================================
def setup_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS triage_logs_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker_name TEXT,
            industry TEXT,
            rejection_reason TEXT,
            decision TEXT,
            status TEXT,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn

# ==========================================
# ADVANCED PARSING ENGINE
# ==========================================
def parse_email(email_text):
    data = {
        "broker_name": "Unknown",
        "pincode": "Unknown",
        "prior_claims": 0,
        "industry": "General",
        "attachments_mentioned": []
    }
    
    # Extract Broker Name
    broker_match = re.search(r'(?i)(?:Brokerage Firm|Broker_Name|Broker Info|Broker)\s*[:\-|>]\s*([a-zA-Z0-s ]+)', email_text)
    if broker_match:
        data['broker_name'] = broker_match.group(1).strip()
        
    # Extract Pincode
    pincode_match = re.search(r'(?i)(?:Pincode|Pin Code)\s*[:\=]\s*(\d{6})', email_text)
    if pincode_match:
        data['pincode'] = pincode_match.group(1).strip()
        
    # Extract Prior Claims
    claims_match = re.search(r'(?i)(?:Prior Claims|History of claims|Claims).*?(\d+)', email_text)
    if claims_match:
        data['prior_claims'] = int(claims_match.group(1).strip())
        
    # Extract Industry
    industry_match = re.search(r'(?i)(?:Industry|Sector)\s*[:\-|>]\s*([a-zA-Z]+)', email_text)
    if industry_match:
        data['industry'] = industry_match.group(1).strip().lower()
        
    # Detect Attachments/Supplementals
    if re.search(r'(?i)supplemental', email_text):
        data['attachments_mentioned'].append("supplemental")
    if re.search(r'(?i)loss run', email_text):
        data['attachments_mentioned'].append("loss runs")
        
    return data

# ==========================================
# TRIAGE ENGINE v2
# ==========================================
def triage_submission(data):
    decision = "ACCEPTED"
    rejection_reason = "N/A"
    status = "ROUTE_TO_RISKCURE"
    
    broker_clean = data['broker_name'].lower().strip()
    
    # 1. Blacklist Check
    if broker_clean in BLACKLISTED_BROKERS:
        return "REJECTED", "BROKER_ON_WATCHLIST", "AUTO_DECLINE"
        
    # 2. Prior Claims Check
    if data['prior_claims'] > MAX_PRIOR_CLAIMS:
        return "REJECTED", f"High Claims Frequency ({data['prior_claims']})", "AUTO_DECLINE"
        
    # 3. Red Pincode Check
    if data['pincode'] in RED_PINCODES:
        return "REJECTED", f"Restricted Pincode ({data['pincode']})", "AUTO_DECLINE"
        
    # 4. Missing Data Protocol (High Risk Industry without Supplementals)
    if data['industry'] in HIGH_RISK_INDUSTRIES:
        if "supplemental" not in data['attachments_mentioned'] or "loss runs" not in data['attachments_mentioned']:
            return "PENDING", "Missing Mandatory Supplemental/Loss Runs", "PENDING_BROKER_INFO"
            
    return decision, rejection_reason, status

# ==========================================
# EOD RECONCILIATION REPORT GENERATOR
# ==========================================
def generate_eod_report(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT decision, COUNT(*) FROM triage_logs_v2 GROUP BY decision")
    stats = dict(cursor.fetchall())
    
    total = sum(stats.values())
    accepted = stats.get("ACCEPTED", 0)
    rejected = stats.get("REJECTED", 0)
    pending = stats.get("PENDING", 0)
    
    with open(REPORT_PATH, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["EOD RECONCILIATION REPORT", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        writer.writerow([])
        writer.writerow(["Metric", "Count"])
        writer.writerow(["TOTAL_INFLOW", total])
        writer.writerow(["ROUTED_TO_RISKCURE", accepted])
        writer.writerow(["AUTO_REJECTED", rejected])
        writer.writerow(["PENDING_BROKER_ACTION", pending])
        
    print(f"\n[SYSTEM] EOD Report generated at: {REPORT_PATH}")

# ==========================================
# MAIN EXECUTION
# ==========================================
def process_inbox():
    print("[SYSTEM] Starting Ghost Underwriter v2 Triage Engine...")
    conn = setup_database()
    cursor = conn.cursor()
    
    # Clear old table data for fresh test run
    cursor.execute("DELETE FROM triage_logs_v2")
    conn.commit()
    
    email_files = glob.glob(os.path.join(INBOX_DIR, "*.txt"))
    
    for file_path in email_files:
        filename = os.path.basename(file_path)
        print(f"\nProcessing {filename}...")
        
        with open(file_path, 'r') as f:
            email_body = f.read()
            
        data = parse_email(email_body)
        print(f"  Extracted -> Broker: '{data['broker_name']}' | Industry: {data['industry']} | Supps: {'Yes' if 'supplemental' in data['attachments_mentioned'] else 'No'}")
        
        decision, rejection_reason, status = triage_submission(data)
        
        if decision == "ACCEPTED":
            print(f"  Result    -> \033[92m{decision}\033[0m")
        elif decision == "PENDING":
            print(f"  Result    -> \033[93m{decision}\033[0m ({rejection_reason})")
        else:
            print(f"  Result    -> \033[91m{decision}\033[0m ({rejection_reason})")
            
        cursor.execute('''
            INSERT INTO triage_logs_v2 (broker_name, industry, rejection_reason, decision, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (data['broker_name'], data['industry'], rejection_reason, decision, status))
        conn.commit()
        
    print("\n[SYSTEM] Triage Complete.")
    
    generate_eod_report(conn)
    conn.close()

if __name__ == "__main__":
    process_inbox()
