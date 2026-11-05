"""The daily check-in questionnaire: wording, answer codes, and its rules.

Pure data and pure functions, no tkinter - the window renders what is here,
and the tests check it without a display.

Every question names its time window and says in one grey line what counts.
Gamers sleep at unusual hours and the questions are answered alone, weeks into
a study; a question that can be read two ways will be, systematically. Hence
"your last main sleep" instead of "last night", and a talk test instead of
"light / hard" for exercise. Wording agreed with Chris on 14.10.2026 (register
D8); changing it mid-study breaks the within-person comparison, so a change
bumps QUESTIONNAIRE_VERSION.

The answer codes are what lands in the `checkin` table (schema.sql); the ids
are its column names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Union

QUESTIONNAIRE_VERSION = 1

# The study day starts at 05:00 local time, not at midnight: a session that
# runs past midnight still belongs to the evening it began in, and its player
# must not be asked again at 00:30.
STUDY_DAY_STARTS_HOUR = 5

# Closing the questionnaire unanswered is allowed; it comes back once more
# after the next match, then not again that day.
MAX_PROMPTS_PER_DAY = 2

REST_SECONDS = 60

Code = Union[int, str]


@dataclass(frozen=True)
class Question:
    id: str                                  # = column in `checkin`
    text: str
    options: tuple[tuple[Code, str], ...]    # (stored code, label shown)
    help: str = ""
    # Shown only when another answer is not one of these values.
    hidden_if: Optional[tuple[str, tuple[Code, ...]]] = None


QUESTIONS: tuple[Question, ...] = (
    Question(
        "sleep_quality", "How well did you sleep?",
        ((1, "1 very poor"), (2, "2"), (3, "3 normal for you"), (4, "4"),
         (5, "5 very good")),
        "Your last main sleep before now, even if it ended at noon. Judge it "
        "against your usual sleep, not anyone else's.",
    ),
    Question(
        "sleep_hours", "How many hours did you actually sleep?",
        ((4, "4 or less"), (5, "5"), (6, "6"), (7, "7"), (8, "8"), (9, "9"),
         (10, "10 or more")),
        "Same main sleep. Time asleep, not time in bed. Round to the nearest "
        "hour. Naps don't count.",
    ),
    Question(
        "alcohol", "Alcohol in the last 24 hours?",
        (("none", "none"), ("1-2", "1–2 drinks"), ("3-4", "3–4 drinks"),
         ("5+", "5 or more")),
        "1 drink = a beer (0.33–0.5 l), a glass of wine, or a shot.",
    ),
    Question(
        "caffeine", "Caffeine in the last 3 hours?",
        (("0", "0"), ("1", "1"), ("2", "2"), ("3+", "3 or more")),
        "Count cups of coffee, cans of energy drink, pre-workout or caffeine "
        "pills. A cola or a black/green tea counts as half.",
    ),
    Question(
        "nicotine", "Nicotine in the last hour?",
        (("no", "no"), ("yes", "yes")),
        "Cigarette, vape, snus or nicotine pouch.",
    ),
    Question(
        "last_meal", "When did you last eat a proper meal?",
        (("<1h", "less than 1 h ago"), ("1-3h", "1–3 h ago"),
         (">3h", "more than 3 h ago")),
        "A meal is a plate of food. Snacks, chips or a bar don't count.",
    ),
    Question(
        "exercise", "Exercise in the last 24 hours? (your hardest session)",
        (("none", "none"), ("light", "light"), ("moderate", "moderate"),
         ("hard", "hard")),
        "Only sessions of 20+ minutes. Everyday walking doesn't count.\n"
        "Light = you could chat normally (walk, easy bike). "
        "Moderate = breathing harder, short sentences only (gym, jog). "
        "Hard = you could barely talk (intervals, a match, heavy legs).",
    ),
    Question(
        "exercise_ended", "When did that session end?",
        (("<2h", "less than 2 h ago"), ("2-6h", "2–6 h ago"),
         (">6h", "more than 6 h ago")),
        "",
        hidden_if=("exercise", ("none",)),
    ),
    Question(
        "ill", "Are you ill right now?",
        (("no", "no"), ("a_bit", "a bit"), ("yes", "yes")),
        "A bit = sniffles, slightly off. Yes = fever, flu, stomach bug, or "
        "you would call in sick.",
    ),
    Question(
        "feeling", "How do you feel right now, overall?",
        ((1, "1 awful"), (2, "2"), (3, "3 normal for you"), (4, "4"),
         (5, "5 great")),
        "Body and mind together, compared to a normal day for you.",
    ),
    Question(
        "adhd_medication", "ADHD or focus medication since you woke up?",
        (("no", "no"), ("yes", "yes"), ("never", "I never take any")),
        "For example methylphenidate (Ritalin, Medikinet, Concerta), "
        "amphetamines (Elvanse/Vyvanse, Adderall) or modafinil.",
    ),
)

QUESTION_IDS = tuple(q.id for q in QUESTIONS)


def is_visible(question: Question, answers: dict) -> bool:
    """Whether a question applies, given the answers so far."""
    if question.hidden_if is None:
        return True
    other, values = question.hidden_if
    return answers.get(other) not in values and answers.get(other) is not None


def missing(answers: dict) -> list[str]:
    """Ids of the questions that apply but are not answered yet."""
    return [q.id for q in QUESTIONS if is_visible(q, answers) and answers.get(q.id) is None]


def cleaned(answers: dict) -> dict:
    """The answers as stored: hidden questions dropped, codes checked.

    A follow-up answered before its trigger was changed back ("hard", then
    "none") would otherwise survive as a contradiction in the data.
    """
    out: dict = {}
    for q in QUESTIONS:
        if not is_visible(q, answers):
            continue
        value = answers.get(q.id)
        if value is None:
            continue
        if value not in {code for code, _ in q.options}:
            raise ValueError(f"{q.id}: {value!r} is not an answer option")
        out[q.id] = value
    return out


def study_day(now_local: datetime) -> date:
    """The study day a local moment belongs to (day starts at 05:00)."""
    return (now_local - timedelta(hours=STUDY_DAY_STARTS_HOUR)).date()
