# Kvitta – teamet och systemet

Här står vilka som är med, vad de gör, och hur systemet och verktygen är uppbyggda. Det är det som behövs, utöver
`info.md` och `händelseförlopp.md`, för att bygga exempeldatan. Namnen är valda så att de är lätta att uttala, lätta
att hålla isär och inte delar förnamn eller efternamn med någon annan.

---

## Företagen

| | |
| --- | --- |
| **Kvitta AB** | Det lilla mjukvarubolaget som bygger Kvitta. E-postdomän `kvitta.se`. |
| **Bergström & Co** | Pilotkunden, ett konsultbolag med cirka 60 anställda. E-postdomän `bergstrom.se`. |

---

## Personerna (8 totalt)

### Teamet på Kvitta AB (7)

| Namn | Roll | Grupp | E-post | Användar-id |
| --- | --- | --- | --- | --- |
| **Maria Lindgren** | Produktägare. Skriver krav, prioriterar, har kontakten med kunden. | Produkt | maria.lindgren@kvitta.se | `u-maria` |
| **David Okafor** | Tech lead och säkerhetsansvarig. Granskar det mesta och samarbetar mest i teamet. | Brygga | david.okafor@kvitta.se | `u-david` |
| **Sofia Berg** | Backendutvecklare. Äger kvittotolkningen. Ensam om momsreglerna och bildlagringen; kvittoläsaren delar hon med David. | Backend | sofia.berg@kvitta.se | `u-sofia` |
| **Ahmed Karimi** | Integrationsutvecklare (Fortnox, utbetalningar). Mentor för Emma. | Backend | ahmed.karimi@kvitta.se | `u-ahmed` |
| **Lucas Holm** | Mobilutvecklare. Driver lösningen med lokal kö i offlinedebatten. | Mobil | lucas.holm@kvitta.se | `u-lucas` |
| **Nina Petrova** | Testare. Varnar för dubbletter och har rätt. | Mobil | nina.petrova@kvitta.se | `u-nina` |
| **Emma Chen** | Ny utvecklare från 9 februari. Bygger attesten med beloppsgränser. | Backend | emma.chen@kvitta.se | `u-emma` |

### Kunden (1)

| Namn | Roll | E-post |
| --- | --- | --- |
| **Anders Nyberg** | Ekonomiansvarig på Bergström & Co. Kundens röst i förlopp 2, 4, 5, 8 och 9. | anders.nyberg@bergstrom.se |

Anders finns bara i mejl, och har därför ingen användar-id.

### Gemensamma adresser (räknas inte som personer)

| Adress | Används till |
| --- | --- |
| `support@kvitta.se` | Kundens felanmälningar och svar till kunden |
| `alerts@kvitta.se` | Automatiska larm, till exempel misslyckade Fortnox-exporter |

### Viktigt för att systemet ska känna igen personerna

- Varje person i teamet har **samma användar-id i alla system**: Slack, ärenden, dokument och pull requests.
- Varje person i teamet skriver **minst ett Slack-meddelande med både e-post och användar-id**, och finns med i minst
  ett möte med båda. Det är så systemet förstår att e-postadressen och användar-id:t är samma person.
- Namnen skrivs alltid fullt ut i avsändarfält och författarfält. I själva texten kan folk skriva bara förnamnet
  ("Thanks Nina"), och det går bra, eftersom inga två delar förnamn.

---

## Grupperna och vem som jobbar med vem

### Planerad bild (när scenariot skrevs)

```text
      Produkt                     Brygga                      Backend
  Maria Lindgren ─────────── David Okafor ─────────── Sofia Berg
        │                        │    │                Ahmed Karimi ── mentor ── Emma Chen
        │                        │    │
  Anders Nyberg (kund)           │    └──────────── Mobil
                                 │                   Lucas Holm
                                 └────────────────── Nina Petrova
```

