# Regulation sources

The rule pack shipped in `arc/core/rules/` encodes requirements derived from the
documents listed below. **The source documents themselves are not redistributed
in this repository** - they are third-party publications with their own copyright
terms, and ARC's GPL-3.0-or-later licence does not extend to them. Obtain them
from the issuing authority.

| Document | Issuing authority | Where to obtain |
|---|---|---|
| National Building Code of India 2016 (NBC 2016), Parts 3 and 4 | Bureau of Indian Standards (BIS) | BIS Standards portal - <https://www.services.bis.gov.in/> |
| Development Control and Promotion Regulations for Greater Mumbai, 2034 (DCPR 2034) | Municipal Corporation of Greater Mumbai (MCGM) | MCGM / Urban Development Department, Government of Maharashtra |
| Unified Development Control and Promotion Regulations for Maharashtra (UDCPR) | Urban Development Department, Government of Maharashtra | <https://urban.maharashtra.gov.in/> |

## How clauses map to rules

Each rule declares its regulation source in its `source` field, and
`arc/core/rules/clause_ledger.json` records the clause-to-rule mapping -
including clauses that are **deliberately excluded** from automation, with the
reason for exclusion. Read the ledger before assuming a clause is covered.

The 12-rule reference pack is a purposive selection chosen to exercise all six
result statuses and the full check-type spectrum.

## Adding another jurisdiction

Rule packs are self-contained. See `docs/rule-authoring-guide.md` and
`arc/core/rules/pack_manifest.json` for the pack format; a new pack needs its own
clause ledger and manifest declaring identifier, version, jurisdiction, and
governance status.
