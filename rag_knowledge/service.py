"""RAG Knowledge Service coordinating retrieval and Gemini synthesis."""

import asyncio
import logging
from typing import Optional
from .retrieval.retriever import KnowledgeRetriever
from .clients.gemini_client import GeminiRAGClient

logger = logging.getLogger("rag.service")


class RAGService:
    """High-level service for knowledge retrieval and LLM synthesis."""

    def __init__(
        self,
        retriever: Optional[KnowledgeRetriever] = None,
        llm_client: Optional[GeminiRAGClient] = None,
    ):
        self.retriever = retriever or KnowledgeRetriever()
        self.llm_client = llm_client or GeminiRAGClient()

    def _get_local_fallback_context(self, query: str) -> str:
        """Reads local knowledge documents from rag_knowledge/data/raw/ and returns context."""
        from pathlib import Path
        raw_dir = Path(__file__).resolve().parent / "data" / "raw"
        if not raw_dir.exists():
            return ""

        context_chunks = []
        for file_path in raw_dir.glob("*.*"):
            if file_path.suffix.lower() in (".md", ".txt", ".json", ".csv"):
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore").strip()
                    if content:
                        context_chunks.append(f"Document: {file_path.name}\n{content}")
                except Exception as e:
                    logger.debug(f"Could not read local fallback document {file_path}: {e}")

        return "\n\n---\n\n".join(context_chunks)

    async def query(
        self,
        user_query: str,
        pre_retrieved: Optional[list] = None,
    ) -> str:
        """Processes a user question, retrieves relevant facts, and synthesizes an answer.

        Args:
            user_query: The question asked by user (e.g. 'When is the hackathon scheduled?').
            pre_retrieved: Optional pre-retrieved results to avoid redundant database calls.

        Returns:
            Grounded answer string ready for voice output.
        """
        clean_q = (user_query or "").strip()
        if not clean_q:
            return "Please specify what you would like to know about."

        import os
        retrieval_top_k = int(os.getenv("RAG_TOP_K", "5"))
        min_context_score = float(os.getenv("RAG_CONTEXT_MIN_SCORE", "50.0"))

        if pre_retrieved is not None:
            results = pre_retrieved
        else:
            results = await asyncio.to_thread(self.retriever.retrieve, clean_q, top_k=retrieval_top_k)

        if not results:
            local_context = self._get_local_fallback_context(clean_q)
            if local_context and self.llm_client.is_configured:
                logger.info("Synthesizing answer from local knowledge documents for query: '%s'", clean_q)
                answer = await self.llm_client.generate_answer(clean_q, local_context)
                if answer and answer.strip():
                    return answer.strip()

            if not self.retriever.store.is_available():
                logger.warning("Knowledge database is unreachable for query: '%s'", clean_q)
                return "The knowledge database is currently unavailable. Please try again shortly."
            logger.info("No RAG results found for query: '%s'", clean_q)
            return f"I don't have specific details on '{clean_q}' in my knowledge base right now."

        top_doc = results[0]
        logger.info(f"Retrieved top match: '{top_doc.get('title')}' (score={top_doc.get('score')})")

        docs_to_use = [results[0]]
        if len(results) > 1:
            top_score = float(results[0].get("score", 0.0))
            for doc in results[1:retrieval_top_k]:
                doc_score = float(doc.get("score", 0.0))
                if doc_score >= min_context_score or doc_score >= top_score * 0.80:
                    docs_to_use.append(doc)

        context_parts = []
        for doc in docs_to_use:
            context_parts.append(f"Title: {doc.get('title')}\nDetails: {doc.get('content')}")
        context = "\n\n".join(context_parts)

        if self.llm_client.is_configured:
            answer = await self.llm_client.generate_answer(clean_q, context)
            if answer:
                return answer

        summary = top_doc.get("summary", "").strip()
        content = top_doc.get("content", "").strip()
        return summary or content


_default_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    global _default_service
    if _default_service is None:
        _default_service = RAGService()
    return _default_service


async def query_rag(query: str) -> str:
    """Query RAG knowledge base."""
    service = get_rag_service()
    return await service.query(query)
