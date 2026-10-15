import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


def get_ai_commentary(market_analysis: dict, news_analysis: dict = None) -> dict:
    if os.getenv("AI_ENABLED", "false").lower() != "true":
        return {
            "enabled": False,
            "status": "disabled",
            "commentary": None,
        }

    api_key = os.getenv("AI_API_KEY")
    base_url = os.getenv(
        "AI_BASE_URL",
        "https://api.openai.com/v1",
    )
    model = os.getenv("AI_MODEL", "gpt-4.1-mini")

    if not api_key:
        return {
            "enabled": True,
            "status": "unavailable",
            "commentary": None,
            "reason": "AI_API_KEY is missing",
        }

    try:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=20.0,
            max_retries=1,
        )

        prompt_data = {
            "symbol": "XAU/USD",
            "technical_analysis": market_analysis,
            "news_analysis": news_analysis,
        }

        response = client.chat.completions.create(
            model=model,
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert cautious gold (XAU/USD) market-analysis assistant. "
                        "Analyze the supplied gold technical indicators, Support & Resistance levels, and economic news calendar. "
                        "Do not invent live prices, news, or economic data. "
                        "Do not claim guaranteed profitability. "
                        "Return JSON with these fields: "
                        "trend (bullish, bearish, or neutral), "
                        "support_resistance_view (string explaining key price barriers), "
                        "news_impact_view (string analyzing how upcoming news affects trading risk), "
                        "risk_notes (array of strings), "
                        "summary (string), "
                        "suggested_view (BUY, SELL, or WAIT). "
                        "This is commentary only, not an executable order. "
                        "If evidence is insufficient or high-impact news is imminent, use WAIT."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt_data),
                },
            ],
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content

        if not content:
            raise ValueError("AI returned an empty response")

        result = json.loads(content)

        # Treat AI output as untrusted commentary.
        if result.get("suggested_view") not in {
            "BUY", "SELL", "WAIT"
        }:
            result["suggested_view"] = "WAIT"

        if result.get("trend") not in {
            "bullish", "bearish", "neutral"
        }:
            result["trend"] = "neutral"

        if not isinstance(result.get("risk_notes"), list):
            result["risk_notes"] = []

        if not isinstance(result.get("summary"), str):
            result["summary"] = "No summary supplied"

        return {
            "enabled": True,
            "status": "ok",
            "commentary": result,
        }

    except Exception:
        # Avoid exposing API keys, request headers, or provider details.
        return {
            "enabled": True,
            "status": "unavailable",
            "commentary": None,
            "reason": "AI provider request failed; check server logs and configuration",
        }
