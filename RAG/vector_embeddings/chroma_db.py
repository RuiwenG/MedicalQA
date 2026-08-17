import sys
import shutil
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from typing import List
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from common_utils.paths import bge_model_path

class ChromaManager:
    def __init__(self, temp_chroma_dir: str = "temp_rag_chroma_db"):
        self.temp_chroma_dir = Path(temp_chroma_dir)
        
    def initialize_db(self, chunks: List[str], embedding_model = bge_model_path):
        """Initialize ChromaDB with text chunks"""
        if self.temp_chroma_dir.exists():
            print(f"ℹ️ Found existing database directory. Deleting to refresh...")
            shutil.rmtree(self.temp_chroma_dir)

        # NOTE: embeddings are still a LOCAL BGE-M3 model. Only Qwen moved to
        # the API, so RAG remains the one pipeline that needs model weights on
        # disk. Fall back to CPU so it can at least run without a GPU.
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

        print(f"⏳ Loading BGE-M3 embedding model on {device}...")
        embed_model = HuggingFaceEmbeddings(
            model_name=str(embedding_model),
            model_kwargs={"device": device},
            encode_kwargs={"batch_size": 16}
        )

        print("⏳ Creating vector database...")
        vectordb = Chroma.from_texts(
            texts=chunks,
            embedding=embed_model,
            persist_directory=str(self.temp_chroma_dir)
        )
        vectordb.persist()
        
        del embed_model  # Free up memory
        return vectordb