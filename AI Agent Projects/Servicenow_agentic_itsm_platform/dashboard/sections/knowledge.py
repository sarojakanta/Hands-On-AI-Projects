import json

import streamlit as st

from dashboard.api import ApiClient
from dashboard.ui import api_call, fmt_ts, has_role, header, note, table


def render(api: ApiClient, me: dict) -> None:
    header("Knowledge Base", "Indexed documents, semantic search with citations, and document lifecycle")
    stats = api_call(api.get, "/knowledge/stats")
    if stats:
        a, b, c = st.columns(3)
        a.metric("Indexed chunks", stats["chunks"])
        b.metric("Indexed incidents", stats["incidents"])
        c.metric("Embedding index", stats["embedder"].split("_")[0])
    note("Search returns excerpts and their sources only; nothing is generated. The default offline embedder matches "
         "shared vocabulary, so configure Ollama, sentence-transformers or a hosted embedding model for true semantic recall.")

    tab_search, tab_docs, tab_manage = st.tabs(["Search", "Documents", "Upload & maintenance"])
    with tab_search:
        q = st.text_input("Search query", key="kb-query", max_chars=500)
        category = st.text_input("Filter by metadata 'category' (optional)", key="kb-cat", max_chars=60)
        limit = st.slider("Results", 1, 10, 5, key="kb-limit")
        if q:
            body = {"query": q, "limit": limit, "filters": {"category": category} if category else {}}
            hits = api_call(api.post, "/knowledge/search", body)
            if hits is not None and not hits["items"]:
                st.info("No relevant excerpts found.")
            for i, h in enumerate((hits or {"items": []})["items"]):
                with st.expander(f"{h['citation']}  -  score {h['score']}", expanded=i == 0):
                    st.write(h["excerpt"])
                    if h.get("suspicious"):
                        st.warning("This excerpt contains instruction-like text and is treated as data only.")
                    st.caption(f"Source: {h['source']} | type: {h['metadata'].get('file_type')}")

    with tab_docs:
        ftype = st.selectbox("Type", ["", "md", "txt", "pdf", "docx"], key="kb-type")
        docs = api_call(api.get, "/knowledge/documents", file_type=ftype, limit=200)
        if docs:
            table(({**d, "updated_at": fmt_ts(d["updated_at"]), "metadata": json.dumps(d["metadata"])}
                   for d in docs["items"]), ["title", "source", "file_type", "chunks", "origin", "metadata", "updated_at"])

    with tab_manage:
        if not has_role(me, "admin"):
            st.info("Only administrators can upload, delete or re-sync documents.")
            return
        uploaded = st.file_uploader("Upload PDF, DOCX, TXT or Markdown (max 20 MB)", type=["pdf", "docx", "txt", "md"],
                                    key="kb-upload")
        meta = st.text_input("Metadata (JSON object, e.g. {\"category\": \"network\"})", "{}", key="kb-meta")
        if uploaded and st.button("Index document", type="primary", key="kb-index"):
            result = api_call(api.upload, "/knowledge/documents", uploaded.name, uploaded.getvalue(), meta)
            if result:
                st.success(f"{result['status'].title()}: {result['title']} ({result['chunks']} chunks)")
        if st.button("Re-sync knowledge directory", key="kb-sync"):
            result = api_call(api.post, "/knowledge/sync")
            if result:
                st.success(str(result))
        docs = api_call(api.get, "/knowledge/documents", limit=200, quiet=True)
        if docs and docs["items"]:
            st.subheader("Delete a document")
            pick = {f"{d['title']} ({d['source']})": d["id"] for d in docs["items"]}
            target = st.selectbox("Document", list(pick), key="kb-del")
            confirm = st.checkbox("I understand this removes the document, its vectors and its file", key="kb-confirm")
            if st.button("Delete", disabled=not confirm, key="kb-delete"):
                if api_call(api.delete, f"/knowledge/documents/{pick[target]}"):
                    st.rerun()
