"""MemoryOS LLM layer (M2 enrichment + M3 Ask synthesis).

All cloud calls are optional: every function degrades gracefully without a
GROQ_API_KEY (heuristic titles, retrieval-only answers). See
ARCHITECTURE.md §6 for the model/prompt/caching decisions.
"""

import json
import os
import re

GROQ_LLM_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODEL = "gemini-2.5-flash"
CATEGORIES = ("meeting", "call", "standup", "brainstorm", "interview",
              "lecture", "personal", "note", "conversation")

ENRICH_PROMPT = """You index conversation transcripts for a personal memory \
system. Analyze the transcript and return ONLY a JSON object:
{
  "title": "specific, useful title, max 8 words",
  "category": "one of: %s",
  "summary": "2-3 sentence summary",
  "entities": [
    {"type": "person|project|topic|action|promise|decision|deadline|question",
     "value": "short name or statement", "context": "brief context"}
  ]
}
Rules: extract only what is actually in the transcript — never invent names \
or facts. Promises are commitments someone made; actions are tasks to do; \
deadlines include the stated time. 3-15 entities is typical.

Transcript:
""" % "|".join(CATEGORIES)

ASK_PROMPT = """You are MemoryOS, the user's second brain. Answer their \
question using ONLY the memory excerpts below. Each excerpt is tagged \
[source N]. Cite sources inline as [N]. If the excerpts don't contain the \
answer, say so plainly — never invent memories.

Question: {question}

Memory excerpts:
{excerpts}
"""


def api_key() -> str | None:
    return os.environ.get("GROQ_API_KEY") or None


def gemini_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or None


def ai_available() -> bool:
    return bool(gemini_key() or api_key())


def _gemini_chat(content: str, key: str, json_mode: bool = False) -> str:
    import httpx

    body = {"contents": [{"parts": [{"text": content}]}],
            "generationConfig": {"temperature": 0.2}}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    r = httpx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={key}",
        json=body, timeout=120)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def llm_chat(content: str, json_mode: bool = False) -> str:
    """Provider chain: Gemini primary → Groq fallback. Raises RuntimeError
    with the collected errors if every configured provider fails."""
    errors = []
    if gemini_key():
        try:
            return _gemini_chat(content, gemini_key(), json_mode)
        except Exception as e:
            errors.append(f"gemini: {e}")
    if api_key():
        try:
            return _chat(content, api_key(), json_mode)
        except Exception as e:
            errors.append(f"groq: {e}")
    raise RuntimeError("; ".join(errors) or "no AI provider configured")


def _chat(content: str, key: str, json_mode: bool = False) -> str:
    import httpx

    body = {
        "model": GROQ_LLM_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
                   headers={"Authorization": f"Bearer {key}"},
                   json=body, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# ---------------- M2: enrichment ----------------

_TS_LINE = re.compile(r"^(?:\[[\d:]+\]\s*)?(?:Speaker \d+:\s*)?")


def heuristic_title(transcript: str) -> str:
    """First real words of the conversation, cleaned up. Local, instant."""
    for line in transcript.splitlines():
        words = _TS_LINE.sub("", line).strip().split()
        if len(words) >= 3:
            title = " ".join(words[:8])
            return title.rstrip(".,;:") + ("…" if len(words) > 8 else "")
    return "Untitled memory"


def enrich_transcript(transcript: str) -> dict:
    """Returns {title, category, summary, entities, mode} — never raises."""
    fallback = {"title": heuristic_title(transcript), "category": "conversation",
                "summary": "", "entities": [], "mode": "heuristic"}
    if not ai_available() or not transcript.strip():
        return fallback
    try:
        raw = llm_chat(ENRICH_PROMPT + transcript[:24000], json_mode=True)
        data = json.loads(raw)
        entities = [e for e in data.get("entities", [])
                    if isinstance(e, dict) and e.get("type") and e.get("value")]
        category = data.get("category", "conversation")
        return {
            "title": str(data.get("title", "")).strip() or fallback["title"],
            "category": category if category in CATEGORIES else "conversation",
            "summary": str(data.get("summary", "")).strip(),
            "entities": entities,
            "mode": "llm",
        }
    except Exception:
        return fallback


# ---------------- M3: Ask synthesis ----------------

def ask_answer(question: str, hits: list[dict],
               titles: dict[str, str] | None = None) -> dict:
    """Build an answer from retrieval hits. With a key: LLM synthesis with
    citations. Without: honest retrieval-only response. Never raises."""
    titles = titles or {}

    def cite(h):
        m, s = divmod(int(h.get("start") or 0), 60)
        return {"recording": h["recording"],
                "title": titles.get(h["recording"], h["recording"]),
                "start": h.get("start") or 0, "stamp": f"{m}:{s:02d}",
                "text": h["text"]}

    citations = [cite(h) for h in hits]
    if not hits:
        return {"answer": "I couldn't find anything about that in your "
                          "memories yet.", "citations": [], "mode": "empty"}

    if not ai_available():
        return {"answer": "Here's what I found in your memories (add a free "
                          "Gemini or Groq API key for synthesized answers):",
                "citations": citations, "mode": "retrieval"}
    try:
        excerpts = "\n".join(
            f"[source {i + 1}] ({c['title']} @ {c['stamp']}"
            f"{', ' + hits[i]['speaker'] if hits[i].get('speaker') else ''}) "
            f"{c['text']}"
            for i, c in enumerate(citations))
        answer = llm_chat(ASK_PROMPT.format(question=question,
                                            excerpts=excerpts)).strip()
        return {"answer": answer, "citations": citations, "mode": "llm"}
    except Exception:
        return {"answer": "The AI service is unreachable right now — here's "
                          "what I found in your memories:",
                "citations": citations, "mode": "retrieval"}
