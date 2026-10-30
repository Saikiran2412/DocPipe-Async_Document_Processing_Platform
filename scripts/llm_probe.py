from groq import Groq

from app.config import settings

client = Groq(api_key=settings.groq_api_key, timeout=settings.llm_timeout)
MESSAGES = [
    {"role": "system", "content": "You summarise documents in two sentences."},
    {"role": "user", "content": "Invoice No: INV-2026-001. Bill to Asha Rao. Widget x 10 $1,000.00, shipping $250.00. Total due $1,250.00 by 2026-11-30."},
]

def run(label, **extra):
    print("---", label)
    try:
        r = client.chat.completions.create(model=settings.llm_model, messages=MESSAGES, **extra)
        c = r.choices[0]
        print("finish_reason:", c.finish_reason)
        print("content:", repr(c.message.content))
        print("tokens: completion=%s reasoning=%s total=%s" % (
            r.usage.completion_tokens,
            getattr(r.usage.completion_tokens_details, "reasoning_tokens", None),
            r.usage.total_tokens))
    except Exception as exc:
        print("ERROR:", type(exc).__name__, str(exc)[:300])

run("A: bigger limit (600)", max_completion_tokens=600)
run("B: low reasoning effort (400)", max_completion_tokens=400, reasoning_effort="low")
