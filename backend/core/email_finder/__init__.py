"""
Company email-finder: a self-contained module, independent of the
product-generation pipeline (backend/core/pipeline.py etc.).

Given a company name + location (+ optionally a known URL), finds the
company's real official contact email — never an LLM-invented one. See
pipeline.py's module docstring for the full fallback chain and confidence
tiers.
"""
from __future__ import annotations
