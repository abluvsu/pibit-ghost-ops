import os
import sqlite3
import csv
from datetime import datetime
from pydantic import BaseModel, Field
import re

# ==========================================
# PATHS
# ==========================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "triage_mis.db")
REPORT_PATH = os.path.join(BASE_DIR, "data", "EOD_Reconciliation.csv")

# ==========================================
# BUSINESS RULES
# ==========================================
RED_PINCODES = ['400001', '600001', '700001']
MAX_PRIOR_CLAIMS = 2
BLACKLISTED_BROKERS = ['apex risk solutions', 'shadow creek brokers']
HIGH_RISK_INDUSTRIES = ['construction', 'transportation', 'manufacturing']

# ==========================================
# PYDANTIC LLM SCHEMA (Mocking Structured Output)
# ==========================================
class ExtractedSubmission(BaseModel):
    broker_name: str = Field(default="Unknown", description="Name of the brokerage firm")
    pincode: str = Field(default="Unknown", description="6-digit postal code")
    prior_claims: int = Field(default=0, description="Number of prior claims in last 3 years")
    industry: str = Field(default="general", description="The industry or sector of the client")
    attachments_mentioned: list[str] = Field(default_factory=list, description="List of document types mentioned in the email")

# ==========================================
# CORE ENGINE
# ==========================================
class GhostUnderwriterEngine:
    def __init__(self):
        self.setup_database()

    def setup_database(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS triage_logs_v3 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broker_name TEXT,
                industry TEXT,
                rejection_reason TEXT,
                decision TEXT,
                status TEXT,
                processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()

    def simulate_llm_extraction(self, email_text: str) -> ExtractedSubmission:
        """Mocks an LLM generating structured JSON via Pydantic based on email text."""
        data = ExtractedSubmission()
        
        # Regex simulating LLM extraction intelligence
        broker_match = re.search(r'(?i)(?:Brokerage Firm|Broker_Name|Broker Info|Broker)\s*[:\-|>]\s*([a-zA-Z0-s ]+)', email_text)
        if broker_match: data.broker_name = broker_match.group(1).strip()
            
        pincode_match = re.search(r'(?i)(?:Pincode|Pin Code)\s*[:\=]\s*(\d{6})', email_text)
        if pincode_match: data.pincode = pincode_match.group(1).strip()
            
        claims_match = re.search(r'(?i)(?:Prior Claims|History of claims|Claims).*?(\d+)', email_text)
        if claims_match: data.prior_claims = int(claims_match.group(1).strip())
            
        industry_match = re.search(r'(?i)(?:Industry|Sector)\s*[:\-|>]\s*([a-zA-Z]+)', email_text)
        if industry_match: data.industry = industry_match.group(1).strip().lower()
            
        if re.search(r'(?i)supplemental', email_text): data.attachments_mentioned.append("supplemental")
        if re.search(r'(?i)loss run', email_text): data.attachments_mentioned.append("loss runs")
            
        return data

    def triage_submission(self, data: ExtractedSubmission):
        decision = "ACCEPTED"
        rejection_reason = "N/A"
        status = "ROUTE_TO_RISKCURE"
        
        broker_clean = data.broker_name.lower().strip()
        
        if broker_clean in BLACKLISTED_BROKERS:
            return "REJECTED", "BROKER_ON_WATCHLIST", "AUTO_DECLINE"
            
        if data.prior_claims > MAX_PRIOR_CLAIMS:
            return "REJECTED", f"High Claims Frequency ({data.prior_claims})", "AUTO_DECLINE"
            
        if data.pincode in RED_PINCODES:
            return "REJECTED", f"Restricted Pincode ({data.pincode})", "AUTO_DECLINE"
            
        if data.industry in HIGH_RISK_INDUSTRIES:
            if "supplemental" not in data.attachments_mentioned or "loss runs" not in data.attachments_mentioned:
                return "PENDING", "Missing Mandatory Supplemental/Loss Runs", "PENDING_BROKER_INFO"
                
        return decision, rejection_reason, status

    def log_to_mis(self, data: ExtractedSubmission, decision: str, rejection_reason: str, status: str):
        self.cursor.execute('''
            INSERT INTO triage_logs_v3 (broker_name, industry, rejection_reason, decision, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (data.broker_name, data.industry, rejection_reason, decision, status))
        self.conn.commit()

    def generate_eod_report(self):
        self.cursor.execute("SELECT decision, COUNT(*) FROM triage_logs_v3 GROUP BY decision")
        stats = dict(self.cursor.fetchall())
        
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
            
    def get_logs(self):
        import pandas as pd
        return pd.read_sql_query("SELECT * FROM triage_logs_v3 ORDER BY processed_at DESC", self.conn)
