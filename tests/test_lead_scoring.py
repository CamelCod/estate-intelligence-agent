"""
Tests for lead scoring logic.
"""

from typing import Dict, List


def score_lead(customer_data: Dict) -> str:
    """
    Score a lead as Hot / Warm / Not Yet based on customer data.

    Args:
        customer_data: dict with keys:
            - property_type: "Villa" | "Compound" | "Apartment" | "Office"
            - camera_count: int
            - has_rtsp: bool
            - travels_frequency: "frequent" | "occasional" | "rare" | "unknown"
            - has_staff: bool
            - current_solution: "none" | "camera_app" | "security_guard" | "other"
    """
    hot_score = 0
    warm_score = 0

    # Property type
    if customer_data.get("property_type") in ("Villa", "Compound"):
        hot_score += 2
    elif customer_data.get("property_type") == "Apartment":
        warm_score += 1

    # Camera count + RTSP access
    camera_count = customer_data.get("camera_count", 0)
    has_rtsp = customer_data.get("has_rtsp", False)

    if camera_count >= 2 and has_rtsp:
        hot_score += 2
    elif camera_count >= 1:
        warm_score += 1

    # Travel frequency
    if customer_data.get("travels_frequency") == "frequent":
        hot_score += 2
    elif customer_data.get("travels_frequency") == "occasional":
        warm_score += 1

    # Staff
    if customer_data.get("has_staff"):
        hot_score += 1

    # Current solution
    current = customer_data.get("current_solution", "none")
    if current == "none":
        hot_score += 1
    elif current == "camera_app":
        warm_score += 1

    # Scoring
    if hot_score >= 5:
        return "Hot"
    elif hot_score >= 3 or warm_score >= 3:
        return "Warm"
    else:
        return "Not Yet"


def get_next_action(score: str) -> str:
    if score == "Hot":
        return "Book 20-min demo call. Show live briefing example. Offer 2-week free trial."
    elif score == "Warm":
        return "Send explainer video + one sample briefing. Follow up in 7 days."
    else:
        return "Add to monthly newsletter list. Check back after 30 days."


def test_hot_lead_villa_with_cameras():
    lead = {
        "property_type": "Villa",
        "camera_count": 6,
        "has_rtsp": True,
        "travels_frequency": "frequent",
        "has_staff": True,
        "current_solution": "none",
    }
    assert score_lead(lead) == "Hot"


def test_warm_lead_apartment():
    lead = {
        "property_type": "Apartment",
        "camera_count": 2,
        "has_rtsp": False,
        "travels_frequency": "unknown",
        "has_staff": True,
        "current_solution": "camera_app",
    }
    assert score_lead(lead) == "Warm"


def test_not_yet_lead_no_cameras():
    lead = {
        "property_type": "Apartment",
        "camera_count": 0,
        "has_rtsp": False,
        "travels_frequency": "rare",
        "has_staff": False,
        "current_solution": "other",
    }
    assert score_lead(lead) == "Not Yet"


def test_next_action_hot():
    assert "demo" in get_next_action("Hot").lower()
    assert "free trial" in get_next_action("Hot").lower()


def test_next_action_warm():
    assert "explainer" in get_next_action("Warm").lower()
    assert "7 days" in get_next_action("Warm").lower()


def test_next_action_not_yet():
    assert "30 days" in get_next_action("Not Yet").lower()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])