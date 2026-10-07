"""Generate a SYNTHETIC corpus for tests and dry runs.

Everything here is invented template text. It exists only to exercise the
pipeline mechanics; it says nothing about real churches or people.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

CHURCH = {
    "baptist": [
        "Join us Sunday for traditional worship with hymns, piano and choir, followed by an altar call.",
        "Our expository preaching walks verse by verse through Scripture. We are a Bible-believing church.",
        "Believers baptism by immersion this Sunday. The Lord's Supper is an ordinance we observe monthly.",
        "Sunday school for all ages at 9:30, then traditional worship in the sanctuary with the choir.",
        "Revival services this week with a guest evangelist and an altar call each night.",
        "We are an elder-led, gospel-centered congregation with small groups meeting in homes.",
    ],
    "anglican": [
        "Holy Eucharist every Sunday with traditional liturgy, organ, choir and the Nicene Creed.",
        "Our traditional worship follows the Book of Common Prayer and the lectionary readings.",
        "Choral Evensong tonight: candles, psalms, vestments and the prayers of the people.",
        "The rector will preach on the lectionary gospel. Communion is offered at every service.",
        "We are a liturgical church that is also charismatic; our band leads modern songs before the liturgy.",
        "High church worship with incense, robes and the creed; all are welcome at the altar rail.",
    ],
    "nondenominational": [
        "Contemporary worship with a full band, drums, lights and coffee in the lobby. Come as you are.",
        "Our worship team leads modern songs; lyrics on the screens and jeans are totally fine.",
        "New sermon series starts Sunday. Kids ministry and nursery available at every service.",
        "Life groups are where we do life together. Find a group near you this fall.",
        "Spirit-filled worship tonight: hands raised, prayer for healing, and a word from our pastor.",
        "We are a gospel-centered, Bible-based church plant reaching our city with a seeker-friendly service.",
    ],
    "pentecostal": [
        "Spirit-filled services with tongues, prophecy and prayer for healing at the altar.",
        "Revival is here: dancing, shouting amen, and a powerful altar call every night.",
        "Our praise and worship band and choir lead a Spirit-led service every Sunday.",
        "Testimony night: members share what God has done. Youth group meets Wednesday.",
    ],
}

SEEKER = [
    "We just moved and are looking for a church that has contemporary worship and a strong kids ministry.",
    "Looking for a church that is not legalistic and not judgmental. Something welcoming.",
    "I miss traditional worship with hymns and an organ. Looking for a church near downtown with a choir.",
    "We want a church that is Bible-believing with expository preaching, not seeker-sensitive.",
    "Looking for a church with liturgy and weekly communion but not too high church.",
    "Need a church that is Spirit-filled but not weird about it. A deal breaker is a celebrity pastor.",
    "Recommendations for a church that is welcoming and inclusive with women in leadership?",
    "Looking for a church that is not political and has small groups for young adults.",
    "We are new in town and want a church with a short service and good youth group.",
    "Looking for a church that is gospel-centered, elder-led, and has sound doctrine.",
    "Any suggestions for a church with blended worship? My wife likes hymns and I like a band.",
    "Looking for a church that is charismatic with traditional liturgy, if that exists.",
    "I want a church where women preach and the community is multiethnic.",
    "Looking for a church that is not seeker-friendly and does not do an altar call every week.",
]

FILLER = ["Thanks in advance.", "Any help appreciated!", "We have two kids.", "Grace and peace.",
          "See you Sunday.", "Doors open at 10.", "Parking is behind the building."]


def build(out: Path, n_church: int = 500, n_seeker: int = 300, seed: int = 7) -> Path:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    trads = list(CHURCH)
    for i in range(n_church):
        t = trads[i % len(trads)]
        parts = rng.sample(CHURCH[t], k=2) + [rng.choice(FILLER)]
        rows.append({"id": f"c{i}", "side": "church", "tradition": t, "source": "synthetic",
                     "text": " ".join(parts), "author": "SHOULD_BE_DROPPED"})
    for i in range(n_seeker):
        s = rng.choice(SEEKER)
        extra = rng.choice(FILLER)
        handle = " cc @someone" if i % 25 == 0 else ""
        rows.append({"id": f"s{i}", "side": "seeker", "source": "synthetic", "text": f"{s} {extra}{handle}"})
    p = out / "synthetic.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return p


if __name__ == "__main__":
    print(build(Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw_synthetic")))
