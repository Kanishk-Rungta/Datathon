"""Kannada policing-vocabulary ASR benchmark.

**This benchmark has never been run.** Running it needs audio for each corpus
line and a speech service with real AI4Bharat weights loaded; neither existed
in the environment this was written in. What is here is the measurement, ready
to execute — the corpus, the metrics, and the runner — so the accuracy question
becomes one command rather than a research task.

Why it exists at all: general-purpose word error rate says very little about
whether this platform works. An officer asks about ``ಸರಗಳ್ಳತನ`` (chain
snatching) in ``ಚಿಕ್ಕಮಗಳೂರು``; a model that transcribes the sentence at 92%
WER but drops the crime term and the district has failed at the only job that
mattered. So the headline metric here is **critical-term recall**, not WER —
whether the words the platform must route on survived.

The entity resolver was calibrated against labelled pairs before its thresholds
were trusted. This is the same discipline applied to speech.

Usage::

    # 1. Record or synthesise one audio file per corpus id into --audio-dir,
    #    named <id>.wav (16 kHz mono).
    # 2. Start the speech service with real weights (not SPEECH_STUB_MODE).
    # 3. Run:
    python -m tests.evals.kannada_asr.harness \\
        --audio-dir ./kn-audio --base-url http://127.0.0.1:9100
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

CORPUS_PATH = Path(__file__).with_name("corpus.jsonl")

#: Below this, the platform should not be described as supporting Kannada
#: speech for policing use. It is a starting position for review, not a
#: certification: the number that matters is agreed with whoever owns the
#: deployment, and this file records what was measured either way.
CRITICAL_TERM_RECALL_TARGET = 0.90


@dataclass(slots=True)
class Fixture:
    id: str
    reference: str
    gloss: str
    critical_terms: list[str]
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Outcome:
    fixture: Fixture
    hypothesis: str
    word_error_rate: float
    terms_found: list[str]
    terms_missed: list[str]

    @property
    def term_recall(self) -> float:
        total = len(self.fixture.critical_terms)
        return len(self.terms_found) / total if total else 1.0


def load_corpus(path: Path = CORPUS_PATH) -> list[Fixture]:
    fixtures: list[Fixture] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        fixtures.append(Fixture(
            id=raw["id"], reference=raw["reference"], gloss=raw["gloss"],
            critical_terms=list(raw.get("critical_terms", [])),
            tags=list(raw.get("tags", [])),
        ))
    return fixtures


def normalise(text: str) -> list[str]:
    """Tokenise for comparison, dropping punctuation but never characters.

    Kannada is not whitespace-poor, so word splitting is sound, but stripping
    diacritics would quietly make near-misses look like matches.
    """
    cleaned = re.sub(r"[^\w\sಀ-೿]", " ", text or "")
    return [token for token in cleaned.split() if token]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over words, divided by reference length."""
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i]
        for j, hyp_word in enumerate(hyp, start=1):
            current.append(min(
                previous[j] + 1,            # deletion
                current[j - 1] + 1,         # insertion
                previous[j - 1] + (ref_word != hyp_word),  # substitution
            ))
        previous = current
    return previous[-1] / len(ref)


def score(fixture: Fixture, hypothesis: str) -> Outcome:
    found = [term for term in fixture.critical_terms if term in (hypothesis or "")]
    missed = [term for term in fixture.critical_terms if term not in (hypothesis or "")]
    return Outcome(
        fixture=fixture, hypothesis=hypothesis,
        word_error_rate=word_error_rate(fixture.reference, hypothesis),
        terms_found=found, terms_missed=missed,
    )


def summarise(outcomes: Iterable[Outcome]) -> dict[str, Any]:
    outcomes = list(outcomes)
    if not outcomes:
        return {"fixtures": 0}
    by_tag: dict[str, list[float]] = {}
    for outcome in outcomes:
        for tag in outcome.fixture.tags:
            by_tag.setdefault(tag, []).append(outcome.term_recall)
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731 - local brevity
    recalls = [o.term_recall for o in outcomes]
    return {
        "fixtures": len(outcomes),
        "critical_term_recall": round(mean(recalls), 4),
        "word_error_rate": round(mean([o.word_error_rate for o in outcomes]), 4),
        "meets_target": mean(recalls) >= CRITICAL_TERM_RECALL_TARGET,
        "target": CRITICAL_TERM_RECALL_TARGET,
        "recall_by_tag": {tag: round(mean(values), 4) for tag, values in sorted(by_tag.items())},
        "worst": [
            {"id": o.fixture.id, "missed": o.terms_missed, "heard": o.hypothesis}
            for o in sorted(outcomes, key=lambda o: o.term_recall)[:5]
            if o.terms_missed
        ],
    }


def transcribe_via_service(base_url: str, audio_path: Path, language: str = "kn") -> str:
    """Call the speech service's /asr endpoint. urllib, like every adapter here."""
    import base64
    from urllib import request as urllib_request

    payload = json.dumps({
        "audio_base64": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
        "audio_format": audio_path.suffix.lstrip(".") or "wav",
        "language": language,
    }).encode("utf-8")
    call = urllib_request.Request(f"{base_url.rstrip('/')}/asr", data=payload, method="POST")
    call.add_header("Content-Type", "application/json")
    with urllib_request.urlopen(call, timeout=120) as response:
        return str(json.loads(response.read().decode("utf-8")).get("text", ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", required=True, type=Path,
                        help="Directory of <fixture-id>.wav files, 16 kHz mono")
    parser.add_argument("--base-url", default="http://127.0.0.1:9100")
    parser.add_argument("--language", default="kn")
    parser.add_argument("--report", type=Path, help="Write the JSON summary here")
    args = parser.parse_args(argv)

    outcomes: list[Outcome] = []
    missing: list[str] = []
    for fixture in load_corpus():
        audio = args.audio_dir / f"{fixture.id}.wav"
        if not audio.exists():
            missing.append(fixture.id)
            continue
        outcomes.append(score(fixture, transcribe_via_service(args.base_url, audio, args.language)))

    report = summarise(outcomes)
    report["missing_audio"] = missing
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.write_text(text, encoding="utf-8")

    if missing:
        print(f"\n{len(missing)} fixture(s) had no audio and were not scored.", file=sys.stderr)
    # Non-zero when the benchmark ran and fell short, so CI can gate on it.
    return 0 if not outcomes or report.get("meets_target") else 1


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
