"""Full-text search index management using SQLite FTS5."""

import re
import sqlite3
from pathlib import PurePosixPath
from typing import Optional

from sp.server.adapters.files import PAGE_SUFFIXES


def init_search_db(conn: sqlite3.Connection) -> None:
    """Ensure search index tables exist. Called at app startup."""
    # Tables are created by config._ensure_schema
    pass


def upsert_page(conn: sqlite3.Connection, path: str, mtime: int, content: str) -> None:
    """Insert or update a page in the search index."""
    try:
        # Insert or update pages_search_index
        conn.execute(
            """
            INSERT INTO pages_search_index (path, mtime)
            VALUES (?, ?)
            ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime
            """,
            (path, mtime),
        )
        
        # Get the row id
        row_id = conn.execute(
            "SELECT id FROM pages_search_index WHERE path = ?", (path,)
        ).fetchone()[0]
        
        # Insert or replace in FTS table
        conn.execute(
            "INSERT OR REPLACE INTO pages_search_fts(rowid, content) VALUES (?, ?)",
            (row_id, content),
        )
        
        conn.commit()
    except sqlite3.OperationalError as e:
        print(f"[SearchIndex] Failed to upsert page {path}: {e}")


def delete_page(conn: sqlite3.Connection, path: str) -> None:
    """Remove a page from the search index."""
    try:
        # Get the row id
        row = conn.execute(
            "SELECT id FROM pages_search_index WHERE path = ?", (path,)
        ).fetchone()
        
        if row:
            row_id = row[0]
            # Delete from FTS table
            conn.execute("DELETE FROM pages_search_fts WHERE rowid = ?", (row_id,))
            # Delete from index table
            conn.execute("DELETE FROM pages_search_index WHERE id = ?", (row_id,))
            conn.commit()
    except sqlite3.OperationalError as e:
        print(f"[SearchIndex] Failed to delete page {path}: {e}")


def delete_tree(conn: sqlite3.Connection, folder_path: str) -> None:
    """Remove a folder subtree from the search index (matching any child pages)."""
    try:
        prefix = (folder_path or "").strip().replace("\\", "/")
        if not prefix.startswith("/"):
            prefix = f"/{prefix}"
        prefix = prefix.rstrip("/")
        like_prefix = f"{prefix}/%"
        rows = conn.execute(
            "SELECT id FROM pages_search_index WHERE path = ? OR path LIKE ?",
            (prefix, like_prefix),
        ).fetchall()
        if not rows:
            return
        row_ids = [row[0] for row in rows if row and row[0] is not None]
        if not row_ids:
            return
        conn.executemany(
            "DELETE FROM pages_search_fts WHERE rowid = ?",
            [(row_id,) for row_id in row_ids],
        )
        conn.executemany(
            "DELETE FROM pages_search_index WHERE id = ?",
            [(row_id,) for row_id in row_ids],
        )
        conn.commit()
    except sqlite3.OperationalError as e:
        print(f"[SearchIndex] Failed to delete subtree {folder_path}: {e}")


def _find_snippet_line(full_content: str, snippet: str) -> int:
    """
    Find the line number where the snippet text appears in the content.
    Returns 1-indexed line number, or 0 if not found.
    """
    if not full_content or not snippet:
        return 0
    
    # Remove FTS5 markers from snippet to get actual text
    clean_snippet = re.sub(r'\[([^\]]+)\]', r'\1', snippet)
    # Remove ellipsis markers
    clean_snippet = clean_snippet.replace('...', '').strip()
    
    # Handle multi-line snippets - try to find a distinctive part
    snippet_lines = [line.strip() for line in clean_snippet.split('\n') if line.strip()]
    if not snippet_lines:
        return 0
    
    # Try each line of the snippet
    content_lines = full_content.split('\n')
    for snippet_line in snippet_lines:
        # Get first few significant words from this snippet line
        words = snippet_line.split()
        if len(words) < 2:
            continue
        
        # Use a reasonable chunk (3-7 words) for searching
        num_words = min(7, max(3, len(words)))
        search_text = ' '.join(words[:num_words])
        
        # Search for this text in content
        for i, content_line in enumerate(content_lines, 1):
            if search_text.lower() in content_line.lower():
                return i
    
    # If no match found with word chunks, try the matched term itself
    # Extract just the matched terms from FTS5 markers
    matched_terms = re.findall(r'\[([^\]]+)\]', snippet)
    if matched_terms:
        search_term = None
        for term in matched_terms:
            if re.search(r"[A-Za-z0-9]", term):
                search_term = term
                break
        if search_term:
            for i, line in enumerate(content_lines, 1):
                if search_term.lower() in line.lower():
                    return i
    
    return 0


def _find_snippet_position(full_content: str, snippet: str) -> int:
    """Return 0-based character offset of the best snippet match, or -1 if not found."""
    if not full_content or not snippet:
        return -1

    clean_snippet = re.sub(r'\[([^\]]+)\]', r'\1', snippet)
    clean_snippet = clean_snippet.replace('...', '').strip()
    snippet_lines = [line.strip() for line in clean_snippet.split('\n') if line.strip()]
    content_lines = full_content.split('\n')

    for snippet_line in snippet_lines:
        words = snippet_line.split()
        if len(words) < 2:
            continue
        num_words = min(7, max(3, len(words)))
        search_text = ' '.join(words[:num_words]).lower()
        for i, content_line in enumerate(content_lines):
            pos = content_line.lower().find(search_text)
            if pos != -1:
                return sum(len(l) + 1 for l in content_lines[:i]) + pos

    matched_terms = re.findall(r'\[([^\]]+)\]', snippet)
    search_term = None
    for term in matched_terms:
        if re.search(r"[A-Za-z0-9]", term):
            search_term = term
            break
    if search_term:
        pos = full_content.lower().find(search_term.lower())
        if pos != -1:
            return pos

    return -1


