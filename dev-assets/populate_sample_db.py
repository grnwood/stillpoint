"""Script to populate the database with all sample vault pages."""
import sys
from pathlib import Path

# Add stillpoint to path
sys.path.insert(0, str(Path(__file__).parent))

from sp.app import config
from sp.server.adapters import files

def scan_and_index_vault(vault_root: Path) -> None:
    """Recursively scan vault and index all pages."""
    print(f"Scanning vault: {vault_root}")
    
    # Set the active vault
    config.set_active_vault(str(vault_root))
    
    # Find all .txt files
    txt_files = sorted(vault_root.rglob("*.txt"))
    print(f"Found {len(txt_files)} .txt files")
    
    for txt_file in txt_files:
        # Get relative path from vault root
        rel_path = txt_file.relative_to(vault_root)
        path_str = f"/{rel_path.as_posix()}"
        
        print(f"Indexing: {path_str}")
        
        try:
            # Read the file content
            content = txt_file.read_text(encoding="utf-8")
            
            # Extract title (first line starting with #)
            title = txt_file.stem
            for line in content.split('\n'):
                if line.startswith('# '):
                    title = line[2:].strip()
                    break
            
            # Extract tags (words starting with @)
            tags = []
            for word in content.split():
                if word.startswith('@') and len(word) > 1:
                    # Remove any trailing punctuation
                    tag = word[1:].rstrip('.,;:!?')
                    if tag and tag not in tags:
                        tags.append(tag)
            
            # Extract links (we'll skip this for now as it requires parsing colon notation)
            links = []
            
            # Extract tasks (lines with "( )" prefix)
            tasks = []
            for line_num, line in enumerate(content.split('\n'), 1):
                stripped = line.strip()
                if stripped.startswith('( )'):
                    # Parse task
                    task_text = stripped[4:].strip()
                    
                    # Extract task tags
                    task_tags = []
                    task_words = task_text.split()
                    for word in task_words:
                        if word.startswith('@') and len(word) > 1:
                            tag = word[1:].rstrip('.,;:!?')
                            if tag:
                                task_tags.append(tag)
                    
                    # Extract due date (date after <)
                    due_date = None
                    for word in task_words:
                        if word.startswith('<') and len(word) > 1:
                            due_date = word[1:].rstrip('.,;:!?')
                            break
                    
                    # Create task ID
                    task_id = f"{path_str}:{line_num}"
                    
                    task = {
                        "id": task_id,
                        "line": line_num,
                        "text": task_text,
                        "status": "open",
                        "priority": None,
                        "due": due_date,
                        "start": None,
                        "tags": task_tags,
                    }
                    tasks.append(task)
            
            # Update the page index
            config.update_page_index(
                path=path_str,
                title=title,
                tags=tags,
                links=links,
                tasks=tasks,
            )
            
        except Exception as e:
            print(f"  Error indexing {path_str}: {e}")
            continue
    
    print("\nIndexing complete!")
    
    # Print summary
    pages = config.search_pages("", limit=1000)
    print(f"\nTotal pages indexed: {len(pages)}")
    
    tag_summary = config.fetch_tag_summary()
    print(f"Total unique tags: {len(tag_summary)}")
    if tag_summary:
        print("Top tags:")
        for tag, count in sorted(tag_summary, key=lambda x: -x[1])[:10]:
            print(f"  @{tag}: {count}")
    
    all_tasks = config.fetch_tasks(include_done=False)
    print(f"\nTotal tasks: {len(all_tasks)}")


if __name__ == "__main__":
    # Get the sample-vault path
    sample_vault = Path(__file__).parent / "sample-vault"
    
    if not sample_vault.exists():
        print(f"Error: Sample vault not found at {sample_vault}")
        sys.exit(1)
    
    scan_and_index_vault(sample_vault)
