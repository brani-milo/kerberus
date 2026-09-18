#!/usr/bin/env python3
"""Direct test of federal courts to verify data availability."""

import requests
from bs4 import BeautifulSoup
import re

COURT_CONFIG = {
    "CH_BGE":    {"folder": "CH_BGE",    "prefix": "CH_BGE",   "mode": "strict"},
    "CH_BGer":   {"folder": "CH_BGer",   "prefix": "CH_BGer",  "mode": "strict"},
    "CH_BVGer":  {"folder": "CH_BVGer",  "prefix": "CH_BVGE",  "mode": "strict"},
    "CH_BStGer": {"folder": "CH_BSTG",   "prefix": "CH_BSTG",  "mode": "strict"},
    "CH_BPatG":  {"folder": "CH_BPatG",  "prefix": "",         "mode": "loose"},
    "CH_EDOEB":  {"folder": "CH_EDOEB",  "prefix": "CH_ED",    "mode": "loose"}
}

DOMAIN = "https://entscheidsuche.ch"

def test_court(session, court_key, config):
    print(f"\n{'='*60}")
    print(f"Testing: {court_key}")
    print(f"{'='*60}")

    # 1. Check if Jobs directory exists
    jobs_url = f"{DOMAIN}/docs/Jobs/{config['folder']}/"
    print(f"Checking: {jobs_url}")

    try:
        r = session.get(jobs_url, timeout=10)
        print(f"  Status: {r.status_code}")

        if r.status_code != 200:
            print(f"  ❌ Jobs directory not accessible")
            return

        # 2. Count job logs
        soup = BeautifulSoup(r.text, "html.parser")
        job_logs = [link.get("href") for link in soup.find_all("a")
                    if link.get("href") and "Job_" in link.get("href") and link.get("href").endswith(".json")]

        print(f"  ✅ Found {len(job_logs)} job logs")

        if len(job_logs) == 0:
            print(f"  ❌ No job logs found")
            return

        # 3. Test first log for file references
        test_log = job_logs[0]
        if not test_log.startswith("http"):
            if test_log.startswith("/"):
                test_log = DOMAIN + test_log
            else:
                test_log = jobs_url + test_log

        print(f"  Testing first log: {test_log.split('/')[-1]}")
        r2 = session.get(test_log, timeout=10)

        if r2.status_code == 200:
            # Build regex pattern
            if config["mode"] == "strict":
                pattern = re.compile(rf'({re.escape(config["prefix"])}[^/"]+\.(?:html|pdf))')
            else:
                pattern = re.compile(r'([a-zA-Z0-9_\\%\-\.]+\.(?:html|pdf))')

            matches = pattern.findall(r2.text)
            print(f"  ✅ Found {len(matches)} file references in first log")

            if len(matches) > 0:
                print(f"  Sample files:")
                for m in list(set(matches))[:5]:
                    print(f"    - {m}")
        else:
            print(f"  ❌ Could not read log: {r2.status_code}")

    except Exception as e:
        print(f"  ❌ Error: {e}")

def main():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    print("🔍 Testing Federal Court Data Availability\n")

    for court_key, config in COURT_CONFIG.items():
        test_court(session, court_key, config)

    print("\n" + "="*60)
    print("Test Complete")
    print("="*60)

if __name__ == "__main__":
    main()
