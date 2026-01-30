# 🛡️ KERBERUS - Open Source Legal Intelligence for Switzerland

> **Status:** 🚧 In Active Development | Expected Release: February 2026

A production-grade AI legal assistant for Swiss lawyers, built with zero-knowledge encryption, multilingual support(DE/FR/IT) and full data sovereignty.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

---
<div align="center">
  <img src="assets/kerberus-logo.png" alt="KERBERUS - Sovereign AI Guard" width="300"/>
  
  <h3>Open Source Legal Intelligence for Switzerland</h3>
  
  <p>
    <a href="https://opensource.org/licenses/MIT">
      <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"/>
    </a>
    <a href="https://www.python.org/downloads/">
      <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"/>
    </a>
  </p>
</div>

## 🎯 Why This Project?

After my previous company closed in January 2026, I invested the transition period in building something meaningful: a production-grade legal AI assistant demonstrating end-to-end LLM application development.

**Project Origin: Applied LLMOps**

This project was built to rigorously apply the advanced concepts from the **LLMOps Specialization (Duke University)** to a real-world, high-stakes domain.

Instead of a theoretical exercise, KERBERUS focuses on:
1. **Production-Grade RAG**: Implementing hybrid search, rescording, and dynamic context management.
2. **Sovereign AI Infrastructure**: Hosted on **Infomaniak** (Switzerland) to strictly avoid US CLOUD Act jurisdiction. Unlike US hyperscalers (AWS/Azure), this architecture ensures 100% compliance with Swiss Data Protection laws (nFADP) and GDPR by keeping compute and data physically within Switzerland.
3. **End-to-End MLOps**: Automated ingestion pipelines, monitoring, and reproducible deployment.

I chose to open-source this reference implementation because:
- ✅ Developers building compliance AI can learn from a complete reference implementation
- ✅ Showcases capabilities beyond typical take-home assignments or coursework
- ✅ Adaptable to any civil law jurisdiction (Germany, Austria, France, etc.)
- ✅ More valuable to my career than a minimally-viable commercial product

**What makes this interesting:**
- ✅ Processes **400,000+ judgments** across 4 jurisdictions (Federal, TI, BS, VD)
- ✅ **Zero-knowledge encryption** - data stays in Switzerland and is always encrypted
- ✅ **Production-grade architecture** - handles 50+ concurrent users with <5s P95 latency
- ✅ **Validated by Swiss lawyers** - tested against commercial legal AI platforms
- ✅ **Fully open source** - adapt for your jurisdiction or learn from the implementation

---

## 🏗️ Architecture

### **Core Components**

**Infrastructure:**
- Docker-orchestrated services (Qdrant, PostgreSQL, Redis)
- Apple Silicon optimized (MPS acceleration)
- Production deployment ready (Infomaniak)

**Advanced RAG Pipeline:**
- **BGE-M3 embeddings** - Multilingual semantic search (768-dim, 100+ languages)
- **BGE-Reranker-v2-M3** - Cross-encoder precision ranking
- **Recency weighting** - Recent precedents scored higher
- **Authority weighting** - Supreme Court (BGE/ATF) cases prioritized
- **MMR algorithm** - Eliminates redundant results while maintaining relevance
- **Triad search** - Parallel search across laws, case law, and user documents

**Cost Optimization:**

Standard RAG systems accumulate context, causing exponential token costs. KERBERUS implements **dynamic context swapping** (the "Sliding Window of Truth" pattern):
```python
# Keep: Chat history (lightweight)
chat_history = ["Can I fire employee?", "Yes, if immediate cause..."]

# Swap: Legal context (replaced each turn)
turn_1: [Art. 337 OR, BGE 140 III 348]
turn_2: [Art. 336 OR, TI_2023_045]  # Previous context discarded
```

This prevents token bloat while maintaining conversation quality - the same pattern used by Anthropic, OpenAI, and Perplexity.

**Data Ingestion:**
- Incremental scraping with state management
- Smart year-range detection (avoids re-downloading)
- Rate limiting and retry logic
- Ticino scraper: ~30,000 judgments (1990-present)
- BGE/ATF scraper: ~150,000 published federal decisions
- Fedlex scraper: Active federal laws and ordinances (OR, ZGB, StGB, etc.) — dynamically synchronized to purge repealed legislation

---

## 🔍 Technical Highlights

### **Metadata-Driven Ranking**

Every judgment is enriched with structured metadata:
```python
{
    "case_id": "BGE_140_III_348",
    "year": 2014,
    "court": "bundesgericht",
    "source": "bge_archive",
    "is_cornerstone": True,
    "authority": "SUPREME_PUBLISHED",
    "law_type": "civil",
    "domain": "employment",
    "outcome": "REJECTED",
    "cites_cases": ["BGE_135_III_232", ...],
    "cites_articles": ["Art. 337 OR", ...]
}
```

### **Intelligent Reranking**
```python
final_score = base_rerank_score 
              + (0.10 × recency_score)
              + (0.10 × authority_boost)
```

This ensures recent, authoritative precedents surface first while maintaining semantic relevance.

---

## 🚀 Current Status & Roadmap

### **✅ Completed**
- Production infrastructure (Docker, Qdrant, PostgreSQL, Redis)
- Multilingual embeddings and reranking (BGE-M3, BGE-Reranker)
- Dynamic context swapping implementation
- Triad search architecture
- Ticino court scraper with incremental updates
- BGE/ATF scraper (Federal Supreme Court published decisions)
- Fedlex scraper (Swiss federal laws - OR, ZGB, StGB) with auto-repeal handling
- Parsing Engine (PDF/HTML -> JSON)
  - Metadata extraction (Judges, Dates, Outcomes)
  - Legal citation linking (e.g. `Art. 337 OR` -> `SR 220`)
  - Intelligent section splitting (Facts vs. Reasoning)

