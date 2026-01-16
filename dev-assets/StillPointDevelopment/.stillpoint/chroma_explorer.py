import argparse
import traceback
from typing import Any, Dict, List, Tuple

import chromadb
import streamlit as st


# -------------------------
# CLI args handling
# -------------------------
def parse_args() -> Tuple[argparse.Namespace, List[str]]:
    """
    Parse CLI args that come after Streamlit's `--`.
    Usage:
        streamlit run chroma_explorer.py -- --path /path/to/chroma
    """
    parser = argparse.ArgumentParser(description="Chroma Explorer")
    parser.add_argument(
        "--path",
        type=str,
        help="Path to Chroma DB directory (for PersistentClient)",
    )
    # Streamlit passes its own args first; anything after `--` lands here
    return parser.parse_known_args()


# -------------------------
# Client helpers
# -------------------------
def get_client_from_path(path: str):
    """
    Connect directly to a local Chroma DB directory using PersistentClient.
    This does NOT require the `chroma run` server to be running.
    """
    # For chromadb < 1.4, PersistentClient is at top-level:
    #   chromadb.PersistentClient(path=...)
    # For newer versions there might be config Settings, but this should work
    return chromadb.PersistentClient(path=path)


def get_http_client(host: str, port: int):
    """
    Connect to a running HTTP Chroma server (started with `chroma run`).
    """
    # Older versions: HttpClient at top-level
    try:
        return chromadb.HttpClient(host=host, port=port)  # type: ignore[attr-defined]
    except AttributeError:
        # Fallback for newer versions using Settings
        from chromadb.config import Settings

        return chromadb.Client(
            Settings(
                chroma_api_impl="rest",
                chroma_server_host=host,
                chroma_server_http_port=port,
            )
        )


def get_collection_name(obj: Any) -> str:
    """
    list_collections() can return Collection objects or dicts depending on version.
    Be defensive.
    """
    if hasattr(obj, "name"):
        return obj.name  # Collection object
    if isinstance(obj, dict):
        return obj.get("name") or obj.get("id") or "unknown"
    return str(obj)


# -------------------------
# Main Streamlit app
# -------------------------
def main():
    args, _ = parse_args()

    st.set_page_config(page_title="Chroma Explorer", layout="wide")
    st.title("🧠 ChromaDB Explorer")

    client = None

    # 1) Prefer path-based connection if provided
    if args.path:
        st.sidebar.header("Connection")
        st.sidebar.write(f"Using path: `{args.path}`")
        try:
            client = get_client_from_path(args.path)
            st.sidebar.success("Connected via PersistentClient")
        except Exception as e:
            st.sidebar.error(f"Failed to connect to path:\n{e}")
            st.sidebar.text(traceback.format_exc())
            st.stop()

    # 2) If no path, allow HTTP connect via sidebar
    if client is None:
        st.sidebar.header("Connect to HTTP server")
        host = st.sidebar.text_input("Host", value="localhost")
        port = st.sidebar.number_input("Port", min_value=1, max_value=65535, value=8000)
        if st.sidebar.button("Connect / Refresh"):
            try:
                client = get_http_client(host, port)
                st.sidebar.success(f"Connected to {host}:{port}")
            except Exception as e:
                st.sidebar.error(f"Failed to connect: {e}")
                st.sidebar.text(traceback.format_exc())
                st.stop()
        else:
            st.info("Provide --path or connect via sidebar.")
            st.stop()

    # At this point we must have a client
    st.success("🎯 Successfully connected to Chroma!")

    # -------------------------
    # Collections
    # -------------------------
    st.subheader("Collections")

    try:
        collections = client.list_collections()
    except Exception as e:
        st.error(f"Error listing collections: {e}")
        st.text(traceback.format_exc())
        st.stop()

    if not collections:
        st.info("No collections found. Create some via your app, then reload.")
        st.stop()

    col_names = [get_collection_name(c) for c in collections]
    selected_name = st.selectbox("Select a collection", options=col_names)

    # Map name back to collection object for more info if needed
    selected_collection_obj = None
    for c in collections:
        if get_collection_name(c) == selected_name:
            selected_collection_obj = c
            break

    # Get the live collection handle
    try:
        collection = client.get_collection(name=selected_name)
    except TypeError:
        # Fallback: dict-style id-based (unlikely with your version, but safe)
        coll_id = getattr(selected_collection_obj, "id", None)
        if isinstance(selected_collection_obj, dict):
            coll_id = selected_collection_obj.get("id")
        if coll_id is None:
            st.error("Could not resolve collection by name or id.")
            st.stop()
        collection = client.get_collection(collection_id=coll_id)  # type: ignore[arg-type]

    # -------------------------
    # Collection info
    # -------------------------
    info_cols = st.columns(3)
    with info_cols[0]:
        try:
            count = collection.count()
        except Exception:
            count = "?"
        st.metric("Number of entries", count)

    with info_cols[1]:
        st.write("**Name**")
        st.code(selected_name, language="text")

    with info_cols[2]:
        st.write("**Raw collection object**")
        st.json(
            {
                "repr": repr(selected_collection_obj),
            },
            expanded=False,
        )

    st.markdown("---")

    # -------------------------
    # Sample docs
    # -------------------------
    st.subheader("Sample documents in collection")

    sample_limit = st.number_input("Sample size", 1, 100, 10)
    if st.button("Show sample docs"):
        try:
            sample = collection.get(
                limit=int(sample_limit),
                include=["metadatas", "documents"],
            )
            st.write("**IDs:**", sample.get("ids"))
            st.write("**Documents:**")
            st.json(sample.get("documents"))
            st.write("**Metadatas:**")
            st.json(sample.get("metadatas"))
        except Exception as e:
            st.error(f"Error fetching sample docs: {e}")
            st.text(traceback.format_exc())

    st.markdown("---")

    # -------------------------
    # Query UI
    # -------------------------
    st.subheader("Semantic query")

    query = st.text_input("Query text", value="")
    n_results = st.slider("Number of results", min_value=1, max_value=50, value=5)

    if st.button("Run query") and query.strip():
        try:
            results = collection.query(
                query_texts=[query],
                n_results=n_results,
                include=["metadatas", "documents", "distances"],
            )
        except Exception as e:
            st.error(f"Error querying collection: {e}")
            st.text(traceback.format_exc())
        else:
            docs: List[List[str]] = results.get("documents", [[]])
            metas: List[List[Dict[str, Any]]] = results.get("metadatas", [[]])
            dists: List[List[float]] = results.get("distances", [[]])
            ids: List[List[str]] = results.get("ids", [[]])

            st.write(f"Got {len(docs[0]) if docs else 0} results (first query set).")

            for idx, (doc, meta, dist, id_) in enumerate(
                zip(docs[0], metas[0], dists[0], ids[0])
            ):
                with st.expander(
                    f"Result {idx+1} (id={id_}, distance={dist:.4f})",
                    expanded=(idx == 0),
                ):
                    st.write("**Document:**")
                    st.code(doc or "", language="markdown")
                    st.write("**Metadata:**")
                    st.json(meta or {})

    st.markdown("---")
    st.caption(
        "Chroma Explorer – connect via --path for PersistentClient, or via sidebar for HTTP."
    )


if __name__ == "__main__":
    main()

