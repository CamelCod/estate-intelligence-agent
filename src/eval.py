"""
eval.py — Propositional Evaluation Engine
Estate Intelligence Agent

Every check is a verifiable proposition: a named boolean claim that is
either satisfied (True) or violated (False). Reports are structured as
a table of propositions so a human or LLM can reason over them directly.

Architecture:
  Proposition       — a single named, typed assertion with pass/fail + evidence
  EvalResult        — an ordered collection of propositions + aggregate verdict
  EvalRunner        — executes named eval suites against inputs
  CognitiveReport   — renders an EvalResult as a propositional report (text or JSON)
"""

from __future__ import annotations

import re
import json
import time
import hashlib
import dataclasses
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from enum import Enum

try:
    from billing import TIER_PRICES_AED, resolve_tier  # when run as package: python -m src.eval
except ModuleNotFoundError:
    from src.billing import TIER_PRICES_AED, resolve_tier  # when run as script: python src/eval.py


# ─────────────────────────────────────────────────────────────────────────────
# TYPES
# ─────────────────────────────────────────────────────────────────────────────

class Severity(str, Enum):
    """How bad is a violation?"""
    CRITICAL  = "critical"   # blocks production use
    MAJOR     = "major"      # degrades quality significantly
    MINOR     = "minor"      # cosmetic or edge-case
    INFO      = "info"       # observation only, never fails eval


class Status(str, Enum):
    PASS    = "PASS"
    FAIL    = "FAIL"
    SKIP    = "SKIP"    # proposition could not be evaluated (missing data)
    INFO    = "INFO"


@dataclass
class Proposition:
    """
    A single verifiable claim about a piece of output.

    Example:
        Proposition(
            name        = "briefing.word_count_within_limit",
            claim       = "Briefing contains ≤ 250 words",
            status      = Status.PASS,
            evidence    = "Word count: 162",
            severity    = Severity.MAJOR,
        )
    """
    name      : str
    claim     : str
    status    : Status
    evidence  : str                  = ""
    severity  : Severity             = Severity.MAJOR
    measured  : Optional[Any]        = None   # raw measured value


@dataclass
class EvalResult:
    """Ordered collection of Propositions + aggregate verdict."""
    suite       : str
    input_hash  : str
    propositions: List[Proposition] = field(default_factory=list)
    elapsed_ms  : float             = 0.0
    metadata    : Dict[str, Any]    = field(default_factory=dict)

    # ── computed properties ────────────────────────────────────────────────

    @property
    def passed(self) -> List[Proposition]:
        return [p for p in self.propositions if p.status == Status.PASS]

    @property
    def failed(self) -> List[Proposition]:
        return [p for p in self.propositions if p.status == Status.FAIL]

    @property
    def critical_failures(self) -> List[Proposition]:
        return [p for p in self.failed if p.severity == Severity.CRITICAL]

    @property
    def score(self) -> float:
        """0.0 – 1.0 score weighting critical failures heavily."""
        if not self.propositions:
            return 0.0
        total  = sum(_SCORE_WEIGHTS[p.severity] for p in self.propositions if p.status != Status.INFO)
        earned = sum(_SCORE_WEIGHTS[p.severity] for p in self.propositions if p.status == Status.PASS)
        return round(earned / total, 3) if total else 1.0

    @property
    def verdict(self) -> str:
        if self.critical_failures:
            return "BLOCKED"
        if self.score >= 0.90:
            return "PASS"
        if self.score >= 0.70:
            return "WARN"
        return "FAIL"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suite"       : self.suite,
            "verdict"     : self.verdict,
            "score"       : self.score,
            "elapsed_ms"  : round(self.elapsed_ms, 1),
            "input_hash"  : self.input_hash,
            "propositions": [dataclasses.asdict(p) for p in self.propositions],
            "metadata"    : self.metadata,
        }


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

# Module-level weights — allocated once, reused on every score() call
_SCORE_WEIGHTS: Dict[Severity, int] = {
    Severity.CRITICAL: 3,
    Severity.MAJOR   : 2,
    Severity.MINOR   : 1,
    Severity.INFO    : 0,
}


def _prop(
    name: str, claim: str, value: bool, evidence: str = "",
    severity: Severity = Severity.MAJOR, measured: Any = None
) -> Proposition:
    """Shortcut to build a Proposition from a boolean value."""
    return Proposition(
        name     = name,
        claim    = claim,
        status   = Status.PASS if value else Status.FAIL,
        evidence = evidence,
        severity = severity,
        measured = measured,
    )


def _info(name: str, claim: str, evidence: str = "") -> Proposition:
    return Proposition(name=name, claim=claim, status=Status.INFO, evidence=evidence)


