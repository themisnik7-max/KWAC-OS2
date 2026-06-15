"""
KWAC OS — AI Gate
Every Claude API call in the entire system goes through this file.
Budget tracking, tiered context loading, usage logging.
No other file calls the Anthropic API directly.
"""
import logging
import time
from typing import Optional, AsyncGenerator
import anthropic
import config

logger = logging.getLogger("kwac.ai")

client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

# Approximate cost per token (claude-sonnet-4-6)
COST_PER_INPUT_TOKEN  = 0.000003   # $3 per million
COST_PER_OUTPUT_TOKEN = 0.000015   # $15 per million


# ── Tiered context loader ────────────────────────────────────

class ContextLoader:
    """
    L0: ~200 tokens  — one-line summary per agent. Used for leaderboard context.
    L1: ~800 tokens  — current week stats + goals. Used for most AI calls.
    L2: ~3000 tokens — full history. Used only for deep CEO analysis.
    """

    @staticmethod
    def l0_agent_summary(agent: dict) -> str:
        """One line. Used when we need to mention an agent but not analyze them."""
        return (
            f"{agent['full_name']}: Level {agent.get('level',1)}, "
            f"{agent.get('xp_this_week',0)} XP this week, "
            f"streak {agent.get('streak_weeks',0)} weeks."
        )

    @staticmethod
    def l1_agent_context(agent: dict, this_week: dict, last_week: dict, goals: dict) -> str:
        """~800 tokens. Current week performance with comparisons."""
        lines = [
            f"Agent: {agent['full_name']}",
            f"Level: {agent.get('level',1)} | XP this week: {this_week.get('xp_earned',0)} | "
            f"XP last week: {last_week.get('xp_earned',0)} | Streak: {agent.get('streak_weeks',0)} weeks",
            "",
            "This week vs last week (this_week / last_week / goal):",
        ]
        key_metrics = [
            "cold_calls", "first_meetings", "second_meetings",
            "exclusive_listings", "sale_contracts", "rental_contracts",
            "followup_calls", "open_houses"
        ]
        for m in key_metrics:
            tw = this_week.get(m, 0) or 0
            lw = last_week.get(m, 0) or 0
            goal = goals.get(m, {}).get("target", "?")
            hit = "✓" if this_week.get("goals_hit", {}).get(m) else "✗"
            lines.append(f"  {m}: {tw} / {lw} / {goal} {hit}")

        return "\n".join(lines)

    @staticmethod
    def l1_team_context(agents_summary: list[dict], week_label: str) -> str:
        """Team overview for CEO. ~1200 tokens for 50 agents."""
        lines = [f"KWAC Team — Week of {week_label}", ""]
        for a in agents_summary:
            lines.append(
                f"• {a['full_name']}: {a.get('xp_this_week',0)} XP | "
                f"calls {a.get('cold_calls',0)} | "
                f"1st mtg {a.get('first_meetings',0)} | "
                f"listings {a.get('exclusive_listings',0)} | "
                f"contracts {a.get('sale_contracts',0)+a.get('rental_contracts',0)}"
            )
        return "\n".join(lines)


# ── Budget guard ─────────────────────────────────────────────

async def _check_budget(db) -> bool:
    """Returns False if monthly spend exceeds the configured limit."""
    from sqlalchemy import text
    result = await db.execute(
        text("""
            SELECT COALESCE(SUM(cost_usd), 0) as total
            FROM ai_usage_log
            WHERE created_at >= date_trunc('month', NOW())
        """)
    )
    spent = float(result.scalar() or 0)
    if spent >= config.ANTHROPIC_MONTHLY_BUDGET_USD:
        logger.warning(f"AI budget exceeded: ${spent:.4f} >= ${config.ANTHROPIC_MONTHLY_BUDGET_USD}")
        return False
    return True


async def _log_usage(db, call_type: str, user_id: Optional[str],
                     tokens_in: int, tokens_out: int, duration_ms: int,
                     success: bool, error: Optional[str] = None):
    from sqlalchemy import text
    cost = (tokens_in * COST_PER_INPUT_TOKEN) + (tokens_out * COST_PER_OUTPUT_TOKEN)
    await db.execute(
        text("""
            INSERT INTO ai_usage_log
                (called_by, call_type, tokens_input, tokens_output, cost_usd, duration_ms, success, error_msg)
            VALUES
                (:user_id, :call_type, :ti, :to, :cost, :dur, :success, :error)
        """),
        {
            "user_id": user_id, "call_type": call_type,
            "ti": tokens_in, "to": tokens_out, "cost": cost,
            "dur": duration_ms, "success": success, "error": error,
        }
    )
    await db.commit()


