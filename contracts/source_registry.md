# Source registry (Stage 1 cross-search + Stage 3 sources)

Used by `denomination.resolve` (step 3: `site:<domain> "<church name>" <city>` web search) and by the deep-search guidebook.
Domains are starting points; locators change. A hit on an official directory page = tier B for denomination (0.95).
Status column: ✔ checked by a human, ? unchecked. **Update this file when you check one.**

## Denomination / network locators

| Group | Domain (site: search) | Status |
|---|---|---|
| Southern Baptist Convention | sbc.net | ? |
| United Methodist Church | umc.org (find-a-church) | ? (students: has attendance) |
| Global Methodist Church | globalmethodist.org | ? |
| Evangelical Lutheran Church in America | elca.org | ? |
| Lutheran Church–Missouri Synod | locator.lcms.org | ? |
| WELS | wels.net | ? |
| Presbyterian Church (USA) | pcusa.org, church-trends.pcusa.org | ? |
| Presbyterian Church in America | pcaac.org, pcanet.org | ? |
| Episcopal Church | episcopalchurch.org, episcopalassetmap.org | ? |
| Anglican Church in North America | anglicanchurch.net | ? |
| Catholic Church | masstimes.org, diocesan sites | ? |
| Assemblies of God | ag.org (Resources/Directories) | ? |
| Church of God (Cleveland, TN) | churchofgod.org | ? |
| Foursquare | foursquare.org | ? |
| Church of the Nazarene | nazarene.org | ? |
| Wesleyan Church | wesleyan.org | ? |
| Free Methodist | fmcusa.org | ? |
| Christian and Missionary Alliance | cmalliance.org | ? |
| Evangelical Free Church of America | efca.org | ? |
| Evangelical Covenant Church | covchurch.org | ? |
| Converge | converge.org | ? |
| American Baptist Churches USA | abc-usa.org | ? |
| Christian Church (Disciples of Christ) | disciples.org | ? |
| United Church of Christ | ucc.org | ? |
| Reformed Church in America | rca.org, crf.rca.org | ? |
| Christian Reformed Church | crcna.org | ? |
| Mennonite Church USA | mennoniteusa.org | ? |
| US Mennonite Brethren | usmb.org | ? |
| Churches of Christ | church-of-christ.org (directory) | ? |
| Seventh-day Adventist | adventist.org | ? |
| Greek Orthodox Archdiocese | goarch.org | ? |
| Orthodox Church in America | oca.org | ? |
| Vineyard USA | vineyardusa.org | ? (students) |
| Calvary Chapel | calvarychapel.com | ? |
| Acts 29 (network) | acts29.com | ? |
| ARC (network) | arcchurches.com | ? |
| 9Marks church search (ecclesiology signal, not a denomination) | 9marks.org | ? |
| The Gospel Coalition directory (signal) | thegospelcoalition.org | ? |

## Other free sources (tiers)
- OSM `amenity=place_of_worship` + `denomination=*` tag (C), Overture places (C).
- Denominational statistics (B, aggregate attendance only): UMData, PC(USA) Church Trends, Episcopal parochial report trends, ELCA congregational data, RCA CRF, WELS statistical report.
- Wayback Machine CDX API (A for the church's own past pages, dated).
- News: local outlets via web search (B), cited and dated.
- Sermons: church RSS/podcast feeds (A), YouTube channel linked from the church site (A; captions labelled), SermonAudio (A; API key needed), Subsplash/Church Center pages (A).
- Setlists: playlist.church (C) — worship style signal.
- Confessions/creeds texts: ccel.org (reference only).
- Form 990: rarely available (churches are not required to file); only for church-run nonprofits.

## Rejected (D8 / quality)
Staff pay sources; congregant demographics; photos of congregants for dress; foot-traffic data (paid); Yelp Fusion (paid); unverified third-party "church APIs" (github ncreighton repo, elbiblio, songer.datasn) until a human checks them.
