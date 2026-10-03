"""Prompt templates for the VLM extraction pipeline.

Each module here follows the same standard structure: a stable SYSTEM prompt
(role, task, hard constraints in priority order, output contract, one worked
example) plus a small `build_*_user_prompt()` function for the per-call
variable content. The two are concatenated into one string before being sent
to `ModelClient.generate()`, since none of the backends (Ollama, NVIDIA NIM,
Groq) currently expose a separate system/user role through that interface — see
backend.app.services.models.provider.ModelClient. Splitting the text this way
still keeps priority-ordered constraints easy to locate and edit, instead of
buried mid-paragraph in one long block.

Currently one module: extraction.py (single-pass page extraction).
"""

from __future__ import annotations
