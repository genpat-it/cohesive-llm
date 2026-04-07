import os
import json
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from app.core.config import settings

class DataLoader:
    def __init__(self):
        self.vector_store = None
        self.code_db = {}
        self.comp_db = {}
        self.tmpl_db = {}
        self.res_list = []

    def load_all(self, store=None):
        print("Loading Resources...")
        self._load_lookups(store)
        self._load_vector_store()
        print("✅ Resources Loaded.")

    def _load_lookups(self, store=None):
        # Load Code Store
        if os.path.exists(settings.CODE_STORE):
            with open(settings.CODE_STORE, 'r') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        if entry.get('id'): 
                            self.code_db[entry['id']] = entry['content']
                            if store: store.put(("code",), entry['id'], {"content": entry['content']})
                    except: continue

        # Load Catalogs
        if os.path.exists(settings.CATALOG_COMPONENTS):
            with open(settings.CATALOG_COMPONENTS, 'r') as f:
                self.comp_db = {c['id']: c for c in json.load(f).get('components', [])}
                if store:
                    for k,v in self.comp_db.items(): store.put(("components",), k, v)
                
        if os.path.exists(settings.CATALOG_TEMPLATES):
            with open(settings.CATALOG_TEMPLATES, 'r') as f:
                self.tmpl_db = {c['id']: c for c in json.load(f).get('templates', [])}
                if store:
                    for k,v in self.tmpl_db.items(): store.put(("templates",), k, v)
               
        if os.path.exists(settings.CATALOG_RESOURCES):
            with open(settings.CATALOG_RESOURCES, 'r') as f:
                raw_resources = json.load(f).get('resources', {})
                self.res_list = raw_resources.get('helper_functions', [])
                self.containers_list = raw_resources.get('containers', [])
                if store:
                    store.put(("resources",), "helper_functions", {"list": self.res_list})
                    store.put(("resources",), "containers", {"list": self.containers_list})

    def _load_vector_store(self):
        print(f"Loading Embeddings {settings.EMBEDDING_MODEL} (CPU)...")
        embeddings = HuggingFaceEmbeddings(
            model_name=settings.EMBEDDING_MODEL,
            model_kwargs={'device': 'cpu', 'trust_remote_code': True},
            encode_kwargs={'normalize_embeddings': True, 'batch_size': 4}
        )
        try:
            self.vector_store = FAISS.load_local(
                settings.FAISS_INDEX_PATH, 
                embeddings, 
                allow_dangerous_deserialization=True
            )
        except Exception as e:
            print(f"⚠️ Vector Store Error: {e}")

# Global Instance
data_loader = DataLoader()