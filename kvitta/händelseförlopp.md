# Kvitta – händelseförlopp

Det här dokumentet beskriver vad som händer i teamet som bygger **Kvitta** under de åtta veckor som exempeldatan
täcker. Det är berättelsen som datan ska bygga på: varje mejl, Slack-meddelande, möte, ärende, dokument och pull
request i datan ska höra hemma någonstans i den, eller vara vanligt vardagsbrus runt omkring.

Vad Kvitta är och vilka delar den består av står i `info.md`.

---

## Ramen

| | |
| --- | --- |
| **Period** | Måndag 2 februari – fredag 27 mars 2026 |
| **Mål** | Kvitta 1.0 släpps till betalande kunder **måndag 23 mars** |
| **Pilotkund** | Konsultbolaget **Bergström & Co**, som har testat Kvitta sedan januari |
| **Balans** | Ungefär hälften vanligt arbete och framgångar, hälften beslut, ändringar och problem, som i ett riktigt team |
| **Personer** | 7 i teamet och 1 kundkontakt, se `team-och-system.md` |
| **Språk i datan** | Engelska. Mejl, Slack, möten, ärenden, dokument och kod skrivs på engelska (agenten söker med engelska nyckelord). Frågorna till AI:n kan ställas på svenska. |

Hela perioden ligger före sommartidens början (29 mars), så alla tider har samma tidszon.

---

## Översikt

| # | Förlopp | Typ | När |
| ---: | --- | --- | --- |
| 1 | Kvittotolkningen byggs | Vanligt arbete, går bra | 2 feb – 6 mars |
| 2 | Momsreglerna | Krav som ändras, hanteras bra | 9 feb – 27 feb |
| 3 | Offline i appen | Designbeslut med debatt | 4 feb – 20 feb |
| 4 | Fortnox-integrationen går sönder | Problem med ett externt beroende | 3 mars – 10 mars |
| 5 | Dubbla utbetalningar | Allvarlig bugg med rotorsak i förlopp 3 | 9 mars – 18 mars |
| 6 | En ny utvecklare kommer in i teamet | Vanligt arbete, samarbete | 9 feb – 20 mars |
| 7 | Attest med beloppsgränser | Vanligt arbete, liten funktion | 16 feb – 6 mars |
| 8 | Release av Kvitta 1.0 | Framgång, allt samlas | 9 mars – 25 mars |
| 9 | GDPR och kvittobilder | Regelkrav som hanteras lugnt | 23 feb – 13 mars |
| – | Vardagsbrus | Allt runt omkring | Hela perioden |

---

## 1. Kvittotolkningen byggs

**Typ:** vanligt arbete som går bra.

Teamet bygger tjänsten som läser av belopp, moms, datum och butik från ett fotograferat kvitto. En utvecklare äger
nästan allt arbete. Det är hennes eller hans område, och ingen annan kan det lika bra.

**Så går det till**

1. Ett ärende skapas: "Kvittotolkning: läs belopp, moms, datum och butik".
2. Utvecklaren skriver ett designdokument om hur tolkningen ska fungera och vilka kvittotyper som ska klaras.
   **Det här är datans långa dokument**, ungefär 14 000–16 000 tecken, så att sökningen måste dela upp det i två
   delar. Det är ett test av den funktionen. Strular det tas dokumentet bort eller kortas.
3. Arbetet sker i några pull requests, som granskas av kollegor med små förbättringsförslag.
4. När momsreglerna ändras (förlopp 2) behöver tolkningen också känna igen 12 % moms. En extra PR löser det.
5. Ett testresultat visar 96 % träffsäkerhet på 500 riktiga kvitton från pilotkunden.
6. Tolkningen visas upp på ett sprintmöte och ärendet stängs.

**Syns i:** ärende med flera versioner, designdokument, pull requests och granskningar, Slack, sprintmöte.

**Frågor appen ska kunna besvara**
- Vem byggde kvittotolkningen, och hur fungerar den?
- Vem kan mest om kvittotolkningen? *(Svaret visar också en kunskapsrisk: bara en person.)*
- Hur träffsäker är den?

---

## 2. Momsreglerna

**Typ:** ett krav som ändras och hanteras bra.

