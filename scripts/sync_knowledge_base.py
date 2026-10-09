"""
Knowledge Base Synchronization CLI — scripts/sync_knowledge_base.py
=====================================================================
Automated utility to scan college, club, and project documents (Markdown, Word, PDF, Excel),
scrub private data, generate embeddings, and synchronize them into the Qdrant Vector DB.

Usage:
    python scripts/sync_knowledge_base.py
    python scripts/sync_knowledge_base.py --source ./data/docs --redact
    python scripts/sync_knowledge_base.py --status
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rag_knowledge import load_env
load_env()

from rag_knowledge.storage.qdrant_storage import get_global_qdrant_store
from rag_knowledge.ingestion.ingest import run_ingestion


def check_status():
    """Checks and prints the current status of Qdrant vector database."""
    print("=" * 60)
    print("  Riva-AGI Knowledge Base Status (Qdrant & RAG)")
    print("=" * 60)
    
    store = get_global_qdrant_store()
    is_live = store.is_available()
    
    qdrant_url = os.getenv("QDRANT_URL", "(not set)")
    collection = store.collection_name
    
    print(f"[*] Target Collection : {collection}")
    print(f"[*] Endpoint          : {qdrant_url}")
    print(f"[*] Connection Status : {'ONLINE ✅' if is_live else 'OFFLINE / NOT CONFIGURED ⚠️'}")
    
    if is_live:
        count = store.count_documents()
        print(f"[*] Document Chunks   : {count}")
    else:
        print("\n[!] To connect live Qdrant Cloud:")
        print("    1. Set QDRANT_URL in your .env")
        print("    2. Set QDRANT_API_KEY in your .env")
    print("=" * 60)
    return is_live


def sync_directory(source_path: str, redact: bool = True, dry_run: bool = False):
    """Syncs raw files from source directory into the knowledge base."""
    target_dir = Path(source_path).resolve()
    if not target_dir.exists():
        print(f"[ERROR] Source path does not exist: {target_dir}")
        return False
        
    print(f"\n[*] Starting Knowledge Base Ingestion from: {target_dir}")
    print(f"[*] Automatic PII Redaction: {'ENABLED' if redact else 'DISABLED'}")
    print(f"[*] Mode: {'DRY RUN (no database write)' if dry_run else 'LIVE UPSERT'}")
    
    try:
        total = run_ingestion(
            source_dir=target_dir,
            dry_run=dry_run,
            redact=redact,
        )
        print(f"\n[SUCCESS] Synchronized {total} document chunks into Qdrant Knowledge Base!")
        return True
    except Exception as e:
        print(f"\n[FAILED] Ingestion error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Synchronize documents into Riva-AGI Knowledge Base")
    parser.add_argument("--source", type=str, default="rag_knowledge/data/raw", help="Source folder containing raw files")
    parser.add_argument("--redact", action="store_true", default=True, help="Auto-redact phone numbers & emails")
    parser.add_argument("--dry-run", action="store_true", help="Simulate ingestion without writing to Qdrant")
    parser.add_argument("--status", action="store_true", help="Check database connection and document counts")

    args = parser.parse_args()

    if args.status:
        check_status()
        return

    check_status()
    sync_directory(source_path=args.source, redact=args.redact, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