- **Backend:** Sofia, Ahmed, Emma. Jobbar mest med kvittotolkningen, integrationerna och attesten.
- **Mobil:** Lucas och Nina. Jobbar mest med appen och offlinestödet.
- **David** granskar i båda grupperna och är med på alla viktiga möten.
- **Maria** jobbar mest med David och med kunden Anders.

### Vad datan faktiskt visar (kontrollerat mot graflagren och SQL-källorna, 29 september 2026)

- **Alla i teamet jobbar direkt med varandra.** Möten och mejl kopplar ihop nästan alla par, så backend och mobil
  blir inte två skilda grupper. Grafen delar i stället upp folket i två grupper: **de sex utvecklarna**, respektive
  **Maria och kunden Anders**.
- **David samarbetar mest** (högst samarbetsvolym). Han granskade 8 av 14 pull requests, i både `kvitta-api` och
  `kvitta-mobile`, och talar på 7 av 10 möten. Men eftersom alla redan jobbar direkt med varandra är han ingen
  "brygga" i nätverksmening.
- **Brobyggarna** (högst betweenness) är **Maria**, som är den enda starka länken till kunden, och **Ahmed**, som
  kopplar nykomlingen Emma till resten av teamet.
- **Kunskapsriskerna är två:**
  - **Fortnox-integrationen hos Ahmed.** Han skrev alla tre Fortnox-PR:erna och incidentanalysen, och alla tre
    Fortnox-delarna har bus factor 1.
  - **Momsreglerna och bildlagringen hos Sofia** (bus factor 1). Själva kvittoläsaren delar hon med David, som
    granskat alla hennes ändringar (bus factor 2).
- **Emma** har minst total aktivitet bland utvecklarna, men är etta på attestdelarna, eftersom det var hennes uppgift.

Se fråga 6 och 7 i `demofrågor.md`.

---

## Vem gör vad i förloppen

| # | Förlopp | Driver | Är också med |
| ---: | --- | --- | --- |
| 1 | Kvittotolkningen byggs | Sofia | David (granskar), Nina (testar), Maria |
| 2 | Momsreglerna | Maria | Anders (tar upp det), Sofia (bygger), David |
| 3 | Offline i appen | Lucas (för lokal kö) | Ahmed (emot, vill kräva nätverk), David (beslutar), Nina (varnar) |
| 4 | Fortnox går sönder | Ahmed | Anders (märker det), David, Maria (svarar kunden) |
| 5 | Dubbla utbetalningar | Ahmed (rättar) | Anders (upptäcker), Lucas, Nina (påpekar i granskningen), David |
| 6 | Emma kommer in i teamet | Emma | Ahmed (mentor), David |
| 7 | Attest med beloppsgränser | Emma | Maria (krav), Ahmed (granskar), Nina (testar) |
| 8 | Release av Kvitta 1.0 | Maria | Alla, Anders (tackar) |
| 9 | GDPR och kvittobilder | David | Sofia (ändrar lagringen), Maria, Anders |

---

## Systemet: repon, delar och filer

Tre repon. Namnen skrivs med små bokstäver, eftersom det är så systemet känner igen en pull request i text
(`kvitta-api#12`).

### `kvitta-api` (backend)

| Del | Filer |
| --- | --- |
| Kvittotolkning | `app/receipts/reader.py`, `app/receipts/vat.py`, `app/receipts/storage.py` |
| Utlägg | `app/expenses/api.py`, `app/expenses/sync.py` |
| Attest | `app/approval/rules.py`, `app/approval/limits.py` |
| Fortnox-integration | `app/integrations/fortnox/client.py`, `app/integrations/fortnox/auth.py` |
| Utbetalningsexport | `app/payouts/export.py` |
| Tester | `tests/receipts/test_reader.py`, `tests/receipts/test_vat.py`, `tests/approval/test_limits.py`, `tests/payouts/test_export.py`, `tests/integrations/test_fortnox.py` |

### `kvitta-mobile` (mobilappen)

| Del | Filer |
| --- | --- |
| Kamera och kvitto | `src/capture/CameraScreen.tsx`, `src/capture/upload.ts` |
| Offlinekö | `src/offline/queue.ts`, `src/offline/retry.ts` |
| Utläggsformulär | `src/expenses/ExpenseForm.tsx` |
| Tester | `src/offline/queue.test.ts` |

