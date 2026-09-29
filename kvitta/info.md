Teamet bygger Kvitta, en app för utläggshantering som säljs till företag.

Vad mjukvaran gör: anställda fotar sina kvitton med mobilen, systemet läser av belopp, moms och datum automatiskt, chefen godkänner, och allt skickas vidare till företagets bokföringssystem. Det ersätter papperskvitton och Excel-listor.

Delarna som teamet bygger:

Mobilappen: fota kvitton, skapa utlägg, se status. Den måste fungera offline, till exempel på resa.

Kvittotolkningen: tjänsten som läser bilden och tar fram belopp, moms, datum och butik.

Attestflödet: regler för vem som godkänner vad, beloppsgränser och påminnelser.

Webbportalen: där chefer attesterar och ekonomiavdelningen administrerar.

Integrationer: export till bokföringssystem (till exempel Fortnox och Visma) och utbetalning via lön.

Läget i scenariot: företaget är ett litet mjukvarubolag. Kvitta har en pilotkund och ska släppa version 1.0 till betalande kunder under perioden. Då finns ett tydligt mål med deadline, och allt som går snett syns.

Varför den passar: alla förstår vad den gör. Den har naturliga delar som blir komponenter och repon. Den har verkliga regler (moms, bokföringslagen, GDPR) som ger krav som ändras. Den har externa beroenden som kan krångla (bokföringssystemens API:er). Och den har tydliga konsekvenser när något går fel: fel belopp, dubbla utbetalningar, arga kunder.