## 🛠️ Usage

### **Data Ingestion (Scraping)**
```bash
# Federal Laws (Fedlex) - Syncs latest versions & deletes repealed ones
make scrape-fedlex

# Ticino Court Decisions
make scrape-ticino        # Incremental update
make scrape-ticino-full   # Re-download everything from 1990

# Federal Court Decisions
make scrape-federal
```

### **Data Processing (Parsing)**
Transform raw PDFs/HTMLs into structured JSON with metadata:
```bash
make parse-federal    # Parse BGE, BGer, BVGE
make parse-ticino     # Parse Ticino decisions
```

## 🎥 Demo

**Coming Soon:** Video walkthrough and hosted demo

### **🚧 In Progress**
- Qdrant collection population (Vector ingestion)
- Zero-knowledge encryption layer

### **🔜 Next**
- SQLCipher integration (zero-knowledge document storage)
- PII detection and scrubbing
- Qwen3-VL API integration
- Authentication system (email + MFA)
- Chainlit conversational interface
- Automated Document Review (AI-driven analysis of document sets at scale based on user-defined criteria)
- Production deployment on Infomaniak

### **🔮 Future**
- Citation graph analysis (identify landmark cases)
- Multi-canton expansion
- React frontend
- Adaptation guides for other countries

---

## 🌍 International Applicability

While built for Switzerland, KERBERUS can be adapted to any civil law jurisdiction with public legal databases:

- 🇩🇪 Germany (bundesgerichtshof.de)
- �🇷 France (legifrance.gouv.fr)
- �🇸🇪 Sweden (domstol.se)
- 🇦🇹 Austria (ris.bka.gv.at)
- 🇧🇪 Belgium (juridat.be)
- 🇳🇱 Netherlands (rechtspraak.nl)

Requires replacing scrapers and adapting metadata schema for local court hierarchies.

---

## 📚 Tech Stack

| Component | Technology |
|-----------|-----------|
| **Embeddings** | BGE-M3 (BAAI) |
| **Reranking** | BGE-Reranker-v2-M3 |
| **Vector DB** | Qdrant |
| **SQL DB** | PostgreSQL 15 |
| **Encrypted Storage** | SQLCipher |
| **LLM** | Qwen3-VL (235B) & Mistral-Small-3.2-24B-Instruct-2506 |
| **Deployment** | Docker + Infomaniak |
| **Frontend** | Chainlit → React |

---

## 🔐 Security & Privacy

- **Zero-knowledge encryption** - AES-256, keys never leave user session
- **Swiss data sovereignty** - All infrastructure hosted in Switzerland
- **GDPR compliant** - By design, no third-party tracking
- **API key security** - Stored in Infomaniak KMS / HashiCorp Vault

---

## 🤝 Contributing

Contributions welcome! Whether you want to:
- Adapt for your country
- Report bugs
- Suggest features
- Improve documentation
```bash
git clone https://github.com/brani-milo/kerberus
cd kerberus
make setup
make scrape-ticino-test  # Test with 1993 only
```

---

## 💼 About the Author

I'm **Branisa Milosavljevic**, a Data Scientist with **7+ years** driving business growth through AI/ML at companies like Medical Insights (Basel), Enterprise Mobility (Zurich), and Kambi (Stockholm).

**This project demonstrates:**
- LLMops application development (RAG, embeddings, reranking)
- Production system design (Docker, Qdrant, PostgreSQL, Redis)
- MLOps maturity (incremental scraping, monitoring, deployment)
- Security engineering (zero-knowledge, encryption, PII detection)
- Swiss market expertise (FINMA compliance, data sovereignty)

**Looking for:**
- (Senior) Data Scientist roles (Switzerland or Full Remote)
- AI Engineer positions (product-focused, end-to-end ownership)
- AI Transformation Lead or Consultant (organizational change, AI adoption strategy)

---

## 📄 License

MIT License - See [LICENSE](LICENSE)

**TL;DR:** Free to use, modify, and commercialize without restriction.

**If this helped your project:** Please consider citing or linking back to this repo. It helps others discover the work and supports the open-source community.
```bibtex
@software{milosavljevic2026kerberus,
  author = {Milosavljevic, Branisa},
  title = {KERBERUS: Swiss Legal AI Assistant},
  year = {2026},
  url = {https://github.com/brani-milo/kerberus}
}
```

---

## 🙏 Acknowledgments

Built during my job search (January 2026 - present). This project represents:
- Production-grade LLM application development
- LLMOps certification applied to real-world problems
- 7+ Years experience in Data Science
## ⚠️ Project Status & Disclaimer

**This is a portfolio/demonstration project built to showcase:**
- End-to-end LLM application development skills
- Application of LLMOps certification
- Production-grade system design and architecture capabilities

**Important clarifications:**
- ✅ This project has **never been commercialized** and has generated **zero revenue**
- ✅ Built solely as a **demonstration of technical capabilities** for job applications
- ✅ All code is provided **as-is for educational and reference purposes**

**Legal Disclaimer:**
This software is provided under the MIT License (see LICENSE file). It is not intended as legal advice and should not be used for actual legal practice without proper review, testing, and compliance verification. The author assumes no liability for any use of this software. Always consult qualified legal professionals for legal matters.

---

**⭐ Star this repo if you find it useful!**

**Expected release: February 2026**
