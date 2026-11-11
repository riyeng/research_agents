# -*- coding: utf-8 -*-
"""
Token tracking and cost estimation for multi-agent peer review system.

Tracks:
- Input/output tokens per agent
- Total tokens per role (selection, persona, debate, editor)
- Cost estimation based on model pricing
- Per-round breakdowns
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import json


@dataclass
class TokenUsage:
    """Token usage for a single LLM call.

    ``input_tokens`` is the TOTAL prompt input as reported by the API's
    ``prompt_tokens`` — it ALREADY INCLUDES any cached tokens.
    ``cache_read_tokens`` and ``cache_creation_tokens`` are SUBSETS of
    ``input_tokens``, not separate additions. estimate_cost() therefore prices the
    fresh (non-cached) remainder at the input rate and the cached subsets at their
    own rates, with NO double-counting.
    ``thinking_tokens`` are a diagnostic subset of ``output_tokens`` in OpenAI
    usage responses. They must not be added or priced a second time.
    """
    role: str
    model: str
    input_tokens: int
    output_tokens: int
    temperature: float
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    thinking_tokens: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class TokenTracker:
    """Tracks token usage across all LLM calls in a peer review session."""

    # OpenAI Standard short-context pricing per 1M tokens, verified 2026-07-29.
    # This experiment truncates prompts well below the 272K long-context threshold.
    PRICING = {
        "gpt-5.6-sol": {
            "input": 5.00,
            "output": 30.00,
            "thinking": 30.00,
            "cache_read": 0.50,
            "cache_creation": 6.25,
        }
    }

    def __init__(self):
        """Initialize token tracker."""
        self.calls: List[TokenUsage] = []
        self.start_time = datetime.now()

    def track(
        self,
        role: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        temperature: float,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        thinking_tokens: int = 0
    ):
        """
        Record token usage for an LLM call.

        Args:
            role: Agent role (e.g., "Econometrician", "Editor-Selection")
            model: Model identifier
            input_tokens: TOTAL prompt tokens as reported by the API's
                ``prompt_tokens`` (already includes any cached tokens below).
            output_tokens: Number of output tokens
            temperature: Temperature used
            cache_read_tokens: Tokens read from prompt cache (billed at a
                discount). A subset of ``input_tokens``.
            cache_creation_tokens: Tokens written to the prompt cache (billed at
                a slight premium). A subset of ``input_tokens``.
            thinking_tokens: Reasoning-token subset of ``output_tokens``.
        """
        usage = TokenUsage(
            role=role,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            temperature=temperature,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            thinking_tokens=thinking_tokens
        )
        self.calls.append(usage)

    def get_total_tokens(self) -> Dict[str, int]:
        """Get total input, output, thinking, and cached tokens.

        ``input`` is the sum of API-reported ``prompt_tokens`` (which already
        includes cached tokens), and ``total`` is input + output. ``thinking`` is
        reported separately as a subset of output.
        ``cache_read`` / ``cache_creation`` surface the cached subset of ``input``
        separately; do not add them to ``input`` when reasoning about total prompt
        volume. ``thinking`` tokens are billed separately at output token rates.
        """
        total_input = sum(call.input_tokens for call in self.calls)
        total_output = sum(call.output_tokens for call in self.calls)
        total_thinking = sum(call.thinking_tokens for call in self.calls)
        total_cache_read = sum(call.cache_read_tokens for call in self.calls)
        total_cache_creation = sum(call.cache_creation_tokens for call in self.calls)
        return {
            "input": total_input,
            "output": total_output,
            "thinking": total_thinking,
            "total": total_input + total_output,
            "cache_read": total_cache_read,
            "cache_creation": total_cache_creation
        }

    def get_tokens_by_role(self) -> Dict[str, Dict[str, int]]:
        """Get token breakdown by role."""
        by_role = {}
        for call in self.calls:
            if call.role not in by_role:
                by_role[call.role] = {
                    "input": 0, "output": 0, "thinking": 0, "total": 0,
                    "cache_read": 0, "cache_creation": 0, "calls": 0
                }
            by_role[call.role]["input"] += call.input_tokens
            by_role[call.role]["output"] += call.output_tokens
            by_role[call.role]["thinking"] += call.thinking_tokens
            by_role[call.role]["total"] += call.input_tokens + call.output_tokens
            by_role[call.role]["cache_read"] += call.cache_read_tokens
            by_role[call.role]["cache_creation"] += call.cache_creation_tokens
            by_role[call.role]["calls"] += 1
        return by_role

    def get_tokens_by_model(self) -> Dict[str, Dict[str, int]]:
        """Get token breakdown by model."""
        by_model = {}
        for call in self.calls:
            if call.model not in by_model:
                by_model[call.model] = {
                    "input": 0, "output": 0, "thinking": 0, "total": 0,
                    "cache_read": 0, "cache_creation": 0, "calls": 0
                }
            by_model[call.model]["input"] += call.input_tokens
            by_model[call.model]["output"] += call.output_tokens
            by_model[call.model]["thinking"] += call.thinking_tokens
            by_model[call.model]["total"] += call.input_tokens + call.output_tokens
            by_model[call.model]["cache_read"] += call.cache_read_tokens
            by_model[call.model]["cache_creation"] += call.cache_creation_tokens
            by_model[call.model]["calls"] += 1
        return by_model

    def estimate_cost(self, model: str = None) -> float:
        """
        Estimate cost based on token usage.

        Args:
            model: Specific model to calculate cost for (None = all models)

        Returns:
            Estimated cost in USD
        """
        total_cost = 0.0

        calls_to_price = self.calls
        if model:
            calls_to_price = [c for c in self.calls if c.model == model]

        # Cached tokens are a SUBSET of the API-reported ``prompt_tokens``
        # (input_tokens), not a separate addition. The OpenAI-compatible endpoint
        # returns ``prompt_tokens`` as the TOTAL prompt including cached tokens,
        # while ``cache_read_input_tokens`` / ``cache_creation_input_tokens`` break
        # out how much of that total was served from / written to cache. To price
        # correctly we bill the FRESH remainder (input_tokens - cache_read -
        # cache_creation) at the input rate and the cached subsets at their own
        # rates. Billing full input_tokens plus the cache subsets would
        # double-charge every cached token.
        for call in calls_to_price:
            pricing = self.PRICING.get(call.model)
            if pricing:
                input_rate = pricing["input"]
                # Fall back gracefully if a model's PRICING lacks cached rates or thinking rate.
                cache_read_rate = pricing.get("cache_read", 0.1 * input_rate)
                cache_creation_rate = pricing.get("cache_creation", 1.25 * input_rate)
                fresh_input_tokens = (
                    call.input_tokens
                    - call.cache_read_tokens
                    - call.cache_creation_tokens
                )
                # Guard against any provider that reports cache tokens on top of
                # (rather than within) prompt_tokens, which would drive this
                # negative; never bill negative fresh input.
                fresh_input_tokens = max(0, fresh_input_tokens)
                input_cost = (fresh_input_tokens / 1_000_000) * input_rate
                output_cost = (call.output_tokens / 1_000_000) * pricing["output"]
                cache_read_cost = (call.cache_read_tokens / 1_000_000) * cache_read_rate
                cache_creation_cost = (call.cache_creation_tokens / 1_000_000) * cache_creation_rate
                total_cost += input_cost + output_cost + cache_read_cost + cache_creation_cost

        return total_cost

    def get_cost_by_model(self) -> Dict[str, float]:
        """Get cost breakdown by model."""
        by_model = {}
        for model in set(call.model for call in self.calls):
            by_model[model] = self.estimate_cost(model)
        return by_model

    def get_summary(self) -> Dict:
        """Get comprehensive summary of token usage and costs."""
        total_tokens = self.get_total_tokens()
        by_role = self.get_tokens_by_role()
        by_model = self.get_tokens_by_model()
        cost_by_model = self.get_cost_by_model()
        total_cost = sum(cost_by_model.values())

        duration = (datetime.now() - self.start_time).total_seconds()

        return {
            "duration_seconds": duration,
            "total_calls": len(self.calls),
            "total_tokens": total_tokens,
            "by_role": by_role,
            "by_model": by_model,
            "cost_by_model": cost_by_model,
            "total_cost": total_cost
        }

    def print_summary(self):
        """Print formatted summary to console."""
        summary = self.get_summary()

        print("\n" + "="*80)
        print("TOKEN USAGE & COST SUMMARY")
        print("="*80)

        # Overall stats
        print(f"Total Calls:     {summary['total_calls']}")
        print(f"Duration:        {summary['duration_seconds']:.1f} seconds")
        print(f"Total Tokens:    {summary['total_tokens']['total']:,}")
        print(f"  Input:         {summary['total_tokens']['input']:,}")
        print(f"  Output:        {summary['total_tokens']['output']:,}")
        print(f"  Thinking:      {summary['total_tokens']['thinking']:,}")
        print(f"  Cached-Read:   {summary['total_tokens']['cache_read']:,}")
        print(f"  Cache-Creation:{summary['total_tokens']['cache_creation']:,}")
        print(f"Estimated Cost:  ${summary['total_cost']:.4f}")

        # By model
        print("\n" + "-"*80)
        print("USAGE BY MODEL")
        print("-"*80)
        for model, tokens in summary['by_model'].items():
            model_name = model
            cost = summary['cost_by_model'].get(model, 0)
            print(f"{model_name.upper():8s} | Calls: {tokens['calls']:2d} | "
                  f"Input: {tokens['input']:7,} | Output: {tokens['output']:7,} | "
                  f"Thinking: {tokens['thinking']:7,} | "
                  f"CacheRead: {tokens['cache_read']:7,} | "
                  f"CacheCreate: {tokens['cache_creation']:7,} | "
                  f"Total: {tokens['total']:7,} | Cost: ${cost:.4f}")

        # By role (top consumers)
        print("\n" + "-"*80)
        print("USAGE BY ROLE (Top 10)")
        print("-"*80)
        sorted_roles = sorted(
            summary['by_role'].items(),
            key=lambda x: x[1]['total'],
            reverse=True
        )[:10]

        for role, tokens in sorted_roles:
            print(f"{role:25s} | Calls: {tokens['calls']:2d} | "
                  f"Input: {tokens['input']:7,} | Output: {tokens['output']:7,} | "
                  f"Thinking: {tokens['thinking']:7,} | "
                  f"CacheRead: {tokens['cache_read']:7,} | "
                  f"CacheCreate: {tokens['cache_creation']:7,} | "
                  f"Total: {tokens['total']:7,}")

        print("="*80 + "\n")

    def save_to_file(self, filepath: str):
        """
        Save detailed token tracking data to JSON file.

        Args:
            filepath: Path to save JSON file
        """
        data = {
            "summary": self.get_summary(),
            "calls": [
                {
                    "role": call.role,
                    "model": call.model,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "cache_read_tokens": call.cache_read_tokens,
                    "cache_creation_tokens": call.cache_creation_tokens,
                    "thinking_tokens": call.thinking_tokens,
                    "temperature": call.temperature,
                    "timestamp": call.timestamp
                }
                for call in self.calls
            ]
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

        print(f"[TokenTracker] Saved detailed usage to: {filepath}")

    def get_markdown_summary(self) -> str:
        """
        Generate markdown-formatted summary for inclusion in report.

        Returns:
            Markdown string
        """
        summary = self.get_summary()

        md = ["# Token Usage & Cost Summary", ""]

        # Overall stats
        md.append("## Overall Statistics")
        md.append("")
        md.append(f"- **Total Calls**: {summary['total_calls']}")
        md.append(f"- **Duration**: {summary['duration_seconds']:.1f} seconds")
        md.append(f"- **Total Tokens**: {summary['total_tokens']['total']:,}")
        md.append(f"  - Input: {summary['total_tokens']['input']:,}")
        md.append(f"  - Output: {summary['total_tokens']['output']:,}")
        md.append(f"  - Cached-Read: {summary['total_tokens']['cache_read']:,}")
        md.append(f"  - Cache-Creation: {summary['total_tokens']['cache_creation']:,}")
        md.append(f"- **Estimated Cost**: ${summary['total_cost']:.4f}")
        md.append("")

        # By model
        md.append("## Usage by Model")
        md.append("")
        md.append("| Model | Calls | Input | Output | Cached-Read | Cache-Creation | Total | Cost |")
        md.append("|-------|-------|-------|--------|-------------|----------------|-------|------|")

        for model, tokens in summary['by_model'].items():
            model_name = model
            cost = summary['cost_by_model'].get(model, 0)
            md.append(f"| {model_name.capitalize()} | {tokens['calls']} | "
                     f"{tokens['input']:,} | {tokens['output']:,} | "
                     f"{tokens['cache_read']:,} | {tokens['cache_creation']:,} | "
                     f"{tokens['total']:,} | ${cost:.4f} |")

        md.append("")

        # By role (top 10)
        md.append("## Usage by Role (Top 10)")
        md.append("")
        md.append("| Role | Calls | Input | Output | Cached-Read | Cache-Creation | Total |")
        md.append("|------|-------|-------|--------|-------------|----------------|-------|")

        sorted_roles = sorted(
            summary['by_role'].items(),
            key=lambda x: x[1]['total'],
            reverse=True
        )[:10]

        for role, tokens in sorted_roles:
            md.append(f"| {role} | {tokens['calls']} | "
                     f"{tokens['input']:,} | {tokens['output']:,} | "
                     f"{tokens['cache_read']:,} | {tokens['cache_creation']:,} | "
                     f"{tokens['total']:,} |")

        return "\n".join(md)


# Test/demo
if __name__ == "__main__":
    print("Testing TokenTracker...\n")

    # Create tracker
    tracker = TokenTracker()

    # Simulate some calls
    tracker.track("Editor-Selection", "anthropic.claude-sonnet-4-5-20250929-v1:0", 5000, 500, 0.0)
    tracker.track("Econometrician", "anthropic.claude-opus-4-8-20250820-v1:0", 8000, 2000, 0.0)
    tracker.track("Policymaker", "anthropic.claude-sonnet-4-5-20250929-v1:0", 8000, 2000, 0.0)
    tracker.track("Historian", "anthropic.claude-haiku-4-5-20251001-v1:0", 8000, 1500, 0.0)
    tracker.track("Debater-Econometrician", "anthropic.claude-opus-4-8-20250820-v1:0", 10000, 3000, 0.35)
    tracker.track("Debater-Policymaker", "anthropic.claude-sonnet-4-5-20250929-v1:0", 10000, 3000, 0.35)
    tracker.track("Debater-Historian", "anthropic.claude-haiku-4-5-20251001-v1:0", 10000, 2500, 0.35)
    tracker.track("Editor-In-Chief", "anthropic.claude-opus-4-8-20250820-v1:0", 12000, 4000, 0.0)
    # Cached-input path: fresh input is small, most of the prompt is served
    # from cache (cache_read) with some newly-cached tokens (cache_creation).
    tracker.track(
        "Debater-Policymaker",
        "anthropic.claude-sonnet-4-5-20250929-v1:0",
        input_tokens=2000,
        output_tokens=1500,
        temperature=0.35,
        cache_read_tokens=18000,
        cache_creation_tokens=4000
    )

    # Print summary
    tracker.print_summary()

    # Test markdown generation
    print("\nMarkdown Summary:")
    print(tracker.get_markdown_summary())

    # Test JSON export
    tracker.save_to_file("test_token_usage.json")

    print("\n✓ TokenTracker tests complete!")