### `kvitta-web` (webbportalen)

| Del | Filer |
| --- | --- |
| Attest | `src/approval/ApprovalList.tsx`, `src/approval/limits.ts` |
| Administration | `src/admin/Settings.tsx` |

---

## Ärenden

Nyckeln `KV` och ett löpnummer, i den ordning ärendena skapas.

| Nyckel | Titel (på engelska i datan) | Förlopp |
| --- | --- | --- |
| `KV-1` | Receipt reader: extract amount, VAT, date and merchant | 1 |
| `KV-2` | Offline mode for expense capture | 3 |
| `KV-3` | Onboarding: first tasks for Emma | 6 |
| `KV-4` | Support 12 % and 6 % VAT and representation rules | 2 |
| `KV-5` | Approval limits per manager | 7 |
| `KV-6` | Retention and access for receipt images | 9 |
| `KV-7` | Fortnox export failing after authentication change | 4 |
| `KV-8` | Duplicate payout of the same expense | 5 |
| `KV-9` | Release Kvitta 1.0 | 8 |
| `KV-10` och uppåt | Små buggar och uppgifter i vardagsbruset, och uppföljningen efter förlopp 5 | Brus |

---

## Dokument

Varje dokument som ska gå att hänvisa till har en identifierare först i titeln, följd av ett kolon.

| Id i datan | Identifierare | Typ | Förlopp | Versioner |
| --- | --- | --- | --- | --- |
| `doc-001` | `REQ-VAT` | Krav | 2 | 2 (version 1 bara 25 %, version 2 alla momssatser och representation) |
| `doc-002` | `DESIGN-RECEIPT-READER` | Teknisk design (**det långa dokumentet**) | 1 | 2 |
| `doc-003` | `ADR-OFFLINE-QUEUE` | Beslut | 3 | 1 |
| `doc-004` | `ADR-IMAGE-RETENTION` | Beslut | 9 | 1 |
| `doc-005` | `RELEASE-CHECKLIST` | Checklista | 8 | 3 (Visma-exporten flyttas till 1.1 i version 2) |
| `doc-006` | `REVIEW-FORTNOX-OUTAGE` | Genomgång efter incident | 4 | 1 |

---

## Slack

Arbetsyta `kvitta`, fem kanaler:

| Kanal | Används till |
| --- | --- |
| `#dev` | Det mesta: frågor, diskussioner, pull requests |
| `#mobile` | Appen och offlinedebatten |
| `#incidents` | Fortnox och dubbelutbetalningen |
| `#releases` | Releaseplanering och 23 mars |
| `#random` | Brus: lunch, fika, helgen |

---

## Möten

| Möte | När | Förlopp |
| --- | --- | --- |
| Standup (tre exempel) | 3 feb, 24 feb, 17 mars | Brus |
| Offline design meeting | 12 feb | 3 |
| VAT rules meeting | 17 feb | 2 |
| Sprint review | 20 feb | 1, 3, 6 |
| Sprint review | 6 mars | 1, 4, 7 |
| Release planning | 10 mars | 8 |
| Duplicate payout review | 13 mars | 5 |
| Go/no-go | 20 mars | 8 |

---

## Hur mycket data det blir, ungefär

| Vad | Planerat | Faktiskt i `data/kvitta_seed.sql` |
| --- | ---: | ---: |
| Personer | 8 (plus 2 gemensamma adresser) | 8 (plus 2 gemensamma adresser) |
| Ärenden | 12–14 | 12 (`KV-1` till `KV-12`) |
| Pull requests | cirka 15 | 14 |
| Dokument | 6 (cirka 10 versioner) | 6 (10 versioner) |
| Möten | 10 (50–60 inlägg) | 10 (45 inlägg) |
| Slack-meddelanden | cirka 60 | 47 (50 rader med redigeringar) |
| Mejl | 12–15 | 14 |