# ── Main AI call function ────────────────────────────────────

async def call_claude(
    system: str,
    user_message: str,
    call_type: str,
    db,
    user_id: Optional[str] = None,
    max_tokens: int = 800,
) -> str:
    """
    Single entry point for all Claude calls.
    Returns the text response or raises ValueError with clear message.
    """
    if not await _check_budget(db):
        raise ValueError(
            f"Monthly AI budget (${config.ANTHROPIC_MONTHLY_BUDGET_USD}) reached. "
            "Contact admin to increase limit."
        )

    t_start = time.monotonic()
    tokens_in = tokens_out = 0
    success = False
    error_msg = None

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        success = True
        return response.content[0].text

    except anthropic.APIError as e:
        error_msg = str(e)
        logger.error(f"Claude API error ({call_type}): {e}")
        raise ValueError(f"AI service error: {e}")

    finally:
        duration_ms = int((time.monotonic() - t_start) * 1000)
        try:
            await _log_usage(db, call_type, user_id, tokens_in, tokens_out,
                             duration_ms, success, error_msg)
        except Exception as log_err:
            logger.error(f"Failed to log AI usage: {log_err}")


# ── Specific call wrappers ───────────────────────────────────

async def ceo_chat(question: str, team_context: str, db, user_id: str) -> str:
    """CEO asks a question about the team. L1 context only."""
    system = """You are the intelligence layer for KWAC, a Keller Williams real estate office in Athens, Greece.
You have access to weekly performance data for the team.
Answer in Greek unless the question is in English.
Be direct and actionable. No fluff. Focus on what the CEO should DO with this information.
Never invent numbers — only use the data provided."""

    user = f"""Team data:\n{team_context}\n\nQuestion: {question}"""

    return await call_claude(system, user, "ceo_chat", db, user_id, max_tokens=600)


async def weekly_insights_batch(team_context: str, winners: dict, db) -> str:
    """
    Called once per week by the scheduler.
    Analyzes the entire team in one call.
    """
    system = """You are a performance coach for a real estate team in Athens.
Generate a brief weekly insights report in Greek.
Format: 3-5 bullet points of actionable observations.
Mention winner names. Be specific. Be encouraging but honest.
Max 300 words."""

    winners_text = "\n".join([
        f"- {v['title']}: {v['agent_name']} ({v['value']} {v['metric']})"
        for v in winners.values()
    ]) if winners else "No winners this week."

    user = f"""Team performance:\n{team_context}\n\nWeekly winners:\n{winners_text}"""

    return await call_claude(system, user, "weekly_insights", db, max_tokens=500)


async def valuation_reasoning(
    stats_result,      # ValuationResult dataclass
    property_input: dict,
    db,
    user_id: str,
) -> str:
    """
    Called AFTER Python stats are computed.
    Claude only writes the human-readable explanation.
    """
    system = """You are a real estate valuation expert in Athens, Greece.
You are given statistical analysis already computed by a Python model.
Write a short, professional valuation reasoning paragraph in Greek (3-5 sentences).
Explain WHY this price range makes sense. Mention location and condition.
Do not invent numbers — use only what is provided."""

    user = f"""Property: {property_input.get('address')}, {property_input.get('area')}
Type: {property_input.get('property_type')}, {property_input.get('sqm')}sqm, floor {property_input.get('floor')}
Year built: {property_input.get('year_built')}, condition: {property_input.get('condition')}
Transaction: {property_input.get('transaction_type')}

Statistical result:
- Estimated price: €{stats_result.price_median:,.0f} (range €{stats_result.price_min:,.0f}–€{stats_result.price_max:,.0f})
- Based on {stats_result.comparables_count} comparable properties
- Confidence: {stats_result.confidence}
- Method: {stats_result.method_notes}

Write the reasoning paragraph:"""

    return await call_claude(system, user, "valuation", db, user_id, max_tokens=300)


async def parse_email_lead(email_body: str, db, user_id: str) -> dict:
    """
    Extract structured lead data from a forwarded email.
    Returns a dict ready to insert into people + buyer_requirements tables.
    """
    system = """Extract lead information from this Greek real estate inquiry email.
Return ONLY valid JSON with these fields (use null for missing values):
{
  "full_name": "...",
  "phone": "...",
  "email": "...",
  "transaction_type": "sale|rental|null",
  "property_type": "...",
  "area": "...",
  "sqm": number_or_null,
  "budget": number_or_null,
  "ilist_code": "...",
  "notes": "..."
}"""

    import json
    raw = await call_claude(system, email_body, "lead_parse", db, user_id, max_tokens=300)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse AI lead extraction: {raw}")
        return {}
