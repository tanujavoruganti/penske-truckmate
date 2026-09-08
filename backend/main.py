import json
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend import rag, agent, tools
from backend.config import DATA_DIR, BASE_DIR

# Import Langfuse AFTER config (which loads .env) so credentials are available at init time
from langfuse import observe, get_client


# ---------------------------------------------------------------------------
# Startup: build RAG knowledge base
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[Startup] Initialising knowledge base...")
    rag.build_knowledge_base()
    print("[Startup] Ready.")
    yield


app = FastAPI(title="TruckMate API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    session_id: Optional[str] = None  # groups multi-turn conversations in Langfuse Sessions view

class ChatResponse(BaseModel):
    response: str
    extracted_data: dict = {}


class CargoItem(BaseModel):
    name: str
    quantity: int = Field(default=1, ge=1)
    weight_lb: Optional[float] = None    # overrides DB lookup when provided
    volume_cuft: Optional[float] = None  # overrides DB lookup when provided

class IntakeForm(BaseModel):
    move_type: str = "household"                # "household" | "commercial" | "delivery"
    distance_miles: float = Field(ge=0)
    rental_days: int = Field(default=1, ge=1)
    items: list[CargoItem] = []
    custom_total_weight_lb: Optional[float] = None   # skip item lookup, use this directly
    custom_total_volume_cuft: Optional[float] = None
    has_cdl: bool = False
    max_budget_usd: Optional[float] = None
    priority: str = "cost"              # "cost" | "space" | "fuel_efficiency"
    chat_context: str = ""              # summary from the warmup conversation


# ---------------------------------------------------------------------------
# Utility endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/trucks")
def get_trucks():
    with open(DATA_DIR / "truck_data.json", encoding="utf-8") as f:
        return json.load(f)

@app.get("/api/objects")
def get_objects():
    with open(DATA_DIR / "common_objects.json", encoding="utf-8") as f:
        return json.load(f)

@app.get("/api/costs")
def get_costs():
    with open(DATA_DIR / "cost_profiles.json", encoding="utf-8") as f:
        return json.load(f)

@app.get("/api/rag/search")
def rag_search(q: str, n: int = 4):
    return rag.search(q, n_results=n)

@app.post("/api/rag/reset")
def rag_reset():
    rag.reset_knowledge_base()
    return {"status": "rebuilt"}


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------

@app.post("/api/chat", response_model=ChatResponse)
@observe(name="chat-request", capture_input=False, capture_output=False)
def chat_endpoint(req: ChatRequest):
    messages = [m.model_dump() for m in req.messages]

    # Extract the latest user message for form auto-fill
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )

    # Set trace-level input/output to just the meaningful values
    get_client().set_current_trace_io(input=last_user)

    response_text = agent.chat(messages)
    extracted    = agent.extract_form_data(last_user) if last_user else {}

    get_client().set_current_trace_io(output=response_text)
    return ChatResponse(response=response_text, extracted_data=extracted)


# ---------------------------------------------------------------------------
# Recommendation pipeline
# ---------------------------------------------------------------------------

@app.post("/api/recommend")
@observe(name="recommend-request", capture_input=False, capture_output=False)
def recommend_endpoint(form: IntakeForm):
    # Set trace-level input showing what the user is asking for
    get_client().set_current_trace_io(
        input={
            "move_type": form.move_type,
            "distance_miles": form.distance_miles,
            "rental_days": form.rental_days,
            "has_cdl": form.has_cdl,
            "priority": form.priority,
            "item_count": len(form.items),
        }
    )

    warnings: list[str] = []

    # 1. Calculate load from item list (or use custom overrides)
    item_dicts = [i.model_dump() for i in form.items]
    load = tools.calculate_load(item_dicts)

    required_weight = form.custom_total_weight_lb or load["required_payload_lb"]
    required_volume = form.custom_total_volume_cuft or load["required_volume_cuft"]

    if load["unresolved_items"]:
        warnings.append(
            f"Could not find database entries for: {', '.join(load['unresolved_items'])}. "
            "Rough defaults were used — actual weight may differ."
        )

    # 2. Filter trucks by constraints
    compatible = tools.filter_trucks(required_weight, required_volume, form.has_cdl)

    if not compatible:
        # Relax weight constraint to at least show volume-compatible trucks with a warning
        compatible_by_volume = tools.filter_trucks(0, required_volume, form.has_cdl)
        if compatible_by_volume:
            warnings.append(
                f"Your estimated cargo ({required_weight:.0f} lbs) exceeds the payload of all "
                "available trucks. Showing closest options — consider splitting into multiple trips "
                "or renting the CDL variant if you have a commercial license."
            )
            compatible = compatible_by_volume
        else:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "No trucks can accommodate this load within the current fleet.",
                    "required_weight_lb": required_weight,
                    "required_volume_cuft": required_volume,
                    "has_cdl": form.has_cdl,
                    "suggestion": "Contact Penske directly for large or specialised freight.",
                },
            )

    # 3. Rank by chosen priority + attach cost estimates
    ranked = tools.rank_trucks(
        compatible,
        form.rental_days,
        form.distance_miles,
        required_weight,
        required_volume,
        form.priority,
    )

    # 4. Filter by budget (soft — show cheapest over-budget option if nothing fits)
    if form.max_budget_usd:
        within_budget = [t for t in ranked if t["estimated_total_usd"] <= form.max_budget_usd]
        over_budget = [t for t in ranked if t["estimated_total_usd"] > form.max_budget_usd]
        if not within_budget:
            warnings.append(
                f"All compatible trucks exceed your stated budget of ${form.max_budget_usd:.0f}. "
                "Showing the most affordable option."
            )
            ranked = [over_budget[0]]
        else:
            ranked = within_budget

    # 5. RAG: pull relevant policy context
    rag_query = (
        f"{form.move_type} move {form.distance_miles} miles "
        f"{'CDL' if form.has_cdl else 'no CDL'} payload {required_weight:.0f} lbs"
    )
    rag_results = rag.search(rag_query, n_results=4)

    # 6. LLM generates natural-language explanation
    explanation = agent.generate_recommendation(
        form_data=form.model_dump(),
        ranked_trucks=ranked,
        load_summary=load,
        rag_context=rag_results,
        warnings=warnings,
    )

    result = {
        "recommended_truck": ranked[0],
        "alternatives": ranked[1:],
        "load_summary": load,
        "explanation": explanation,
        "warnings": warnings,
        "policy_snippets": [r["content"][:400] for r in rag_results[:2]],
    }
    get_client().set_current_trace_io(
        output={"recommended_truck": ranked[0]["name"], "estimated_total_usd": ranked[0]["estimated_total_usd"]}
    )
    return result


# ---------------------------------------------------------------------------
# Serve frontend (must come last so API routes take priority)
# ---------------------------------------------------------------------------

frontend_dir = BASE_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir / "static"), html=False), name="static")

    @app.get("/", include_in_schema=False)
    def serve_index():
        return FileResponse(str(frontend_dir / "index.html"))
