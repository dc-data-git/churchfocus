"""Generate data/fields.yaml from the workbook's Field Dictionary + hand-written search hints.
Run once; then edit data/fields.yaml by hand. Usage:
    python scripts/make_fields_yaml.py ../US_Religious_Groups_217_All_Six_Layers.xlsx
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from denomkb.workbook import load  # noqa: E402

# Fields that should NOT be extracted from documents:
SKIP = {
    # census numbers come from the census, not from web pages
    "identity.congregation_count", "identity.membership_count", "identity.adherent_count",
    "identity.adult_population_share",
    # editorial judgments / internal bookkeeping
    "identity.classification_notes", "identity.denominational_identity_strength", "identity.canonical_name",
    # congregation-level: denominational documents rarely settle these
    "practice_culture.typical_church_culture", "practice_culture.children_programs",
    "practice_culture.accessibility", "practice_culture.dress_expectations", "practice_culture.service_length",
}
# distinguishing_metadata is editorial except these two
DM_KEEP = {"distinguishing_metadata.distinctives", "distinguishing_metadata.internal_subgroups"}

KEYWORDS = {
    "aliases": "also known as abbreviation formerly called name", "former_names": "formerly known renamed name changed",
    "abbreviations": "abbreviation acronym", "primary_family": "tradition denomination family protestant catholic orthodox baptist methodist lutheran pentecostal",
    "secondary_influences": "influenced influence roots heritage", "broad_branch": "protestant catholic orthodox evangelical mainline",
    "orientation_tags": "evangelical mainline conservative progressive charismatic holiness fundamentalist",
    "entity_type": "denomination fellowship association convention church body network", "organizational_status": "active dissolved merged",
    "us_presence": "united states congregations states", "geographic_concentrations": "concentrated region states south midwest",
    "demographic_profile": "members african american hispanic immigrant ethnic", "official_website": "website",
    "parent_organization": "part of member of communion global international", "networks_associations": "member of council association world alliance national association of evangelicals",
    "nicene_trinitarian": "trinity nicene creed triune", "self_identification": "we are describes itself identity",
    "founding_date": "founded established organized formed year", "founding_location": "founded organized in city",
    "founders": "founded by founder", "origin_movement": "movement origins revival roots",
    "parent_denomination": "separated from split from withdrew broke away", "major_splits": "split schism separated withdrew",
    "major_mergers": "merged merger united union formed by", "descendant_denominations": "split off later formed offshoot",
    "historical_relationships": "relationship history", "separation_reasons": "separated because over dispute",
    "historical_controversies": "controversy dispute", "current_controversies": "controversy dispute debate recent",
    "doctrinal_standards": "confession of faith statement articles creed doctrinal standards",
    "scripture_inspiration": "scripture inspired word of god bible", "scripture_authority": "scripture authority rule of faith",
    "scripture_inerrancy": "inerrant inerrancy infallible without error", "scripture_interpretation": "interpretation interpret scripture tradition",
    "biblical_canon": "canon books apocrypha deuterocanonical", "god_trinity": "trinity father son holy spirit one god three persons",
    "christology": "jesus christ divine human son of god incarnation", "original_sin": "sin fall original sin depravity",
    "atonement": "atonement cross death of christ sacrifice", "justification": "justification justified faith grace",
    "sanctification": "sanctification holiness entire sanctification", "assurance": "assurance of salvation",
    "perseverance": "perseverance eternal security once saved fall away", "election": "election predestination chosen",
    "free_will": "free will prevenient grace choose", "salvation_summary": "salvation saved grace faith repentance",
    "baptism_recipients": "baptism infants believers", "baptism_mode": "baptism immersion sprinkling pouring",
    "baptism_meaning": "baptism symbol sacrament ordinance", "baptism_necessity": "baptism necessary salvation required",
    "baptism_recognition": "baptism rebaptism recognize other churches", "communion_presence": "lord's supper communion eucharist real presence body blood memorial",
    "communion_frequency": "lord's supper communion weekly monthly quarterly", "communion_admission": "communion open closed admitted table",
    "communion_elements": "bread wine grape juice", "continuationism": "spiritual gifts continue ceased cessation",
    "tongues": "speaking in tongues", "prophecy": "prophecy prophets", "healing": "divine healing", "spirit_baptism": "baptism in the holy spirit",
    "millennium": "millennium premillennial amillennial postmillennial", "tribulation": "tribulation rapture",
    "israel_church_relationship": "israel church dispensation", "second_coming": "second coming return of christ",
    "afterlife": "heaven hell eternal judgment resurrection", "nature_of_church": "church body of christ universal local",
    "membership_requirements": "membership members join", "church_discipline": "church discipline excommunication",
    "sacraments_number": "sacraments seven two ordinances", "sacraments_theology": "sacrament means of grace ordinance",
    "mary_saints": "mary saints intercession", "veneration_images": "icons images veneration", "creation_views": "creation creator evolution",
    "polity": "polity government governance congregational presbyterian episcopal", "local_autonomy": "autonomous autonomy local church",
    "denominational_authority": "authority general assembly convention conference synod", "bishops_role": "bishop bishops",
    "elders_role": "elders", "deacons_role": "deacons", "pastor_selection": "call pastor appointed called",
    "property_ownership": "property trust clause own", "pastor_removal": "remove pastor dismiss",
    "congregation_discipline": "discipline congregation", "ordination_process": "ordination ordained process",
    "credentialing_authority": "credential license ordain", "ordination_requirements": "ordination requirements seminary",
    "women_ordination": "women ordination ordain women", "women_senior_pastors": "women pastor senior pastor",
    "women_preaching": "women preach preaching", "clergy_marriage": "clergy married marriage priests",
    "clergy_celibacy": "celibacy celibate", "leadership_accountability": "accountability", "lay_participation": "laity lay members",
    "congregational_voting": "vote congregation", "lgbtq_relationships": "homosexual sexuality lgbtq same-sex",
    "same_sex_marriage": "same-sex marriage", "divorce_remarriage": "divorce remarriage", "abortion": "abortion life unborn",
    "alcohol": "alcohol abstain beverages", "military_service": "military service war", "pacifism": "nonresistance pacifism peace",
    "racial_reconciliation": "racial reconciliation racism", "political_engagement": "political public policy",
    "social_teachings": "social principles social teaching justice", "liturgical_intensity": "liturgy liturgical worship order",
    "typical_worship_style": "worship style", "contemporary_music": "contemporary music praise", "hymns": "hymns hymnal",
    "choir": "choir", "vestments": "vestments robes", "icons": "icons", "altar": "altar", "sermon_emphasis": "preaching sermon",
    "spontaneous_prayer": "prayer", "tongues_in_worship": "tongues worship", "prophecy_in_worship": "prophecy worship",
    "altar_calls": "altar call invitation", "formal_confession": "confession penance", "lectionary": "lectionary readings",
    "church_calendar": "church year advent lent calendar", "worship_day": "sabbath sunday saturday worship day",
    "fasting": "fasting", "health_diet": "health diet food", "tithing": "tithe tithing giving", "evangelism": "evangelism",
    "missions": "missions missionaries", "ecumenical_relations": "ecumenical council of churches cooperation",
    "languages": "language spanish korean", "distinctives": "distinctive unique emphasis", "internal_subgroups": "conferences districts synods subgroups",
}
# Christian-specific fields: marked "Not applicable" for non-Christian groups instead of searched.
# Everything else (governance, ethics, worship day, women's ordination, ...) is searched for every group.
CHRISTIAN_ONLY_THEOLOGY_EXCEPT = {"theology.creation_views", "theology.afterlife", "theology.doctrinal_standards",
                                  "theology.membership_requirements", "theology.nature_of_church"}
CHRISTIAN_ONLY_OTHER = {
    "identity.nicene_trinitarian",
    "governance.bishops_role", "governance.elders_role", "governance.deacons_role",
    "practice_culture.contemporary_music", "practice_culture.hymns", "practice_culture.altar_calls",
    "practice_culture.tongues_in_worship", "practice_culture.prophecy_in_worship", "practice_culture.lectionary",
    "practice_culture.church_calendar", "practice_culture.icons", "practice_culture.altar",
    "practice_culture.vestments", "practice_culture.formal_confession",
}


def main(xlsx: str):
    kb = load(xlsx)
    out = {}
    for fid, f in kb.fields.items():
        layer, sub = f["layer"], f["subtopic"]
        extract = fid not in SKIP and (layer != "distinguishing_metadata" or fid in DM_KEEP)
        out[fid] = {
            "description": f["description"],
            "extract": extract,
            "christian_only": (layer == "theology" and fid not in CHRISTIAN_ONLY_THEOLOGY_EXCEPT)
                              or fid in CHRISTIAN_ONLY_OTHER,
            "keywords": KEYWORDS.get(sub, f["description"].lower()),
        }
    path = Path(__file__).resolve().parents[1] / "data" / "fields.yaml"
    header = ("# Field spec for extraction. Generated by scripts/make_fields_yaml.py, then hand-edited.\n"
              "# extract: false        -> never filled from documents (census numbers, editorial judgments)\n"
              "# christian_only: true  -> marked 'Not applicable' for non-Christian groups instead of searched\n"
              "# keywords              -> BM25 search terms used to pick source excerpts for this field\n")
    path.write_text(header + yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    print(path, sum(v["extract"] for v in out.values()), "extractable of", len(out))


if __name__ == "__main__":
    main(sys.argv[1])