Pilotkundens ekonomiansvarige mejlar och påpekar två saker: restaurangkvitton har 12 % moms, inte 25 %, och
representation (till exempel kundmiddagar) har egna regler för hur mycket moms som får dras av.

**Så går det till**

1. Mejlet från pilotkunden kommer in och vidarebefordras till teamet.
2. Produktägaren skapar ett ärende och skriver om kravdokumentet om moms: **version 1** hade bara 25 %, **version 2**
   beskriver 25, 12 och 6 % moms och reglerna för representation.
3. På ett möte bestäms hur det ska lösas: tolkningen känner igen momssatsen (förlopp 1), och den anställde markerar
   själv om ett utlägg är representation.
4. Ändringen görs i en pull request och granskas.
5. Pilotkunden testar och svarar att det nu blir rätt.

**Syns i:** mejl från kunden, kravdokument i två versioner, möte, ärende, pull request, Slack.

**Frågor appen ska kunna besvara**
- Varför ändrades momskravet, och vem tog upp det först?
- Vilka dokument beskriver kravet, och hur ändrades det mellan versionerna?
- Blev kunden nöjd med lösningen?

---

## 3. Offline i appen

**Typ:** ett designbeslut med debatt.

Mobilappen måste fungera på resor, i tunnelbanan och utomlands. Två utvecklare är oense om hur.

**Så går det till**

1. En diskussion i Slack: den ena vill att appen sparar utlägg lokalt och skickar dem automatiskt när nätet kommer
   tillbaka, med nya försök om det misslyckas. Den andra vill att appen kräver nätverk för att slippa problem med
   dubbletter och synk. Båda har bra argument.
2. Frågan tas upp på ett möte. Alternativen vägs mot varandra. Beslutet blir: **spara lokalt och försök igen
   automatiskt.**
3. Beslutet skrivs ner i ett beslutsdokument, med de avvisade alternativen och varför.
   **Medveten motsägelse:** ärendet säger fortfarande "appen kräver nätverk" i fyra dagar efter mötet, innan någon
   uppdaterar det. Under de dagarna säger källorna olika saker.
4. Funktionen byggs i mobilappen och i backend, med pull requests och granskningar.
5. I en av granskningarna skriver en testare en varning: *"Vad händer om samma utlägg skickas två gånger? Finns det
   skydd mot dubbletter i utbetalningen?"* Frågan får ett kort svar ("det löser vi senare") och ingen åtgärd.

**Syns i:** Slack-debatt, möte, beslutsdokument, ärende, pull requests och granskningar.

**Frågor appen ska kunna besvara**
- Vem förespråkade vad innan beslutet togs?
- Varför valdes lokal lagring med omförsök?
- Var är beslutet dokumenterat?

---

## 4. Fortnox-integrationen går sönder

**Typ:** ett problem med ett externt beroende.

Fortnox, bokföringssystemet som pilotkunden använder, ändrar hur inloggningen mot deras API fungerar, utan tydlig
förvarning. Kvittas export till bokföringen slutar fungera.

**Så går det till**

1. Tisdag 3 mars: ett automatiskt larm om misslyckade exporter kommer från `alerts@`-adressen.
2. Samma dag mejlar pilotkunden supporten: utläggen syns inte i bokföringen.
3. Felsökning i Slack. Integrationsutvecklaren hittar orsaken i Fortnox ändringslogg.
4. En snabbfix går ut på kvällen den 4 mars. En ordentlig lösning, med den nya inloggningen på rätt sätt, kommer i
   en egen pull request veckan efter.
5. En kort genomgång skrivs: vad som hände, hur länge exporten var nere (cirka två dagar) och vad teamet gör för att
   upptäcka sådant tidigare.
6. Supporten mejlar kunden och förklarar.

**Syns i:** larmmejl, kundmejl, Slack, ärende, två pull requests, dokument med genomgången, mejl till kunden.

**Frågor appen ska kunna besvara**
- Vad hände med Fortnox-exporten, och hur länge var den nere?
- Vilka källor beskriver incidenten?
- Vad gör teamet för att det inte ska hända igen?

---

## 5. Dubbla utbetalningar

**Typ:** en allvarlig bugg vars rotorsak ligger i förlopp 3.

En anställd hos pilotkunden får samma utlägg, en tågresa för 1 840 kronor, utbetalt två gånger.

