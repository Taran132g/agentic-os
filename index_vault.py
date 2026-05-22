"""
Standalone vault indexer — run once to build the initial RAG index.

Usage:
    python3 index_vault.py           # index only changed files
    python3 index_vault.py --force   # re-index everything

Requires: ollama serve + ollama pull nomic-embed-text
"""
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

def main():
    force = "--force" in sys.argv

    print("Checking Ollama...")
    from tools.rag import _ollama_alive, EMBED_MODEL
    if not _ollama_alive():
        print("Ollama is not running.")
        print("Start it with:  ollama serve")
        print("Then pull model: ollama pull nomic-embed-text")
        sys.exit(1)

    print(f"Ollama online — using {EMBED_MODEL}")
    print(f"Mode: {'force re-index all' if force else 'incremental (changed files only)'}")
    print()

    from tools.rag import index_vault, _vault_files, get_stats
    files = _vault_files()
    print(f"Vault files found: {len(files)}")
    print("Indexing... (this may take a few minutes on first run)")
    print()

    t0 = time.time()
    result = index_vault(force=force)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    elapsed = round(time.time() - t0, 1)
    print(f"Done in {elapsed}s")
    print(f"  Files indexed : {result['files']}")
    print(f"  Chunks stored : {result['chunks']}")
    print(f"  Files skipped : {result['skipped']} (unchanged)")

    stats = get_stats()
    print(f"\nRAG database: {stats['chunks']} total chunks from {stats['files_indexed']} files")
    print("Ready for semantic search.")


if __name__ == "__main__":
    main()
