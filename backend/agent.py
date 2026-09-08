"""
Ollama LLM interaction layer.
Three responsibilities:
  1. Conversational chat (left panel assistant)
  2. Form data extraction from user messages (auto-fills the right panel)
  3. Natural-language recommendation generation (given pre-computed results)

Provider is selected at runtime via LLM_PROVIDER in .env:
  LLM_PROVIDER=ollama  → routes all calls to local Ollama
  LLM_PROVIDER=gemini  → routes all calls to Google Gemini API
"""
import json
import re
from backend.config import (
    LLM_PROVIDER,
    OLLAMA_MODEL, OLLAMA_BASE_URL,
    GEMINI_API_KEY, GEMINI_MODEL,
)

# Import Langfuse AFTER config (which loads .env) so credentials are available at init time
from langfuse import observe, get_client

# ── System prompts ───────────────────────────────────────────────────

_CHAT_SYSTEM = """You are TruckMate, a friendly and knowledgeable truck rental assistant for Penske.
Your job is to help customers figure out what they need to move or transport, then help them fill in the details form on the right side of the screen.

Conversation rules:
- Keep every reply SHORT: 2-3 sentences max.
- Be warm, professional, and to the point.
- Ask about: what they're moving, how far, how many days, whether they have a CDL.
- If they mention specific items (sofa, mattress, boxes), acknowledge them — those will be added to the form automatically.
- Do NOT invent truck prices, specs, or policies.
- Do NOT ask for information already visible in the conversation.
- After 2-3 exchanges when you have a reasonable picture, tell them the form on the right looks good and they can hit "Get My Recommendation" whenever ready.
"""

_EXTRACT_SYSTEM = """You are a data extraction assistant. Extract structured move/rental details from the user message.
Return ONLY a valid JSON object with any of these fields if clearly stated:
  "move_type": "household" | "commercial" | "delivery"
  "distance_miles": number
  "rental_days": number
  "has_cdl": true | false
  "room_hint": "studio" | "1bed" | "2bed" | "3bed"
  "items": [{"name": "...", "quantity": N}]
  "priority": "cost" | "space" | "fuel_efficiency"

Rules:
- Include a field ONLY if the user explicitly states it.
- For "room_hint": studio/bachelor → "studio", 1 bedroom → "1bed", 2 bedrooms → "2bed", 3+ bedrooms → "3bed".
- For "items": only list items the user specifically names (not inferred from room count).
- Return {} if nothing can be extracted.
- Return ONLY the JSON object, no explanation, no markdown fences."""


# ── Low-level provider routing ───────────────────────────────────────

@observe(as_type="generation", name="ollama-generation", capture_input=False, capture_output=False)
def _call_ollama(system: str, messages: list[dict], temperature: float | None = None) -> str:
    import ollama as _ollama
    full = ([{"role": "system", "content": system}] if system else []) + messages
    opts = {"temperature": temperature} if temperature is not None else {}
    try:
        resp = _ollama.chat(model=OLLAMA_MODEL, messages=full, options=opts or None)
        content = resp["message"]["content"].strip()

        # Record what was sent/received and token usage so Langfuse can calculate cost
        get_client().update_current_generation(
            model=OLLAMA_MODEL,
            input=full,
            output=content,
            usage_details={
                "input": resp.get("prompt_eval_count", 0),
                "output": resp.get("eval_count", 0),
            },
        )
        return content
    except Exception as e:
        raise RuntimeError(
            f"Ollama error ({e}). Make sure `ollama serve` is running and "
            f"`ollama pull {OLLAMA_MODEL}` has been done."
        )


@observe(as_type="generation", name="gemini-generation", capture_input=False, capture_output=False)
def _call_gemini(system: str, messages: list[dict], temperature: float | None = None) -> str:
    import google.generativeai as genai

    if not GEMINI_API_KEY or GEMINI_API_KEY == "your_api_key_here":
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add your key to .env and restart the server."
        )

    genai.configure(api_key=GEMINI_API_KEY)

    gen_config = {}
    if temperature is not None:
        gen_config["temperature"] = temperature

    model = genai.GenerativeModel(
        GEMINI_MODEL,
        system_instruction=system or None,
        generation_config=gen_config or None,
    )

    # Gemini uses "model" instead of "assistant" and requires alternating user/model turns.
    history = []
    for msg in messages[:-1]:
        role = "model" if msg["role"] == "assistant" else "user"
        history.append({"role": role, "parts": [msg["content"]]})

    last = messages[-1]["content"] if messages else ""
    chat = model.start_chat(history=history)
    resp = chat.send_message(last)
    text = resp.text.strip()

    get_client().update_current_generation(
        model=GEMINI_MODEL,
        input=history + [{"role": "user", "parts": [last]}],
        output=text,
        usage_details={
            "input": getattr(getattr(resp, "usage_metadata", None), "prompt_token_count", 0),
            "output": getattr(getattr(resp, "usage_metadata", None), "candidates_token_count", 0),
        },
    )
    return text


def _llm(system: str, messages: list[dict], temperature: float | None = None) -> str:
    """Route to the configured provider. Raises RuntimeError on failure."""
    if LLM_PROVIDER == "gemini":
        return _call_gemini(system, messages, temperature)
    return _call_ollama(system, messages, temperature)


def _safe_llm(system: str, messages: list[dict], temperature: float | None = None) -> tuple[str, str | None]:
    """
    Like _llm but returns (text, error_message).
    Use this in public-facing calls so errors surface as friendly messages.
    """
    try:
        return _llm(system, messages, temperature), None
    except Exception as e:
        return "", str(e)


