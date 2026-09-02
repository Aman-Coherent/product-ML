"""
50-real-company German test harness for backend/core/email_finder/.

German company websites are legally required (Telemediengesetz S 5) to
publish an "Impressum" page with a real, verifiable contact email - in
practice this is often the single most reliable source of a company's
true official email, MORE reliable than a "Kontakt" page (which is often
just a web form, exactly like the English "Contact Us" forms already seen
to produce nothing useful - see Tetra Pak in the original validation run).

This script measures whether the crawler actually finds and uses German
navigation (Kontakt/Impressum/Uber uns) vs falling through to English-only
guessed paths that 404 on a German site and produce nothing.

Usage (from repo root):
    python scripts/eval_email_finder_de.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.email_finder.pipeline import find_company_email  # noqa: E402
from backend.core.llm_router import pick_groq_fallback_key  # noqa: E402

# 50 real, well-known German companies spanning the app's supply-chain
# categories (raw material, machinery, packaging, finished goods), all with
# real known URLs (isolates crawler/extractor accuracy from the web-search/
# domain-guess steps, which were already validated separately).
GERMAN_COMPANIES = [
    {"name": "BASF SE", "location": "Ludwigshafen, Germany", "url": "https://www.basf.com"},
    {"name": "Bayer AG", "location": "Leverkusen, Germany", "url": "https://www.bayer.com"},
    {"name": "Siemens AG", "location": "Munich, Germany", "url": "https://www.siemens.com"},
    {"name": "Volkswagen AG", "location": "Wolfsburg, Germany", "url": "https://www.volkswagen.com"},
    {"name": "Bayerische Motoren Werke AG", "location": "Munich, Germany", "url": "https://www.bmw.com"},
    {"name": "Mercedes-Benz Group AG", "location": "Stuttgart, Germany", "url": "https://www.mercedes-benz.com"},
    {"name": "Robert Bosch GmbH", "location": "Stuttgart, Germany", "url": "https://www.bosch.com"},
    {"name": "ThyssenKrupp AG", "location": "Essen, Germany", "url": "https://www.thyssenkrupp.com"},
    {"name": "Continental AG", "location": "Hanover, Germany", "url": "https://www.continental.com"},
    {"name": "Henkel AG & Co. KGaA", "location": "Dusseldorf, Germany", "url": "https://www.henkel.com"},
    {"name": "Merck KGaA", "location": "Darmstadt, Germany", "url": "https://www.merckgroup.com"},
    {"name": "Linde plc", "location": "Munich, Germany", "url": "https://www.linde.com"},
    {"name": "Evonik Industries AG", "location": "Essen, Germany", "url": "https://www.evonik.com"},
    {"name": "Wacker Chemie AG", "location": "Munich, Germany", "url": "https://www.wacker.com"},
    {"name": "Covestro AG", "location": "Leverkusen, Germany", "url": "https://www.covestro.com"},
    {"name": "Lanxess AG", "location": "Cologne, Germany", "url": "https://www.lanxess.com"},
    {"name": "Heidelberger Druckmaschinen AG", "location": "Heidelberg, Germany", "url": "https://www.heidelberg.com"},
    {"name": "Krones AG", "location": "Neutraubling, Germany", "url": "https://www.krones.com"},
    {"name": "KUKA AG", "location": "Augsburg, Germany", "url": "https://www.kuka.com"},
    {"name": "Trumpf SE + Co. KG", "location": "Ditzingen, Germany", "url": "https://www.trumpf.com"},
    {"name": "DMG Mori AG", "location": "Bielefeld, Germany", "url": "https://www.dmgmori.com"},
    {"name": "Voith GmbH & Co. KGaA", "location": "Heidenheim, Germany", "url": "https://www.voith.com"},
    {"name": "Rittal GmbH & Co. KG", "location": "Herborn, Germany", "url": "https://www.rittal.com"},
    {"name": "Festo SE & Co. KG", "location": "Esslingen, Germany", "url": "https://www.festo.com"},
    {"name": "Miele & Cie. KG", "location": "Gutersloh, Germany", "url": "https://www.miele.de"},
    {"name": "BSH Hausgerate GmbH", "location": "Munich, Germany", "url": "https://www.bsh-group.com"},
    {"name": "Adidas AG", "location": "Herzogenaurach, Germany", "url": "https://www.adidas-group.com"},
    {"name": "Puma SE", "location": "Herzogenaurach, Germany", "url": "https://about.puma.com"},
    {"name": "MAN Truck & Bus SE", "location": "Munich, Germany", "url": "https://www.man.eu"},
    {"name": "ZF Friedrichshafen AG", "location": "Friedrichshafen, Germany", "url": "https://www.zf.com"},
    {"name": "Schaeffler AG", "location": "Herzogenaurach, Germany", "url": "https://www.schaeffler.com"},
    {"name": "Mahle GmbH", "location": "Stuttgart, Germany", "url": "https://www.mahle.com"},
    {"name": "Brose Fahrzeugteile SE & Co. KG", "location": "Coburg, Germany", "url": "https://www.brose.com"},
    {"name": "Hella GmbH & Co. KGaA", "location": "Lippstadt, Germany", "url": "https://www.hella.com"},
    {"name": "Webasto SE", "location": "Stockdorf, Germany", "url": "https://www.webasto.com"},
    {"name": "Freudenberg SE", "location": "Weinheim, Germany", "url": "https://www.freudenberg.com"},
    {"name": "tesa SE", "location": "Norderstedt, Germany", "url": "https://www.tesa.com"},
    {"name": "Symrise AG", "location": "Holzminden, Germany", "url": "https://www.symrise.com"},
    {"name": "Wacker Neuson SE", "location": "Munich, Germany", "url": "https://www.wackerneuson.com"},
    {"name": "Claas KGaA mbH", "location": "Harsewinkel, Germany", "url": "https://www.claas.com"},
    {"name": "Liebherr-Hausgerate GmbH", "location": "Ochsenhausen, Germany", "url": "https://www.liebherr.com"},
    {"name": "Putzmeister Holding GmbH", "location": "Aichtal, Germany", "url": "https://www.putzmeister.com"},
    {"name": "Wirtgen Group", "location": "Windhagen, Germany", "url": "https://www.wirtgen-group.com"},
    {"name": "Heidelberg Materials AG", "location": "Heidelberg, Germany", "url": "https://www.heidelbergmaterials.com"},
    {"name": "Knauf Gips KG", "location": "Iphofen, Germany", "url": "https://www.knauf.com"},
    {"name": "Wurth Group", "location": "Kunzelsau, Germany", "url": "https://www.wuerth.com"},
    {"name": "Andreas Stihl AG & Co. KG", "location": "Waiblingen, Germany", "url": "https://www.stihl.com"},
    {"name": "Dragerwerk AG & Co. KGaA", "location": "Lubeck, Germany", "url": "https://www.draeger.com"},
    {"name": "Bischof + Klein SE & Co. KG", "location": "Lengerich, Germany", "url": "https://www.bischofklein.com"},
    {"name": "Storopack Hans Reichenecker GmbH", "location": "Metzingen, Germany", "url": "https://www.storopack.com"},
]


async def _run_one(sem: asyncio.Semaphore, groq_key: str | None, company: dict, idx: int):
    async with sem:
        try:
            result = await asyncio.wait_for(
                find_company_email(
                    company_id=f"de-{idx}",
                    company_name=company["name"],
                    location=company["location"],
                    url=company["url"],
                    redis=None,
                    groq_api_key=groq_key,
                ),
                timeout=90.0,
            )
            return company, result
        except asyncio.TimeoutError:
            return company, None


async def main() -> None:
    groq_key, _ = pick_groq_fallback_key(user_keys=[])
    # Deliberately the real default batch concurrency (see EmailBatch.concurrency
    # in db/email_models.py) - the point of this run is to prove
    # crawler.py's own internal Jina semaphore + 429 retry keeps results
    # reliable even when the OUTER per-company concurrency is much higher
    # than Jina's free tier can sustain directly, not to hand-tune this
    # script's own concurrency down to whatever happens to work.
    sem = asyncio.Semaphore(10)

    tasks = [_run_one(sem, groq_key, c, i) for i, c in enumerate(GERMAN_COMPANIES)]
    results = await asyncio.gather(*tasks)

    tier_counts: dict[str, int] = {}
    none_found = []
    scraped_rows = []

    print(f"{'Company':<36} {'Primary email':<38} {'Tier':<24} {'Conf':<5} {'Source page':<50}")
    print("-" * 155)
    for company, result in results:
        if result is None:
            print(f"{company['name']:<36} TIMEOUT")
            tier_counts["timeout"] = tier_counts.get("timeout", 0) + 1
            continue
        tier = result.primary_tier.value if result.primary_tier else "NONE_FOUND"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        if not result.primary_email:
            none_found.append(company["name"])
        source = result.primary_source_page or ""
        print(f"{company['name']:<36} {(result.primary_email or '-'):<38} {tier:<24} {result.primary_confidence:<5.2f} {source:<50}")
        if result.primary_tier and result.primary_tier.value.startswith("scraped"):
            scraped_rows.append((company["name"], result.primary_email, source))

    print("\n=== Tier breakdown (50 companies) ===")
    for tier, count in sorted(tier_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {tier:<28} {count}")

    print(f"\n=== Real emails scraped straight off the site (highest trust): {len(scraped_rows)}/50 ===")
    for name, email, source in scraped_rows:
        print(f"  {name}: {email}  <- {source}")

    print(f"\n=== No email found at all: {len(none_found)}/50 ===")
    for name in none_found:
        print(f"  {name}")


if __name__ == "__main__":
    asyncio.run(main())
