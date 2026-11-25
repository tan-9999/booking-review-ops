"""
Operational topic + sentiment classification. Pure Python: no Django, no network.

Method
------
1. Polarity comes from WHERE the guest wrote, not from guessing tone:
   Booking splits every review into "liked" (positive_text) and "disliked"
   (negative_text). A topic found in the disliked text is a negative mention,
   in the liked text a positive one. This sidesteps the classic weaknesses of
   sentiment models: negation ("not clean"), sarcasm, mixed reviews.
2. Each text is split into sentences; each sentence is matched against a
   lexicon of phrases per topic (English plus the most frequent Spanish,
   French and German terms seen in the real data).
3. One sentence may hit several topics ("dirty shared kitchen" -> cleanliness
   + facilities). Each (topic, polarity) is counted once per review, with the
   first matching sentence kept as evidence for the dashboard.
4. Placeholder answers ("nothing", "-", "n/a") are ignored.

Why a lexicon instead of an ML model: transparent (every tag can be explained
by the phrase that triggered it), deterministic, testable, free, and tunable by
ops staff. Limitations are measured by `topic_report` and listed in the README.
"""
import re
from dataclasses import dataclass

CLASSIFIER_VERSION = 1  # bump when the lexicon changes -> classify_reviews re-tags everything

POSITIVE = "positive"
NEGATIVE = "negative"

# label shown in the dashboard, then regex fragments (joined with |, word-bounded)
TOPICS: dict[str, tuple[str, list[str]]] = {
    "cleanliness": ("Cleanliness", [
        r"(un)?clean\w*", r"dirt\w*", r"filth\w*", r"stain\w*", r"dust\w*", r"mou?ld\w*",
        r"smell\w*", r"stink\w*", r"stank", r"odou?rs?", r"hygien\w*", r"spotless", r"tidy", r"grim[ey]",
        r"sticky", r"housekeeping", r"cockroach\w*", r"roach(es)?", r"bed ?bugs?", r"bugs?",
        r"insects?", r"pests?", r"rats?", r"mice", r"hairs? (in|on)", r"gross", r"disgusting",
        r"limpi\w*", r"sucio\w*", r"suciedad", r"propre\w*", r"propret[ée]", r"sale(t[ée])?",
        r"sauber\w*", r"schmutz\w*", r"dreck\w*", r"sporc\w*", r"pulit\w*", r"skitn\w*",
    ]),
    "check_in": ("Check-in & access", [
        r"check[- ]?ins?", r"check[- ]?outs?", r"arriv(al|ed|e|ing)", r"key ?cards?", r"keys?",
        r"door ?codes?", r"access codes?", r"codes?", r"self[- ]check\w*", r"luggage", r"bags? storage",
        r"deposit", r"queue\w*", r"waited", r"waiting", r"remote reception", r"no reception",
        r"tablet", r"instructions",
        r"llegada", r"entrada", r"arriv[ée]e", r"ankunft",
    ]),
    "staff": ("Staff & service", [
        r"staff", r"reception(ist)?s?", r"front desk", r"managers?", r"management", r"employees?",
        r"hosts?", r"customer service", r"service", r"friendly", r"unfriendly", r"rude\w*",
        r"(un)?helpful", r"polite", r"impolite", r"welcom\w*", r"attitude", r"accommodating",
        r"responsive", r"team",
        r"personnel", r"recepci[oó]n\w*", r"r[ée]ception", r"rezeption", r"amables?", r"atenci[oó]n",
        r"accueil\w*", r"freundlich\w*", r"mitarbeiter\w*", r"personale",
    ]),
    "noise": ("Noise", [
        r"nois\w*", r"loud\w*", r"quiet\w*", r"sounds?", r"(thin|thicker) walls?", r"walls (are|were|could)",
        r"soundproof\w*", r"rumor\w*", r"slam\w*", r"traffic",
        r"music", r"party", r"partying",  # not "parties": French "parties communes" = common areas r"snor\w*", r"construction", r"ear ?plugs?",
        r"(couldn'?t|could not|can'?t|cannot|hard to|difficult to|unable to) sleep",
        r"woke\w*", r"waking",
        r"ruido\w*", r"bruit\w*", r"bruyant\w*", r"laut\w*", r"l[äa]rm",
    ]),
    "facilities": ("Facilities", [
        r"kitchens?", r"laundry", r"washing machines?", r"lifts?", r"elevators?", r"wi-?fi", r"internet",
        r"pool", r"gym", r"parking", r"lounge", r"common (area|room|space)s?", r"shared (bathroom|kitchen|facilit\w*)s?",
        r"facilit\w*", r"fridges?", r"microwaves?", r"kettles?", r"lockers?", r"rooftop", r"bar",
        r"tv", r"television", r"stairs", r"terrace",
        r"cocina", r"instalaciones", r"cuisine", r"[ée]quipements?", r"k[üu]che",
    ]),
    "room": ("Rooms & bathrooms", [
        r"beds?", r"bunks?", r"mattress\w*", r"pillows?", r"sheets?", r"linens?", r"blankets?",
        r"duvets?", r"dorms?", r"pods?", r"curtains?", r"privacy", r"showers?", r"toilets?",
        r"bathrooms?", r"windows?", r"small", r"tiny", r"cramped", r"spacious", r"size",
        r"(un)?comfortable", r"comfy", r"broken", r"worn", r"outdated", r"dated", r"old", r"renovat\w*",
        r"maintenance", r"leak\w*", r"damp", r"air ?con\w*", r"aircon", r"a/c", r"heat(ing|er)",
        r"fans?", r"stuffy", r"hot water", r"water pressure", r"temperature", r"freezing",
        r"cama\w*", r"habitaci[oó]n\w*", r"ba[ñn]os?", r"ducha\w*", r"chambres?", r"douche\w*",
        r"zimmer", r"bett(en)?", r"dusche\w*", r"bagn[oi]", r"letto", r"camera", r"senge?\w*",
    ]),
    "location": ("Location", [
        r"locat\w*", r"central\w*", r"close to", r"nearby", r"near (the|to|by|central|town|city|everything|darling|station)",
        r"walk(ing|able)? (distance|to|from)", r"walkable", r"\d+ ?min(ute)?s?'? walk", r"minutes? (from|to)",
        r"convenien\w*", r"stations?", r"trains?", r"bus(es)?", r"trams?", r"metro", r"light rail",
        r"transport\w*", r"beach", r"harbou?r", r"cbd", r"city", r"airport", r"neighbou?rhood",
        r"surroundings", r"(nice|great|good|safe|unsafe|sketchy|dodgy|lively|busy|central|local) area",
        r"the area", r"venue", r"stadium", r"icc", r"shops", r"far from", r"downtown", r"down town",
        r"ubicaci[oó]n", r"ubicad[oa]", r"emplacement", r"situ[ée]", r"lage", r"gelegen", r"posizione",
    ]),
    "value": ("Value for money", [
        r"price\w*", r"pricey", r"value", r"money", r"worth\w*", r"expensive", r"cheap\w*",
        r"overpriced", r"afford\w*", r"budget", r"costs?", r"paid", r"pay(ing)?", r"rates?",
        r"fees?", r"charg\w*", r"refund\w*", r"bargain", r"\$\s?\d+",
        r"precio\w*", r"caro", r"barato", r"prix", r"cher", r"preis\w*", r"teuer", r"g[üu]nstig", r"prezzo",
    ]),
    "food": ("Food & breakfast", [
        r"breakfast", r"dinners?", r"meals?", r"food", r"coffee", r"tea", r"buffet", r"restaurant",
        r"cena", r"desayuno", r"comida", r"d[îi]ner", r"petit[- ]d[ée]jeuner", r"repas",
        r"fr[üu]hst[üu]ck", r"abendessen", r"essen", r"colazione", r"toast", r"rice", r"chicken",
    ]),
}

