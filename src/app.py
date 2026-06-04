import streamlit as st
from engine import GhostUnderwriterEngine
import os
import yaml

st.set_page_config(page_title="Ghost Underwriter (v4 Enterprise)", page_icon="👻", layout="wide")

# Initialize Engine
@st.cache_resource
def get_engine():
    return GhostUnderwriterEngine()

engine = get_engine()

st.title("👻 Ghost Underwriter: Enterprise Triage (v4)")
st.markdown("Automated intake and triage for commercial insurance submissions. Built for scale.")

tab1, tab2, tab3 = st.tabs(["📧 Triage Engine", "🗄️ MIS Database", "⚙️ System Logs & Config"])

with tab1:
    st.subheader("Process Incoming Broker Email")
    
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    inbox_dir = os.path.join(base_dir, "data", "inbox")
    examples = ["Custom Email"]
    email_contents = {}
    
    if os.path.exists(inbox_dir):
        for f in os.listdir(inbox_dir):
            if f.endswith(".txt"):
                with open(os.path.join(inbox_dir, f), 'r') as file:
                    content = file.read()
                    examples.append(f)
                    email_contents[f] = content

    selected_example = st.selectbox("Load Example Submission:", examples)
    default_text = email_contents.get(selected_example, "")
    
    email_input = st.text_area("Paste Email Content Here:", value=default_text, height=300)
    
    if st.button("Run Triage Engine", type="primary"):
        if email_input:
            with st.spinner("Extracting structured data (OpenAI / Pydantic Mock)..."):
                extracted_data = engine.simulate_llm_extraction(email_input)
                
            st.markdown("### 🧩 Structured Data Extracted")
            st.json(extracted_data.model_dump())
            
            with st.spinner("Running Enterprise Business Rules..."):
                decision, reason, status = engine.triage_submission(extracted_data)
                engine.log_to_mis(extracted_data, decision, reason, status)
                engine.generate_eod_report()
                
            st.markdown("### ⚖️ Triage Decision")
            if decision == "ACCEPTED":
                st.success(f"**{decision}**: {status}")
            elif decision == "PENDING":
                st.warning(f"**{decision}**: {status} - {reason}")
            else:
                st.error(f"**{decision}**: {status} - {reason}")
                
            st.info("Log automatically written to SQLite MIS Database.")
        else:
            st.warning("Please enter email text.")

with tab2:
    st.subheader("Live MIS Database Logs")
    if st.button("Refresh Logs"):
        pass
        
    try:
        df = engine.get_logs()
        st.dataframe(df, use_container_width=True)
    except Exception as e:
        st.error("No data logged yet.")
        
    st.subheader("End of Day (EOD) Reconciliation")
    report_path = os.path.join(base_dir, "data", "EOD_Reconciliation.csv")
    if os.path.exists(report_path):
        import pandas as pd
        eod_df = pd.read_csv(report_path, skiprows=2)
        st.dataframe(eod_df, use_container_width=True)
        
        with open(report_path, "r") as file:
            st.download_button(
                label="Download EOD CSV",
                data=file,
                file_name="EOD_Reconciliation.csv",
                mime="text/csv"
            )

with tab3:
    st.subheader("System Logs (Observability)")
    log_path = os.path.join(base_dir, "logs", "ghost_ops.log")
    if os.path.exists(log_path):
        with open(log_path, "r") as f:
            logs = f.readlines()
            st.code("".join(logs[-20:]), language="text")
            
    st.subheader("Active YAML Configuration")
    config_path = os.path.join(base_dir, "config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            st.code(f.read(), language="yaml")