def _fingerprint(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:12]


# ─────────────────────────────────────────────────────────────────────────────
# BRIEFING EVAL SUITE
# ─────────────────────────────────────────────────────────────────────────────

_EMOJI_RE  = re.compile(
    "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F9FF\U00002702-\U000027B0]",
    re.UNICODE
)
_MD_RE     = re.compile(r"(\*\*|__|\*|_|#{1,6} |`{1,3}|-{3,}|\[.+?\]\(.+?\))")
_SECURITY_WORDS = re.compile(
    r"\b(security|surveillance|monitoring system|tracking|spy|"
    r"CCTV agent|AI-powered security|smart camera system)\b",
    re.IGNORECASE
)


def eval_briefing(text: str, customer_name: str = "") -> EvalResult:
    """
    Evaluate a daily briefing against all format propositions.
    Returns an EvalResult with a score and full proposition table.
    """
    t0   = time.monotonic()
    props: List[Proposition] = []

    # ── P1: Length ─────────────────────────────────────────────────────────
    words = len(text.split())
    props.append(_prop(
        "briefing.word_count_within_limit",
        "Briefing contains ≤ 250 words",
        words <= 250,
        f"Word count: {words}",
        Severity.MAJOR, words
    ))

    # ── P2: Minimum content ────────────────────────────────────────────────
    props.append(_prop(
        "briefing.has_minimum_content",
        "Briefing contains at least 30 words",
        words >= 30,
        f"Word count: {words}",
        Severity.CRITICAL, words
    ))

    # ── P3: No raw markdown ────────────────────────────────────────────────
    md_hits = _MD_RE.findall(text)
    props.append(_prop(
        "briefing.no_markdown_formatting",
        "Briefing contains no markdown formatting",
        len(md_hits) == 0,
        f"Markdown tokens found: {md_hits[:5]}" if md_hits else "Clean",
        Severity.MAJOR, md_hits
    ))

    # ── P4: Emoji count ────────────────────────────────────────────────────
    emoji_count = len(_EMOJI_RE.findall(text))
    props.append(_prop(
        "briefing.emoji_count_within_limit",
        "Briefing contains ≤ 3 emoji",
        emoji_count <= 3,
        f"Emoji count: {emoji_count}",
        Severity.MINOR, emoji_count
    ))

    # ── P5: Ends with question ─────────────────────────────────────────────
    stripped = text.strip()
    has_question = stripped.endswith("?") or "?" in stripped[-60:]
    props.append(_prop(
        "briefing.ends_with_engagement_question",
        "Briefing ends with an engagement question",
        has_question,
        "Question found in last 60 chars" if has_question else "No closing question detected",
        Severity.MAJOR
    ))

    # ── P6: SIRA compliance — no forbidden words ───────────────────────────
    hits = _SECURITY_WORDS.findall(text)
    props.append(_prop(
        "briefing.sira_compliant_no_forbidden_words",
        "Briefing contains no SIRA-risk words (security, surveillance, …)",
        len(hits) == 0,
        f"Forbidden words found: {hits}" if hits else "SIRA-clean",
        Severity.CRITICAL, hits
    ))

    # ── P7: Customer name present ──────────────────────────────────────────
    if customer_name:
        first_name = customer_name.split()[0]
        name_present = first_name.lower() in text.lower()
        props.append(_prop(
            "briefing.addresses_customer_by_name",
            f"Briefing addresses customer as '{first_name}'",
            name_present,
            f"Name '{first_name}' {'found' if name_present else 'not found'} in text",
            Severity.MINOR
        ))

    # ── P8: No speculation ────────────────────────────────────────────────
    speculation = re.findall(r"\b(probably|maybe|might have|I think|I believe|not sure)\b", text, re.I)
    props.append(_prop(
        "briefing.no_speculation",
        "Briefing makes no speculative claims",
        len(speculation) == 0,
        f"Speculative phrases: {speculation}" if speculation else "No speculation detected",
        Severity.MAJOR, speculation
    ))

    # ── INFO: paragraph count ──────────────────────────────────────────────
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    props.append(_info(
        "briefing.paragraph_count",
        f"Briefing has {len(paragraphs)} paragraphs",
        f"{len(paragraphs)} paragraph(s) detected"
    ))

    elapsed = (time.monotonic() - t0) * 1000
    return EvalResult(
        suite="briefing",
        input_hash=_fingerprint(text),
        propositions=props,
        elapsed_ms=elapsed,
        metadata={"word_count": words, "customer_name": customer_name}
    )