def _prepare_fts_query(query: str) -> str:
    """
    Prepare FTS5 query by adding prefix matching (*) to simple terms.
    Preserves quoted phrases, boolean operators, and existing wildcards.
    
    Examples:
        "pickle" -> "pickle*"
        "pickle recipe" -> "pickle* recipe*"
        '"exact phrase"' -> '"exact phrase"'
        "pickle AND recipe" -> "pickle* AND recipe*"
        "pickle*" -> "pickle*" (already has wildcard)
    """
    if not query or not query.strip():
        return query
    
    # Pattern to match:
    # - Quoted phrases: "..."
    # - Terms with wildcards: word*
    # - Boolean operators: AND, OR, NOT, NEAR
    # - Regular words: word
    token_pattern = r'"[^"]*"|\w+\*|\b(?:AND|OR|NOT|NEAR)\b|\w+'
    
    def process_token(match):
        token = match.group(0)
        # Don't modify quoted phrases
        if token.startswith('"'):
            return token
        # Don't modify boolean operators
        if token.upper() in ('AND', 'OR', 'NOT', 'NEAR'):
            return token
        # Don't modify terms that already have wildcards
        if '*' in token:
            return token
        # Add prefix wildcard to regular words
        return token + '*'
    
    return re.sub(token_pattern, process_token, query)


def _normalize_subtree_filter(subtree: Optional[str]) -> Optional[str]:
    """Normalize subtree filters, accepting page paths and returning folder paths."""
    if not subtree:
        return None
    cleaned = subtree.strip()
    if not cleaned or cleaned == "/":
        return None
    if cleaned.lower().endswith(PAGE_SUFFIXES):
        parent = str(PurePosixPath(cleaned).parent)
        if parent in (".", "/"):
            return None
        return parent
    return cleaned


def search_pages(
    conn: sqlite3.Connection,
    query: str,
    subtree: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """
    Search pages using FTS5 full-text search.
    
    Args:
        conn: Database connection
        query: Search query supporting FTS5 syntax (AND/OR/NEAR/NOT/"exact phrase")
               and @tag filtering (e.g., "search term @tag1 @tag2")
        subtree: Optional path prefix to limit search (e.g., "/Projects/StillPoint")
        limit: Maximum number of results to return
    
    Returns:
        List of dicts with keys: path, snippet, rank
    """
    if not query or not query.strip():
        return []

    normalized_subtree = _normalize_subtree_filter(subtree)
    
    # Extract @tags from query
    tag_pattern = r'@(\w+)'
    tags = re.findall(tag_pattern, query)
    # Remove @tags from FTS query
    fts_query = re.sub(tag_pattern, '', query).strip()
    
    # Add prefix matching to FTS query terms
    if fts_query:
        fts_query = _prepare_fts_query(fts_query)
    
    if not fts_query and not tags:
        return []
    
    try:
        # Build the SQL query - include full content to calculate line numbers
        if fts_query and tags:
            # Search content AND tags
            sql = """
                SELECT DISTINCT
                    p.path,
                    snippet(pages_search_fts, 0, '[', ']', '...', 10) AS snippet,
                    bm25(pages_search_fts) AS rank,
                    fts.content AS full_content
                FROM pages_search_fts fts
                JOIN pages_search_index p ON p.id = fts.rowid
                JOIN page_tags pt ON pt.page = p.path
                WHERE pages_search_fts MATCH ?
                    AND pt.tag IN ({})
            """.format(','.join('?' * len(tags)))
            params = [fts_query] + tags
        elif fts_query:
            # Search content only
            sql = """
                SELECT
                    p.path,
                    snippet(pages_search_fts, 0, '[', ']', '...', 10) AS snippet,
                    bm25(pages_search_fts) AS rank,
                    fts.content AS full_content
                FROM pages_search_fts fts
                JOIN pages_search_index p ON p.id = fts.rowid
                WHERE pages_search_fts MATCH ?
            """
            params = [fts_query]
        else:
            # Search tags only (no FTS query)
            sql = """
                SELECT DISTINCT
                    psi.path,
                    '...' AS snippet,
                    0 AS rank,
                    '' AS full_content
                FROM pages_search_index psi
                JOIN page_tags pt ON pt.page = psi.path
                WHERE pt.tag IN ({})
            """.format(','.join('?' * len(tags)))
            params = tags
        
        # Add subtree filter if specified
        if normalized_subtree:
            normalized_prefix = normalized_subtree.rstrip('/') + '/'
            sql += " AND (p.path = ? OR p.path LIKE ?)" if fts_query or (fts_query and tags) else " AND (psi.path = ? OR psi.path LIKE ?)"
            params.extend([normalized_subtree.rstrip('/'), normalized_prefix + '%'])
        
        # Add ordering and limit
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(sql, params).fetchall()
        
        results = []
        for row in rows:
            path = row[0]
            snippet = row[1]
            rank = row[2]
            full_content = row[3] if len(row) > 3 else ""
            
            # Calculate line number by finding where snippet content appears
            line_num = _find_snippet_line(full_content, snippet)
            position = _find_snippet_position(full_content, snippet)
            print(f"[SearchIndex] Path: {path}, Snippet: {snippet[:80]}..., Line: {line_num}")
            
            results.append({
                "path": path,
                "snippet": snippet,
                "rank": rank,
                "line": line_num,
                "pos": position,
            })
        
        return results
    except sqlite3.OperationalError as e:
        print(f"[SearchIndex] Search failed for query '{query}': {e}")
        return []
