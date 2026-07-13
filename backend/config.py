import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from the project root (safe no-op if file doesn't exist)
load_dotenv(BASE_DIR / ".env")

DATA_DIR        = BASE_DIR / "data"
POLICY_DOCS_DIR = DATA_DIR / "policy_docs"
CHROMA_DIR      = BASE_DIR / ".chromadb"

# ── LLM provider ─────────────────────────────────────────────────────
# Switch between "ollama" and "gemini" by editing .env → LLM_PROVIDER
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()

# ── Ollama ────────────────────────────────────────────────────────────
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL",    "gemma4:latest")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# ── Gemini ────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL",   "gemma-3-27b-it")

# ── RAG embedding model (always local, independent of LLM provider) ──
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# ── Cargo calculation constants ───────────────────────────────────────
PACKING_EFFICIENCY   = 0.75   # 75% of truck volume is practically usable
WEIGHT_SAFETY_MARGIN = 1.12   # 12% buffer on top of estimated cargo weight