# ─────────────────────────────────────────────────────────────────────────────
# LEAD SCORING EVAL SUITE
# ─────────────────────────────────────────────────────────────────────────────

def eval_lead(lead_data: Dict[str, Any]) -> EvalResult:
    """
    Evaluate a lead record against qualification propositions.

    lead_data keys:
        property_type   : str  (Villa | Apartment | Compound | Office)
        camera_count    : int
        has_rtsp        : bool
        travel_frequency: str  (frequent | occasional | rarely)
        has_staff       : bool
        current_solution: str  (none | camera_app | NVR | other)
    """
    t0    = time.monotonic()
    props : List[Proposition] = []

    pt  = lead_data.get("property_type", "").lower()
    cc  = lead_data.get("camera_count", 0)
    rtsp = lead_data.get("has_rtsp", False)
    trav = lead_data.get("travel_frequency", "").lower()
    staff= lead_data.get("has_staff", False)
    sol  = lead_data.get("current_solution", "none").lower()

    props.append(_prop(
        "lead.property_type_qualifies",
        "Property type is Villa or Compound (ideal fit)",
        pt in ("villa", "compound"),
        f"Property type: {pt}",
        Severity.CRITICAL
    ))

    props.append(_prop(
        "lead.has_minimum_cameras",
        "Lead has at least 1 camera",
        cc >= 1,
        f"Camera count: {cc}",
        Severity.CRITICAL, cc
    ))

    props.append(_prop(
        "lead.cameras_have_rtsp",
        "Cameras support RTSP (required for pipeline)",
        rtsp,
        "RTSP confirmed" if rtsp else "No RTSP — manual setup required",
        Severity.MAJOR
    ))

    props.append(_prop(
        "lead.owner_travels_frequently",
        "Owner travels frequently (primary use case)",
        trav == "frequent",
        f"Travel frequency: {trav}",
        Severity.MAJOR
    ))

    props.append(_prop(
        "lead.has_staff_to_monitor",
        "Owner employs staff (staff accountability value prop applies)",
        staff,
        "Staff present" if staff else "No staff reported",
        Severity.MINOR
    ))

    props.append(_prop(
        "lead.no_competing_solution",
        "Owner has no existing monitoring solution (clear air)",
        sol in ("none", "camera_app"),
        f"Current solution: {sol}",
        Severity.MINOR
    ))

    # Tier recommendation — use billing resolve_tier for consistency
    tier = resolve_tier(cc)
    price = TIER_PRICES_AED.get(tier)

    props.append(_info(
        "lead.recommended_tier",
        f"Recommended tier: {tier} ({price or 'Quote'} AED/month)",
        f"{cc} cameras → {tier} tier"
    ))

    elapsed = (time.monotonic() - t0) * 1000
    return EvalResult(
        suite="lead_qualification",
        input_hash=_fingerprint(lead_data),
        propositions=props,
        elapsed_ms=elapsed,
        metadata={"recommended_tier": tier, "camera_count": cc}
    )


# ─────────────────────────────────────────────────────────────────────────────
# RTSP EVAL SUITE
# ─────────────────────────────────────────────────────────────────────────────

def eval_rtsp_url(url: str) -> EvalResult:
    """Evaluate an RTSP URL against structural propositions (no live check)."""
    t0    = time.monotonic()
    props : List[Proposition] = []

    is_rtsp = url.lower().startswith("rtsp://")
    props.append(_prop(
        "rtsp.scheme_is_rtsp",
        "URL scheme is rtsp://",
        is_rtsp,
        f"Scheme: {url[:10]}",
        Severity.CRITICAL
    ))

    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        has_host = bool(parsed.hostname)
        port     = parsed.port or 554
        props.append(_prop(
            "rtsp.has_resolvable_host",
            "URL contains a hostname or IP",
            has_host,
            f"Host: {parsed.hostname}",
            Severity.CRITICAL, parsed.hostname
        ))
        props.append(_prop(
            "rtsp.port_is_standard_or_custom",
            "RTSP port is in valid range (1–65535)",
            1 <= port <= 65535,
            f"Port: {port}",
            Severity.MAJOR, port
        ))
        props.append(_info(
            "rtsp.has_stream_path",
            "URL includes a stream path",
            f"Path: {parsed.path or '(none)'}"
        ))
    except Exception as exc:
        props.append(Proposition(
            name="rtsp.parse_error",
            claim="URL is parseable",
            status=Status.FAIL,
            evidence=str(exc),
            severity=Severity.CRITICAL
        ))

    elapsed = (time.monotonic() - t0) * 1000
    return EvalResult(
        suite="rtsp_url",
        input_hash=_fingerprint(url),
        propositions=props,
        elapsed_ms=elapsed
    )


