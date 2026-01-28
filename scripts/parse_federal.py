
import logging
import sys
import os
from pathlib import Path
from multiprocessing import Pool, cpu_count
from tqdm import tqdm

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.parsers.federal_parser import FederalParser

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/parse_federal.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("FederalRunner")

def process_file(file_info):
    input_path, output_dir = file_info
    parser = FederalParser()
    try:
        data = parser.parse(input_path)
        
        # Create output filename from input filename but json
        output_file = output_dir / f"{input_path.stem}.json"
        
        parser.save_json(data, output_file)
        return True
    except Exception as e:
        logger.error(f"Error processing {input_path}: {e}")
        return False

def main():
    BASE_DIR = Path(__file__).parent.parent
    INPUT_DIR = BASE_DIR / "data" / "federal_archive_full"
    OUTPUT_DIR = BASE_DIR / "data" / "parsed" / "federal"

    if not INPUT_DIR.exists():
        logger.error(f"Input directory does not exist: {INPUT_DIR}")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Collect all HTML and PDF files
    files = list(INPUT_DIR.rglob("*.html")) + list(INPUT_DIR.rglob("*.pdf"))
    logger.info(f"Found {len(files)} files to parse.")

    # Prepare arguments for multiprocessing
    tasks = [(f, OUTPUT_DIR) for f in files]

    # Run processing
    # Using 75% of CPUs to avoid freezing system
    num_processes = max(1, int(cpu_count() * 0.75))
    
    logger.info(f"Starting parsing with {num_processes} processes...")
    
    with Pool(processes=num_processes) as pool:
        results = list(tqdm(pool.imap(process_file, tasks), total=len(files)))

    success_count = sum(results)
    logger.info(f"Parsing complete. Successfully parsed {success_count}/{len(files)} files.")

if __name__ == "__main__":
    main()
