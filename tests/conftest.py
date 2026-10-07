import os
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"
os.environ.setdefault("DENOM_KB_PATH", str(FIX / "kb_small.json"))


@pytest.fixture
def kb():
    from app.denom.kb import DenomKB
    return DenomKB(FIX / "kb_small.json")


@pytest.fixture
def profile_factory():
    from app.models import Preference, PreferenceProfile

    def make(prefs, max_miles=15):
        return PreferenceProfile(session_id="t", max_miles=max_miles,
                                 preferences=[Preference(feature=f, want=w, weight=wt) for f, w, wt in prefs])
    return make