TOPIC_LABELS = {key: label for key, (label, _) in TOPICS.items()}

_PATTERNS = {
    key: re.compile(r"(?<![\w$])(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)
    for key, (_, parts) in TOPICS.items()
}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+|\s+[-•]\s+")
_PLACEHOLDERS = {
    "", "-", "--", ".", "n/a", "na", "none", "nothing", "nothing.", "nothing really", "not much",
    "no complaints", "nothing to complain about", "nothing to dislike", "all good", "nil", "nik", "ns",
    "nada", "rien", "nichts", "ninguno", "nothing at all", "no", "nope", "no problem", "no problems",
    "no problem, all good", "nothing to report", "everything was perfect", "everything was great",
    "tout était parfait", "todo perfecto", "alles gut", "nothing bad", "nothing in particular",
}
SNIPPET_MAX = 280


@dataclass(frozen=True)
class TopicMention:
    topic: str
    polarity: str      # positive | negative
    snippet: str       # sentence that triggered it (evidence shown to ops staff)
    matched: str       # the phrase that matched (explainability / debugging)


def is_placeholder(text: str) -> bool:
    return (text or "").strip().lower().rstrip("!.") in _PLACEHOLDERS


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def _clip(sentence: str) -> str:
    return sentence if len(sentence) <= SNIPPET_MAX else sentence[: SNIPPET_MAX - 1].rstrip() + "…"


def classify_text(text: str, polarity: str) -> list[TopicMention]:
    if is_placeholder(text):
        return []
    found: dict[str, TopicMention] = {}
    for sentence in split_sentences(text):
        for topic, pattern in _PATTERNS.items():
            if topic in found:
                continue
            match = pattern.search(sentence)
            if match:
                found[topic] = TopicMention(topic, polarity, _clip(sentence), match.group(0).lower())
    return list(found.values())


def classify_review(positive_text: str, negative_text: str) -> list[TopicMention]:
    return classify_text(positive_text, POSITIVE) + classify_text(negative_text, NEGATIVE)


# ------------------------------------------------------------------ overall sentiment

def sentiment_from_rating(rating) -> str:
    """
    Review-level sentiment from Booking's own 1–10 score, using its bands:
    8+ = positive ("Very good" and up), 6–7.9 = mixed, below 6 = negative.
    The score is the guest's explicit verdict, so it beats inferring tone from text.
    """
    value = float(rating)
    if value >= 8:
        return POSITIVE
    if value >= 6:
        return "mixed"
    return NEGATIVE
