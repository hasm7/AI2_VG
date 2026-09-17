# Beslutad projektinriktning

## Projektets sammanhang och mål

Projektet ska imitera informationskällor från ett software engineering-team som arbetar med en gemensam produkt.

Materialet ska beskriva samma projekt, personer och händelser över tid. Källorna ska bidra med olika delar av sammanhanget så att ett minnessystem kan hitta relationer mellan behov, diskussioner, beslut, arbetsuppgifter och implementation.

Målet är att kunna hitta kontext och samband över flera källor, exempelvis:

- vem som pratade med vem inför ett beslut
- vilka underlag som beskriver bakgrunden till ett beslut
- vilka källor som handlar om samma händelse
- vilka personer, ärenden och samtal som hör ihop
- var information saknas, har förändrats eller motsäger annan information

## -------------------------------------------------------------------------------------------------

## De sex datakällorna och deras nio SQL-tabeller

SQL bevarar källmaterialet inför grafimporten. Käll-ID:n, tider, versioner och hänvisningar följer med så att grafen kan bygga relationerna och behålla rätt sammanhang.

Nedan anges tabellindelningen och vad varje rad representerar. Exakta kolumner och datatyper är ännu inte fastställda.


### 1. Mail

Mailtrådar mellan kunden, den produktansvariga och teamet.

Här finns behov, förtydliganden och besked som kommunicerats mellan parterna.

**Tabell: `mail_messages`**

En rad innehåller ett mail med avsändare, mottagare, ämne, text, tid och svarshänvisning. Mottagarna sparas som en strukturerad lista.


### 2. Slack – projektchatt

Teamets löpande kommunikation i kanaler, meddelanden och svarstrådar.

Här finns frågor, diskussioner och informella överenskommelser under arbetets gång.

**Tabell: `slack_messages`**

En rad innehåller ett meddelande med avsändare, kanal, text, tid och trådhänvisning. Tidigare meddelandeversioner bevaras vid redigering.


### 3. Teams – mötestranskript

Mötenas samtal sparade som text, med uppgift om vem som talar och när.

Här finns muntliga diskussioner, avvägningar och beslut.

**Tabell: `teams_meetings`**

En rad innehåller mötets grunduppgifter: ID, titel, tid och tillgängliga deltagaruppgifter.

**Tabell: `teams_transcript_segments`**

En rad innehåller ett yttrande med mötes-ID, talare, text, tidsangivelser och ordning i samtalet.


### 4. Ärenden/tickets

Arbetsuppgifter och buggar med ansvarig, kommentarer och ändringshistorik.

Här finns också acceptanskriterier: vad som ska vara uppfyllt för att uppgiften ska räknas som klar. Ärendena kopplar behov till konkret arbete.

**Tabell: `issue_versions`**

En rad innehåller en ärendeversion med beskrivning, acceptanskriterier, status, ansvarig och tidpunkt. Samma ärende-ID följer versionerna.

**Tabell: `issue_comments`**

En rad innehåller en kommentar med ärende-ID, författare, text och tid.


### 5. Krav och teknisk dokumentation

Dokument som beskriver vad produkten ska klara och hur lösningen är tänkt att fungera.

Tidigare versioner sparas så att det går att följa hur kraven och den tekniska beskrivningen förändrats.

**Tabell: `document_versions`**

En rad innehåller en dokumentversion med dokument-ID, titel, innehåll, författare, version och tidpunkt.


### 6. PR:er, kodgranskningar och kodändringar

Pull requests (PR:er) med beskrivningar av föreslagna ändringar, granskarnas kommentarer och den tillhörande koden före och efter ändringen.

Det gör det möjligt att jämföra vad kraven säger, vad PR:n beskriver och vad kodändringen visar.

**Tabell: `pr_versions`**

En rad innehåller en PR-version med PR-ID, beskrivning, status, kodversion och kod före/efter. Ändringar i flera filer sparas som en strukturerad lista.

**Tabell: `pr_reviews`**

En rad innehåller en kommentar eller ett granskningsbeslut med författare, tid och hänvisning till granskad PR-/kodversion. Fil och kodposition anges när det gäller specifik kod.

## -------------------------------------------------------------------------------------------------

## Kodmaterialets omfattning

Kodmaterialet avgränsas till utvalda funktioner med en sammanhängande ändringshistorik. Kodexemplen ska ge tillräckligt sammanhang för att jämföra vad en PR beskriver med vad kodändringen visar.

Granskningar och kommentarer ska kunna kopplas till den kodversion de avser.

## Samband och historik

Materialet ska hänga ihop över källorna. Ett behov kan exempelvis uttryckas i ett mail, diskuteras i Slack och på ett möte, dokumenteras som krav och följas vidare till ett ärende och en PR.

Källorna får innehålla olika perspektiv, ändrade besked, luckor och motsägelser. Tidigare versioner av krav, ärenden och PR-material ska bevaras så att utvecklingen kan följas över tid.

## Lagring och användning

SQL-lagret ska bevara det simulerade källmaterialet i dess grundform och fungera som underlag för import till grafdatabasen.

Grafdatabasen ska fungera som minnessystem. Agenterna ska använda den för att hitta sammanhang och analysera materialet. Källinnehåll och spårbara hänvisningar ska följa med tillsammans med de objekt och relationer som extraheras, så att agenterna kan läsa underlaget bakom sambanden.

Flödet är:

```text
Simulerade källor → SQL med originalmaterial → Extraktion och överföring
→ Grafdatabas/minnessystem → Agenternas analys
```

## Arbetsordning

Minnessystemet byggs först: lagring, överföring, relationer och sökning efter relevant kontext. Djupare reasoning och det fulla multiagentsystemet kommer därefter.
