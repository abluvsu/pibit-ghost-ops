import pytest
import sys
import os

# Ensure src directory is in path for imports
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from engine import GhostUnderwriterEngine, ExtractedSubmission

@pytest.fixture
def engine():
    return GhostUnderwriterEngine()

def test_triage_clean_submission(engine):
    data = ExtractedSubmission(
        broker_name="Good Broker Inc",
        pincode="110020",
        prior_claims=0,
        industry="retail",
        attachments_mentioned=[]
    )
    decision, reason, status = engine.triage_submission(data)
    assert decision == "ACCEPTED"
    assert status == "ROUTE_TO_RISKCURE"

def test_triage_blacklisted_broker(engine):
    data = ExtractedSubmission(
        broker_name="Apex Risk Solutions",
        pincode="110020",
        prior_claims=0,
        industry="retail",
        attachments_mentioned=[]
    )
    decision, reason, status = engine.triage_submission(data)
    assert decision == "REJECTED"
    assert reason == "BROKER_ON_WATCHLIST"

def test_triage_high_claims(engine):
    data = ExtractedSubmission(
        broker_name="Good Broker Inc",
        pincode="110020",
        prior_claims=5,
        industry="retail",
        attachments_mentioned=[]
    )
    decision, reason, status = engine.triage_submission(data)
    assert decision == "REJECTED"
    assert "High Claims Frequency" in reason

def test_triage_red_pincode(engine):
    data = ExtractedSubmission(
        broker_name="Good Broker Inc",
        pincode="400001",
        prior_claims=0,
        industry="retail",
        attachments_mentioned=[]
    )
    decision, reason, status = engine.triage_submission(data)
    assert decision == "REJECTED"
    assert "Restricted Pincode" in reason

def test_triage_missing_supplementals(engine):
    data = ExtractedSubmission(
        broker_name="Good Broker Inc",
        pincode="110020",
        prior_claims=0,
        industry="construction",
        attachments_mentioned=[]
    )
    decision, reason, status = engine.triage_submission(data)
    assert decision == "PENDING"
    assert "Missing Mandatory Supplemental" in reason

def test_triage_construction_with_supplementals(engine):
    data = ExtractedSubmission(
        broker_name="Good Broker Inc",
        pincode="110020",
        prior_claims=0,
        industry="construction",
        attachments_mentioned=["loss runs", "supplemental"]
    )
    decision, reason, status = engine.triage_submission(data)
    assert decision == "ACCEPTED"
