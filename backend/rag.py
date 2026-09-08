import json
import chromadb
from chromadb.utils import embedding_functions
from backend.config import DATA_DIR, POLICY_DOCS_DIR, CHROMA_DIR, EMBEDDING_MODEL

# Import Langfuse AFTER config (which loads .env) so credentials are available at init time
from langfuse import observe, get_client

COLLECTION_NAME = "truck_rental_kb"


def _get_client() -> chromadb.PersistentClient:
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def _get_embedding_fn():
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )


def _chunk_text(text: str, chunk_size: int = 300, overlap: int = 40) -> list[str]:
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i : i + chunk_size]))
        i += chunk_size - overlap
    return chunks


def build_knowledge_base() -> None:
    """Index all documents into ChromaDB. Safe to call multiple times — skips if already built."""
    client = _get_client()
    emb_fn = _get_embedding_fn()

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=emb_fn,
        metadata={"hnsw:space": "cosine"},
    )

    if collection.count() > 0:
        print(f"[RAG] Knowledge base already contains {collection.count()} chunks. Skipping rebuild.")
        return

    documents, metadatas, ids = [], [], []

    # --- Policy documents ---
    for doc_path in sorted(POLICY_DOCS_DIR.glob("*.txt")):
        text = doc_path.read_text(encoding="utf-8")
        for i, chunk in enumerate(_chunk_text(text)):
            documents.append(chunk)
            metadatas.append({"source": doc_path.name, "type": "policy"})
            ids.append(f"policy_{doc_path.stem}_{i}")

    # --- Truck specifications (one rich document per truck) ---
    with open(DATA_DIR / "truck_data.json") as f:
        truck_data = json.load(f)

    for truck in truck_data["trucks"]:
        text = (
            f"Truck: {truck['name']}\n"
            f"Category: {truck['category']}\n"
            f"Loading Space: {truck['max_loading_space_cuft']} cubic feet\n"
            f"Max Payload: {truck['max_payload_lb']} lbs\n"
            f"Fuel Tank: {truck['max_fuel_tank_gal']} gallons | MPG: {truck['max_mpg']}\n"
            f"GVW: {truck['max_gvw_lb']} lbs\n"
            f"CDL Required: {'Yes' if truck['cdl_required'] else 'No'}\n"
            f"Description: {truck['description']}\n"
            f"Best For: {', '.join(truck['best_for'])}\n"
            f"Approximate Rooms: {truck['approximate_rooms']}"
        )
        documents.append(text)
        metadatas.append({"source": "truck_data.json", "type": "truck", "truck_id": truck["id"]})
        ids.append(f"truck_{truck['id']}")

    # --- Common objects (one document per category) ---
    with open(DATA_DIR / "common_objects.json") as f:
        obj_data = json.load(f)

    for cat_key, cat in obj_data["categories"].items():
        lines = [f"Category: {cat['label']}"]
        for item_key, item in cat["items"].items():
            aliases = ", ".join(item.get("aliases", []))
            line = f"  {item_key}: weight={item['weight_lb']} lbs, volume={item['volume_cuft']} cu ft"
            if aliases:
                line += f" (also known as: {aliases})"
            lines.append(line)
        documents.append("\n".join(lines))
        metadatas.append({"source": "common_objects.json", "type": "objects", "category": cat_key})
        ids.append(f"objects_{cat_key}")

    collection.add(documents=documents, metadatas=metadatas, ids=ids)
    print(f"[RAG] Indexed {len(documents)} chunks into knowledge base.")


@observe(as_type="retriever", name="rag-search", capture_input=False, capture_output=False)
def search(query: str, n_results: int = 4, doc_type: str | None = None) -> list[dict]:
    """Return the top-N most relevant chunks for a query."""
    get_client().update_current_span(input=query)

    client = _get_client()
    emb_fn = _get_embedding_fn()
    collection = client.get_collection(name=COLLECTION_NAME, embedding_function=emb_fn)

    kwargs: dict = dict(
        query_texts=[query],
        n_results=min(n_results, collection.count()),
        include=["documents", "metadatas", "distances"],
    )
    if doc_type:
        kwargs["where"] = {"type": doc_type}

    results = collection.query(**kwargs)

    output = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        output.append({
            "content": doc,
            "source": meta.get("source", "unknown"),
            "type": meta.get("type", "unknown"),
            "relevance_score": round(1 - dist, 3),
        })

    # Log retrieved sources so they're visible in the Langfuse UI
    get_client().update_current_span(
        output=[{"source": r["source"], "score": r["relevance_score"]} for r in output]
    )
    return output


def reset_knowledge_base() -> None:
    """Delete and rebuild the knowledge base from scratch."""
    client = _get_client()
    try:
        client.delete_collection(COLLECTION_NAME)
        print("[RAG] Collection deleted.")
    except Exception:
        pass
    build_knowledge_base()
