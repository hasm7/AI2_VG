# Kvitta – demofrågor

Tio frågor som ska visa vad en graf med flera lager (multilayer graph RAG) kan svara på som en vanlig SQL-fråga inte
kan. Datan byggs så att varje fråga har ett tydligt facit. Frågorna blir också underlag för agentens testfrågor.

**Skillnaden i en mening:** SQL svarar på det som står i en kolumn ("lista Anders mejl", "hur många pull requests har
Ahmed gjort"). Grafen svarar på det som kräver att man förstår, kopplar ihop och drar slutsatser över flera källor.

Personer, ärenden och dokument som nämns finns i `team-och-system.md`. Förloppen finns i `händelseförlopp.md`.

---

## Frågorna

### 1. Varför fick en anställd hos Bergström & Co dubbla utbetalningar?
**Facit:** Appen skickade samma utlägg igen efter ett nätverksavbrott, och utbetalningsexporten kontrollerade inte
dubbletter. Grunden var offlinebeslutet i februari (spara lokalt och försök igen automatiskt).
**Varför SQL inte klarar det:** kundens mejl i mars och designbeslutet i februari har ingen gemensam kolumn.
Orsakssambandet finns bara i texten.
**Lager som visas:** kunskapslagret, rotorsak och påverkan (sambandet mellan förlopp 3 och 5).

### 2. Varnade någon för det här innan det hände?
**Facit:** Ja. Nina Petrova frågade i en granskning i februari om samma utlägg kan skickas två gånger, och fick
svaret "det löser vi senare".
**Varför SQL inte klarar det:** varningen ligger i en granskning av en annan pull request än buggen. SQL vet inte att
de handlar om samma sak.
**Lager som visas:** sökningen, kunskapslagret, rotorsak och påverkan.

### 3. Vem tyckte vad innan offlinebeslutet togs?
**Facit:** Lucas Holm ville spara lokalt och försöka igen automatiskt. Ahmed Karimi ville kräva nätverk för att
slippa dubbletter. David Okafor beslutade på mötet 12 februari, och beslutet finns i `ADR-OFFLINE-QUEUE`.
**Varför SQL inte klarar det:** åsikterna står i Slack och i en mötesutskrift, inte i något fält.
**Lager som visas:** referenser, kunskapslagret (vem var med före beslutet).

### 4. Vilka källor beskriver Fortnox-incidenten?
**Facit:** alla sex datakällorna. Via ärendenumret `KV-7`: ärendet, Slack i `#incidents`, Marias två svarsmejl till
Anders, sprint review 6 mars, de tre pull requests (snabbfixen `kvitta-api#55`, den ordentliga lösningen
`kvitta-api#58` och uppföljningen `kvitta-api#61`) och dokumentet `REVIEW-FORTNOX-OUTAGE`. Två källor nämner inte
`KV-7`, eftersom de skrevs innan ärendet fanns: larmmejlet från `alerts@kvitta.se` och Anders Nybergs första mejl.
De hittas bara genom att sökningen förstår att de handlar om samma sak.
**Varför SQL inte klarar det:** källorna ligger i sex olika tabeller och hänger ihop bara genom ett ärendenummer som
står i fritext, och två av dem saknar även det.
**Lager som visas:** referenser, kunskapslagret, sökningen (embeddings) för de två mejlen utan ärendenummer.

### 5. Hur och varför ändrades momskravet?
**Facit:** Anders Nyberg påpekade att restaurangkvitton har 12 % moms och att representation har egna regler.
`REQ-VAT` gick från version 1 (bara 25 %) till version 2 (25, 12 och 6 % samt representation), och Sofia Berg byggde
om kvittotolkningen.
**Varför SQL inte klarar det:** svaret kräver att ett kundmejl, två dokumentversioner och en kodändring kopplas ihop,
och att skillnaden mellan versionerna förklaras.
**Lager som visas:** referenser, kunskapslagret, dokumentversioner.

### 6. Var finns det en kunskapsrisk i teamet?
**Facit:** två ställen. Fortnox-integrationen: Ahmed Karimi skrev alla Fortnox-ändringar och incidentanalysen, och
alla tre Fortnox-komponenter har bus factor 1. Kvittotolkningen: Sofia Berg ensam äger momsreglerna och
bildlagringen (bus factor 1), medan själva kvittoläsaren delas med David Okafor, som granskat alla hennes ändringar.
**Varför SQL inte klarar det:** svaret kräver att aktivitet räknas per person och systemdel och vägs samman till
expertis och "bus factor".
**Lager som visas:** arkitektur, expertis och samarbete, grafalgoritmer.

### 7. Vem är länken mellan kunden och teamet, och vem samarbetar mest?
**Facit:** Maria Lindgren är länken till kunden: hon och Anders Nyberg bildar en egen grupp, och hon har högst
betweenness tillsammans med Ahmed Karimi (som kopplar nykomlingen Emma till resten). David Okafor samarbetar mest:
han granskar ändringar i både backend och mobil och är med på nästan alla möten.
**Varför SQL inte klarar det:** svaret kräver en analys av hela samarbetsnätverket, inte en rad i en tabell.
**Lager som visas:** expertis och samarbete, grafalgoritmer (betweenness och grupper).

### 8. Stämmer pull requestens beskrivning av dubblettfixen med vad koden gör?
**Facit:** nej. Beskrivningen lovar att alla dubbletter stoppas, men koden skyddar bara utbetalningsexporten. Nina
Petrova påpekade det i granskningen, och ett uppföljningsärende skapades.
**Varför SQL inte klarar det:** svaret kräver att ett påstående i en beskrivning jämförs med en kodändring och en
granskning.
**Lager som visas:** sökningen, arkitektur (vilka delar koden rör), källorna.

### 9. Vad krävdes innan teamet sa go till releasen, och vad plockades bort?
**Facit:** krävdes: att dubbelutbetalningen (`KV-8`) var löst och att Fortnox-exporten fungerade igen (`KV-7`), plus en
grön regression. Bortplockat: Visma-exporten flyttades till version 1.1 på releaseplaneringen 10 mars.
**Varför SQL inte klarar det:** go/no-go-mötet hänger ihop med två andra förlopp genom vad som sägs, inte genom
nycklar.
**Lager som visas:** kunskapslagret, samband mellan förlopp.

### 10. Vilka problem har kunden haft under perioden?
**Facit:** momsen, Fortnox-exporten och dubbelutbetalningen. Alla var lösta före releasen, och kunden tackade efteråt.
**Varför SQL inte klarar det:** ordet "problem" står inte någonstans. Svaret kräver att mejlen förstås och kopplas
till rätt ärenden.
**Lager som visas:** sökningen (betydelse, inte bara ord), kunskapslagret.

---

## Krav på datan för att frågorna ska fungera

| Fråga | Datan måste |
| ---: | --- |
| 1, 2 | ha förlopp 3 och 5 inom 30 dagar från varandra, och säga i texten att omförsöken orsakade dubbletterna |
| 2, 8 | ha Ninas varning och kommentar uttryckligen i granskningarna, med ordet "duplicate" |
| 3 | ha både Lucas och Ahmeds argument i Slack och på mötet, och Davids beslut |
| 4 | låta alla sex källorna nämna `KV-7`, utom larmmejlet och Anders första mejl, som skrevs innan ärendet fanns |
| 5 | ha kundmejlet som nämner momsen, båda versionerna av `REQ-VAT` med `change_summary`, och Sofias pull request |
| 6 | låta Ahmed stå för allt Fortnox-arbete och Sofia för momsreglerna och bildlagringen |
| 7 | låta Maria vara den som har mest kontakt med kunden, och David granska och delta mest i hela teamet |
| 9 | låta go/no-go-mötet nämna `KV-7` och `KV-8` |
| 10 | låta varje kundmejl nämna rätt ärende |

---

## Så visar du det i presentationen

1. **Börja med SQL:** visa att en SQL-fråga kan lista Anders mejl eller räkna pull requests, men inte svara på
   fråga 1.
2. **Ställ fråga 1 till AI:n:** svaret knyter ihop kundens mejl i mars med ett beslut i februari, med källhänvisningar.
3. **Visa grafen:** filtret `Causes` visar kedjan från offlinebeslutet till dubbelutbetalningen.
4. **Ställ fråga 2 och 8:** vem varnade, och stämmer beskrivningen? Det visar att systemet hittar motsägelser.
5. **Avsluta med fråga 6 eller 7:** kunskapsrisk, länken till kunden och vem som samarbetar mest, sådant som ingen
   enskild källa säger.
