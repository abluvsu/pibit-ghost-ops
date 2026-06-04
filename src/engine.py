import os
import sqlite3
import csv
import yaml
from datetime import datetime
from pydantic import BaseModel, Field
import re
import json
from dotenv import load_dotenv
from openai import OpenAI

# Setup Enterprise Logging
from logger import logger

# ==========================================
# CONFIGURATION & PATHS
# ==========================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

with open(CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

DB_NAME = config["engine"]["database_name"]
DB_PATH = os.path.join(BASE_DIR, "data", DB_NAME)
REPORT_PATH = os.path.join(BASE_DIR, "data", "EOD_Reconciliation.csv")

RED_PINCODES = config["business_rules"]["red_pincodes"]
MAX_PRIOR_CLAIMS = config["business_rules"]["max_prior_claims"]
BLACKLISTED_BROKERS = [b.lower() for b in config["business_rules"]["blacklisted_brokers"]]
HIGH_RISK_INDUSTRIES = [i.lower() for i in config["business_rules"]["high_risk_industries"]]

# Load Environment Variables for LLM
load_dotenv(os.path.join(BASE_DIR, ".env"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ==========================================
# PYDANTIC LLM SCHEMA
# ==========================================
class ExtractedSubmission(BaseModel):
    broker_name: str = Field(default="Unknown", description="Name of the brokerage firm")
    pincode: str = Field(default="Unknown", description="6-digit postal code")
    prior_claims: int = Field(default=0, description="Number of prior claims in last 3 years")
    industry: str = Field(default="general", description="The industry or sector of the client")
    attachments_mentioned: list[str] = Field(default_factory=list, description="List of document types mentioned like 'supplemental' or 'loss runs'")

# ==========================================
# CORE ENGINE
# ==========================================
class GhostUnderwriterEngine:
    def __init__(self):
        logger.info("Initializing Ghost Underwriter v4 Engine...")
        self.setup_database()
        
        if OPENAI_API_KEY:
            logger.info("OpenAI API Key found. Real LLM extraction ENABLED.")
            self.client = OpenAI(api_key=OPENAI_API_KEY)
        else:
            logger.warning("No OpenAI API Key found. Falling back to deterministic Mock LLM.")
            self.client = None

    def setup_database(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS triage_logs_v4 (
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
        """Uses OpenAI if available, gracefully falls back to deterministic regex."""
        
        if self.client:
            try:
                # Real LLM Call
                response = self.client.chat.completions.create(
                    model="gpt-3.5-turbo-0125",
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": "You are a commercial underwriting extraction bot. Extract the following fields from the email into JSON: broker_name, pincode, prior_claims (int), industry, attachments_mentioned (list of strings)."},
                        {"role": "user", "content": email_text}
                    ]
                )
                result_dict = json.loads(response.choices[0].message.content)
                logger.info("Successfully extracted data via OpenAI.")
                return ExtractedSubmission(**result_dict)
            except Exception as e:
                logger.error(f"OpenAI extraction failed: {e}. Falling back to mock extraction.")
                
        # Deterministic Fallback
        logger.info("Executing Mock LLM Extraction...")
        data = ExtractedSubmission()
        
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
            logger.warning(f"Triage Block: Broker {broker_clean} is on the Blacklist.")
            return "REJECTED", "BROKER_ON_WATCHLIST", "AUTO_DECLINE"
            
        if data.prior_claims > MAX_PRIOR_CLAIMS:
            logger.warning(f"Triage Block: {data.prior_claims} claims exceeds max of {MAX_PRIOR_CLAIMS}.")
            return "REJECTED", f"High Claims Frequency ({data.prior_claims})", "AUTO_DECLINE"
            
        if data.pincode in RED_PINCODES:
            logger.warning(f"Triage Block: Pincode {data.pincode} is in a Red Zone.")
            return "REJECTED", f"Restricted Pincode ({data.pincode})", "AUTO_DECLINE"
            
        if data.industry in HIGH_RISK_INDUSTRIES:
            # Check for attachments
            attachments_str = " ".join(data.attachments_mentioned).lower()
            if "supplemental" not in attachments_str or "loss run" not in attachments_str:
                logger.warning(f"Triage Block: High risk industry ({data.industry}) missing mandatory attachments.")
                return "PENDING", "Missing Mandatory Supplemental/Loss Runs", "PENDING_BROKER_INFO"
                
        logger.info("Triage Passed: Submission clean. Routing to RiskCURE.")
        return decision, rejection_reason, status

    def log_to_mis(self, data: ExtractedSubmission, decision: str, rejection_reason: str, status: str):
        self.cursor.execute('''
            INSERT INTO triage_logs_v4 (broker_name, industry, rejection_reason, decision, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (data.broker_name, data.industry, rejection_reason, decision, status))
        self.conn.commit()
        logger.info("Successfully wrote decision to legacy MIS SQLite database.")

    def generate_eod_report(self):
        self.cursor.execute("SELECT decision, COUNT(*) FROM triage_logs_v4 GROUP BY decision")
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
            
        logger.info(f"EOD Reconciliation CSV generated at {REPORT_PATH}")
            
    def get_logs(self):
        import pandas as pd
        return pd.read_sql_query("SELECT * FROM triage_logs_v4 ORDER BY processed_at DESC", self.conn)
