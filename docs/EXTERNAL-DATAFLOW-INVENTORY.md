# Joost — Externe gegevensstromen

**Status:** repository-inventarisatie, geen providercontracten of productie-instellingen gecontroleerd.

Deze lijst beschrijft wat uit de code aantoonbaar is. Een provider in deze lijst
betekent niet automatisch dat hij in productie actief is.

## Categorieën

| Categorie | Voorbeelden uit de repository | Mogelijke doorgestuurde gegevens | Belangrijk aandachtspunt |
|---|---|---|---|
| Publieke overheids- en open bronnen | RDW, PDOK, politie.nl, openKVK/Overheid.io, crt.sh, CertSpotter | Zoektermen, kentekens, adressen, bedrijfsgegevens, domeinen | Per bron voorwaarden, logging en bewaartermijn vastleggen |
| Zoekmachines en sociale platforms | Google, Bing, DuckDuckGo, LinkedIn, Facebook, Instagram, Reddit, TikTok, YouTube, X | Namen, e-mailadressen, gebruikersnamen, zoekvragen | Query kan direct naar externe partij herleidbaar zijn |
| Betaalde/API-providers | Brave, RapidAPI-providers, Hunter, EmailRep, HIBP, Picarta, 2Chat, MarinePlan, Equasis | Identifiers, e-mail, telefoonnummer, foto, locatie, scheepsgegevens | DPA, kosten, providerretentie en toegangsbeheer controleren |
| Lokale diensten | SpiderFoot, Ollama, WhatsApp-service | Onderzoeksquery of foto binnen eigen infrastructuur | Netwerkgrenzen en credentials blijven tenant-/beheerdergebonden |
| AI-diensten | OpenRouter en optioneel Ollama | Afhankelijk van actie: tekst, onderzoekscontext of afbeelding | Expliciet beleid nodig voor persoonsgegevens en modelretentie |
| Telemetry/licensing | license.iveras.com en api.ipify.org | Install-ID, hostname, IP, OS/kernel, hardware, versie | Huidige velden zijn voorlopig toegestaan; bewaren tot vervanging. Providerretentie en rechtsgrond nog verifiëren |
| Webhooks/notifications | Configureerbare webhook-URL’s, Telegram, e-mail/SMS | Gebeurtenissen, meldingen en mogelijk onderzoeksmetadata | Per tenant doel, toestemming, secret en dataminimalisatie vastleggen |
| Health checks | RDW, PDOK, HIBP, Overheid.io en andere bereikbaarheidstests | Vaste testquery’s; meestal geen tenantdata, maar wel externe netwerkmetadata | Alleen veilige synthetische queries gebruiken; health checks mogen geen identifiers uit cases meenemen |
| Foto-/beeldanalyse | Google Lens, Yandex, Bing Images, TinEye en kaartlinks | Afhankelijk van de actie: publieke afbeeldings-URL, locatie of beeldcontext | Expliciete gebruikersactie, providerretentie en URL-toegang controleren |

## Wat nog niet uit de repository volgt

- Welke providers daadwerkelijk in de productieomgeving zijn geconfigureerd.
- Welke contracten, DPA’s, regio’s en providerretenties gelden.
- Welke gebruikersrollen externe acties mogen starten.
- Of alle gevoelige query’s vooraf worden geminimaliseerd of gemaskeerd.
- Welke externe resultaten permanent worden opgeslagen en hoe lang.
- Of health checks uitsluitend vaste synthetische input gebruiken in alle
  omgevingen.
- Of afbeeldings-URL’s naar externe beeldzoekdiensten mogen worden doorgestuurd
  en onder welke gebruikersbevestiging.

## Aanbevolen vervolgstap

Gebruik vóór publieke/professionele uitrol het
[`PROVIDER-REGISTER-TEMPLATE.md`](PROVIDER-REGISTER-TEMPLATE.md) als
providerregister met per provider:
doel, invoervelden, persoonsgegevens, rechtsgrond/toestemming, regio, retentie,
kostentype, credential-eigenaar, logging, tenant-scope en verwijderprocedure.

Voor OpenRouter geldt aanvullend het aparte beleid in
[`AI-OPENROUTER-PRIVACY.md`](AI-OPENROUTER-PRIVACY.md). Een AI-verzoek mag pas
als privacy- en providerinstellingen aantoonbaar passen bij de gegevens die de
onderzoeker expliciet heeft geselecteerd.

## Voorlopige Joost-regels

- Publieke technische gegevens mogen naar een provider worden gestuurd als de
  provider voor die taak is goedgekeurd.
- Zakelijke identifiers vereisen geconfigureerde provider-goedkeuring.
- Gevoelige persoonsgegevens — zoals privételefoonnummers, gezichten en
  privé-adressen — mogen alleen naar expliciet goedgekeurde providers.
- De lokale WhatsApp-service blijft uitgeschakeld voor productie totdat die
  expliciet wordt geactiveerd. De eerste productfase gebruikt per tenant een
  apart account en uitsluitend handmatige verzending.
- Onderzoeksdata mag niet automatisch in WhatsApp-berichten worden geplaatst.