# ── Public functions ─────────────────────────────────────────────────

def active_provider() -> str:
    """Return a human-readable description of the active provider."""
    if LLM_PROVIDER == "gemini":
        return f"Gemini API ({GEMINI_MODEL})"
    return f"Ollama ({OLLAMA_MODEL})"


@observe(name="chat-response", capture_input=False, capture_output=False)
def chat(messages: list[dict]) -> str:
    # Only log the last user message as input — not the full history
    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    get_client().update_current_span(input=last_user)

    text, err = _safe_llm(_CHAT_SYSTEM, messages)
    if err:
        error_reply = (
            f"I'm having trouble reaching the AI model right now. "
            f"Active provider: {active_provider()}. Error: {err}"
        )
        get_client().update_current_span(output=error_reply)
        return error_reply

    get_client().update_current_span(output=text)
    return text


@observe(name="extract-form-data", capture_input=False, capture_output=False)
def extract_form_data(user_message: str) -> dict:
    """
    Pull structured form fields from the latest user message.
    Returns {} on any failure — best-effort only.
    """
    get_client().update_current_span(input=user_message)
    try:
        raw = _llm(
            _EXTRACT_SYSTEM,
            [{"role": "user", "content": user_message}],
            temperature=0,
        )
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "",          raw)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            get_client().update_current_span(output={})
            return {}
        data = json.loads(match.group())
        result = data if isinstance(data, dict) else {}
        get_client().update_current_span(output=result)
        return result
    except Exception:
        get_client().update_current_span(output={})
        return {}


@observe(name="generate-recommendation", capture_input=False, capture_output=False)
def generate_recommendation(
    form_data: dict,
    ranked_trucks: list[dict],
    load_summary: dict,
    rag_context: list[dict],
    warnings: list[str],
) -> str:
    if not ranked_trucks:
        msg = (
            "Based on your requirements, none of the trucks in the current fleet can "
            "accommodate your load. Please contact a Penske representative directly."
        )
        get_client().update_current_span(
            input={"move_type": form_data.get("move_type"), "distance_miles": form_data.get("distance_miles")},
            output=msg,
        )
        return msg

    top  = ranked_trucks[0]
    alts = ranked_trucks[1:3]
    rag_snippets = "\n".join(f"- {r['content'][:250]}" for r in rag_context[:3])
    alt_text = (
        "\n".join(f"- {t['name']} at ${t['estimated_total_usd']:.2f}" for t in alts)
        or "No alternatives within constraints."
    )

    prompt = f"""You are TruckMate, a Penske truck rental assistant. Write a clear, friendly recommendation.

CUSTOMER CONTEXT:
- Move type: {form_data.get('move_type', 'general')}
- Distance: {form_data.get('distance_miles')} miles over {form_data.get('rental_days')} day(s)
- Has CDL: {'Yes' if form_data.get('has_cdl') else 'No'}
- Priority: {form_data.get('priority', 'cost')}
- Cargo weight (with safety buffer): {load_summary.get('required_payload_lb')} lbs
- Required truck volume: {load_summary.get('required_volume_cuft')} cu ft

TOP RECOMMENDATION:
- Truck: {top['name']}
- Payload: {top['max_payload_lb']} lbs | Utilization: {top['payload_utilization_pct']}%
- Space: {top['max_loading_space_cuft']} cu ft | Utilization: {top['volume_utilization_pct']}%
- Estimated total: ${top['estimated_total_usd']:.2f}
- CDL required: {'Yes' if top['cdl_required'] else 'No'}

ALTERNATIVES:
{alt_text}

RELEVANT POLICY:
{rag_snippets}

WARNINGS:
{chr(10).join(warnings) if warnings else 'None'}

Write exactly 3 short paragraphs in natural prose (no bullet points, no headers):
1. The recommended truck and the single strongest reason why it fits.
2. Plain-English cost breakdown and what is/isn't included.
3. Best alternative (if any) and any important warnings or policy points.

Be specific with numbers. Do not pad."""

    # Log a clean summary at the span level, not the huge prompt string
    get_client().update_current_span(
        input={
            "move_type": form_data.get("move_type"),
            "distance_miles": form_data.get("distance_miles"),
            "top_truck": top["name"],
            "estimated_total_usd": top["estimated_total_usd"],
        }
    )

    text, err = _safe_llm("", [{"role": "user", "content": prompt}])
    if err:
        result = _fallback_text(top, alts, load_summary, warnings)
        get_client().update_current_span(output=result)
        return result

    get_client().update_current_span(output=text)
    return text


def _fallback_text(top, alts, load_summary, warnings):
    parts = [
        f"Based on your requirements, I recommend the **{top['name']}**. "
        f"It covers your {load_summary.get('required_payload_lb')} lb load "
        f"({top['payload_utilization_pct']}% of payload) and "
        f"{load_summary.get('required_volume_cuft')} cu ft of volume "
        f"({top['volume_utilization_pct']}% of space), with comfortable headroom.",
        f"Estimated total: **${top['estimated_total_usd']:.2f}** (base + mileage + fuel). "
        f"Insurance plans are $14–$29/day extra.",
    ]
    if alts:
        parts.append(f"Alternative: **{alts[0]['name']}** at ${alts[0]['estimated_total_usd']:.2f}.")
    if warnings:
        parts.append("Note: " + " ".join(warnings))
    return "\n\n".join(parts)