**Så går det till**

1. Måndag 9 mars: pilotkundens ekonomiansvarige mejlar och undrar varför samma utlägg har betalats ut två gånger.
2. Ett ärende skapas med hög prioritet. Felsökning i Slack.
3. Orsaken hittas: appen skickade utlägget igen efter ett nätverksavbrott, precis som beslutet i **förlopp 3**
   sa att den skulle. Men utbetalningsexporten kontrollerar inte om ett utlägg redan har skickats. Det är den risk
   som testaren varnade för i förlopp 3.
4. Buggen rättas: varje utlägg får ett unikt id som exporten kontrollerar. Ett test läggs till som bevisar att
   dubbletter stoppas.
   **Medveten motsägelse:** PR-beskrivningen säger "prevents all duplicate expenses", men koden skyddar bara
   utbetalningsexporten. Appen kan fortfarande skicka samma utlägg två gånger. Testaren påpekar det i granskningen,
   PR:en godkänns ändå, och ett uppföljningsärende skapas till efter releasen.
5. På ett möte konstaterar teamet att varningen från testaren borde ha tagits på allvar, och att granskningar med
   öppna frågor inte ska godkännas.
6. Kunden får en förklaring och en rättelse i nästa löneutbetalning.
7. Ärendet stängs onsdag 18 mars, i tid före releasen.

**Syns i:** kundmejl, ärende, Slack, pull request med test, möte, mejl till kunden.

**Frågor appen ska kunna besvara**
- Varför blev det dubbla utbetalningar?
- Hänger det ihop med offlinebeslutet?
- Varnade någon för det här innan? Vem, och när?

---

## 6. En ny utvecklare kommer in i teamet

**Typ:** vanligt arbete och samarbete.

En ny utvecklare börjar måndag 9 februari. Förloppet visar hur en ny person växer in i teamet.

**Så går det till**

1. Ett introduktionsärende skapas: "Onboarding: första uppgifter", med en lista över små, lagom svåra uppgifter.
2. En erfaren kollega blir mentor. Välkomstmeddelande i Slack, och den nya är med på möten.
3. De första pull requests är små (en textändring, ett enklare fel) och får många granskningskommentarer.
4. Den första riktiga funktionen blir attesten med beloppsgränser (**förlopp 7**), med stöd av mentorn.
5. Mot slutet av perioden granskar den nya själv andras kod.

**Syns i:** introduktionsärende, Slack, pull requests och granskningar, möten.

**Frågor appen ska kunna besvara**
- Vem är nyast i teamet, och vem har hjälpt henne eller honom?
- Vilka jobbar mest ihop?
- Vad har den nya byggt?

---

## 7. Attest med beloppsgränser

**Typ:** vanligt arbete, en liten funktion utan problem.

Chefer ska bara få attestera utlägg upp till en viss summa. Större belopp går vidare till ekonomichefen.

**Så går det till**

1. Produktägaren skapar ett ärende med tydliga acceptanskriterier: gräns per chef, som standard 5 000 kronor.
2. Den nya utvecklaren (förlopp 6) bygger funktionen i webbportalen och backend, med mentorn som granskare.
3. Testaren testar och godkänner.
4. Funktionen släpps och ärendet stängs.

**Syns i:** ärende, pull requests och granskningar, Slack.

**Frågor appen ska kunna besvara**
- Hur fungerar attesten med beloppsgränser?
- Vem byggde den, och vilka delar av systemet berörs?

---

## 8. Release av Kvitta 1.0

**Typ:** en framgång där allt samlas.

**Så går det till**

1. Ett releaseärende skapas: "Release Kvitta 1.0".
2. Ett planeringsmöte går igenom vad som måste vara med. En mindre funktion (export till Visma) **skjuts upp till
   1.1**, eftersom pilotkunden använder Fortnox och tiden inte räcker.
3. En releasechecklista skrivs som dokument och uppdateras i flera versioner allt eftersom punkter bockas av.
4. Ett go/no-go-möte fredag 20 mars: buggen i förlopp 5 är löst och Fortnox fungerar igen efter förlopp 4. Beslut:
   **go**.
5. Releasen går ut måndag 23 mars. Glada meddelanden i Slack.
6. Onsdag 25 mars mejlar pilotkunden och tackar: allt fungerar, och de vill fortsätta som betalande kund.