# ─────────────────────────────────────────────────────────────────────────────
# SUBSCRIPTION EVAL SUITE
# ─────────────────────────────────────────────────────────────────────────────

def eval_subscription(sub_data: Dict[str, Any]) -> EvalResult:
    """
    Evaluate whether a customer subscription is in a valid billable state.

    sub_data keys:
        status          : str   (active | trialing | past_due | cancelled | unpaid)
        tier            : str   (starter | standard | estate | custom)
        camera_count    : int
        trial_days_left : int
        payment_method  : bool  (has valid payment method on file)
    """
    t0    = time.monotonic()
    props : List[Proposition] = []

    status     = sub_data.get("status", "").lower()
    tier       = sub_data.get("tier", "").lower()
    cameras    = sub_data.get("camera_count", 0)
    trial_left = sub_data.get("trial_days_left", 0)
    has_pm     = sub_data.get("payment_method", False)

    # Tier camera limits — mirrors billing.TIER_CAMERA_LIMITS (single source there)
    tier_limits: Dict[str, int] = {"starter": 3, "standard": 8, "estate": 16, "custom": 9999}
    limit = tier_limits.get(tier, 0)
    props.append(_prop(
        "subscription.is_active_or_trialing",
        "Subscription status allows service delivery",
        status in ("active", "trialing"),
        f"Status: {status}",
        Severity.CRITICAL, status
    ))

    limit = tier_limits.get(tier, 0)
    props.append(_prop(
        "subscription.camera_count_within_tier",
        f"Camera count ({cameras}) within tier limit ({limit} for {tier})",
        cameras <= limit,
        f"{cameras} cameras, tier limit {limit}",
        Severity.MAJOR, cameras
    ))

    props.append(_prop(
        "subscription.has_payment_method",
        "Customer has a valid payment method on file",
        has_pm,
        "Payment method on file" if has_pm else "No payment method — required before trial ends",
        Severity.MAJOR if status == "trialing" else Severity.CRITICAL
    ))

    if status == "trialing":
        props.append(_prop(
            "subscription.trial_has_days_remaining",
            "Trial period has not expired",
            trial_left > 0,
            f"Trial days remaining: {trial_left}",
            Severity.MAJOR, trial_left
        ))

    props.append(_info(
        "subscription.tier_info",
        f"Current tier: {tier.capitalize()} — {cameras} cameras",
        f"Tier: {tier}, Cameras: {cameras}, Status: {status}"
    ))

    elapsed = (time.monotonic() - t0) * 1000
    return EvalResult(
        suite="subscription",
        input_hash=_fingerprint(sub_data),
        propositions=props,
        elapsed_ms=elapsed,
        metadata=sub_data
    )


# ─────────────────────────────────────────────────────────────────────────────
# COGNITIVE REPORT RENDERER
# ─────────────────────────────────────────────────────────────────────────────

