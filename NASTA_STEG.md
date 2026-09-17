# Nästa steg: koppla samman källorna

Anteckning om vad som är klart och vad som diskuterats om nästa steg.
Inget av detta är beslutat än.

## Läget nu

Alla sex datakällor är importerade från PostgreSQL till Neo4j, en knapp per
källa i viewern. Importerna är idempotenta — upprepade klick skapar inga
dubbletter, eftersom alla noder MERGE:as på sin nyckel.

| Källa | Nod | Relationer |
|---|---|---|
| Mail | `MailMessage` | `SENT_MAIL`, `MAIL_RECIPIENT` |
| Slack | `SlackMessage` | `SENT_SLACK_MESSAGE`, `SLACK_THREAD_REPLY_TO` |
| Teams | `TeamsMeeting`, `TeamsTranscriptSegment` | `PARTICIPATED_IN_MEETING`, `HAS_TEAMS_TRANSCRIPT_SEGMENT`, `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` |
| Ärenden | `Issue` | `OWNS_ISSUE`, `COMMENTED_ON_ISSUE` |
| Dokumentation | `Document` | `AUTHORED_DOCUMENT` |
| PR:er | `PullRequest` | `AUTHORED_PR`, `REVIEWED_PR` |

Genomgående princip: en nod per logiskt objekt, med senaste versionen som
properties. Underordnat material — kommentarer, granskningar, tidigare
versioner — ligger som lista på noden i stället för som egna noder.

## Vad som redan hänger ihop

Personerna kopplar källorna till varandra. Anna är samma `Person`-nod i Slack,
Teams, ärendet och dokumentet. Erik i ärendet och PR:en.

Det gör att frågor som *vad har Anna varit inblandad i* redan fungerar och ger
svar från flera källor.

## Vad som saknas

Sambandet i sak. Att mailet, mötet, kravet, ärendet och PR:en handlar om
**samma sak** — 60 minuters timeout för admin — framgår bara av texten. Det
finns ingen relation som säger det.

Utan den kopplingen går det inte att fråga *vad ledde fram till PR #42*.

## Diskuterat förslag: Topic-noder

Ett extraktionssteg som skapar `Topic`-noder och kopplar källorna till dem:

```
mail-001      ─┐
slack-001     ─┤
meeting-001   ─┼─> Topic "admin session timeout"
doc-001       ─┤
AUTH-17       ─┤
PR #42        ─┘
```

Relation: `MailMessage -[:ABOUT]-> Topic`, och motsvarande för övriga källor.

Arbetsgång:

1. En agent läser varje källnod — texten finns redan som properties.
2. Den bedömer vad noden handlar om.
3. Samma ämne får samma `Topic`-nod.
4. Relationen `ABOUT` skapas.

Det motsvarar steget "Extraktion och överföring" i projektinriktningen.

## Varför inte en Project-nod

Övervägdes och valdes bort. Om allt i databasen hör till samma projekt får
alla noder samma svar, och relationen skiljer inget från något. En
`Project`-nod blir meningsfull först med flera projekt.

Poängen med `Topic` är motsatsen: bara vissa källor pekar på ett visst ämne.
Nästa historia i materialet hamnar på ett annat `Topic`.

## Obesvarade frågor

- **När körs extraktionen?** Egen knapp i viewern efter importerna, eller
  separat skript?
- **Vem gör bedömningen?** En LLM som läser texten, eller regler som skrivs
  för hand?
- **Hur undviks dubbletter av Topic?** Samma ämne måste hitta samma nod även
  när det formuleras olika i olika källor.
- **Deterministiska kopplingar först?** Delar av sambanden står redan i
  klartext — PR:ens titel är identisk med ärendets, `change_summary` på
  doc-001 nämner mail och möte. Sådant går att plocka ut med mönstermatchning
  utan LLM.
