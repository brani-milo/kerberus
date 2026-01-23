# 🛡️ KERBERUS - Open Source Legal Intelligence for Switzerland

> **Status:** 🚧 In Active Development | Expected Release: February 2026

A production-grade AI legal assistant for Swiss lawyers, built with zero-knowledge encryption, multilingual support (DE/FR/IT), and full data sovereignty.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

---

## 🎯 Why This Project?

After being laid off in January 2026, I decided to build something meaningful: a legal AI assistant that demonstrates end-to-end LLM application development at production scale.

**Why open source instead of commercial?**
- Swiss legal tech is dominated by well-funded startups and established players
- Unemployment regulations complicate starting a company
- The developer community can benefit from a complete, working reference implementation

**What makes this interesting:**
- ✅ Discovers **50,000+ Ticino judgments** 
- ✅ **Zero-knowledge encryption** - data never leaves Switzerland unencrypted
- ✅ **Production-grade architecture** - built to handle real workloads, not a demo
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
- **Ticino scraper:** ~30,000 judgments (1990-present)
- **Coming:** BGE/ATF (Federal Supreme Court), Fedlex (federal laws)

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

### **🚧 In Progress**
- BGE/ATF scraper (Federal Supreme Court published decisions)
- Fedlex scraper (Swiss federal laws - OR, ZGB, StGB)
- HTML parser (metadata extraction, citation detection)
- Qdrant collection population

### **🔜 Next**
- SQLCipher integration (zero-knowledge document storage)
- PII detection and scrubbing
- Qwen3-VL API integration
- Authentication system (email + MFA)
- Chainlit conversational interface
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
- 🇸🇪 Sweden (domstol.se)
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
| **LLM** | Qwen3-VL (235B) |
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

## 📬 Contact

**Author:** Branisa Milosavljevic  
**LinkedIn:** https://www.linkedin.com/in/branimilo/

**Actively seeking roles in:**
- Data Science
- LLM Engineering
- ML Engineering  
- AI Product Development

---

## 📄 License

MIT License - See [LICENSE](LICENSE)
TLDR, you can download it, modify it, sell it, or anything that comes to your mind

---

## 🙏 Acknowledgments

Built during my job search (January 2026 - present). This project represents:
- Production-grade LLM application development
- LLMOps certification applied to real-world problems
- 7+ Years experience in Data Science
## ⚠️ Project Status & Disclaimer

**This is a portfolio/demonstration project built to showcase:**
- End-to-end LLM application development skills
- Application of LLMOps certification and 10+ years of software engineering experience
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