class CognitiveReport:
    """
    Renders one or more EvalResults as a propositional report.

    The "propositional" framing means each check is written as a named
    assertion rather than a prose description. Readers can scan the table,
    see at a glance which claims hold and which don't, and trace any failure
    back to its evidence without reading prose.
    """

    VERDICT_EMOJI = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "BLOCKED": "🚫"}
    STATUS_EMOJI  = {
        Status.PASS: "✓",
        Status.FAIL: "✗",
        Status.SKIP: "—",
        Status.INFO: "ℹ",
    }
    SEVERITY_BADGE = {
        Severity.CRITICAL: "[CRIT]",
        Severity.MAJOR   : "[MAJR]",
        Severity.MINOR   : "[MINR]",
        Severity.INFO    : "[INFO]",
    }

    @classmethod
    def render_text(cls, result: EvalResult) -> str:
        """Render a single EvalResult as a plain-text propositional table."""
        lines = []
        v     = result.verdict
        lines.append(f"╔══ EVAL: {result.suite.upper()} ══╗")
        lines.append(f"  Verdict  : {cls.VERDICT_EMOJI.get(v, v)} {v}")
        lines.append(f"  Score    : {result.score:.1%}  ({len(result.passed)}/{len(result.propositions)} propositions satisfied)")
        lines.append(f"  Duration : {result.elapsed_ms:.1f}ms")
        lines.append(f"  Input    : sha256:{result.input_hash}")
        lines.append("")
        lines.append("  PROPOSITIONS")
        lines.append("  " + "─" * 64)

        for p in result.propositions:
            mark = cls.STATUS_EMOJI[p.status]
            badge = cls.SEVERITY_BADGE[p.severity]
            lines.append(f"  {mark} {badge} {p.name}")
            lines.append(f"       Claim   : {p.claim}")
            if p.evidence:
                lines.append(f"       Evidence: {p.evidence}")
            lines.append("")

        if result.critical_failures:
            lines.append("  CRITICAL FAILURES (block deployment)")
            for p in result.critical_failures:
                lines.append(f"  ⛔  {p.name}: {p.evidence}")
            lines.append("")

        lines.append("╚" + "═" * 40 + "╝")
        return "\n".join(lines)

    @classmethod
    def render_json(cls, result: EvalResult, indent: int = 2) -> str:
        return json.dumps(result.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def render_batch_text(cls, results: List[EvalResult]) -> str:
        """Render multiple EvalResults as a combined propositional report."""
        sections = [cls.render_text(r) for r in results]
        summary  = cls._batch_summary(results)
        return summary + "\n\n" + "\n\n".join(sections)

    @classmethod
    def _batch_summary(cls, results: List[EvalResult]) -> str:
        total_props   = sum(len(r.propositions) for r in results)
        total_passed  = sum(len(r.passed) for r in results)
        total_failed  = sum(len(r.failed) for r in results)
        blocked       = [r for r in results if r.verdict == "BLOCKED"]
        avg_score     = sum(r.score for r in results) / len(results) if results else 0

        lines = [
            "═" * 50,
            "  COGNITIVE EVAL REPORT — ESTATE INTELLIGENCE AGENT",
            "═" * 50,
            f"  Suites evaluated  : {len(results)}",
            f"  Total propositions: {total_props}",
            f"  Satisfied         : {total_passed}  ({total_passed/total_props:.0%})" if total_props else "  Satisfied: 0",
            f"  Violated          : {total_failed}",
            f"  Average score     : {avg_score:.1%}",
            f"  Blocked suites    : {len(blocked)}",
        ]
        if blocked:
            lines.append(f"  ⛔  Blocked: {', '.join(r.suite for r in blocked)}")
        lines.append("═" * 50)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# EVAL RUNNER
# ─────────────────────────────────────────────────────────────────────────────

class EvalRunner:
    """
    Registers and dispatches eval suites by name.
    Provides a single run() entry-point for the API layer.
    """

    _suites: Dict[str, Callable] = {
        "briefing"     : eval_briefing,
        "lead"         : eval_lead,
        "rtsp"         : eval_rtsp_url,
        "subscription" : eval_subscription,
    }

    @classmethod
    def run(cls, suite: str, payload: Any, **kwargs) -> EvalResult:
        fn = cls._suites.get(suite)
        if fn is None:
            raise ValueError(f"Unknown eval suite: '{suite}'. Available: {list(cls._suites)}")
        return fn(payload, **kwargs)

    @classmethod
    def run_all(cls, payloads: Dict[str, Any]) -> List[EvalResult]:
        """
        Run multiple suites in one call.
        payloads: {"briefing": text, "lead": lead_dict, ...}
        """
        return [cls.run(suite, payload) for suite, payload in payloads.items()]

    @classmethod
    def available_suites(cls) -> List[str]:
        return list(cls._suites)


# ─────────────────────────────────────────────────────────────────────────────
# QUICK SELF-TEST (run: python src/eval.py)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample_briefing = (
        "Good evening, Ahmed. Here's your home briefing for Monday, 6 April.\n\n"
        "Mariam arrived at 7:02 AM as expected. Khalid was not logged today "
        "— no camera activity detected at the staff entrance after 9 AM.\n\n"
        "A delivery arrived at 7:58 AM via the front gate. The package was "
        "moved inside at 9:44 AM by Mariam.\n\n"
        "The side gate camera was offline from 11:30 AM to 1:21 PM. "
        "It reconnected automatically and is now live.\n\n"
        "Everything else looked normal today. "
        "Is there anything specific you would like me to check? 🏡"
    )

    sample_lead = {
        "property_type"   : "Villa",
        "camera_count"    : 6,
        "has_rtsp"        : True,
        "travel_frequency": "frequent",
        "has_staff"       : True,
        "current_solution": "camera_app",
    }

    sample_sub = {
        "status"         : "trialing",
        "tier"           : "standard",
        "camera_count"   : 6,
        "trial_days_left": 10,
        "payment_method" : False,
    }

    results = EvalRunner.run_all({
        "briefing"    : sample_briefing,
        "lead"        : sample_lead,
        "subscription": sample_sub,
    })

    # Pass customer_name separately for briefing
    results[0] = eval_briefing(sample_briefing, customer_name="Ahmed Al Mansouri")

    print(CognitiveReport.render_batch_text(results))
