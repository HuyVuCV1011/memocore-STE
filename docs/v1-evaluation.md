# Archived V1 Evaluation

This document preserves the useful conclusions from the V1 reliability review. The original provider-expansion proposal was implemented in V1.1 and has been replaced by `implementation_plan.md`.

## Findings

- `llama3.2:1b` was too weak for dependable multi-category extraction.
- Prompt structure, schema validation, and defensive normalization materially improved reliability.
- Provider-specific prompt construction was the wrong boundary.
- Extraction needed a provider-agnostic `chat(ChatRequest) -> ChatResponse` interface.
- Hosted providers could share an OpenAI-compatible HTTP adapter.
- Gemini should use its OpenAI-compatible endpoint unless a measured need justifies a native adapter.
- Legacy Ollama environment variables should remain temporarily supported.
- Invalid model output, not only HTTP errors, should trigger fallback.
- Relative weekday arithmetic should be computed in Python for small models.

## Delivered

- `ExtractionError` moved into the shared LLM base module.
- Prompt ownership moved into `ExtractionService`.
- `system_extraction.md` and `user_extraction.md` replaced the old combined prompt.
- `openai_provider.py` and `provider_factory.py` were added.
- Ollama, OpenAI, Gemini, DeepSeek, OpenRouter, and Groq configuration paths are supported.
- The default local model became `qwen3:14b`.
- Benchmark fixtures and fallback tests were added.

## Remaining Product Lesson

Reliable extraction is necessary but insufficient. Memocore must now be evaluated as a secretary system: does it remember evidence-backed context, track commitments, and reduce manual follow-up work?
