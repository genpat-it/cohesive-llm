import warnings
from abc import ABC, abstractmethod

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings


class VectorStoreAdapter(ABC):
    """Abstract interface for Vector DB operations."""

    @abstractmethod
    def similarity_search_with_score(self, query: str, k: int) -> list[tuple[Document, float]]:
        pass

class FaissAdapter(VectorStoreAdapter):
    def __init__(self, index_path: str, embeddings: Embeddings):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from langchain_community.vectorstores import FAISS
            self.vector_store = FAISS.load_local(
                index_path,
                embeddings,
                allow_dangerous_deserialization=True
            )

    def similarity_search_with_score(self, query: str, k: int) -> list[tuple[Document, float]]:
        return self.vector_store.similarity_search_with_score(query, k=k)

class ChromaAdapter(VectorStoreAdapter):
    def __init__(self, index_path: str, embeddings: Embeddings, collection_name: str = "izs_catalog"):
        import chromadb
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                from langchain_chroma import Chroma
            except ImportError:
                from langchain_community.vectorstores import Chroma

        client = chromadb.PersistentClient(path=index_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.vector_store = Chroma(
                client=client,
                collection_name=collection_name,
                embedding_function=embeddings
            )

    def similarity_search_with_score(self, query: str, k: int) -> list[tuple[Document, float]]:
        return self.vector_store.similarity_search_with_score(query, k=k)
