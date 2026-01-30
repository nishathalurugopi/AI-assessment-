**AI Assessment**

This project normalizes a raw inventory dataset into a clean, structured format suitable for downstream IPAM/DNS workflows.
It applies deterministic validation and normalization rules first, and uses a local LLM (TinyLlama via llama.cpp) only for ambiguous cases.

**Repository Structure**
Assessment/
├── inventory_raw.csv        # Input (messy inventory data)
├── inventory_clean.csv      # Output (normalized inventory)
├── anomalies.json           # Detected anomalies with remediation suggestions
├── prompts.md               # Human-readable LLM prompt audit log
├── run.py                   # Entry point
├── validation.py            # Core normalization + validation logic
├── approach.md              # High-level approach
├── cons.md                  # Limitations and tradeoffs

**How It Works**
1. Rules-first normalization
2. LLM enrichment
3. Anomaly reporting
4. Auditability

**Running the Pipeline**
1. Requirements
   Python 3.9+
   pip install llama-cpp-python

2. Local LLM Model
   
   The model file is **not included in this repository** due to GitHub file size limits.
   Steps:
   > Create a folder.
       models/
   > Download the model
       tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf
The pipeline will still run without the model, but LLM enrichment will be skipped.

**Execute**
python run.py

**Outputs**
> inventory_clean.csv
> anomalies.json
> prompts.md