**Syns i:** releaseärende, möten, releasechecklista i flera versioner, Slack, mejl från kunden.

**Frågor appen ska kunna besvara**
- Vad ingick i 1.0, och vad sköts upp och varför?
- Blev releasen i tid?
- Vad krävdes innan teamet sa go?

---

## 9. GDPR och kvittobilder

**Typ:** ett regelkrav som hanteras lugnt.

Kvittobilder kan innehålla personuppgifter, till exempel namn på hotellkvitton. Säkerhetsansvarig vill veta hur länge
bilderna sparas.

**Så går det till**

1. Säkerhetsansvarig frågar i Slack hur länge kvittobilderna sparas. Svaret är: för alltid, ingen har bestämt något.
2. Ett ärende skapas, och ett kort beslutsdokument skrivs: bilderna sparas i sju år, som bokföringslagen kräver för
   underlag, och raderas sedan automatiskt. Bara ekonomiavdelningen kan se bilderna.
3. En liten ändring görs i lagringen och i behörigheterna.
4. Pilotkunden får en kort information om hur deras bilder hanteras.

**Syns i:** Slack, ärende, beslutsdokument, pull request, mejl till kunden.

**Frågor appen ska kunna besvara**
- Hur länge sparas kvittobilderna, och varför?
- Vem tog upp frågan?

---

## Vardagsbrus

Ett riktigt team pratar om mycket som inte hör till något av förloppen. Därför ska datan också innehålla vanligt brus:

- korta standup-möten där alla säger vad de gör,
- Slack-meddelanden om lunch, en trasig testmiljö eller en fråga om hur man kör något lokalt,
- små buggar som hittas och rättas samma dag,
- rutinmejl, till exempel en påminnelse om tidrapportering eller ett automatiskt byggmeddelande.

Bruset gör att appen måste hitta rätt bland mycket. Det gör också att datan känns som en riktig arbetsplats.

---

## Hur förloppen hänger ihop

```text
 3 Offline-beslutet ──orsakar──► 5 Dubbla utbetalningar ──måste lösas före──► 8 Release 1.0
                                                                               ▲
 2 Momsreglerna ──ändrar──► 1 Kvittotolkningen                                │
                                                                               │
 4 Fortnox går sönder ──måste vara löst före───────────────────────────────────┘

 6 Ny utvecklare ──bygger──► 7 Attest med beloppsgränser

 9 GDPR ──påverkar──► hur kvittobilder från 1 sparas
```

- **3 orsakar 5.** Det viktigaste sambandet: ett beslut i ett förlopp orsakar ett problem i ett annat, några veckor
  senare.
- **2 påverkar 1.** Ett ändrat krav kräver en ändring i en annan del av systemet.
- **4 och 5 påverkar 8.** Releasen kan inte gå ut förrän båda är lösta.
- **6 och 7** visar hur en ny person tar sig in i arbetet.
- **Pilotkunden** finns med i 2, 4, 5, 8 och 9, och ger en röst utifrån.

---

## Vad appen ska kunna fånga upp

| Del av appen | Vad den hittar i Kvitta |
| --- | --- |
| **Referenser** | Kopplar ihop mejl, Slack, möten, dokument och pull requests med rätt ärende, när de nämner det |
| **Kunskapslagret** | Ett ämne per förlopp, med händelserna i ordning och vem som var med |
| **Arkitektur** | Systemets delar: mobilappen, kvittotolkningen, attesten, webbportalen, integrationerna, och hur de hänger ihop |
| **Rotorsak och påverkan** | Varför de dubbla utbetalningarna hände, och att offlinebeslutet låg bakom |
| **Expertis och samarbete** | Vem som kan vad, vem som jobbar med vem, och hur den nya utvecklaren växer in |
| **Grafalgoritmer** | Teamets grupper, vem som är brygga mellan dem, och kunskapsrisken kring kvittotolkningen |
| **Sökningen** | Att allt ovan går att hitta när du ställer en fråga |

---

## Nästa steg

1. **Teamet och systemet** är bestämda i `team-och-system.md`: personer, roller, grupper, repon, filer, ärendenycklar,
   dokument, Slack-kanaler och möten.
2. **Demofrågorna** finns i `demofrågor.md`: frågor som visar vad grafen kan svara på som en vanlig SQL-fråga inte
   kan.
3. **Datan:** när scenariot är klart byggs själva exempeldatan utifrån det här dokumentet och de tekniska dokumenten i
   `docs/`.


--------------------------------------------------------------------------------



Stora händelseförlopp och deras delhändelser

1. Kvittotolkningen byggs (2 feb – 6 mars) · vanligt arbete, går bra

Ärendet skapas
Designdokumentet skrivs
Pull requests byggs och granskas
En extra ändring för 12 % moms (efter förlopp 2)
Testresultat: 96 % träffsäkerhet
Demo på sprintmötet, ärendet stängs

2. Momsreglerna (9 – 27 feb) · krav som ändras, hanteras bra

Pilotkunden mejlar om 12 % moms och representation
Mejlet vidarebefordras till teamet
Kravdokumentet skrivs om från version 1 till version 2
Mötet beslutar hur det ska lösas
Pull request och granskning
Kunden bekräftar att det blev rätt

3. Offline i appen (4 – 20 feb) · designbeslut med debatt

Debatt i Slack: spara lokalt eller kräva nätverk
Mötet väger alternativen och beslutar att spara lokalt med omförsök
Beslutsdokumentet skrivs
Byggs i mobilappen och backend
Testaren varnar för dubbletter i en granskning, men ingen gör något

4. Fortnox-integrationen går sönder (3 – 10 mars) · externt problem

Automatiskt larm om misslyckade exporter
Pilotkunden mejlar supporten
Felsökning i Slack, orsaken hittas i Fortnox ändringslogg
En snabbfix på kvällen den 4 mars
En ordentlig lösning veckan efter
Genomgång av vad som hände
Förklaring till kunden

5. Dubbla utbetalningar (9 – 18 mars) · allvarlig bugg, rotorsak i förlopp 3

Kunden mejlar: samma utlägg (1 840 kr) utbetalt två gånger
Ärende med hög prioritet, felsökning
Orsaken: omförsöken från förlopp 3 och en export som saknar dubblettkontroll
Rättelse med unikt id och ett nytt test
Mötet konstaterar att varningen borde ha tagits på allvar
Förklaring och rättelse till kunden
Ärendet stängs före releasen

6. En ny utvecklare kommer in i teamet (9 feb – 20 mars) · samarbete

Introduktionsärende med första uppgifter
En mentor utses, och den nya välkomnas i Slack
De första små pull requests, med många kommentarer
Den första riktiga funktionen: förlopp 7
Granskar själv andras kod mot slutet

7. Attest med beloppsgränser (16 feb – 6 mars) · liten funktion, inga problem

Ärende med acceptanskriterier (gräns 5 000 kr)
Byggs av den nya utvecklaren med mentorn som granskare
Testas och godkänns
Släpps, ärendet stängs

8. Release av Kvitta 1.0 (9 – 25 mars) · framgång

Releaseärendet skapas
Planeringsmöte: Visma-exporten skjuts upp till 1.1
Releasechecklistan i flera versioner
Go/no-go-möte 20 mars med beslut go
Releasen 23 mars, glädje i Slack
Pilotkunden tackar och blir betalande kund (25 mars)

9. GDPR och kvittobilder (23 feb – 13 mars) · regelkrav, hanteras lugnt

Säkerhetsansvarig frågar hur länge bilderna sparas
Ärende och beslutsdokument: 7 år, sedan automatisk radering, bara ekonomi ser bilderna
En liten ändring i lagring och behörigheter
Information till kunden
Vardagsbrus (hela perioden)
Korta standup-möten
Slack om lunch, en trasig testmiljö och hur man kör saker lokalt
Små buggar som rättas samma dag
Rutinmejl: påminnelse om tidrapportering och automatiska byggmeddelanden
Hur förloppen hänger ihop
3 orsakar 5: offlinebeslutet ligger bakom de dubbla utbetalningarna.
2 ändrar 1: den nya momsregeln kräver en ändring i tolkningen.
4 och 5 måste vara lösta före 8.
6 bygger 7: den nya utvecklarens första funktion.
9 påverkar 1: hur kvittobilderna sparas.
Pilotkunden syns i förlopp 2, 4, 5, 8 och 9.