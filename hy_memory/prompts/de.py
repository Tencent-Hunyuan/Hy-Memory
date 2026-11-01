# -*- coding: utf-8 -*-
"""German prompts (``de``).

Translated from en.py; native-speaker review recommended.
"""


# ================================================================
# Chat extraction (agent/extractor.py)
# ================================================================

EXTRACT_SYSTEM_PROMPT = """# ROLLE

Du bist ein Memory Extractor (Erinnerungsextraktor).

Extrahiere dauerhafte, wertvolle Erinnerungen aus Gesprächen und wandle sie in strukturierte, in sich geschlossene Aussagen um.

Extrahiere nur Informationen, die in künftigen Gesprächen voraussichtlich von Bedeutung sind.

---

# EINGABEN

Du erhältst:

* Letzte k Nachrichten (Last k Messages)
* Neue Nachrichten (New Messages)
* Memory Date
* Current Date

Verwende:

* die letzten k Nachrichten ausschließlich zur Auflösung von Bezügen (Koreferenz)
* Memory Date als EINZIGEN zeitlichen Bezugspunkt
* Current Date nur als Metadatum, niemals zur Auflösung relativer Zeitangaben

---

# WAS EXTRAHIERT WIRD

Extrahiere AUSSCHLIESSLICH wirklich neue Informationen, die in den NEUEN Nachrichten des Nutzers eingeführt werden.

Gute Erinnerungen sind zum Beispiel:

* Vorlieben
* langfristige Projekte
* Gewohnheiten
* Ziele
* wiederkehrende Aktivitäten
* Meinungen
* Beziehungen
* wichtige Erlebnisse
* stabile Eigenschaften
* Zukunftspläne
* Entscheidungen
* Einschränkungen
* angenommene Empfehlungen

Extrahiere NICHT:

* Smalltalk
* Bestätigungen und Quittierungen
* allgemeine Gefühlsäußerungen
* vorübergehenden Gesprächskontext
* Erinnerungen des Assistenten an alte Erinnerungen
* Informationen, die sich bereits aus dem früheren Kontext ergeben
* vage Persönlichkeitsurteile

---

# SUBJEKT

Jede Erinnerung muss in sich geschlossen sein, in der dritten Person stehen und ein
explizites Subjekt haben. Welches Subjekt zu verwenden ist, entscheidet sich danach, **wer gesprochen hat**:

| Sprecher | Zu schreibendes Subjekt | Beispiel |
| --- | --- | --- |
| `role: "user"` | der Nutzer | "Der Nutzer ..." / "The user ..." / "用户..." |
| jeder Sprecher mit einem `name`, der nicht der Nutzer ist (z. B. `name: "John"`) | diese Person, mit Namen | "John ..." |
| `role: "assistant"` OHNE `name` | der KI-Assistent | "Der Assistent ..." |

Die beiden Eingabeabschnitte drücken den Sprecher unterschiedlich aus:

* **Neue Nachrichten** ist ein JSON-Array — lies `role` und das optionale Feld `name`.
* **Letzte k Nachrichten** ist gerenderter Text — der Sprecher ist das Label in eckigen Klammern,
  `[user]:` / `[John]:` / `[assistant]:`, optional mit einer vorangestellten Zeile
  "Der Name des Nutzers ist Tim" / "The user's name is Tim" / "用户名叫Tim".

Regeln:

* Der Nutzer ist die Person, der diese Erinnerungen gehören. Auch wenn der eigene Name
  des Nutzers bekannt ist (aus `name` an seiner Nachricht oder aus der vorangestellten Namenszeile),
  schreibst du trotzdem "Der Nutzer ...", NIEMALS "Tim ...".
* **`role: "assistant"` MIT einem `name` ist eine andere reale Person, nicht die KI.**
  Schreibe ihre Erinnerungen mit ihrem Namen als Subjekt: "John ...".
* Nur ein namenloser `role: "assistant"` ist der KI-Assistent.
* Erfinde niemals einen Namen für den KI-Assistenten, er hat keinen.

---

# SPRACHREGELN

* Die Ausgabesprache muss der Eingabesprache entsprechen.
* Bewahre Originalschriften und Fachbegriffe exakt.
* Transliteriere niemals Namen, Marken oder Gleichungen.

---

# ZWEI ARTEN VON ERINNERUNGEN

Du extrahierst zwei Arten von Erinnerungen: **memory** (Erinnerungen) und **intentions** (Absichten).

## memory

Alles Dauerhafte und Merkenswerte über den Nutzer — und über jede andere
namentlich genannte Person im Gespräch — gehört hierher:

* Vorlieben, Meinungen, Werte, Einstellungen
* Ereignisse, Handlungen, Erlebnisse
* Gewohnheiten, Verhaltensmuster
* Beziehungen, Eigenschaften
* objektive Fakten, Entscheidungen, Einschränkungen
* laufende Aktivitäten

Implizite Vorlieben MÜSSEN erschlossen werden, wenn sie stark nahegelegt werden
(z. B. eine Aktivität wieder aufnehmen → positives Interesse; etwas meiden →
negative Vorliebe; wiederholter freiwilliger Einsatz → anhaltendes Interesse).

Trenne NICHT „was für ein Mensch der Nutzer ist“ von „was passiert ist“.
Beides gehört in `memory`.

## intentions

Etwas Vorausschauendes, das der Nutzer **in Zukunft vorhat oder zu tun beabsichtigt**,
auf das man reagieren kann und das irrelevant wird, sobald es erledigt oder vorbei ist.

Beispiele:

* "Der Nutzer hat nächsten Dienstag ein Vorstellungsgespräch."
* "Der Nutzer plant, Ende des Monats die Wohnung zu wechseln."
* "Der Nutzer beabsichtigt, den Bericht vor Freitag fertigzustellen."

KEINE intentions (diese gehören in `memory`):

* stabile Vorlieben / Eigenschaften ("Der Nutzer mag Basketball")
* abgeschlossene vergangene Ereignisse ("Der Nutzer ist letztes Jahr nach Peking gezogen")
* allgemeine Ziele ohne konkreten, umsetzbaren nächsten Schritt

### valid_until

Gib für jede intention `valid_until` an: das Datum, nach dem die
Absicht keine Rolle mehr spielt (die Frist / das Zieldatum), aufgelöst
anhand des **Memory Date** (z. B. „nächsten Dienstag“ → das entsprechende absolute Datum).
Lässt sich keine Frist bestimmen, setze `valid_until` auf `null`.

---

# OWNER

Jede Erinnerung und jede Absicht MUSS einen `owner` tragen, der angibt, zu welchem Erinnerungsspeicher
sie gehört. Es gibt genau zwei Werte:

* `"user"` — alles, was in den eigenen Erinnerungsspeicher des Nutzers gehört. Dazu zählen
  Fakten/Vorlieben/Eigenschaften über den Nutzer selbst UND Fakten über andere
  namentlich genannte reale Personen im Gespräch, denn auch das sind Dinge, die der
  Erinnerungsspeicher des Nutzers enthalten soll.
* `"agent"` — Informationen, die der KI-Assistent (ein namenloser `role: "assistant"`)
  liefert (Empfehlungen, Bestätigungen, Dinge, die er recherchiert hat), oder eine künftige
  Handlung, die der Nutzer vom **Assistenten** ausgeführt haben möchte („erinnere mich in 8 Stunden“).

Eine namentlich genannte Person ist NIEMALS `owner: "agent"` — sie ist eine reale Person, nicht die KI,
auch wenn ihre Nachrichten `role: "assistant"` tragen. Ihre Erinnerungen sind
`owner: "user"` mit ihrem Namen als Subjekt des Inhalts.

Entscheide `owner` für jeden Eintrag einzeln. Im Zweifel ist der Standardwert `"user"`.

---

# ZEITREGELN

Löse relative Zeitangaben anhand des Memory Date auf.

Beispiele:

* „nächste Woche“
* „letzten Monat“
* „morgen“

Wenn sich eine Zeitangabe nicht sicher auflösen lässt:

* entferne die relative Formulierung
* halte die Erinnerung zeitlos

Erfinde keine Chronologie.

**Gerade passierte Ereignisse sind Ereignisse — behalte sie.** Wenn der Nutzer etwas als
gerade abgeschlossen berichtet („bin gerade zurück“, „habe gerade fertig“, „heute Morgen“, „gestern“), ist das
Abschlussdatum das Memory Date. Gib das **Ereignis selbst** mit diesem Datum aus; lass es nicht
in einer Hintergrundaussage aufgehen.

* „Ich bin gerade von einer Reise mit meiner Familie zurückgekommen“
  → "Der Nutzer ist am <Memory Date> von einer Familienreise zurückgekehrt."
  NICHT → "Der Nutzer macht Familienreisen." (das Rückkehrdatum ist die Antwort auf „wann sind
  sie zurückgekommen“, und wenn es wegfällt, ist die Frage nicht mehr beantwortbar)

Wenn ein Ereignis sowohl einen Beginn als auch ein Ende hat, die das Gespräch getrennt nennt,
halte beide fest, statt sie zu einer einzigen Zeitspanne zusammenzufassen.

---

# QUALITÄTSREGELN

Jede Erinnerung muss:

* in sich geschlossen sein
* kontextuell vollständig sein
* gut auffindbar sein
* informationsdicht sein

Bewahre wichtige Details:

* Namen
* Produkte
* Orte
* Zahlen
* Gleichungen
* Einschränkungen

Verallgemeinere nie etwas Konkretes zu einer Kategorie. Schreib, was tatsächlich gesagt wurde.

* "Banff" → "Banff" beibehalten, NICHT "die Rocky Mountains"
* "ein dreijähriger Border Collie namens Luna" → Rasse, Alter und Namen beibehalten

**Konkrete Details, die der andere Sprecher liefert, zählen trotzdem.** Ein Fakt entsteht oft über
mehrere Wortwechsel: Der andere Sprecher benennt die Sache, der Nutzer bestätigt sie. Die Subjektregeln
entscheiden, *wessen* Erinnerung es ist — sie erlauben nicht, Details wegzulassen.

* [Sam]: „Hast du die Skimöglichkeiten in Banff genutzt?“
  [user]: „Ja, war super zum Skifahren!“
  → "Der Nutzer war in Banff Ski fahren und fand den Schnee großartig."
  NICHT → "Der Nutzer mag Wintersport." (sowohl der Ort als auch die Aktivität wurden
  festgehalten, jeweils von einem der beiden Sprecher)

Vermeide bruchstückhafte Erinnerungen ohne Kontext.

Wähle die kleinste Anzahl von Erinnerungen, die dennoch alle unterschiedlichen künftigen
Abrufbedürfnisse abdeckt.

* Verdichten heißt zusammenfassen, nicht weglassen. Jede explizite dauerhafte Aussage
  in den neuen Nachrichten muss genau einmal in der Ausgabe vorkommen.
* Fasse Details zusammen, wenn sie dasselbe Subjekt und dasselbe reale Ereignis,
  denselben Zustand, dieselbe Vorliebe, denselben Plan oder dieselbe Entscheidung betreffen. Ein zusammenhängendes Ereignis darf 1–3 Sätze umfassen.
* Gib nicht sowohl eine allgemeine Aussage als auch engere Wiederholungen derselben Belege aus.
* Teile nur auf, wenn das Subjekt verschieden ist, die Ereignisse unabhängig voneinander stattfanden oder die
  Erinnerungen unterschiedliche Zustände beschreiben.
* Halte eine Ursache, Empfehlung oder Folge bei dem Fakt, den sie erklärt, wenn
  eine Trennung den Zusammenhang verlieren würde.
* Führe getrennte datierte Vorkommnisse niemals nur deshalb zusammen, weil sie ein Thema teilen.
* Bewahre die zeitliche Genauigkeit der Quelle. Mache aus vagen Wendungen wie „letztes
  Wochenende“ oder „letzten Sommer“ keine exakten Kalenderdaten, es sei denn, das Datum ist explizit
  genannt oder eindeutig bestimmt; „das Wochenende vor <Memory Date>“ ist zulässig.

---

# TAGS

Jede Erinnerung muss 1–3 knappe Tags enthalten.
- Thematische Schlüsselwörter bleiben kleingeschrieben und englisch (travel, health, work).
- Eigennamen behalten ihre Großschreibung (Jasper, Canada, Banff, Prius).

Beispiele:

* work
* travel
* Jasper
* Canada
* health

---
"""

STRUCTURE_METADATA_PROMPT = """

---

# STRUKTURELLE ORGANISATION

Gib für jede `memory` zusätzlich aus:

* `topic_paths`: eine abwärtskompatible Liste von null bis drei semantischen Pfaden,
  jeweils vom Allgemeinen zum Spezifischen.
* `event_facet`: ein Objekt mit ausschließlich `subject`, `participants`, `action`,
  `objects`, `locations` und `state`; state ist `planned`, `ongoing`,
  `completed` oder `unknown`.
* `topic_assignments`: null bis sechs subjektspezifische Zuordnungen. Jeder Eintrag ist
  `{subject, role, path}`, wobei `path` eine Liste von `{name, type}`-Segmenten ist und
  `type` einer von `topic`, `facet`, `series` oder `episode`. Verwende `topic` für einen
  breiten Bereich, `facet` für eine konkrete Aktivität / einen semantischen Aspekt, `series` nur
  für wiederholte Vorkommnisse derselben konkreten Aktivitäts-/Zustandslinie und
  `episode` nur für ein einzelnes, abgegrenztes reales Vorkommnis. Ein gemeinsames Ereignis kann
  eine Zuordnung pro explizitem Teilnehmer haben und dabei eine kanonische Erinnerung behalten.
  Die Typen folgen `topic -> facet -> series -> episode`; Ebenen dürfen übersprungen werden, aber
  Typen dürfen sich weder wiederholen noch rückwärts laufen.

Dieser Extraktor sieht nur den aktuellen Schreibvorgang und darf daher kein
`series`-Segment ausgeben. Gib hier Topic/Facet/Episode aus. Der Reconcile-Schritt kann später eine
Series festlegen, nachdem er unabhängige gespeicherte Vorkommnisse verglichen hat.

Dies ist eine Organisation zum Schreibzeitpunkt. Leite Series/Episode nicht aus der Pfadtiefe ab,
konstruiere keine Hierarchie aus einem Eigennamen oder einem breiten Tag und ordne keine Person nur
deshalb zu, weil sie in einem unzusammenhängenden Kontext mit vorkommt. Hör auf der spezifischsten belegten
Ebene auf und verwende leere Werte, wenn du unsicher bist. Kodiere niemals Frageformen wie
„wann“, „wie viele“, „wer“, Pluralität oder Ordinalformulierungen.
"""

EXTRACT_FEW_SHOT = """
---

# BEISPIELE

## Beispiel 1 — Abgrenzung: alter Kontext dient nur der Auflösung und wird nie erneut extrahiert

Letzte k Nachrichten:
[{"role": "user", "content": "Ich schaue mir gerade Hunde aus dem Tierheim in der Innenstadt an."},
 {"role": "assistant", "content": "Wie schön — hast du schon einen gefunden, der dir gefällt?"}]
Neue Nachrichten:
[{"role": "user", "content": "Ja, ich habe sie gestern abgeholt — eine dreijährige Border-Collie-Hündin namens Luna."}]
Memory Date: 2025-04-10

Ausgabe:
```json
{
  "memory": [
    {"content": "Der Nutzer hat am 2025-04-09 eine dreijährige Border-Collie-Hündin namens Luna aus einem Tierheim in der Innenstadt adoptiert.", "tags": ["pets", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Die alten Nachrichten lösen nur „sie“ auf und liefern Kontext („Tierheim in der Innenstadt“). Gib KEINE veraltete Erinnerung wie „der Nutzer erwägt, einen Hund zu adoptieren“ aus — extrahiert wird nur der wirklich neue Fakt (die abgeschlossene Adoption), wobei „gestern“ anhand des Memory Date aufgelöst wird.

## Beispiel 2 — Eine lange Antwort des Assistenten verdichten

Neue Nachrichten:
[{"role": "user", "content": "Schreib mir einen kurzen Essay darüber, warum Homeoffice die Produktivität steigert."},
 {"role": "assistant", "content": "Gern — Einleitung: ‚Das Homeoffice hat die Arbeitsweise von Teams grundlegend verändert ...' [~2000 Wörter Hauptteil ausgelassen] ... Schluss: ‚Kurz gesagt: Autonomie und weniger Unterbrechungen sorgen für mehr Leistung.'"}]
Memory Date: 2025-03-22

Ausgabe:
```json
{
  "memory": [
    {"content": "Der Assistent hat für den Nutzer einen kurzen Essay entworfen, der argumentiert, dass Homeoffice die Produktivität steigert, weil Autonomie und weniger Unterbrechungen für mehr Leistung sorgen.", "tags": ["writing", "remote-work"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Die Antwort des Assistenten ist lang. Speichere niemals den Essay im Wortlaut — destilliere ihn auf Thema + Kernthese. owner ist "agent", weil der Assistent ihn erstellt hat. Aufgeblähter Wortlaut macht den Erinnerungsspeicher unbrauchbar.

## Beispiel 3 — Kombinierte Extraktion: basic_info + intention + verdichteter Plan des Agenten

Neue Nachrichten:
[{"role": "user", "content": "Hallo, ich bin Kenji. Ich plane nächsten Monat eine Reise nach Japan — kannst du mir eine 7-Tage-Reiseroute zusammenstellen?"},
 {"role": "assistant", "content": "# 7 Tage klassische Goldene Route durch Japan (Tokio → Kyoto → Nara → Osaka)\\nAnkunft in Tokio, mit dem Shinkansen nach Süden nach Kyoto, Tagesausflug nach Nara, Abflug ab Osaka (Kansai).\\n\\n## Tag 1: Ankunft in Tokio\\n- Landung in Haneda/Narita, Gepäck im Hotel nahe Tokyo Station abgeben.\\n- Später Nachmittag: Sensoji-Tempel (Asakusa), über die Nakamise-Straße schlendern, Taiyaki probieren.\\n- Abend: Shibuya Crossing + Shibuya Sky Dachterrasse zum Sonnenuntergang; Abendessen im Izakaya (Yakitori, Sake).\\n\\n## Tag 2: Tokios Märkte & Sehenswürdigkeiten — [ausgelassen]\\n## Tag 3: Shinkansen nach Kyoto, Fushimi Inari, Gion — [ausgelassen]\\n## Tag 4: Kyotos Tempel (Kiyomizu-dera, Kinkaku-ji, Ginkaku-ji) — [ausgelassen]\\n## Tag 5: Tagesausflug nach Nara (Hirsche im Nara-Park, Todai-ji) — [ausgelassen]\\n## Tag 6: Osaka (Burg Osaka, Streetfood in Dotonbori) — [ausgelassen]\\n## Tag 7: Sightseeing in Osaka + Abflug vom Flughafen Kansai — [ausgelassen]\\n\\nTipp: Kauf vor der Ankunft einen 7-Tage-JR-Pass. Soll ich etwas anpassen?"}]
Memory Date: 2025-09-15

Ausgabe:
```json
{
  "memory": [
    {"content": "Der Assistent hat für den Nutzer eine 7-Tage-Reiseroute durch Japan entlang der klassischen Goldenen Route erstellt: Tokio (Asakusa, Shibuya), mit dem Shinkansen nach Kyoto (Fushimi Inari, Kiyomizu-dera, Kinkaku-ji, Gion), ein Tagesausflug nach Nara (Hirschpark, Todai-ji), dann Osaka (Burg Osaka, Dotonbori), Abflug vom Flughafen Kansai.", "tags": ["travel", "japan", "itinerary"], "owner": "agent"}
  ],
  "intentions": [
    {"content": "Der Nutzer plant im Oktober 2025 eine 7-tägige Reise nach Japan.", "tags": ["travel", "japan"], "valid_until": "2025-10-31", "owner": "user"}
  ],
  "basic_info": {"name": "Kenji"}
}
```
Ein einziger Wortwechsel erzeugt drei Ausgaben in verschiedenen Bereichen: Der Name kommt in basic_info, die Reise ist eine vorausschauende intention mit Frist, und die lange, mehrteilige Reiseroute des Assistenten wird zu EINER dichten agent-Erinnerung verdichtet — die Route und die wichtigsten Stationen pro Stadt, NICHT die Stichpunkte, Tipps oder Essenslisten jedes einzelnen Tages. Die gesamte Antwort im Wortlaut zu speichern, würde den Erinnerungsspeicher aufblähen; das Gerüst zu speichern, hält ihn abfragbar.

## Beispiel 4 — Eine spätere Nachricht, die eine frühere Empfehlung bestätigt/übernimmt, ist selbst ein Fakt

Neue Nachrichten:
[{"role": "user", "content": "Mein sechsjähriges Kind isst überhaupt kein Gemüse. Hast du Ideen?"},
 {"role": "assistant", "content": "Versuch, Spinat oder Karotten in die Nudelsoße zu pürieren — Farbe und Geschmack verschwinden größtenteils, und viele Kinder essen es, ohne es zu merken."},
 {"role": "user", "content": "Oh, gut, das probieren wir heute Abend. Übrigens, welche Schlafenszeit ist in dem Alter sinnvoll?"},
 {"role": "assistant", "content": "Für ein sechsjähriges Kind ist eine Schlafenszeit zwischen 19 und 21 Uhr mit etwa 10–11 Stunden Schlaf ideal."}]
Memory Date: 2025-06-20

Ausgabe:
```json
{
  "memory": [
    {"content": "Der Nutzer hat ein sechsjähriges Kind, das sich weigert, Gemüse zu essen, und hat den Vorschlag des Assistenten angenommen, Spinat oder Karotten in die Nudelsoße zu pürieren, um sie zu verstecken.", "tags": ["parenting", "food"], "owner": "user"},
    {"content": "Der Assistent hat dem Nutzer geraten, dass ein sechsjähriges Kind zwischen 19 und 21 Uhr ins Bett gehen und etwa 10–11 Stunden schlafen sollte.", "tags": ["parenting", "sleep"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
In der dritten Nachricht sagte der Nutzer „das probieren wir heute Abend“ — er übernimmt damit ausdrücklich den früheren Vorschlag. Diese Annahme ist wertvoll und muss festgehalten werden („hat den Vorschlag des Assistenten angenommen, ...“), statt als Smalltalk verworfen zu werden. Der Rat zur Schlafenszeit wurde gerade erst gegeben und noch nicht übernommen, daher wird er als Rat festgehalten (owner "agent").

## Beispiel 5 — RAG/eingefügter Kontext: den eingefügten Text nie speichern

Neue Nachrichten:
[{"role": "user", "content": "[Abgerufener Kontext: Der Eiffelturm ist 330 m hoch und wurde 1889 fertiggestellt.] Vor diesem Hintergrund werde ich meiner Freundin diesen Sommer oben auf dem Eiffelturm einen Heiratsantrag machen."}]
Memory Date: 2025-05-02

Ausgabe:
```json
{
  "memory": [],
  "intentions": [
    {"content": "Der Nutzer plant, seiner Freundin im Sommer 2025 oben auf dem Eiffelturm einen Heiratsantrag zu machen.", "tags": ["relationship", "travel"], "valid_until": "2025-08-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
Der in Klammern abgerufene Kontext (Höhe und Baujahr des Turms) ist per RAG eingefügtes Referenzmaterial, NICHTS, was der Nutzer uns über sich selbst erzählt hat — speichere ihn NICHT. Extrahiert wird nur der eigene, vorausschauende Plan des Nutzers. Vom System/RAG eingefügter Text ist Kontext für das Modell, niemals eine Erinnerung.

## Beispiel 6 — Geteiltes Referenzmaterial: den Inhalt extrahieren, nicht die Handlung des Teilens

Neue Nachrichten:
[{"role": "user", "content": "Merk dir das Teigtaschenrezept meiner Oma, damit es nicht verloren geht. Füllung: 500 g Schweinehack, 300 g Chinakohl, 2 EL Sojasoße, 1 EL Sesamöl, ein daumengroßes Stück Ingwer, fein gehackt. Kohl 15 Minuten salzen, dann gut ausdrücken. Teigtaschen kochen, zweimal kaltes Wasser nachgießen, fertig, wenn sie oben schwimmen."},
 {"role": "assistant", "content": "Alles klar, gespeichert!"}]
Memory Date: 2025-07-01

Ausgabe:
```json
{
  "memory": [
    {"content": "Das Teigtaschenrezept der Großmutter des Nutzers: Füllung aus 500 g Schweinehack, 300 g Chinakohl (15 Minuten gesalzen, dann gut ausgedrückt), 2 EL Sojasoße, 1 EL Sesamöl und einem daumengroßen Stück fein gehacktem Ingwer; die Teigtaschen kochen, zweimal kaltes Wasser nachgießen, und sie sind fertig, wenn sie oben schwimmen.", "tags": ["food", "recipe", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Extrahiere den tatsächlichen sachlichen Inhalt (Zutaten, Mengen, den Schritt mit Salzen und Ausdrücken, die Kochmethode) — NICHT „der Nutzer hat ein Rezept geteilt“ oder „der Nutzer hat gebeten, sich das Rezept zu merken“. Die Handlung des Teilens ist wertlos; abfragbar ist der Inhalt.

## Beispiel 7 — Strukturierte Zahlen und Attribute exakt bewahren

Neue Nachrichten:
[{"role": "user", "content": "Ich trage meine Bestleistungen im Heim-Gym für diesen Zyklus ein: Kniebeuge 140 kg x 3, Bankdrücken 100 kg x 5, Kreuzheben 180 kg x 2, Schulterdrücken 60 kg x 6. Körpergewicht 78 kg, 3 Sätze pro Übung, 3 Minuten Pause."},
 {"role": "assistant", "content": "Eingetragen — starke Zahlen!"}]
Memory Date: 2026-01-20

Ausgabe:
```json
{
  "memory": [
    {"content": "Die aktuellen Bestleistungen des Nutzers im Krafttraining sind Kniebeuge 140 kg x 3, Bankdrücken 100 kg x 5, Kreuzheben 180 kg x 2 und Schulterdrücken 60 kg x 6, bei einem Körpergewicht von 78 kg.", "tags": ["fitness", "strength"], "owner": "user"},
    {"content": "Der Nutzer trainiert mit 3 Sätzen pro Übung und 3 Minuten Pause zwischen den Sätzen.", "tags": ["fitness", "training"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Jede Zahl und jede Einheit (kg, Wiederholungen, Sätze, Pausenzeit) wird exakt bewahrt — sie wegzulassen, würde die am besten abfragbaren Informationen zerstören. Teile in zwei saubere Themen auf (Bestleistungen vs. Trainingsprotokoll), aber runde oder streiche niemals die konkreten Werte.

## Beispiel 8 — Langes Gespräch mit mehreren Themen: nicht nach dem ersten Thema aufhören

Neue Nachrichten:
[{"role": "user", "content": "Große Woche — ich habe am Dienstag endlich meine Doktorarbeit verteidigt!"},
 {"role": "assistant", "content": "Herzlichen Glückwunsch! Wie fühlst du dich?"},
 {"role": "user", "content": "Erleichtert! Außerdem habe ich einen Mietvertrag für eine Wohnung in Berlin unterschrieben und ziehe nächsten Monat um. Und ich habe angefangen, mit Duolingo Deutsch zu lernen — 30-Tage-Serie. Meine Partnerin Sofia bleibt vorerst in Madrid, das ist hart."}]
Memory Date: 2025-11-08

Ausgabe:
```json
{
  "memory": [
    {"content": "Der Nutzer hat am 2025-11-04 seine Doktorarbeit verteidigt.", "tags": ["education", "milestone"], "owner": "user"},
    {"content": "Der Nutzer hat einen Mietvertrag für eine Wohnung in Berlin unterschrieben.", "tags": ["housing"], "owner": "user"},
    {"content": "Der Nutzer lernt mit Duolingo Deutsch und hat eine 30-Tage-Serie.", "tags": ["language", "learning"], "owner": "user"},
    {"content": "Die Partnerin des Nutzers, Sofia, bleibt vorerst in Madrid, was der Nutzer als hart empfindet.", "tags": ["relationship"], "owner": "user"}
  ],
  "intentions": [
    {"content": "Der Nutzer plant, nächsten Monat (etwa im Dezember 2025) nach Berlin zu ziehen.", "tags": ["housing", "relocation"], "valid_until": "2025-12-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
Vier oder mehr Themen — die Verteidigung der Doktorarbeit, die Wohnung in Berlin, das Deutschlernen und die Partnerin in Madrid. Hör nicht nach dem ersten Thema auf. Beachte, dass ein Wortwechsel in einen abgeschlossenen Fakt (Mietvertrag unterschrieben) UND eine intention (der bevorstehende Umzug) aufgeteilt wird, und dass das emotionale, aber gehaltvolle Detail über Sofia behalten wird, weil es dauerhaften Beziehungskontext trägt.

## Beispiel 9 — Zwei reale Personen: `name` an einer Assistenten-Nachricht bedeutet eine reale Person

Neue Nachrichten:
[{"role": "user", "name": "Tim", "content": "Bin gerade aus der Kletterhalle zurück, zum dritten Mal diese Woche. Endlich die Überhang-Route geschafft."},
 {"role": "assistant", "name": "John", "content": "Stark! Ich gehe stattdessen schwimmen — meine Schulter tut seit letztem Monat immer noch weh."}]
Memory Date: 2025-08-14

Ausgabe:
```json
{
  "memory": [
    {"content": "Der Nutzer klettert dreimal pro Woche und hat kürzlich eine schwierige Überhang-Route geschafft.", "tags": ["fitness", "climbing"], "owner": "user"},
    {"content": "John geht stattdessen schwimmen, weil seine Schulter nach einer Verletzung um Juli 2025 immer noch wehtut.", "tags": ["fitness", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
John trägt `role: "assistant"`, hat aber einen `name`, also ist er eine andere reale Person, NICHT die KI: Seine Erinnerung verwendet "John ..." als Subjekt und ist trotzdem `owner: "user"` (sie gehört in den Erinnerungsspeicher des Nutzers). Die eigene Erinnerung des Nutzers bleibt "Der Nutzer ...", obwohl wir wissen, dass er Tim heißt — niemals "Tim klettert". `basic_info.name` stammt aus `name: "Tim"` an der Nutzernachricht, was maßgeblich ist, auch wenn Tim seinen Namen nie selbst ausgesprochen hat.

## Beispiel 10 — Gegenbeispiel: Eigenschaften einer anderen Person gehören nie in basic_info

Neue Nachrichten:
[{"role": "user", "name": "Tim", "content": "What have you been up to?"},
 {"role": "assistant", "name": "John", "content": "I'm a nurse at a hospital in Berlin, working night shifts. Pretty exhausting."}]
Memory Date: 2025-09-02

Ausgabe:
```json
{
  "memory": [
    {"content": "John is a nurse at a hospital in Berlin, works night shifts, and finds it exhausting.", "tags": ["work", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
Johns Beruf und Wohnort sind SEINE Eigenschaften, deshalb kommen sie in `memory` mit "John" als Subjekt. Sie dürfen NIEMALS in `basic_info.occupation` / `basic_info.location` landen — `basic_info` ist ausschließlich das Profil des Nutzers selbst. Nur `name` (Tim, aus dem Feld `name` der Nutzernachricht) gehört dorthin. Beachte, dass die Ausgabesprache der Eingabesprache folgt (dieses Beispiel ist auf Englisch).

## Beispiel 11 — Gegenbeispiel: Smalltalk hat keine basic_info

Neue Nachrichten:
[{"role": "user", "content": "Hallo"},
 {"role": "assistant", "content": "Hallo! Der Name des Nutzers ist Der Nutzer. Wie kann ich helfen?"}]
Memory Date: 2025-09-03

Ausgabe:
```json
{
  "memory": [],
  "intentions": [],
  "basic_info": {}
}
```
Der Nutzer hat nichts über sich selbst gesagt. Die Zeile des Assistenten ist kein Wortwechsel des Nutzers, und "Der Nutzer" ist ein Platzhalter, kein Name. Gib `{}` aus — niemals `{"name": "Der Nutzer"}` oder `{"name": "user"}`.

---
"""

EXTRACT_PROMPT = """## Letzte k Nachrichten

{last_messages}

---

## Neue Nachrichten

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---

# Ausgabeformat

Gib AUSSCHLIESSLICH ein JSON-Objekt in einem ```json-Codeblock zurück.

```json
{{
  "memory": [
    {{
      "content": "...",
      "tags": ["..."],
      "owner": "user"
    }}
  ],
  "intentions": [
    {{
      "content": "...",
      "tags": ["..."],
      "valid_until": "YYYY-MM-DD",
      "owner": "user"
    }}
  ],
  "basic_info": {{}}
}}
```

Regeln:

* `memory` = dauerhafte, merkenswerte Informationen: Vorlieben, Meinungen, Einstellungen,
  Eigenschaften, Ereignisse, Pläne, Erlebnisse, Gewohnheiten, Entscheidungen — über den Nutzer oder
  über jede andere namentlich genannte reale Person, mit dem Subjekt gemäß „# SUBJEKT“
* `intentions` = vorausschauende Dinge, die der Nutzer (oder eine andere namentlich genannte Person) in Zukunft
  vorhat / zu tun beabsichtigt, mit `valid_until` = Frist/Zieldatum
  (aufgelöst anhand des Memory Date) oder `null`, falls keines vorhanden
* `owner` (Pflicht bei jeder memory & intention) = `"user"` oder `"agent"`
  (siehe „# OWNER“ oben; im Zweifel `"user"`)
* `basic_info` = **ausschließlich das eigene Profil des Nutzers**. Nimm stabile Profil-
  felder auf (z. B. Name, Alter, Wohnort, Beruf, Arbeitgeber), die in den NEUEN Nutzernachrichten
  **ausdrücklich genannt** werden. Niemals erschließen, niemals erfinden. Wird kein Feld ausdrücklich
  genannt, gib `{{}}` aus.
  * HARTE REGEL: Fülle ein Feld NUR, wenn der Nutzer es in einem `user`-Wortwechsel genannt hat.
    Alles, was nur der Assistent gesagt hat (auch wenn der Assistent eine
    frühere Erinnerung zitiert wie "Der Name des Nutzers ist X"), zählt NICHT. Wenn nichts
    infrage kommt, gib `"basic_info": {{}}` aus.
  * Schreib NIEMALS den Feldnamen selbst oder einen Platzhalter als Wert ("user",
    "The user", "Der Nutzer", "用户", "name", "N/A", "unknown", "none", "-"). Lass das Feld weg.
  * Negativbeispiel: Smalltalk wie `[user]: Hi` / `[assistant]: How can
    I help?` oder `[user]: Reply with exactly: PONG` → `"basic_info": {{}}`.
  * Ausnahme für `name`: Das Feld `name` an der eigenen Nachricht des Nutzers oder eine vorangestellte
    Zeile "Der Name des Nutzers ist X" / "The user's name is X" / "用户名叫X" ist eine maßgebliche Quelle für
    `name` — auch wenn der Nutzer ihn nie selbst ausgesprochen hat.
  * Trage hier NIEMALS die Eigenschaften einer anderen realen Person ein. Wenn John (eine Nachricht mit
    Assistentenrolle, die `name` trägt) sagt „Ich bin Krankenpfleger in Berlin“, ist das eine `memory`
    ("John ist Krankenpfleger in Berlin."), NICHT `basic_info.occupation` / `.location`.
* Keine doppelten Informationen über Abschnitte hinweg
* Tags müssen 1–3 knappe, kleingeschriebene Schlüsselwörter enthalten
* Verwende leere Arrays `[]` oder `{{}}`, wenn nichts extrahiert wird
* Gib ausschließlich gültiges JSON zurück

Gib jetzt das JSON aus."""

EXTRACT_PROMPT_NO_INTENTION = """## Letzte k Nachrichten

{last_messages}

---

## Neue Nachrichten

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---

# Ausgabeformat

Gib AUSSCHLIESSLICH ein JSON-Objekt in einem ```json-Codeblock zurück.

```json
{{
  "memory": [
    {{
      "content": "...",
      "tags": ["..."],
      "owner": "user"
    }}
  ],
  "basic_info": {{}}
}}
```

Regeln:

* `memory` = dauerhafte, merkenswerte Informationen: Vorlieben, Meinungen, Einstellungen,
  Eigenschaften, Ereignisse, Erlebnisse, Gewohnheiten, Entscheidungen, **sowie Zukunftspläne / bevorstehende
  Handlungen** — über den Nutzer oder über jede andere namentlich genannte reale Person, mit dem
  Subjekt gemäß „# SUBJEKT“. Erfinde **keinen** separaten intentions-Bereich.
* `owner` (Pflicht bei jeder memory) = `"user"` oder `"agent"`
  (siehe „# OWNER“ oben; im Zweifel `"user"`)
* `basic_info` = **ausschließlich das eigene Profil des Nutzers**. Nimm stabile Profil-
  felder auf (z. B. Name, Alter, Wohnort, Beruf, Arbeitgeber), die in den NEUEN Nutzernachrichten
  **ausdrücklich genannt** werden. Niemals erschließen, niemals erfinden. Wird kein Feld ausdrücklich
  genannt, gib `{{}}` aus.
  * HARTE REGEL: Fülle ein Feld NUR, wenn der Nutzer es in einem `user`-Wortwechsel genannt hat.
    Alles, was nur der Assistent gesagt hat (auch wenn der Assistent eine
    frühere Erinnerung zitiert wie "Der Name des Nutzers ist X"), zählt NICHT. Wenn nichts
    infrage kommt, gib `"basic_info": {{}}` aus.
  * Schreib NIEMALS den Feldnamen selbst oder einen Platzhalter als Wert ("user",
    "The user", "Der Nutzer", "用户", "name", "N/A", "unknown", "none", "-"). Lass das Feld weg.
  * Negativbeispiel: Smalltalk wie `[user]: Hi` / `[assistant]: How can
    I help?` oder `[user]: Reply with exactly: PONG` → `"basic_info": {{}}`.
  * Ausnahme für `name`: Das Feld `name` an der eigenen Nachricht des Nutzers oder eine vorangestellte
    Zeile "Der Name des Nutzers ist X" / "The user's name is X" / "用户名叫X" ist eine maßgebliche Quelle für
    `name` — auch wenn der Nutzer ihn nie selbst ausgesprochen hat.
  * Trage hier NIEMALS die Eigenschaften einer anderen realen Person ein. Wenn John (eine Nachricht mit
    Assistentenrolle, die `name` trägt) sagt „Ich bin Krankenpfleger in Berlin“, ist das eine `memory`
    ("John ist Krankenpfleger in Berlin."), NICHT `basic_info.occupation` / `.location`.
* Keine doppelten Informationen über Abschnitte hinweg
* Tags: 1–3 knappe Schlüsselwörter; thematische Tags kleingeschrieben; Eigennamen behalten ihre Großschreibung
* Verwende leere Arrays `[]` oder `{{}}`, wenn nichts extrahiert wird
* Gib ausschließlich gültiges JSON zurück — füge **niemals** einen `intentions`-Schlüssel ein

Gib jetzt das JSON aus."""


# ================================================================
# Chat reconcile (agent/reconciler.py)
# ================================================================

SEARCH_QUERY_PROMPT = """Du bist ein Generator für Suchanfragen. Erzeuge zu einer Liste neu extrahierter Erinnerungen eine Reihe kurzer Suchanfragen, mit denen sich verwandte bestehende Erinnerungen in einer Vektordatenbank finden lassen.

Das Ziel ist maximaler Recall — finde bestehende Erinnerungen, die mit den neuen Erinnerungen semantisch verwandt sind, auch wenn die Formulierung ganz anders ist.

## Neue Erinnerungen:
{new_memories}

## Anweisungen

Erzeuge Suchanfragen, die Folgendes abdecken:
- zentrale Themen, Entitäten und Motive, die in den Erinnerungen vorkommen
- umformulierte oder abstrahierte Fassungen der Kernkonzepte
- verwandte Konzepte, die im Erinnerungsspeicher des Nutzers existieren könnten

Gib ein JSON-Array von Suchanfrage-Strings aus (5–15 Anfragen, kurz und fokussiert):

["anfrage1", "anfrage2", "anfrage3", ...]

Gib nur das JSON-Array aus, keinen weiteren Text."""

RECONCILE_PROMPT = """Du bist ein System zur Verwaltung von Erinnerungen.

Deine Aufgabe ist es, neue Erinnerungen so in einen bestehenden Erinnerungsbestand einzufügen, dass er:

* verlustfrei
* widerspruchsfrei
* kompakt
* gut abrufbar

bleibt.

---

# Operationen

## ADD

Verwende sie, wenn die Information völlig neu ist.

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## UPDATE

Verwende sie, wenn:

* die neue Erinnerung mit einer bestehenden vereinbar ist
* beide dasselbe Thema beschreiben
* das Zusammenführen Vollständigkeit oder Abrufbarkeit verbessert

Regeln:

* verlustfrei zusammenführen
* alle nützlichen Informationen bewahren
* KEINE Duplikate erzeugen
* wenn sich die beiden in einem Wert widersprechen, behalte den Wert der Erinnerung mit dem späteren
  `memory_at`; lass niemals eine ältere Erinnerung einen neueren Wert überschreiben

### Dasselbe Vorkommnis vs. ein weiteres Vorkommnis

Verwende **UPDATE** nur, wenn die neue Erinnerung Details zu demselben realen
Vorkommnis oder demselben dauerhaften Fakt ergänzt, der bereits festgehalten ist.

Wenn dieselbe Person dieselbe Art von Aktivität/Ereignis zu einem anderen Zeitpunkt erneut ausführt
(eine weitere Reise, ein weiteres Spiel, eine weitere Verletzung, ein weiterer Kauf, ein weiterer Gesundheitsschreck usw.), behalte das Vorkommnis
als separates **ADD**. Bewahre sein eigenes Datum und seine eigenen Details. Wenn eine bestehende Erinnerung
eindeutig ein weiteres Vorkommnis in genau dieser konkreten Serie ist, verknüpfe sie mit
`related: [{{"memory_id":"...","relation":"same_series"}}]`.

Führe getrennte datierte Vorkommnisse nicht zu einer langen Zähl-/Zeitleisten-Erinnerung zusammen.
Die Abrufschicht baut Zeitleisten aus den verknüpften L2-Erinnerungen auf.

```json
{{
  "op": "UPDATE",
  "memory_id": "...",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## SUPERSEDE

Verwende sie NUR, wenn:

* die neue Erinnerung einer bestehenden Erinnerung widerspricht
* beide nicht gleichzeitig wahr sein können
* die alte Erinnerung jetzt eine falsche Antwort zum aktuellen Zustand liefern würde
* das `memory_at` der neuen Erinnerung gleich dem oder SPÄTER als das `memory_at` der bestehenden Erinnerung ist

Beispiele:

* Stadt hat sich geändert
* Arbeitgeber hat sich geändert
* Beziehungsstatus hat sich geändert

NICHT verwenden für:

* ergänzte Details
* vereinbare Vorlieben
* Unterschiede in der Formulierung
* semantische Gleichwertigkeit
* eine neue Erinnerung, deren `memory_at` FRÜHER liegt als das der bestehenden Erinnerung — sie ist ein
  älterer Zustand und darf niemals einen neueren ersetzen (siehe „Zeitliche Reihenfolge“)

Regeln:

* content darf NUR den neuen Zustand enthalten
* unveränderte Informationen NICHT kopieren
* das System behält den alten Knoten und hängt ihn automatisch an die Entwicklungskette an; um die Historie musst du dich nicht kümmern

```json
{{
  "op": "SUPERSEDE",
  "memory_id": "...",
  "content": "...",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

### Mehrere bestehende Erinnerungen zu einer Kette zusammenfalten

Wenn ZWEI ODER MEHR bestehende Erinnerungen DIESELBE Dimension verfolgen und die neue Erinnerung
die jüngste Änderung auf dieser Dimension ist — ihr `memory_at` liegt später als das `memory_at` jeder
zusammengefalteten Erinnerung —, darfst du sie zu einer einzigen Entwicklungs-
kette zusammenfalten. Verwende `memory_ids` (eine geordnete Liste, ÄLTESTE → NEUESTE) statt einer einzelnen
`memory_id`:

```json
{{
  "op": "SUPERSEDE",
  "memory_ids": ["<oldest_id>", "<newer_id>"],
  "content": "<nur der neueste Zustand>",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

Die aufgeführten Erinnerungen werden von der ältesten zur neuesten verkettet, und die neue Erinnerung wird zum
Kettenkopf. `content` beschreibt weiterhin NUR den neuesten Zustand — kopiere niemals den
Text der älteren Erinnerungen hinein; sie bleiben in der Kettenhistorie abfragbar.

---

# Regeln

* Bewahre alle bedeutsamen Informationen.
* Überspringe exakte Duplikate.
* Führe verwandte Fragmente bevorzugt zusammen.
* Getrennte datierte Vorkommnisse bleiben getrennte ADDs; verwende `same_series` nur für
  dasselbe Subjekt und dieselbe konkrete Art von Aktivität/Ereignis.
* Halte jede Erinnerung auf ein zusammenhängendes Thema fokussiert.
* Befolge die „Zeitliche Reihenfolge“ unten.
* **Führe NIEMALS über verschiedene Subjekte hinweg zusammen und ersetze NIEMALS über sie hinweg.** Das Subjekt ist die Person,
  um die es im Inhalt geht: "Der Nutzer ..." vs. "John ..." vs. "Mary ...". Erinnerungen über
  verschiedene Personen sind unabhängige Fakten, selbst wenn der Satz ansonsten
  identisch ist — "Der Nutzer mag Kaffee" und "John mag Kaffee" sind zwei getrennte
  Erinnerungen, niemals ein UPDATE oder ein SUPERSEDE voneinander.
* `owner` (`user`/`agent`): übernimm ihn aus der zu schreibenden Erinnerung. Führe NIEMALS über verschiedene owner hinweg zusammen und ersetze NIEMALS über sie hinweg — ein Fakt des Nutzers und ein Fakt des Assistenten sind verschieden, selbst beim selben Thema.
* Ein SUPERSEDE zielt entweder auf EINE Erinnerung (`memory_id`) oder faltet mehrere Erinnerungen auf derselben Dimension zu einer Kette zusammen (`memory_ids`, älteste→neueste). Ein UPDATE zielt auf genau EINE Erinnerung.
* Berühre dieselbe bestehende Erinnerung niemals mit mehr als einer Operation.
* Die Ausgabesprache entspricht der Sprache der eingegebenen Erinnerungen.

---

# Zeitliche Reihenfolge

Jede Erinnerung, ob neu oder bestehend, hat ein `memory_at`: wann sie geschehen ist. Erinnerungen werden
NICHT immer in der Reihenfolge geschrieben, in der sie geschehen sind — Verläufe können verspätet importiert werden,
und eine Erinnerung kann hinzugefügt werden, während ältere Verläufe noch verarbeitet werden. Eine neue
Erinnerung kann daher ÄLTER sein als eine bestehende.

* Entscheide, welche Erinnerung neuer ist, AUSSCHLIESSLICH anhand von `memory_at`. „Neue Erinnerungen“ bedeutet „werden
  jetzt geschrieben“, nicht „sind jünger“.
* Eine ältere Erinnerung darf niemals eine neuere ersetzen: kein SUPERSEDE einer neueren
  Erinnerung und kein UPDATE, das einen neueren Wert mit einem älteren überschreibt.
* Wenn eine neue Erinnerung älter ist als eine bestehende Erinnerung, mit der sie in Konflikt steht, behalte die
  bestehende Erinnerung als aktuellen Zustand. Füge die neue per ADD als vergangenen Zustand hinzu, formuliert
  in der Vergangenheit mit ihrem Zeitpunkt (zum Beispiel „2023 lebte der Nutzer in Shanghai“),
  oder gib nichts aus, wenn sie keine Information hinzufügt.
* `memory_at: null` bedeutet unbekannte Zeit. Unbekannt ist NICHT neuer: Ersetze niemals per SUPERSEDE eine
  Erinnerung mit bekanntem `memory_at` durch eine, deren `memory_at` null ist.
* Erfinde keine Chronologie.

---

# Optional: `related`

Bei jeder Operation DARFST du ein `related`-Array hinzufügen, das festhält, wie die zu schreibende Erinnerung
mit einer BESTEHENDEN Erinnerung zusammenhängt. Das ändert nichts daran, was gespeichert wird — es hält
lediglich eine Verknüpfung fest.

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."],
  "related": [{{"memory_id": "<existing_id>", "relation": "causes"}}]
}}
```

Nur diese vier Relationen sind erlaubt:

* `causes` — die beiden Erinnerungen stehen in einem Ursache-Wirkungs-Verhältnis
  (z. B. „hat angefangen, täglich zu laufen“ → „das Knie hat angefangen zu schmerzen“)
* `elaborates` — ergänzt Details zur bestehenden Erinnerung oder ist eine spätere Entwicklung davon
* `contrasts` — sie stehen in einem Spannungsverhältnis, sind aber BEIDE noch wahr (wenn eine die andere
  falsch macht, ist das ein SUPERSEDE, keine `contrasts`-Verknüpfung)
* `same_series` — getrennte datierte Vorkommnisse derselben konkreten Aktivität oder
  derselben Ereignisart für dasselbe Subjekt (zum Beispiel zwei verschiedene Skireisen)

Regeln:

* Verknüpfe nur mit Erinnerungen, die unter „Bestehende Erinnerungen“ erscheinen — erfinde niemals eine ID.
* Gib eine Verknüpfung nur aus, wenn die Beziehung **explizit und spezifisch** ist. Dass zwei Erinnerungen
  lediglich dasselbe breite Thema betreffen, ist KEINE Relation — lass `related` weg.
* `same_series` erfordert dasselbe Subjekt und eine konkrete wiederholte Handlung bzw. ein konkretes wiederholtes Ereignis;
  gemeinsame breite Tags wie health, travel, sport, food oder work reichen nicht aus.
* Lass das Feld ganz weg, wenn es nichts zu verknüpfen gibt. Null Verknüpfungen sind der Normalfall.
* Verwende `related` niemals als Ersatz für UPDATE / SUPERSEDE.

---
{few_shot_section}
###################
Eingabedaten
###################

Current date: {current_date}

## Bestehende Erinnerungen

{existing_memories}

## Neue Erinnerungen

{new_memories}

---

# Ausgabe

Gib AUSSCHLIESSLICH ein JSON-Array in einem ```json-Codeblock zurück.

Wenn keine Änderungen nötig sind:

```json
[]
```

Erzeuge jetzt das JSON-Array."""

RECONCILE_STRUCTURE_METADATA_PROMPT = """

Zusätzlicher Vertrag für die strukturelle Ausgabe:

Bewahre oder verfeinere für jedes ADD-, UPDATE- oder SUPERSEDE-Ergebnis `topic_paths`,
`event_facet` und `topic_assignments`. `event_facet` enthält ausschließlich `subject`,
`participants`, `action`, `objects`, `locations` und `state`.
`topic_assignments` enthält subjektspezifische Einträge `{subject, role, path}`;
jedes Pfadsegment ist `{name, type}`, und type ist `topic|facet|series|episode`.
Die Typen folgen `topic -> facet -> series -> episode`; Ebenen dürfen übersprungen werden, aber
Typen dürfen sich weder wiederholen noch rückwärts laufen.
Verwende Series nur für wiederholte Vorkommnisse einer konkreten Aktivitäts-/Zustandslinie und
Episode nur für ein einzelnes, abgegrenztes reales Vorkommnis. Leite diese Typen niemals aus
der Tiefe, einem breiten gemeinsamen Tag oder einem unzusammenhängenden gemeinsamen Vorkommen ab. Wenn eine bestehende Erinnerung
bereits einen semantisch gleichwertigen subjektspezifischen Pfad hat, verwende exakt deren Knoten-
namen und -typen wieder, statt eine synonyme Abzweigung anzulegen. Um eine exakte Wiederverwendung anzufordern, gib
`structure_references: [{"memory_id":"<existing_id>", "subject":"...",
"through":"topic|facet|series|episode"}]` aus. Die referenzierte ID muss unter
„Bestehende Erinnerungen“ erscheinen. `through` bedeutet, das bestehende Pfadpräfix bis einschließlich des
genannten Strukturtyps zu kopieren; ein explizit verfasstes tieferes Suffix bleibt neu. Die Wiederverwendung
ist verboten, wenn sich das Subjekt, die konkrete Aktivitäts-/Zustandslinie oder die Episode-Grenze
unterscheidet. Diese Felder beschreiben die Erinnerung, niemals eine künftige Frageform wie
„wann“, „wie viele“ oder „wer“.

Series ist eine Relation des Reconcile-Zeitpunkts: Lege sie nur fest, wenn die neue Erinnerung und
mindestens eine unabhängige bestehende Erinnerung getrennte Vorkommnisse derselben
konkreten Aktivitäts-/Zustandslinie sind. Mehrere Aussagen aus dem aktuellen Schreibvorgang belegen
keine Wiederholung.
"""

RECONCILE_FEW_SHOT = """
###################
Beispiele
###################

## Beispiel 1 — Eine neue Erinnerung: ein Teil widerspricht einer bestehenden Kette, ein Teil ist unabhängig

Bestehende Erinnerungen:
[
  {"memory_id": "m1", "content": "Der Nutzer arbeitet als Produktmanager bei Stripe.", "owner": "user", "history_versions": [{"content": "Der Nutzer arbeitet als Produktmanager bei einem Fintech-Start-up."}]}
]
Neue Erinnerungen:
[
  {"content": "Der Nutzer hat Stripe verlassen, ist jetzt Product Lead bei Notion und hat vor Kurzem am Wochenende mit dem Töpfern angefangen.", "owner": "user"}
]

Ausgabe:
```json
[
  {"op": "SUPERSEDE", "memory_id": "m1", "content": "Der Nutzer arbeitet als Product Lead bei Notion.", "supersede_reason": "Arbeitgeber hat von Stripe zu Notion gewechselt", "owner": "user", "tags": ["work"]},
  {"op": "ADD", "content": "Der Nutzer hat vor Kurzem am Wochenende mit dem Töpfern angefangen.", "owner": "user", "tags": ["hobby"]}
]
```
EINE EINZIGE neue Erinnerung enthält zwei Dinge: den Jobwechsel (dieselbe Dimension wie die bestehende Beschäftigungskette m1, die bereits eine Historie hat → SUPERSEDE, wird zum neuen Kettenkopf) und ein unabhängiges Detail (Töpfern → ADD). Teile die eine Erinnerung auf und wähle pro Teil die passende Operation — der neue Kopf beschreibt NUR den Beschäftigungsstatus, niemals das Töpfern.

## Beispiel 2 — Zwei unverknüpfte bestehende Erinnerungen + die neue Änderung zu einer Kette mit 3 Knoten zusammenfalten

Bestehende Erinnerungen:
[
  {"memory_id": "a1", "content": "Der Nutzer lernt Gitarre spielen.", "owner": "user", "memory_at": "2024-02-10 09:00"},
  {"memory_id": "a2", "content": "Der Nutzer kann jetzt ein paar ganze Lieder auf der Gitarre spielen.", "owner": "user", "memory_at": "2024-09-05 09:00"}
]
Neue Erinnerungen:
[
  {"content": "Der Nutzer tritt jetzt bei lokalen Open-Mic-Abenden mit der Gitarre auf.", "owner": "user"}
]

Ausgabe:
```json
[
  {"op": "SUPERSEDE", "memory_ids": ["a1", "a2"], "content": "Der Nutzer tritt bei lokalen Open-Mic-Abenden mit der Gitarre auf.", "supersede_reason": "Gitarrenkönnen hat sich entwickelt: lernen → Lieder spielen → live auftreten", "owner": "user", "tags": ["music", "hobby"]}
]
```
a1 und a2 waren nie verknüpft, aber sie verfolgen dieselbe Dimension (Fortschritt auf der Gitarre), und die neue Erinnerung ist der jüngste Schritt. Falte sie mit `memory_ids` in der Reihenfolge älteste→neueste zusammen; sie werden zu einer Kette mit 3 Knoten (a1 ← a2 ← neuer Kopf). Der `content` des Kopfes beschreibt NUR den aktuellen Zustand — er wiederholt weder „lernt“ noch „ein paar Lieder“.

## Beispiel 3 — Der neue Kopf darf den Inhalt der alten Erinnerung NICHT kopieren

Bestehende Erinnerungen:
[
  {"memory_id": "c1", "content": "Der Nutzer wohnt in einer gemieteten Einzimmerwohnung in Shibuya, Tokio, zahlt ¥180,000/Monat, liebt die 5 Minuten Fußweg zum Bahnhof, beklagt sich aber, dass es nachts laut ist und es keinen Parkplatz gibt.", "owner": "user"}
]
Neue Erinnerungen:
[
  {"content": "Der Nutzer hat gerade ein Haus mit drei Schlafzimmern in einem Vorort von Yokohama gekauft und ist dort eingezogen, mit Garten und Doppelgarage.", "owner": "user"}
]

Ausgabe:
```json
[
  {"op": "SUPERSEDE", "memory_id": "c1", "content": "Der Nutzer hat ein Haus mit drei Schlafzimmern in einem Vorort von Yokohama gekauft und ist dort eingezogen, mit Garten und Doppelgarage.", "supersede_reason": "Wohnsitz hat sich von einer gemieteten Wohnung in Shibuya zu einem eigenen Haus in Yokohama geändert", "owner": "user", "tags": ["housing"]}
]
```
Die neue Erinnerung ersetzt c1 per SUPERSEDE und wird zum Kettenkopf. Der `content` des Kopfes beschreibt NUR das neue Haus in Yokohama. Schleppe die charakteristischen Details der alten Wohnung (Shibuya, ¥180,000 Miete, der Fußweg zum Bahnhof, die Beschwerden über Lärm/Parkplatz) NICHT in den neuen Knoten — sie gehören eindeutig zum alten Wohnsitz, nicht zum neuen. Sie bleiben in der Kettenhistorie und bleiben abrufbar; sie zu kopieren, würde den Kopf in sich widersprüchlich machen (gleichzeitig zur Miete in Shibuya UND im Eigentum in Yokohama).

## Beispiel 4 — Verschiedene Subjekte werden nie zusammengeführt, egal wie ähnlich die Formulierung ist

Bestehende Erinnerungen:
[
  {"memory_id": "d1", "content": "Der Nutzer trinkt jeden Morgen schwarzen Kaffee.", "owner": "user"}
]
Neue Erinnerungen:
[
  {"content": "John trinkt jeden Morgen schwarzen Kaffee.", "owner": "user"},
  {"content": "Der Nutzer trinkt jetzt zweimal täglich schwarzen Kaffee, morgens und nach dem Mittagessen.", "owner": "user"}
]

Ausgabe:
```json
[
  {"op": "ADD", "content": "John trinkt jeden Morgen schwarzen Kaffee.", "owner": "user", "tags": ["food"]},
  {"op": "UPDATE", "memory_id": "d1", "content": "Der Nutzer trinkt zweimal täglich schwarzen Kaffee, morgens und nach dem Mittagessen.", "owner": "user", "tags": ["food"]}
]
```
Die beiden neuen Erinnerungen sind fast identisch formuliert, haben aber VERSCHIEDENE Subjekte. "John ..." handelt von einer anderen Person, also ist es ein einfaches ADD — niemals ein UPDATE oder SUPERSEDE von d1, obwohl d1 fast dasselbe über den Nutzer sagt. Nur die Erinnerung, deren Subjekt ebenfalls "Der Nutzer" ist, darf mit d1 zusammengeführt werden.

## Beispiel 5 — Wiederkehrendes Ereignis: Anzahl erhöhen und eine Zeitleiste führen (UPDATE, nicht ADD)

Bestehende Erinnerungen:
[
  {"memory_id": "i1", "content": "John hat sich letzte Saison am Knöchel verletzt und brauchte eine Pause sowie Physiotherapie.", "owner": "user", "memory_at": "2023-05-21 19:48"}
]
Neue Erinnerungen:
[
  {"content": "John hat sich bei einem kürzlichen Spiel erneut den Knöchel verdreht.", "owner": "user", "memory_at": "2023-10-13 13:50"}
]

Ausgabe:
```json
[
  {"op": "UPDATE", "memory_id": "i1", "content": "John hat sich zweimal am Knöchel verletzt: zuerst letzte Saison (etwa in der Saison 2022–2023), mit Pause und Physiotherapie; dann bei einem Spiel um den 2023-10-13.", "owner": "user", "tags": ["health", "basketball", "injury"]}
]
```
Dieselbe Ereignisart tritt erneut auf → UPDATE der alten Erinnerung. Nenne die **Anzahl** und führe die Vorkommnisse mit Zeitangaben auf, soweit bekannt. Füge KEINEN zweiten Fakt „John hat sich am Knöchel verletzt“ per ADD hinzu und verwende KEIN SUPERSEDE (die erste Verletzung ist nicht unwahr geworden).
"""


# ================================================================
# Working / coding memory (coding/)
# ================================================================

CODING_EXTRACT_PROMPT = """\
Du extrahierst dauerhafte WORKING-Erinnerungen aus einem Abschnitt einer Aufgabenausführungs-Session.
Eine „working“-Session ist jede praktische Aufgabe, bei der der Nutzer einen Computer /
ein System über Tools bedient — das umfasst SOWOHL Software-/Code-Arbeit (Schreiben, Debuggen,
Refactoring, Bauen, Deployen, Nutzung von APIs/Bibliotheken) ALS AUCH System- und Betriebs-
arbeit (Ausführen von Shell-/PowerShell-Befehlen, Analyse oder Bereinigung von Festplatten/Speicher, Datei-
& Ordneroperationen, Installieren/Konfigurieren von Software, Einrichten von Umgebungen/Geräten,
Prüfung des Hardware-/Festplattenzustands, Backup, Planung/Automatisierung, Datenverarbeitung).

Du erzeugst ZWEI Ebenen aus DEMSELBEN Abschnitt:

  EXPERIENCES (prozedural, „wie man es macht“) — wiederverwendbare Methode, nur extrahiert,
  wenn sie die unten stehende WERTSCHWELLE überwindet. Ein Bündel aus (task, search_keys, solution,
  boundary_envs).

  FACT (episodisch, „was diesmal passiert ist“) — GENAU EIN Datensatz über DIESE
  Session: was der Nutzer wollte, wie vorgegangen wurde, wie es ausgegangen ist. Keine
  Wertschwelle, aber es gibt nie mehr als einen.

Die beiden Ebenen beantworten unterschiedliche Fragen und werden getrennt gespeichert. Eine Methode,
die sich verallgemeinern lässt, gehört in EXPERIENCES; die Geschichte dieser konkreten Session —
nur gültig für dieses System / dieses Projekt / genau jetzt — gehört in den FACT.

═══════════════════════════════════════════════════════════════════
WERTSCHWELLE (NUR FÜR EXPERIENCES) — überschreite sie NICHT leichtfertig.
═══════════════════════════════════════════════════════════════════
Eine Erinnerung MUSS ALLE folgenden Bedingungen erfüllen:
  (a) Sie löst ein Problem, das beim selben Nutzer wahrscheinlich wieder auftritt
  (b) Die Lösung ist nicht trivial: Sie kombiniert mehrere Informationen / Schritte, ODER
      enthält eine Entscheidungsbegründung, ODER hält einen hart erkämpften Workaround fest, ODER ist ein
      wiederverwendbares Verfahren/Rezept für eine operative Aufgabe (z. B. wie man die Festplattennutzung
      analysiert und bereinigt, wie man Dateien stapelweise ordnet, wie man den Hardwarezustand
      prüft, wie man einen geplanten Job einrichtet)
  (c) Ein künftiger Agent / Nutzer würde es lieber nachschlagen, als es neu herauszufinden

WICHTIG — Analyse-, Diagnose- und Untersuchungsaufgaben ZÄHLEN:
Eine Aufgabe muss NICHTS ändern oder „reparieren“, um es wert zu sein, erinnert zu werden.
Rein lesende / analytische / Fehlersuch-Arbeit ist wertvoll, wenn ihr VERFAHREN oder ihre
SCHLUSSFOLGERUNG wiederverwendbar ist. Extrahiere ausdrücklich:
  - Analyse-/Diagnoserezepte: das wiederverwendbare Verfahren + was es ergeben hat
    (z. B. „wie man analysiert, welche Ordner den Platz auf Laufwerk C: belegen“ — die Scan-Befehle,
    die Stolperfallen und der Befund). „Es wurde nur ein Bericht erzeugt, nichts geändert“
    ist KEIN Grund zum Überspringen — die wiederverwendbare METHODE ist die Erinnerung.
  - Untersuchungsergebnisse, EINSCHLIESSLICH negativer / Nicht-gefunden-Ergebnisse
    (z. B. „Ordner X auf C: und D: mit rg / fs_search / SQL-Index gesucht —
    nirgends gefunden“). Eine festgehaltene Sackgasse erspart dem nächsten Agenten, dieselbe
    fruchtlose Suche zu wiederholen, und überwindet daher die Schwelle.
Verwirf eine Erinnerung NICHT bloß deshalb, weil „der Assistent die Befehle neu
erzeugen könnte“ oder „nichts tatsächlich geändert wurde“. Wenn das Verfahren oder die Schlussfolgerung
künftigen Aufwand sparen würde, behalte sie.

Extrahiere NICHT:
  - Wirklich triviale Einzelschritt-Operationen ohne wiederverwendbares Verfahren („eine Datei umbenennen“,
    „git status“, „einmal ls ausführen“)
  - Einmalige, bereits gelöste Probleme ohne übertragbare Lehre
  - Informationen, die bereits im Repo/System stehen (package.json nicht duplizieren)
  - Wegwerfaufgaben pro PR / pro Session, es sei denn, sie enthalten ein wiederverwendbares Muster
  - Das NACHFRAGEN BEIM NUTZER nach fehlenden Informationen. Einen Parameter anfordern,
    auflisten, was unbekannt ist, oder Pflichtfelder erfragen ist Gesprächs-
    verhalten, keine Methode: Jeder Agent weiß bereits, wie man eine Frage stellt, also
    gibt es später nichts nachzuschlagen. Gib niemals eine Experience aus, deren task oder
    search_keys die Form „wie man nach X fragt“, „das fehlende Y einsammeln“,
    „Pflichtfelder erfragen“, „was vor dem Start zu klären ist“ haben. Beachte: Das
    bleibt nur aus EXPERIENCES heraus — dass die Session mit dem Warten auf den Nutzer endete,
    ist ein realer Zustand und gehört in den FACT.

Wenn der Abschnitt keinen substanziellen Fortschritt gemacht hat, weil er beim Warten auf den
Nutzer ins Stocken geriet — der Assistent hat nur nach Parametern gefragt oder wurde blockiert, bevor er
echte Arbeit geleistet hat —, gib "experiences": [] aus und schreibe nur den FACT. Es wurde noch nichts
gelernt, also gibt es keine Methode zu speichern. Das ist wichtiger, als es aussieht:
Eine aus einem Stillstand destillierte Experience liest sich beim Abruf wie eine legitime Fähigkeit,
und ein späterer Abschnitt derselben Aufgabe passt darauf und fragt den Nutzer erneut,
statt weiterzumachen — so reproduziert sich der Stillstand für den Rest der
Session. Beurteile die Substanz danach, was die Tools tatsächlich getan haben, nicht danach, ob der
Wortwechsel flüssigen Text erzeugt hat.

Faustregel: Wenn der Nutzer (oder ein künftiger Agent, der dieselbe Art von Aufgabe erledigt)
das lieber aufgeschrieben hätte, statt es erneut herauszufinden, behalte es. Ein mehrstufiges
operatives Verfahren (auch ein System-/Ops- oder ein rein lesendes Analyse-/Suchverfahren, nicht
nur Code) qualifiziert sich in der Regel. Verwirf es nur, wenn es ein einzelner Befehl ist, den jeder Nutzer
in 30 Sekunden über `--help` findet, UND es keine Entscheidung/Stolperfalle enthält, die es wert wäre,
erinnert zu werden.

═══════════════════════════════════════════════════════════════════
EXPERIENCE-FELDER
═══════════════════════════════════════════════════════════════════

task          — Name in Anfrageform; formuliere so, wie ein Nutzer später FRAGEN würde.
                „ein Release auf der xx-Plattform veröffentlichen“, nicht
                „publish_release_internal“
                In sich geschlossen (kein „dies“, „die Datei“, „wir“).

search_keys   — 0 bis 5 alternative Formulierungen, die wichtige Aspekte der
                solution erfassen, auf die `task` allein nicht passen würde. Formuliere sie als Fragen
                oder Themen, nach denen künftige Nutzer FRAGEN würden. Verschieden von task.
                Weglassen, wenn task bereits alles abdeckt.

solution      — Vollständiger, in sich geschlossener Inhalt, den der Nutzer tatsächlich braucht:
                Befehle, Speicherorte von Zugangsdaten (NICHT die WERTE der Zugangsdaten),
                Dateipfade, geordnete Schritte, Stolperfallen, Entscheidungsbegründung.
                Fasse mehrteilige End-to-End-Arbeit zu EINER solution zusammen, wenn sie
                eine zusammenhängende Aufgabe bildet; teile nur dann in mehrere Erinnerungen auf, wenn
                der Nutzer voneinander unabhängige Aufgaben erledigt hat.

boundary_envs — Konkrete Festlegungen, von denen die solution abhängt, mehrzeilig als „key: value“:
                  Runtime / SDK / Bibliothek + Version
                  API-Endpunkt oder Methodensignatur
                  externer Dienst / Middleware-Version
                  NAME der Umgebungsvariable (nicht der Wert)
                  Pfad der Konfigurationsdatei
                  Host- / Cluster- / Regionskennung
                Ist die solution umgebungsunabhängig, lass das Feld als "" (leer).
                Zweck: Wenn das zugrunde liegende SDK / die Middleware aktualisiert wird, können künftige
                Nutzer hier prüfen, ob die solution noch gilt.

confidence  — 0.0 bis 1.0
  1.0   der Nutzer hat dies ausdrücklich als Regel / Anweisung genannt
  0.7   mit guten Belegen aus Tool-Aktionen erschlossen
  0.5   schwach erschlossen; eine einzige Beobachtung; unsicher

═══════════════════════════════════════════════════════════════════
FACT-FELD — GENAU EINER pro Abschnitt, keine Wertschwelle
═══════════════════════════════════════════════════════════════════

Du schreibst EINEN Fact für den gesamten Abschnitt — einen wiederherstellbaren Zustandsdatensatz für diese
Session, keinen polierten Rückblick und niemals eine Liste getrennter Befunde. Ein
künftiger Agent muss daraus weitermachen können, ohne einen Vorschlag, eine
Schlussfolgerung oder eine Teilsuche mit etwas Verifiziertem zu verwechseln.

subject    — WER oder WAS Gegenstand dieser Session ist: die einzelne Entität, an der oder für die
             die Arbeit durchgeführt wurde. Eine kurze Nominalphrase, kein Satz, kein Verb.
             Wähle die spezifischste Entität, um die es in der Session *geht*, nicht das
             Tool, das verwendet wurde:
               den Namen einer Person, wenn die Session einer namentlich genannten Person dient
                 ("Dylan", "Matthew")
               die Datei / das Modul / das Symbol, wenn die Arbeit auf Code zielt
                 ("auth/middleware.ts", "parse_plan_text")
               den Dienst / Host / das Gerät / das Laufwerk bei Ops-Arbeit
                 ("D:\\WXWork", "redis-cache-prod", "ci.yml")
             Das ist ein HARTER SCHLÜSSEL, keine Prosa: Zwei Sessions über verschiedene Personen
             oder verschiedene Dateien MÜSSEN verschiedene subjects erhalten, und zwei Sessions
             über dieselbe MÜSSEN denselben String erhalten, identisch geschrieben.
             Bevorzuge den exakten Bezeichner, wie er im Trace vorkommt, gegenüber einer
             Beschreibung davon. Wenn der Abschnitt wirklich kein einzelnes Subjekt hat,
             gib "" aus.

             Warum es wichtig ist: subject ist das Tor, das entscheidet, ob eine spätere
             Session diese als „dieselbe Aufgabe, fortgesetzt“ überschreiben darf. Wenn
             es falsch ist, ersetzt der Zustand einer Person stillschweigend den einer anderen.

statement  — Ein kompakter Datensatz über DIESE Session. Zwei Teile, und der zweite ist
             derjenige, der verloren geht.
             Bewahre aufgabenbezogene Vorgaben und konkrete Einschränkungen, statt sie durch
             eine allgemeine Bewertung des Ergebnisses zu ersetzen. Nenne
             jede Regel einmal: wiederholter Anweisungstext konkurriert mit den Daten,
             die die Erinnerung festhalten muss.

             TEIL 1 — was passiert ist, etwa 300 lateinische Zeichen (~150 CJK).
             Beginne mit dem konkreten Ziel und Objekt („den von D:\\WXWork belegten
             Speicherplatz zurückgewinnen“, „den Ordner new_file_search finden“), nicht mit einem allgemeinen
             „einen Bug beheben“ / „eine Datei finden“. Dann grob, was untersucht, versucht
             oder geändert wurde. Dann, wie es endete, mit der Bedeutung genau eines von:
             abgeschlossen, teilweise abgeschlossen, fehlgeschlagen, blockiert oder
             noch zu verifizieren. Nenne negative Ergebnisse und Sackgassen,
             ohne sie zu übertreiben.

             TEIL 2 — die AUFGABENBEZOGENEN VORGABEN: alles, worauf die GESAMTE Aufgabe aufbaut,
             auch wenn dieser Abschnitt es nicht festgelegt hat und auch wenn es sich
             zu elementar anfühlt, um es zu nennen. Bindungen (wofür jeder Name / jedes Symbol / jedes Label
             steht), feste Werte, die die Aufgabe vorgibt statt herleitet, übernommene
             Konventionen (Indexbasis, Einheiten, Zeitzone, Standardwerte) und
             das eigene Ergebnis dieses Abschnitts MIT seinem Wert („x = 7“, nicht „der Wert
             wurde berechnet“).

             Schreib TEIL 2 als Daten, ein Eintrag pro Zeile, Werte wie vorgegeben:
               retry budget = 5; timeout 30s; index starts at 0
               step 3 input: n=64, tolerance 1e-6
               slot B: type=private, cost 688.7-721.3 per unit
             Eine Zeile sollte fast nur aus Werten bestehen. Behalte die Notation des Traces —
             Symbole, Einheiten, Zahlen, Schreibweisen — und lass seine Sätze weg: Streiche
             die Wendungen, die eine Vorgabe ankündigen, statt selbst eine zu sein („ich würde
             bevorzugen“, „es sollte irgendwo zwischen … und … liegen“). Sie wiederholen sich bei jedem
             Eintrag, sodass eine Liste mit 15 Einträgen 15-mal dafür bezahlt.
             Führe sie in JEDEM Fact mit; schreib niemals „wie oben“ oder „wie in der
             vorherigen Session“ — der Abruf liefert eine Handvoll Facts nach
             Ähnlichkeit, und das Bezugsobjekt ist womöglich nicht darunter.

             Ziel: insgesamt unter 1200 Zeichen. Wenn TEIL 2 nicht hineinpasst, korrigiere die
             Notation, niemals die Abdeckung: statement ist der Abrufvektor UND
             das einzige Feld, das ein späterer Abschnitt lesen kann (details wird weder eingebettet
             noch eingefügt), also ist alles, was daraus gestrichen wird, verloren. Verdichte die Formulierung
             um eine Vorgabe herum, niemals die Vorgabe selbst.

             Der Test: Wenn der nächste Abschnitt das ursprüngliche Material nicht sehen kann, kann
             er allein anhand dieses Datensatzes weitermachen? Nur ein Ja zählt. Eine fehlende
             Bindung scheitert nicht lautstark — der Name wirkt weiterhin bedeutungsvoll, also
             wird er in seiner allgemeinen Bedeutung gelesen, und die Antwort, die
             folgt, ist flüssig und betrifft eine andere Frage.

             SPRACHE — dieselbe Sprache wie der Session-Trace: statement ist der
             Abrufvektor, und Anfragen kommen in der Arbeitssprache des Nutzers,
             also zahlt eine andere Sprache bei jedem Abruf einen sprachübergreifenden Malus.

             TREUE ZU DEN BELEGEN — halte nur tatsächlich ausgeführte Aktionen und
             explizit im Trace vorhandene Belege fest. Bewahre wesentliche
             Befehle, Flags, Argumente, Fehler und Zahlen im Wortlaut.
             Ergänze NIEMALS einen Befehl um übliche Flags und mache niemals
             aus Ratschlägen oder Plänen Aktionen:
               "ran Get-ChildItem -Force" → "ran Get-ChildItem"
                 (der Trace enthielt kein `-Force`; erfinde es nicht)
             Begrenze jeden negativen Befund auf das, was tatsächlich durchsucht wurde, und
             zähle JEDE explizite Abdeckungsgrenze auf, die ihn entkräften könnte,
             insbesondere fehlende Berechtigungen und Tiefenbegrenzungen: Behalte die Wurzeln,
             Filter und die Tiefe bei, die galten („nicht gefunden unter C:\\ und
             D:\\ bis Tiefe 3; einige Verzeichnisse wurden wegen Access
             denied nicht abgedeckt“), weite es niemals zu „existiert nicht“ aus. Lass keine Grenze
             weg, um den Datensatz hübscher zu machen.
             Mache aus einer Teilsuche niemals eine globale Abwesenheit.
             Ein blockierter Versuch behält seinen Blocker.
             Bewahre die Unterscheidung zwischen versucht, beobachtet, verifiziert,
             erschlossen und ungelöst — drücke sie natürlich aus („versuchte …, wurde aber
             durch Access denied blockiert“, „vermutlich …, noch nicht verifiziert“), nicht als
             Labels. Ohne Tool-Ausgabe oder ausdrückliche Bestätigung behaupte NIEMALS, eine
             Korrektur sei erfolgreich gewesen, eine Ursache sei festgestellt oder ein Pfad existiere nicht.

             Halte KEINE Begrüßungen oder reines Geplauder fest — das ist der einzige Fall,
             der "fact": null bekommt. Ein Abschnitt, der lediglich Anforderungen nannte oder
             vor der eigentlichen Arbeit ins Stocken geriet, hat trotzdem etwas festgelegt.

details    — Die konkrete AUSGABE der Session, wörtlich übernommen. `statement` ist
             eine Zusammenfassung, und eine Zusammenfassung lässt zwangsläufig die einzelnen Werte weg;
             ein späterer Abruf will fast immer einen bestimmten Wert, nicht das Wesentliche.
             Gib daher jedes adressierbare Ergebnis, das diese Session erzeugt hat,
             hier in seiner ursprünglichen Form und Struktur wieder:
               ein Plan / Zeitplan / eine Reiseroute → behalte jede Zeile, jeden Tag, jeden
                 Slot und jeden Preis genau wie geschrieben
               ein Konfigurations- oder Codeblock → behalte den Block
               die wesentliche Ausgabe eines Befehls → behalte die Zeilen, die die
                 Befunde tragen (Größen, Anzahlen, Versionen, Fehlertext)
               ein strukturiertes Ergebnis (Tabelle, Liste von Datensätzen) → behalte die Zeilen
             Bewahre das ursprüngliche Layout — Zeilenumbrüche, Präfixe wie `Day 1:` / key: value,
             Einheiten, Währungssymbole, IDs. Nicht neu zusammenfassen, nicht
             umordnen, keine Zahlen runden, nicht übersetzen.

             Übernimm NUR, was im Trace vorhanden ist. Fülle niemals eine Lücke: Wenn ein Slot
             leer gelassen wurde, lass ihn leer oder sag das — ein erfundener Wert
             ist schlimmer als ein fehlender, weil er abgerufen und verwendet wird.
             Obergrenze etwa 4000 Zeichen; ist die Ausgabe länger, behalte die Teile,
             die konkrete Werte tragen, und lass schmückende Prosa weg, mit einem Hinweis darauf, was
             weggelassen wurde.

             "" wenn die Session kein solches Artefakt erzeugt hat (reine Untersuchung
             noch ohne Befunde oder ein Stillstand beim Warten auf den Nutzer).

             Dieses Feld ist NICHT Teil des Abrufvektors — `statement` und
             `subject` sorgen fürs Finden, `details` liefert den Wert, sobald er gefunden ist.
             Schreib hier also keinen Abruf-Köder und dupliziere nicht die
             Zusammenfassung; schreib das Rohmaterial.

files      — 0..N absolute oder repo-relative DATEI- ODER VERZEICHNIS-Pfade, die in dieser Session
             tatsächlich bearbeitet oder untersucht wurden. Behalte untersuchte
             Wurzeln wie `C:\\` und `D:\\`. Nimm nur Pfade auf, auf die
             tatsächlich zugegriffen bzw. die tatsächlich durchsucht wurden, keine lediglich vorgeschlagenen oder geplanten Pfade;
             [] wenn keine zutreffen.
═══════════════════════════════════════════════════════════════════
HOST-KONTEXT (vom SDK übergeben)
═══════════════════════════════════════════════════════════════════
- workspace_id present: {workspace_present}
- branch present: {branch_present}

Wenn keine workspace_id vorhanden ist, darf sich die solution nicht auf unausgesprochenen
projektlokalen Kontext stützen — schreib jedes Repo / jeden Pfad / jeden Dienst, den sie braucht, in
`solution` oder `boundary_envs` aus, damit die Erinnerung für sich allein nutzbar bleibt.

═══════════════════════════════════════════════════════════════════
GESPRÄCH
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
AUSGABE (ausschließlich striktes JSON, kein Markdown)
═══════════════════════════════════════════════════════════════════
{{
  "experiences": [
    {{
      "task": "...",
      "search_keys": ["...", "..."],
      "solution": "...",
      "boundary_envs": "...",
      "confidence": 0.9
    }}
  ],
  "fact": {{
    "subject": "...",
    "statement": "...",
    "files": ["..."],
    "details": "..."
  }}
}}

STANDARDMÄSSIG EXTRAHIEREN. Wenn der Abschnitt echte Arbeit geleistet hat, MUSST du mindestens
eine Experience ausgeben — auch wenn die Aufgabe einfach aussieht oder auf Anhieb gelungen ist,
halte trotzdem den wiederverwendbaren Kern des Getanen fest (die Methode / den Befehl / das How-to).
Wenn die Session dünn ist, genügt die eine wertvollste Experience.

Die EINZIGE Ausnahme ist der oben beschriebene Stillstand: Wenn der Abschnitt keine echte Arbeit geleistet hat,
weil er beim Nachfragen nach fehlenden Informationen beim Nutzer blockiert war, gib
"experiences": [] aus und schreibe nur den Fact. Erfinde keine Experience, um
diese Anweisung zu erfüllen — eine erfundene („wie man nach der Abfahrts-
stadt fragt“) ist schlimmer als keine, weil sie später abgerufen und befolgt wird. Leer ist
NUR aus diesem Grund erlaubt, niemals als Abkürzung, wenn tatsächlich Arbeit geleistet wurde.

"fact" ist ein EINZELNES Objekt, niemals eine Liste — ein Datensatz pro Session.

Schreib IMMER den Fact. Die Schwelle für den Fact ist nicht „war das wertvoll“, sondern „ist
überhaupt etwas passiert“, und ein Abschnitt, der dich erreicht, hat diese Schwelle bereits überwunden.
Das gilt auch für Abschnitte, die eher nach Vorbereitung als nach Arbeit aussehen: Eine einzelne
Nutzernachricht, die Anforderungen, Parameter oder einen Ausgangszustand nennt, ohne dass
Tools ausgeführt wurden, ist genau das Material, das eine spätere Session nicht selbst rekonstruieren kann
— schreib Anforderungen, Parameter und Ausgangszustand samt Situation in `statement` (kompakte Notation,
keine wörtlichen Sätze aus dem Gespräch), schieb sie nicht in `details` ab (`details` geht nicht in den
Abrufvektor ein und wird auf der Leseseite auch nicht mehr eingefügt). Verwende
"fact": null NUR für Begrüßungen und reines Geplauder ganz ohne Inhalt.
Im Zweifel schreib den Fact: Ein unnötiger Datensatz kostet eine Zeile, während ein weggelassener
jede spätere Session kostet, die ihn gebraucht hätte und nicht wissen kann, dass er existierte.
"""

CODING_EXTRACT_PROMPT_CODING = """\
╔═══════════════════════════════════════════════════════════════════════════╗
║ HARTE VORGABE — lies das vor allem anderen; sie hat Vorrang vor jeder     ║
║ späteren Formulierung in diesem Prompt, die nach einer Erzählung klingt.  ║
║                                                                           ║
║ Du schreibst KEINEN Bericht darüber, was eine Person getan hat. Du        ║
║ schreibst, was ein KÜNFTIGER AGENT braucht, um zu handeln. Dieser Agent   ║
║ hat bereits seine eigene Aufgabe; ihn interessiert nicht, was dieser      ║
║ Nutzer wollte. Er muss wissen: was hier entschieden wurde, an welchen     ║
║ Code-Koordinaten, was versucht und verworfen wurde und ob es verifiziert  ║
║ wurde.                                                                    ║
║                                                                           ║
║ Konkret gilt in jedem `statement` und jeder `solution`:                   ║
║   · Das grammatische Subjekt ist der CODE oder DIE ENTSCHEIDUNG — niemals ║
║     „der Nutzer“ und niemals „der Agent“. Schreib nicht                   ║
║     „Der Nutzer versuchte …“, „Der Nutzer wollte …“,                      ║
║     „Der Agent prüfte dann …“.                                            ║
║   · Schließe niemals mit einer Buchhaltungsfloskel wie „die Session wurde ║
║     abgeschlossen“, „der Fehler wurde notiert“, „noch keine Lösung        ║
║     gefunden“. Schließe mit der Entscheidung und ihrem                    ║
║     Verifikationsstatus. Wurde nichts entschieden, schließe mit dem, was  ║
║     AUSGESCHLOSSEN wurde, und der nächsten unerforschten Spur — das ist   ║
║     der wiederverwendbare Teil.                                           ║
║   · Behalte das konkrete Symptom (Fehlerstring, fehlschlagender Befehl,   ║
║     falsche Ausgabe) — genau darauf trifft eine künftige Suche.           ║
║                                                                           ║
║ schlecht: „Der Nutzer versuchte, eine QTable mit header_rows als RST zu   ║
║        schreiben, und bekam einen TypeError. Die Session wurde mit        ║
║        notiertem Fehler abgeschlossen.“                                   ║
║ gut:  „Der RST-Writer von ascii.rst lehnt header_rows ab:                 ║
║        `tbl.write(..., format='ascii.rst', header_rows=['name','unit'])`  ║
║        löst TypeError aus, weil RST.__init__ es nicht weiterreicht,       ║
║        anders als ascii.fixed_width, das es akzeptiert. Kein Fix          ║
║        geschrieben — der Writer braucht dieselbe                          ║
║        header_rows-Durchreichung wie fixed_width.“                        ║
╚═══════════════════════════════════════════════════════════════════════════╝

Du extrahierst dauerhafte WORKING-Erinnerungen aus einem Abschnitt einer Aufgabenausführungs-Session.
Eine „working“-Session ist jede praktische Aufgabe, bei der der Nutzer einen Computer /
ein System über Tools bedient — das umfasst SOWOHL Software-/Code-Arbeit (Schreiben, Debuggen,
Refactoring, Bauen, Deployen, Nutzung von APIs/Bibliotheken) ALS AUCH System- und Betriebs-
arbeit (Ausführen von Shell-/PowerShell-Befehlen, Analyse oder Bereinigung von Festplatten/Speicher, Datei-
& Ordneroperationen, Installieren/Konfigurieren von Software, Einrichten von Umgebungen/Geräten,
Prüfung des Hardware-/Festplattenzustands, Backup, Planung/Automatisierung, Datenverarbeitung).

Du erzeugst ZWEI Ebenen aus DEMSELBEN Abschnitt:

  EXPERIENCES (prozedural, „wie man es macht“) — wiederverwendbare Methode, nur extrahiert,
  wenn sie die unten stehende WERTSCHWELLE überwindet. Ein Bündel aus (task, search_keys, solution,
  boundary_envs).

  FACT (episodisch, „was diesmal festgelegt wurde“) — GENAU EIN Datensatz über DIESE
  Session. Er ist KEINE Erzählung dessen, was der Nutzer getan hat. Er hält in dieser Reihenfolge fest:
  die Aufgabe und ihr konkretes Symptom (das macht ihn später auffindbar), die
  Code-Koordinaten, WORAUF MAN SICH FESTGELEGT HAT und was verworfen wurde, und wie weit es
  verifiziert wurde. Keine Wertschwelle, aber es gibt nie mehr als einen.

Die beiden Ebenen beantworten unterschiedliche Fragen und werden getrennt gespeichert. Eine Methode,
die sich verallgemeinern lässt, gehört in EXPERIENCES; die Geschichte dieser konkreten Session —
nur gültig für dieses System / dieses Projekt / genau jetzt — gehört in den FACT.

═══════════════════════════════════════════════════════════════════
WERTSCHWELLE (NUR FÜR EXPERIENCES) — überschreite sie NICHT leichtfertig.
═══════════════════════════════════════════════════════════════════
Eine Erinnerung MUSS ALLE folgenden Bedingungen erfüllen:
  (a) Sie löst ein Problem, das beim selben Nutzer wahrscheinlich wieder auftritt
  (b) Die Lösung ist nicht trivial: Sie kombiniert mehrere Informationen / Schritte, ODER
      enthält eine Entscheidungsbegründung, ODER hält einen hart erkämpften Workaround fest, ODER ist ein
      wiederverwendbares Verfahren/Rezept für eine operative Aufgabe (z. B. wie man die Festplattennutzung
      analysiert und bereinigt, wie man Dateien stapelweise ordnet, wie man den Hardwarezustand
      prüft, wie man einen geplanten Job einrichtet)
  (c) Ein künftiger Agent / Nutzer würde es lieber nachschlagen, als es neu herauszufinden

WICHTIG — Analyse-, Diagnose- und Untersuchungsaufgaben ZÄHLEN:
Eine Aufgabe muss NICHTS ändern oder „reparieren“, um es wert zu sein, erinnert zu werden.
Rein lesende / analytische / Fehlersuch-Arbeit ist wertvoll, wenn ihr VERFAHREN oder ihre
SCHLUSSFOLGERUNG wiederverwendbar ist. Extrahiere ausdrücklich:
  - Analyse-/Diagnoserezepte: das wiederverwendbare Verfahren + was es ergeben hat
    (z. B. „wie man analysiert, welche Ordner den Platz auf Laufwerk C: belegen“ — die Scan-Befehle,
    die Stolperfallen und der Befund). „Es wurde nur ein Bericht erzeugt, nichts geändert“
    ist KEIN Grund zum Überspringen — die wiederverwendbare METHODE ist die Erinnerung.
  - Untersuchungsergebnisse, EINSCHLIESSLICH negativer / Nicht-gefunden-Ergebnisse
    (z. B. „Ordner X auf C: und D: mit rg / fs_search / SQL-Index gesucht —
    nirgends gefunden“). Eine festgehaltene Sackgasse erspart dem nächsten Agenten, dieselbe
    fruchtlose Suche zu wiederholen, und überwindet daher die Schwelle.
Verwirf eine Erinnerung NICHT bloß deshalb, weil „der Assistent die Befehle neu
erzeugen könnte“ oder „nichts tatsächlich geändert wurde“. Wenn das Verfahren oder die Schlussfolgerung
künftigen Aufwand sparen würde, behalte sie.

Extrahiere NICHT:
  - Wirklich triviale Einzelschritt-Operationen ohne wiederverwendbares Verfahren („eine Datei umbenennen“,
    „git status“, „einmal ls ausführen“)
  - Einmalige, bereits gelöste Probleme ohne übertragbare Lehre
  - Informationen, die bereits im Repo/System stehen (package.json nicht duplizieren)
  - Wegwerfaufgaben pro PR / pro Session, es sei denn, sie enthalten ein wiederverwendbares Muster

Faustregel: Wenn der Nutzer (oder ein künftiger Agent, der dieselbe Art von Aufgabe erledigt)
das lieber aufgeschrieben hätte, statt es erneut herauszufinden, behalte es. Ein mehrstufiges
operatives Verfahren (auch ein System-/Ops- oder ein rein lesendes Analyse-/Suchverfahren, nicht
nur Code) qualifiziert sich in der Regel. Verwirf es nur, wenn es ein einzelner Befehl ist, den jeder Nutzer
in 30 Sekunden über `--help` findet, UND es keine Entscheidung/Stolperfalle enthält, die es wert wäre,
erinnert zu werden.

═══════════════════════════════════════════════════════════════════
CODE-ARBEIT — was ein künftiger Agent nicht neu herleiten kann und daher erfasst werden MUSS
═══════════════════════════════════════════════════════════════════
Wenn der Abschnitt Softwarearbeit an einem Repository ist, ist die wiederverwendbare Methode nur
die Hälfte. Die andere Hälfte ist, WO die Arbeit liegt und WELCHE VERSION gewonnen hat. Das lässt sich
später nicht durch Lesen des Codes wiederherstellen — der Code zeigt den aktuellen Zustand,
nicht, auf welchem Branch er liegt, und auch nicht, was bereits verworfen wurde.

Dieser Abschnitt verlangt KEINEN längeren Datensatz. Er verlangt, DASSELBE BUDGET
für wertvollere Tokens AUSZUGEBEN: Lass wiederholte Ziele weg, die Erzählung dessen, was der
Assistent „dann beschloss“, und eingefügten Code, den du nur wiedergibst; verwende diese
Zeichen stattdessen für die vier folgenden Punkte. Ein Datensatz, der den Branch weglässt, aber
30 Zeilen Diff zitiert, ist ein schlechter Tausch.

1. BRANCH / ARBEITSORT — im Wortlaut, im `statement` des Facts. Wenn der Abschnitt
   `feat/x` / `fix/y` erwähnt oder auscheckt, MUSS dieses Token erscheinen. Ein Agent,
   der auf dem falschen Branch weitermacht, erzeugt Änderungen, die stillschweigend verworfen werden,
   daher wiegt dieses eine Token schwerer als jedes andere Detail.

2. WELCHE VERSION GEWONNEN HAT — wenn der Nutzer seine Meinung geändert hat, nenne den Gewinner und markiere
   den Verlierer in einem Satzteil: „festgelegt auf A; die Zwischenversion B wurde verworfen“. Nicht beide
   vollständig — ein Satzteil. Nur die neueste zu behalten ist falsch: Ein Agent, der auf den
   verworfenen Code stößt, muss ihn erkennen, sonst „repariert“ er die Dinge zurück zur verworfenen
   Variante. Wendungen wie `scratch that / go back to / actually, let's / no, not that`
   (bzw. „vergiss das / zurück zu / eigentlich lass uns / nein, nicht das“)
   markieren das: Was folgt, gilt; was vorausgeht, ist überholt.

3. EXAKTE KOORDINATEN, KEIN EINGEFÜGTER CODE — repo-relativer Pfad
   (`qutebrowser/utils/debug.py`, niemals bloß `debug.py`) plus die berührte Funktion /
   Klasse. Wenn sich eine Signatur geändert hat, gib die neue Signatur an. Wenn eine Konstante,
   ein Regex oder ein Literal festgelegt wurde, zitiere GENAU DAS im Wortlaut — aber nur das, nicht den
   umgebenden Block. Eine genaue Zeile schlägt einen Absatz voller Näherungen.

4. VERIFIZIERT vs. NUR GESCHRIEBEN — wenige Wörter genügen („Tests ausgeführt und bestanden“ /
   „nur per git diff bestätigt, Tests nicht ausgeführt“). „Die Änderung ist drin“ und „die Änderung ist drin
   und getestet“ führen einen künftigen Agenten zu sehr unterschiedlichen nächsten Schritten.

Anti-Muster — das Ziel zusammenfassen und die Koordinaten weglassen:
  schlecht: „hat die Logik zum Parsen von Farben sauberer umgebaut.“
  gut:  „auf feat/q4, in qutebrowser/config/configtypes.py QtColor._parse_value:
         festgelegt auf `mult = 360.0 if kind == 'h' else 255.0`; die Zwischenversion, die
         für alles 255.0 verwendete, wurde verworfen. Tests nicht ausgeführt.“
  (die gute Variante ist KÜRZER als die meisten schlechten — sie verwendet nur jedes Zeichen
   für etwas, das sich nicht neu herleiten lässt)

═══════════════════════════════════════════════════════════════════
EXPERIENCE-FELDER
═══════════════════════════════════════════════════════════════════

task          — Name in Anfrageform; formuliere so, wie ein Nutzer später FRAGEN würde.
                „ein Release auf der xx-Plattform veröffentlichen“, nicht „publish_release_internal“
                In sich geschlossen (kein „dies“, „die Datei“, „wir“).

search_keys   — 0 bis 5 alternative Formulierungen, die wichtige Aspekte der
                solution erfassen, auf die `task` allein nicht passen würde. Formuliere sie als Fragen
                oder Themen, nach denen künftige Nutzer FRAGEN würden. Verschieden von task.
                Weglassen, wenn task bereits alles abdeckt.

solution      — Vollständiger, in sich geschlossener Inhalt, den der Nutzer tatsächlich braucht:
                Befehle, Speicherorte von Zugangsdaten (NICHT die WERTE der Zugangsdaten),
                Dateipfade, geordnete Schritte, Stolperfallen, Entscheidungsbegründung.
                Fasse mehrteilige End-to-End-Arbeit zu EINER solution zusammen, wenn sie
                eine zusammenhängende Aufgabe bildet; teile nur dann in mehrere Erinnerungen auf, wenn
                der Nutzer voneinander unabhängige Aufgaben erledigt hat.

boundary_envs — Konkrete Festlegungen, von denen die solution abhängt, mehrzeilig als „key: value“:
                  Runtime / SDK / Bibliothek + Version
                  API-Endpunkt oder Methodensignatur
                  externer Dienst / Middleware-Version
                  NAME der Umgebungsvariable (nicht der Wert)
                  Pfad der Konfigurationsdatei
                  Host- / Cluster- / Regionskennung
                Ist die solution umgebungsunabhängig, lass das Feld als "" (leer).
                Zweck: Wenn das zugrunde liegende SDK / die Middleware aktualisiert wird, können künftige
                Nutzer hier prüfen, ob die solution noch gilt.

confidence  — 0.0 bis 1.0
  1.0   der Nutzer hat dies ausdrücklich als Regel / Anweisung genannt
  0.7   mit guten Belegen aus Tool-Aktionen erschlossen
  0.5   schwach erschlossen; eine einzige Beobachtung; unsicher

═══════════════════════════════════════════════════════════════════
FACT-FELD — GENAU EINER pro Abschnitt, keine Wertschwelle
═══════════════════════════════════════════════════════════════════

Du schreibst EINEN Fact für den gesamten Abschnitt — einen wiederherstellbaren Zustandsdatensatz für diese
Session, keinen polierten Rückblick und niemals eine Liste getrennter Befunde. Ein
künftiger Agent muss daraus weitermachen können, ohne einen Vorschlag, eine
Schlussfolgerung oder eine Teilsuche mit etwas Verifiziertem zu verwechseln.

statement  — Eine kompakte Zusammenfassung DIESER Session. Sie hat zwei Aufgaben zugleich und
             muss BEIDE erfüllen: Sie ist der Abrufvektor (ein künftiger Agent findet sie, indem er
             mit einer neuen Problemmeldung sucht, formuliert so, wie ein Nutzer ein
             Problem formuliert), und sie ist die Nutzlast, auf deren Grundlage dieser Agent dann handelt. Also:

             BEGINNE mit der Aufgabe und dem Symptom, in den Worten, die ein Nutzer verwenden würde.
                Das macht sie auffindbar — halte die Oberflächenform nah daran, wie das
                Problem gemeldet würde. Nenne das konkrete Ziel und Objekt
                („Speicherplatz unter D:\\WXWork zurückgewinnen“, „den Farbton-Multiplikator
                in QtColor._parse_value auf feat/q4 anpassen“, „den Ordner new_file_search
                finden“), niemals ein allgemeines „einen Bug beheben“ / „nach einer Datei suchen“. Nenne
                das konkrete Symptom: den Fehlerstring, den fehlschlagenden Befehl, die
                falsche Ausgabe.

             DANN nenne die Koordinaten und die Entscheidung.
                Repo-relativer Pfad, Symbol und Branch bei Code-Arbeit. Gib an, worauf
                man sich FESTGELEGT hat, und markiere im selben Satzteil, was verworfen wurde
                („festgelegt auf X; die Zwischenversion Y wurde verworfen“). Das ist der Teil, der
                sich nicht durch Lesen des Codes neu herleiten lässt — der Code zeigt nur den
                Gewinner, niemals, dass Y versucht und verworfen wurde. Ein Agent, der später auf die
                verworfene Variante stößt, muss sie erkennen können, sonst
                „repariert“ er den Code zurück zur verworfenen Version.

             SCHLIESSE mit dem Belegstatus.
                Genau eines von: abgeschlossen, teilweise abgeschlossen, fehlgeschlagen, blockiert oder
                noch zu verifizieren — plus wie es geprüft wurde („Tests ausgeführt und
                bestanden“ / „nur per git diff bestätigt, Tests nicht ausgeführt“). Nenne
                negative Ergebnisse und Sackgassen, ohne sie zu übertreiben.

             ⚠️ Hör niemals bei der Erzählung auf. „Der Nutzer versuchte X und stieß auf Fehler Y; noch
             keine Lösung gefunden“ ist ein verschwendeter Datensatz: Er wird problemlos abgerufen, gibt einem
             künftigen Agenten aber nichts, womit er handeln kann. Wenn die Session wirklich zu keiner
             Entscheidung gekommen ist, nenne, was ausgeschlossen wurde und was die nächste unerforschte Spur ist —
             das ist der wiederverwendbare Teil.

             Das Subjekt der Sätze sollte der Code und die Entscheidung sein, nicht
             „der Nutzer“. 2 bis 4 Sätze, fließende Prosa, keine beschrifteten Abschnitte.
             Harte Obergrenze ~400 lateinische Zeichen (~200 CJK-Zeichen).

             TREUE ZU DEN BELEGEN — verpflichtend:
             - Halte nur tatsächlich ausgeführte Aktionen und explizit im Trace
               vorhandene Belege fest. Bewahre wesentliche Befehle, Flags,
               Argumente, Fehler und Zahlen im Wortlaut. Ergänze NIEMALS einen
               Befehl um übliche Flags und mache aus Ratschlägen/Plänen keine Aktionen.
             - Bewahre die Unterscheidung zwischen versucht, beobachtet, verifiziert,
               erschlossen und ungelöst. Füge diese Labels nicht in die Prosa ein;
               drücke ihre Bedeutung natürlich aus, z. B. „versuchte …, wurde aber durch
               Access denied blockiert“ oder „vermutlich … , noch nicht verifiziert“.
             - Ohne Tool-Ausgabe oder ausdrückliche Bestätigung behaupte NIEMALS, eine Korrektur
               sei erfolgreich gewesen, eine Ursache sei festgestellt, eine Datei / ein Ordner existiere
               nicht oder Tests seien bestanden.
             - Begrenze jeden negativen Befund auf das, was tatsächlich durchsucht wurde, und
               zähle JEDE explizite Abdeckungsgrenze auf, die ihn entkräften könnte,
               insbesondere fehlende Berechtigungen und die maximale Suchtiefe:
               „nicht gefunden in den durchsuchten Teilen von C:\\ und D:\\; einige
               Verzeichnisse wurden wegen Access denied übersprungen, und die Suche war zudem
               tiefenbegrenzt“. Lass keine Grenze weg, um die
               Zusammenfassung hübscher zu machen. Mache aus einer Teilsuche niemals eine globale
               Abwesenheit.
             - Stelle Kausalität nur dann als sicher dar, wenn der Trace sie direkt verifiziert.
               Andernfalls behalte die Einschränkung bei („vermutet“ / „erschlossen“ / „noch nicht
               verifiziert“); schreib eine
               falsche Testannahme nicht in die Grundursache des Systems um.

             ENTSCHEIDEND — dieser Text IST der Abrufvektor, und er ist das Einzige,
             womit eine künftige Anfrage abgeglichen wird. Eine allgemeine Zusammenfassung ist
             wertlos, denn JEDE Session sieht aus wie eine allgemeine Zusammenfassung: Sie
             würde auf jede Anfrage passen und keine beantworten. Daher MUSST du
             die konkreten Anker nennen, die DIESE Session auffindbar machen — echte Datei-
             pfade, Funktions- / Symbolnamen, Branch-Namen, Befehle, Fehler-
             strings, Versionen, Konfigurationsschlüssel und -werte, Host- / Dienstnamen.
             Wenn später jemand nach einem dieser Details sucht, muss diese
             Zusammenfassung das sein, was zurückkommt.
               gut:      „Behebung eines fehlschlagenden `npm run build` in CI. .github/workflows/ci.yml
                          und package.json gelesen; zurückgeführt auf webpack 5.74, das unter node 18
                          auf ERR_OSSL_EVP_UNSUPPORTED stößt. Das Anheben von node im Workflow auf 20
                          ließ den Build durchlaufen; mit node 18 lässt sich der Fehler lokal weiterhin reproduzieren.“
               gut:      „auf Branch feat/q0, in qutebrowser/utils/debug.py signal_name:
                          PyQt<5.11 gibt den Namen nur über repr(sig) preis, und repr kommt sowohl
                          in einer Form mit eckigen als auch mit runden Klammern. Festgelegt auf zwei Muster,
                          die nacheinander mit fullmatch geprüft werden, Abbruch beim ersten Treffer; die
                          Variante mit einem einzigen Regex wurde verworfen. Per git diff bestätigt, Tests
                          nicht ausgeführt.“
               schlecht: „Nutzer wollte einen Build-Bug behoben haben, der Agent hat sich ein paar Konfigurations-
                          dateien angesehen, etwas Code geändert und es behoben.“
                          (kein Pfad, kein Befehl, kein Fehlerstring, keine Version — nichts,
                           worauf ein Abruf greifen könnte; jede Session liest sich genau so)
               schlecht: „Kompatibilitätsproblem beim Parsen von Signalnamen auf einem Feature-
                          Branch behoben.“
                          (welcher Branch? welche Datei? welche Funktion? welche der beiden
                           konkurrierenden Versionen hat gewonnen? nichts davon übersteht einen
                           Abruf eine Woche später)
             Beispiele zur Belegtreue (schlecht → gut):
               "ran Get-ChildItem -Force" → "ran Get-ChildItem"
                 (der Trace enthielt kein `-Force`; erfinde es nicht)
               „bestätigt, dass new_file_search nicht existiert“ →
                 „new_file_search wurde in den durchsuchten Teilen von C:\\ und
                  D:\\ nicht gefunden; einige Verzeichnisse wurden wegen Access denied oder durch die Tiefen-
                  begrenzung übersprungen, die Untersuchung ist also nur teilweise abgeschlossen“
               „identische Payloads verursachen einen fälschlichen Lock-Fehler“ →
                 „das Verifikationsskript nahm an, der letzte Schreiber sei immer ein fester
                  Rang; diese Annahme gilt nicht, ob es also einen Lock-Bug gibt,
                  ist noch nicht verifiziert“

             Halte KEINE Begrüßungen oder reines Geplauder fest. Wenn der Abschnitt
             überhaupt nichts festgelegt hat, gib "fact": null aus.

files      — 0..N absolute oder repo-relative DATEI- ODER VERZEICHNIS-Pfade, die in dieser Session
             tatsächlich bearbeitet oder untersucht wurden. Behalte untersuchte
             Wurzeln wie `C:\\` und `D:\\`. Nimm nur Pfade auf, auf die
             tatsächlich zugegriffen bzw. die tatsächlich durchsucht wurden, keine lediglich vorgeschlagenen oder geplanten Pfade;
             [] wenn keine zutreffen.

═══════════════════════════════════════════════════════════════════
HOST-KONTEXT (vom SDK übergeben)
═══════════════════════════════════════════════════════════════════
- workspace_id present: {workspace_present}
- branch present: {branch_present}

Wenn keine workspace_id vorhanden ist, darf sich die solution nicht auf unausgesprochenen
projektlokalen Kontext stützen — schreib jedes Repo / jeden Pfad / jeden Dienst, den sie braucht, in
`solution` oder `boundary_envs` aus, damit die Erinnerung für sich allein nutzbar bleibt.

═══════════════════════════════════════════════════════════════════
GESPRÄCH
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
AUSGABE (ausschließlich striktes JSON, kein Markdown)
═══════════════════════════════════════════════════════════════════
{{
  "experiences": [
    {{
      "task": "...",
      "search_keys": ["...", "..."],
      "solution": "...",
      "boundary_envs": "...",
      "confidence": 0.9
    }}
  ],
  "fact": {{
    "statement": "...",
    "files": ["..."]
  }}
}}

VERPFLICHTEND: Du MUSST mindestens EINE Experience ausgeben. Gib KEINE leere Liste zurück.
Auch wenn die Aufgabe einfach aussieht oder auf Anhieb gelungen ist, halte trotzdem den
wiederverwendbaren Kern des Getanen fest (die Methode / den Befehl / das How-to). Wenn die Session dünn ist,
ist nur die eine wertvollste Experience erforderlich — aber gib niemals
ein leeres "experiences"-Array aus.

"fact" ist ein EINZELNES Objekt, niemals eine Liste — ein Datensatz pro Session. Verwende
"fact": null nur, wenn der Abschnitt reines Geplauder ist, das nichts festgelegt hat.
"""

CODING_RECONCILE_PROMPT = """\
Du entscheidest, wie neue Entwürfe für Coding-Erinnerungen mit den bestehenden Erinnerungen des Nutzers zusammengeführt werden.

Entscheide für jeden neuen Entwurf eines von:
  ADD     — keine bestehende Erinnerung beschreibt dieselbe Aufgabe / denselben Aspekt
  UPDATE  — eine bestehende Erinnerung beschreibt dieselbe Aufgabe; der neue Entwurf ersetzt sie
            (die solution kann sich geändert haben, die Umgebung kann aktualisiert worden sein usw.)
  SKIP    — Duplikat einer bestehenden Erinnerung; nichts Neues

Zusätzlich darfst du DELETE-Operationen für bestehende Kandidaten ausgeben, wenn der neue
Inhalt sie überholt oder ungültig macht:
  DELETE  — das neue Gespräch ersetzt/storniert/entwertet AUSDRÜCKLICH eine
            bestehende Erinnerung, die sich nicht per UPDATE reparieren lässt. Beispiele:
              * der Nutzer sagt „verwende X nicht mehr“, und X existiert nur als
                separate Erinnerung (nicht die, die hinzugefügt/aktualisiert wird)
              * die boundary_envs des Kandidaten (Dienst / Endpunkt / SDK) existieren nicht mehr,
                und das neue Gespräch bestätigt die Abkündigung

Regeln:
- „Dieselbe Aufgabe“ bedeutet, dass die bestehende Erinnerung und der neue Entwurf beide gültige
  Antworten auf dieselbe künftige Nutzeranfrage wären.
- Wenn sich die solution unterscheidet, bevorzuge UPDATE (wir führen keine Historie).
- Wenn sowohl task als auch solution weitgehend übereinstimmen, SKIP.
- DELETE nur, wenn es AUSDRÜCKLICHE Belege dafür gibt, dass der Kandidat ungültig/überholt ist.
  Niemals DELETE, nur weil ein Kandidat „veraltet“ aussieht.
- Es kann mehr Ausgabe-Operationen als Entwürfe geben, wenn DELETEs erzeugt werden.

═══════════════════════════════════════════════════════════════════
NEUE ENTWÜRFE
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
BESTEHENDE KANDIDATEN (pro Entwurf, abgerufen nach Ähnlichkeit der Aufgabe)
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
AUSGABE (ausschließlich striktes JSON, kein Markdown)
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "action": "ADD"}},
    {{"draft_idx": 1, "action": "UPDATE", "target_experience_id": "exp_xxx", "reason": "..."}},
    {{"draft_idx": 1, "action": "DELETE", "target_experience_id": "exp_yyy", "reason": "Nutzer hat Y ausdrücklich für veraltet erklärt"}},
    {{"draft_idx": 2, "action": "SKIP",   "reason": "Duplikat"}}
  ]
}}

Wenn es keine Entwürfe gibt, gib {{"ops": []}} aus.
"""

CODING_FACT_RECONCILE_PROMPT = """\
Du pflegst WORKING FACTS auf Session-Ebene. Jedes statement fasst einen ganzen
Trace / eine ganze Session zusammen: die Aufgabe des Nutzers, das grobe Vorgehen und das Ergebnis. Es ist keine
atomare Aussage.

Jeder unten stehende neue Session-Fact WIRD gespeichert; das ist nicht deine Entscheidung. Deine einzige
Aufgabe ist es, Fortsetzungen von Aufgaben zu erkennen: Treibt die neue Session DIESELBE
Nutzeraufgabe weiter voran wie ein bestehender Session-Fact? Wenn ja, ersetze den älteren Fact per SUPERSEDE, sodass
die Verknüpfungen eine sessionübergreifende Fortschrittskette für diese Aufgabe bilden.

Markiere einen bestehenden Fact NUR dann als ersetzt, wenn es starke Belege dafür gibt, dass:
  - die Subjekte übereinstimmen: beide Datensätze betreffen DIESELBE Entität — dieselbe
    Person, dieselbe Datei / dasselbe Modul, denselben Host / Dienst. Ein gemeinsames Subjekt ist
    notwendig, aber nicht hinreichend; UND
  - das Ziel des Nutzers im Wesentlichen DASSELBE ist, nicht bloß im selben Thema/Projekt liegt;
    UND
  - der neue Trace die im bestehenden Trace festgehaltene Arbeit fortsetzt, wiederholt, erweitert, verifiziert oder
    abschließt.

Ersetze NICHT, wenn:
  - die beiden Datensätze verschiedene Subjekte nennen. Zwei Personen in einer Gruppe, zwei Dateien
    in einem Modul, zwei Hosts in einem Cluster sind PARALLEL, nicht aufeinanderfolgend: Der Zustand einer
    Person ist keine neuere Version des Zustands einer anderen. Wo ein Subjekt
    als (unspecified) angezeigt wird, urteile ersatzweise allein anhand des statement.
  - die Sessions lediglich dieselbe Datei, dasselbe Symbol, dieselbe Komponente, dasselbe Repository,
    denselben Fehler oder dieselbe Technologie erwähnen, aber unterschiedliche Nutzerziele verfolgen.
  - eine Aufgabe lediglich Kontext oder eine Technik für eine andere Aufgabe liefert.
  - die Beziehung unsicher ist. Sei konservativ: INSERT ist der sichere Standard;
    das Zusammenführen unabhängiger Sessions zerstört ihre getrennte Aufgabenhistorie.

Behandle jedes statement als belegbegrenzten Text. Deute eine Schlussfolgerung darin
nicht in eine festgestellte Ursache um und verwende eine gemeinsame Vermutung / einen gemeinsamen Fehler nicht als Beweis dafür, dass
die Nutzerziele dieselben sind. Deine Entscheidung betrifft ausschließlich die Fortsetzung der Aufgabe;
„korrigiere“, ergänze oder beschönige keinen der beiden Session-Datensätze.

Beispiele:
  bestehend: „Der Nutzer musste das npm run build in CI reparieren; zurückgeführt auf
              ERR_OSSL_EVP_UNSUPPORTED unter Node 18, Workflow noch nicht geändert.“
  neu:       „Der Nutzer setzte die Reparatur desselben CI-Builds fort; Node in
              .github/workflows/ci.yml auf 20 angehoben, und npm run build lief durch.“
                                                                 → SUPERSEDE

  bestehend: „Der Nutzer musste die Behandlung abgelaufener JWTs in
              auth/middleware.ts korrigieren; eine Refresh-Logik wurde hinzugefügt.“
  neu:       „Der Nutzer wollte Performance-Logging in
              auth/middleware.ts ergänzen; eine Latenzmetrik wurde eingeführt.“
                                                                 → beide behalten
             (dieselbe Datei / dasselbe Projekt, anderes Nutzerziel)

═══════════════════════════════════════════════════════════════════
NEUE SESSION-FACTS
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
BESTEHENDE SESSION-FACTS (pro neuem Fact, abgerufen nach Ähnlichkeit des statement)
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
AUSGABE (ausschließlich striktes JSON, kein Markdown)
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "supersedes": ["fact_xxx"], "reason": "setzt dieselbe CI-Reparatur fort"}},
    {{"draft_idx": 1, "supersedes": []}}
  ]
}}

Verwende nur Fact-IDs, die in der Kandidatenliste für genau diesen draft_idx erscheinen.
Wenn nirgends etwas ersetzt wird, gib {{"ops": []}} aus.
"""

CODING_CHAIN_SUMMARY_PROMPT = """\
Eine Nutzeraufgabe in einem Working-Memory-Speicher ist über mehrere Sessions vorangekommen. Unten stehen
die Session-Zusammenfassungen in der Fortschrittskette dieser Aufgabe, die älteste zuerst, endend mit
der aktuellen Session.

Fasse zusammen, wie DIESELBE Aufgabe über die Sessions hinweg bis zu ihrem aktuellen Stand vorangekommen ist.
Decke jede wichtige Phase in chronologischer Reihenfolge ab. Bewahre für jede Phase,
soweit vorhanden: die tatsächliche Aktion oder Änderung, ihr direktes Ergebnis und jedes
ungelöste Problem oder jede einschränkende Bedingung. Schließe mit einem ausdrücklichen aktuellen Status.

Regeln:
  - Dies ist reine Verdichtung. Gib NUR wieder, was die Einträge sagen; füge niemals einen Befehl,
    eine Ursache, ein Motiv, eine Aktion, ein Testergebnis, einen Erfolgszustand oder eine
    globale negative Schlussfolgerung hinzu und ergänze nichts davon.
  - Bewahre den Belegstatus exakt: versucht ist nicht abgeschlossen, beobachtet ist
    nicht verifiziert, erschlossen ist nicht festgestellt, und ungelöst bleibt ungelöst.
    Behalte Unsicherheitswörter und Grenzen von Suche/Berechtigung/Abdeckung bei.
  - Gib den aktuellen Endstatus getreu wieder (abgeschlossen, teilweise abgeschlossen,
    fehlgeschlagen, blockiert oder noch zu verifizieren). Werte ihn niemals auf, gestützt auf
    eine plausible Kausalgeschichte oder einen späteren Eintrag, der ihn nicht verifiziert hat.
  - Wenn Einträge eine vermutete Ursache und ein späteres Ergebnis nennen, verknüpfe sie nur dann
    kausal, wenn ein Eintrag direkt sagt, dass diese Ursache verifiziert wurde.
  - Bewahre konkrete Abrufanker: Dateipfade, Symbole, Befehle, Fehler-
    strings, Versionen, Konfigurationsschlüssel/-werte und wichtige Zahlenwerte, die
    für den Fortschritt relevant sind.
  - Verdichte bei Ketten aus 2–3 Generationen den Verlauf nicht übermäßig zu einem
    Satz. Bevorzuge 2–4 kompakte Absätze, wo sinnvoll, oder gut lesbare Prosa mit
    einem klaren Aufgabenziel, einem Verlauf Phase für Phase und dem aktuellen Status.
  - Kopiere nicht mechanisch die gesamte Historie. Lass Wiederholungen und Details von geringem Wert
    weg und behalte wichtige Aktionen, direkte Ergebnisse und offene Reste.
  - Erfinde keine Phasenüberschriften oder Daten. Nenne ein Datum nur, wenn die Einträge
    es bedeutsam machen; nackte Zeitstempel sind Rauschen.
  - Wenn die Einträge besagen, dass ältere Revisionen weggelassen wurden, sag ausdrücklich, dass
    frühere Historie weggelassen wurde; erwecke nicht den Eindruck, die gelieferten Einträge seien vollständig.
  - Schreib in derselben Sprache, in der die Einträge geschrieben sind.
  - Keine Einleitung und keine umschließenden Anführungszeichen.

Beispiel
  Einträge:
    (2026-07-20) Der Nutzer musste das npm run build in CI reparieren; zurückgeführt auf
                 ERR_OSSL_EVP_UNSUPPORTED unter Node 18, Workflow noch nicht
                 geändert.
    (2026-07-24) Der Nutzer setzte diese CI-Reparatur fort; Node in
                 .github/workflows/ci.yml auf 20 angehoben, aber der Abhängigkeits-Cache
                 reproduzierte weiterhin den alten Fehler.
    (now)        Der Nutzer setzte die Verifikation derselben Reparatur fort; nach dem Leeren des
                 npm-Caches lief npm run build durch.
  Ausgabe:
    Ziel der Aufgabe ist die Reparatur des npm run build in CI. Zunächst wurde der Fehler auf
    ERR_OSSL_EVP_UNSUPPORTED unter Node 18 zurückgeführt, der Workflow war noch nicht geändert;
    dann wurde .github/workflows/ci.yml auf Node 20 angehoben, doch der Abhängigkeits-
    Cache hielt den alten Fehler aufrecht. Schließlich lief npm run build nach dem Leeren des npm-Caches
    durch, sodass diese Build-Reparatur derzeit als abgeschlossen verifiziert ist.

═══════════════════════════════════════════════════════════════════
EINTRÄGE
═══════════════════════════════════════════════════════════════════
{chain_block}

═══════════════════════════════════════════════════════════════════
AUSGABE (nur die Zusammenfassung selbst, sonst nichts)
═══════════════════════════════════════════════════════════════════
"""


# ================================================================
# Episode / topic summaries
# ================================================================

EPISODE_SUMMARY_PROMPT = """Du fasst EINE Episode eines Gesprächs mit dem Nutzer zusammen.

Eine Episode ist ein einzelner zusammenhängender Gesprächsabschnitt — ein Thema, über das gesprochen wurde,
möglicherweise über mehrere Schreib-Sessions hinweg.

## Während dieser Episode erfasste Erinnerungen (älteste → neueste)

{memories}

---

Schreib eine einzige dichte Zusammenfassung dessen, was in dieser Episode passiert ist / besprochen wurde.

Regeln:

* 2–4 Sätze, dritte Person, beginnend mit „Der Nutzer ...“ (oder mit der namentlich genannten Person,
  wenn die Erinnerungen von jemand anderem handeln).
* Verbinde die Erinnerungen zu einer zusammenhängenden Darstellung — eine Episode hat meist einen
  roten Faden (einen Plan, ein Problem, einen Ausflug, eine Entscheidung).
* Bewahre konkrete Details: Namen, Orte, Zahlen, Daten.
* Erfinde NICHTS, was nicht in den Erinnerungen steht.
* Zähle die Erinnerungen NICHT einzeln auf; führe sie zusammen.
* Die Ausgabesprache muss der Sprache der Erinnerungen entsprechen.

Gib NUR den Zusammenfassungstext zurück, keine Einleitung, kein JSON, keine Anführungszeichen."""

TOPIC_SUMMARY_PROMPT = """Du fasst alles zusammen, was über EIN Thema bekannt ist.

## Thema

{topic_name}

## Erinnerungen zu diesem Thema (älteste → neueste)

{memories}

---

Schreib eine einzige dichte Zusammenfassung dessen, was über den Nutzer in Bezug auf dieses
Thema bekannt ist.

Regeln:

* 2–4 Sätze, dritte Person, beginnend mit „Der Nutzer ...“ (oder mit der namentlich genannten Person,
  wenn die Erinnerungen von jemand anderem handeln).
* Behandle das Thema als Ganzes — nenne die aktuelle Situation und erwähne, wie sie sich
  entwickelt hat, nur dann, wenn die Veränderung von Bedeutung ist.
* Bewahre konkrete Details: Namen, Orte, Zahlen, Daten.
* Erfinde NICHTS, was nicht in den Erinnerungen steht.
* Zähle die Erinnerungen NICHT einzeln auf; führe sie zusammen.
* Die Ausgabesprache muss der Sprache der Erinnerungen entsprechen.

Gib NUR den Zusammenfassungstext zurück, keine Einleitung, kein JSON, keine Anführungszeichen."""


# ================================================================
# System 2 evolve (pipelines/system2_agent.py)
# ================================================================

SYSTEM2_AGENT_PROMPT = """Du bist ein Agent für kognitive Verarbeitung. Deine Aufgabe ist es, aus den Roh-Erinnerungsdaten eines Nutzers kognitive Strukturen höherer Ordnung zu entwickeln.

## Erinnerungsarchitektur

### VDB (Vektordatenbank) — nur lesend
| Ebene | Beschreibung |
|-------|-------------|
| L0 BASIC_INFO | Attribute des Nutzers (Name, Alter, Beruf) |
| L1 RAW | Ursprüngliche Gesprächsprotokolle |
| L2 FACT | Aus Gesprächen extrahierte atomare Fakten |
| L3 SUMMARY | Session-Zusammenfassungen |
| L4 IDENTITY | Identitätsmerkmale, Vorlieben, Gewohnheiten |
| L5 KNOWLEDGE | Fachwissen |

### Graph (Wissensgraph) — du bist der einzige Schreibende
| Ebene | Beschreibung |
|-------|-------------|
| L6 SCHEMA | Verhaltensmuster — domänengebunden, atomar, nach der Erstellung unveränderlich |

## Was ist ein L6-Schema?

Ein Schema ist ein ereignisübergreifendes Verhaltensmuster im langfristigen Nutzermodell und
besteht aus den folgenden drei Komponenten:


- **Umstand**: Die Domäne / das Thema / die Situation, in der dieses Muster beobachtet wird. Ein Schema MUSS innerhalb seines Umstands bleiben — verallgemeinere NICHT über Domänen hinweg. Domänenübergreifende Abstraktion übernimmt das System separat.
- **Muster**: Das gewohnheitsmäßige Verhalten, der Denkstil oder die Handlungstendenz des Nutzers in diesem Umstand.
- **Einsicht**: Der zugrunde liegende psychologische Antrieb oder das mentale Modell.

### Inhaltsformat
Verwende einen einzigen Satz, der alle drei vereint:
„Wenn [Umstand], [Muster des Nutzers] — Ausdruck von [Einsicht].“

Bei dauerhaften Mustern im Zusammenhang mit Emotionen, Therapie, Stress, Trauer oder Trauma
lösche den konkreten autobiografischen Hintergrund nicht zugunsten eines allgemeinen Persönlichkeits-
labels. Bevorzuge eine verankerte Struktur, die Folgendes bewahrt:
„Auslöser/Hintergrund -> wiederkehrende emotionale Reaktion -> beobachtete Bewältigungsressource.“
Nimm nur Auslöser, Reaktionen und Bewältigungsressourcen auf, die in nutzereigenen
Belegen vorhanden sind. Ratschläge des Assistenten sind keine beobachtete Bewältigungsressource des Nutzers.

### Regeln
- **Atomar**: Ein Muster pro Schema. Zwei verschiedene Muster → zwei Schemas.
- **Unveränderlich**: Einmal erstellt, ändert sich der Inhalt NIE.
- **Nur Evidenz**: Für neue Fakten, die lediglich die bestehende Formulierung stützen → rufe
  `add_evidence` auf. Wenn neue Evidenz den aktuellen dauerhaften Zustand wesentlich verändert,
  korrigiert oder eingrenzt, verwende `revise_schema`, sodass die alte Version für die
  Nachvollziehbarkeit erhalten bleibt, aber aus dem aktiven Abruf entfernt wird.
- **Domänengebunden**: Bleib innerhalb der beobachteten Domäne. Domänenübergreifende Abstraktion übernimmt ein separater Systemprozess.
- **Fakten von Mustern trennen**: Eine einzelne explizite Identität, ein Gesundheitszustand,
  eine Gerätenutzung, eine Zugehörigkeit, ein Projekt oder eine Vorliebe gehört in den L2-Zustandsspeicher,
  nicht in L6. L6 erfordert nutzereigene Evidenz, die mindestens zwei unabhängige
  Raw-Schreibvorgänge oder Sessions repräsentiert. Ein zusammengeführter L2-Fakt kann mehrere Schreibvorgänge repräsentieren;
  prüfe seine source_raw_memory_ids, statt Faktknoten zu zählen.
- **Subjektsicherheit**: Fakten, die einen Quell-Chunk teilen, können andere Personen oder
  Beispiele beschreiben. Erstelle ein Nutzer-Schema nur, wenn das Material das Muster ausdrücklich
  dem Nutzer zuschreibt. Andernfalls gib für dieses Material keine Operation aus.
- **Kandidatenabdeckung**: Berücksichtige bei einer Source-Context-Prüfung jeden aufgeführten
  Kandidatenfakt: Verwende ihn entweder als Evidenz in `create_schema`/`add_evidence`
  oder gib `skip_fact` aus. Raw-Kontext darf sein Subjekt bestätigen oder ausgelassene
  Details wiederherstellen, darf aber keine unzusammenhängenden Schemas anstoßen.
- **Evidenzschwelle**: Das Erstellen von L6 erfordert einen oder mehrere nutzereigene Evidenz-
  fakten, die mindestens zwei unabhängige Raw-Schreibvorgänge oder Sessions repräsentieren.
  Ratschläge des Assistenten begründen für sich allein kein Nutzermuster.

### Gute vs. schlechte Beispiele
✅ „Beim Kochen hält sich der Nutzer streng an die Rezeptschritte und misst die Zutaten genau ab — Ausdruck eines Bedürfnisses nach äußerer Struktur, um mit Unsicherheit umzugehen.“
✅ „Beim Spielen schaut der Nutzer immer in Komplettlösungen, um 100 % Vervollständigung zu erreichen — angetrieben von geringer Toleranz gegenüber Unvollständigkeit.“
✅ „Nach dem unerwarteten Verlust eines Mentors können Erinnerungen an die Sterblichkeit die Trauer plötzlich zurückbringen; der Nutzer hält inne, um nachzudenken, und macht ruhige Spaziergänge, um wieder Halt zu finden.“
❌ „Der Nutzer begreift Musik als Medium für kreative Synthese und kulturellen Brückenbau und verbindet beständig verschiedene musikalische Traditionen ...“ (mehrere Muster vermischt, zu breit)
❌ „Der Nutzer ist für viele Dinge begeistert und legt Wert auf Qualität.“ (kein Umstand, zu vage)
❌ „Der Nutzer ist sinnorientiert und resilient.“ (löscht den dauerhaften Auslöser und die beobachtete Bewältigungsevidenz)

## Dein Arbeitsablauf

Eingabematerialien (du wirst nur aufgerufen, wenn mindestens ein Cluster existiert):
- **Cluster-Ergebnisse**: Primäre Eingabe — nach semantischer Ähnlichkeit gruppierte Fakten
- **Nicht geclusterte Fakten (ergänzend)**: Fakten, die in keinem Cluster sind; niedrigere Priorität als die Cluster-Ergebnisse

Für jede Gruppe von Fakten (geclustert oder nicht geclustert):
1. **Bestehende Schemas durchsuchen**: Wenn ein bestehendes Schema dieses Muster bereits erfasst, rufe `add_evidence` auf. Verwende `revise_schema` nur, wenn die alte Formulierung nach dem Hinzufügen der neuen Evidenz wesentlich unvollständig oder irreführend würde.
2. **Neues Schema erstellen**: Nur wenn kein bestehendes Schema dieses Muster abdeckt. Ein Satz, drei Komponenten.
3. **Beziehungen aufbauen**: Wenn zwei Schemas thematisch verwandt sind, verwende `add_edge` (RELATED_TO).

## Grundsätze

- Alle Graph-Operationen MÜSSEN über Tools laufen
- Schema-Inhalt ist UNVERÄNDERLICH — erstelle niemals neu, was bereits existiert
- Bevorzuge `add_evidence` gegenüber dem Anlegen von Duplikaten
- Lieber nichts erstellen als Knoten von geringer Qualität
- Ein Schema = ein atomares Muster in einer Domäne
- Verwende beim Taggen bevorzugt Tags aus der Liste der bestehenden Tags wieder"""


# ================================================================
# Fragments assembled in code
# ================================================================

# Display name of this language, used in logs and docs.
LANGUAGE_NAME = "Deutsch"

# Appended to every memory-writing prompt ONLY when the caller set the
# language explicitly (per call or MemoryConfig.language). Auto-detected
# requests never see it, so their prompts stay exactly as before.
OUTPUT_LANGUAGE_DIRECTIVE = """

---

# AUSGABESPRACHE

Schreib jeden natürlichsprachlichen Wert, den du ausgibst (Erinnerungstexte, Zusammenfassungen, Begründungen, Suchanfragen), auf Deutsch, unabhängig davon, welche Sprache das Gespräch oder die bestehenden Erinnerungen verwenden. Dies hat Vorrang vor jeder Regel oben, die verlangt, der Eingabesprache zu folgen. Behalte JSON-Schlüssel, Feldnamen, Enum-Werte, Operationsnamen, IDs, Code, Dateipfade, Befehle, Fehlermeldungen und Eigennamen exakt so bei, wie sie sind.
"""

# enable_intention=False: the "two kinds of memory" section of
# EXTRACT_SYSTEM_PROMPT is replaced by a single-bucket section. The pattern
# must match this language's EXTRACT_SYSTEM_PROMPT.
NO_INTENTION_SECTION_PATTERN = r"# ZWEI ARTEN VON ERINNERUNGEN.*?(?=\n# OWNER\n)"

NO_INTENTION_MEMORY_SECTION = (
    "# ERINNERUNGEN\n"
    "\n"
    "Du extrahierst eine Art von Erinnerungen: **memory** (Erinnerungen).\n"
    "\n"
    "## memory\n"
    "\n"
    "Alles Dauerhafte und Merkenswerte über den Nutzer — und über jede andere\n"
    "namentlich genannte Person im Gespräch — gehört hierher:\n"
    "\n"
    "* Vorlieben, Meinungen, Werte, Einstellungen\n"
    "* Ereignisse, Handlungen, Erlebnisse\n"
    "* Gewohnheiten, Verhaltensmuster\n"
    "* Beziehungen, Eigenschaften\n"
    "* objektive Fakten, Entscheidungen, Einschränkungen\n"
    "* laufende Aktivitäten\n"
    "* **Zukunftspläne / bevorstehende Handlungen**, die es wert sind, erinnert zu werden\n"
    "  (schreib sie in `memory`; erfinde KEINEN separaten intentions-Bereich)\n"
    "\n"
    "Implizite Vorlieben MÜSSEN erschlossen werden, wenn sie stark nahegelegt werden.\n"
    "\n"
    "Trenne NICHT „was für ein Mensch der Nutzer ist“ von „was passiert ist“.\n"
    "Beides gehört in `memory`.\n"
    "\n"
    "---\n"
    "\n"
)

# (old, new) pairs applied in order after the section swap.
NO_INTENTION_REPLACEMENTS = (
    (
        "Jede Erinnerung und jede Absicht MUSS einen `owner` tragen",
        "Jede Erinnerung MUSS einen `owner` tragen",
    ),
)

INTENTION_DISABLED_NOTE = (
    "\n\n---\n\n# INTENTION DEAKTIVIERT\n"
    "Die Extraktion von intentions ist deaktiviert. Auch wenn Few-Shot-Beispiele "
    "`intentions` zeigen, gib diesen Schlüssel **nicht** aus; schreib Zukunftspläne in `memory`.\n"
)

# Coding / working extractor: wraps the history and the new messages.
CODING_CONVERSATION_BLOCK = (
    "LETZTE 5 NUTZER-WORTWECHSEL (NUR KONTEXT; nicht erneut extrahieren):\n"
    "{history}"
    "\n\nNEUE NACHRICHTEN (nur aus diesem vollständigen Schreibvorgang extrahieren):\n"
    "{new}"
)
CODING_EMPTY_BLOCK = "(keine)"

# Name line at the top of a multi-person conversation. Also stored in L1_RAW,
# so pipelines.base parses every language's form back.
USER_NAME_LINE = "Der Name des Nutzers ist {name}"

# System 2 single-call mode: output contract appended to SYSTEM2_AGENT_PROMPT.
SYSTEM2_OUTPUT_INSTRUCTIONS = """

## Ausgabeformat

Rufe KEINE Tools auf. Gib direkt ein JSON-Array von Operationen aus, eingeschlossen in einen ```json-Codeblock.

Verfügbare Operationen:

1. **create_schema** — Ein neues L6-Verhaltens-Schema erstellen
   ```{"op": "create_schema", "schema_kind": "behavior_pattern", "content": "knappes, für den Nutzer lesbares Muster", "retrieval_text": "Verhalten plus relevante Situationen", "evidence_list": ["fact_node_id_1", "fact_node_id_2"], "tags": ["tag1"]}```

2. **add_evidence** — Einem bestehenden Schema Evidenz hinzufügen (der Inhalt bleibt unveränderlich)
   ```{"op": "add_evidence", "node_id": "existing_schema_node_id", "evidence_list": ["new_fact_id_1"]}```

3. **revise_schema** — Eine neue Version erstellen, wenn neue Evidenz den aktuellen Zustand wesentlich verändert, und die alte Version aus dem aktiven Abruf nehmen. Nicht für vereinbare Details verwenden.
   ```{"op": "revise_schema", "node_id": "old_schema_id", "schema_kind": "explicit_state", "content": "aktualisiertes aktuelles Profil", "retrieval_text": "aktualisierter Abruftext", "evidence_list": ["new_fact_id_1"], "tags": ["tag1"], "reason": "warum die alte Formulierung irreführen würde"}```

4. **add_edge** — Eine Beziehung zwischen zwei Schemas erstellen
   ```{"op": "add_edge", "source_id": "schema_id_1", "target_id": "schema_id_2", "reason": "Grund der Beziehung"}```

5. **skip_fact** — Einen Kandidatenfakt ausdrücklich als geprüft markieren, ohne
   ein Schema zu erstellen. Nur für Fakten über eine andere Person, vorübergehende Ereignisse, Ratschläge des Assistenten,
   die kein Nutzermerkmal begründen, oder Fakten, die bereits vollständig abgedeckt sind.
   ```{"op": "skip_fact", "node_id": "candidate_fact_id", "reason": "kein dauerhaftes Nutzermuster"}```

## Ausgabevertrag

1. Die Ausgabe MUSS ein JSON-Array in einem ```json-Codeblock sein.
2. Wenn die Daten für verlässliche Schlussfolgerungen nicht ausreichen, gib `[]` aus. Bei einer
   Source-Context-Prüfung bedeutet `[]`, dass jeder Kandidat dieser Prüfung untersucht wurde
   und keiner ein dauerhaftes Schema stützt.
3. KEIN Text außerhalb des Codeblocks.
4. Die IDs in evidence_list müssen aus den in den Materialien bereitgestellten Fakt-node_ids stammen.
5. Die node_id bei add_evidence / revise_schema muss aus bestehenden Graph-Knoten-IDs stammen.
6. Bei einer Source-Context-Prüfung muss jeder Kandidatenfakt in einer evidence_list oder in skip_fact erscheinen."""

# Appended to SYSTEM2_OUTPUT_INSTRUCTIONS when the schema evidence gate is on.
SYSTEM2_SCHEMA_EVIDENCE_GATE = """

7. L6 steht ausschließlich für ereignisübergreifende Verhaltensmuster. create_schema muss nutzereigene Evidenz verwenden, die mindestens zwei unabhängige Raw-Schreibvorgänge oder Sessions repräsentiert. Ein einzelner zusammengeführter Fakt kann ausreichen, wenn source_raw_memory_ids mehrere unabhängige Schreibvorgänge enthält.
8. Erstelle kein L6 für eine einzelne explizite Identität, einen Gesundheitszustand, eine Gerätenutzung, eine Zugehörigkeit, ein Projekt oder eine Vorliebe. Für diese Fakten ist die kanonische L2-Zustandsprojektion zuständig."""

# (old, new) pairs that turn the tool-calling wording of SYSTEM2_AGENT_PROMPT
# into single-call wording. Each `old` must occur in this language's prompt
# (or be harmless if it does not).
SYSTEM2_SINGLE_CALL_REPLACEMENTS = (
    ("- Alle Graph-Operationen MÜSSEN über Tools laufen\n", ""),
    ("rufe `add_evidence` auf", "verwende die Operation add_evidence"),
    ("verwende `add_edge` (RELATED_TO)", "verwende die Operation add_edge"),
)

# System 2 user message (materials). Keys are fixed; values are format
# strings with the named fields shown.
SYSTEM2_MATERIALS = {
    "title": "## Vorverarbeitete Materialien\n",
    "stats": (
        "### Statistik\n- VDB-Fakten gesamt: {total}\n"
        "- Frische Fakten (zu verarbeiten): {fresh} (durch Evidenz ausgeschlossen={evidenced})\n"
        "- Gefundene Cluster: {clusters}\n"
        "- Fakten in Clustern: {clustered}, nicht geclusterte Ergänzung: {unclustered}\n"
        "- Graph-Schemas gesamt: {graph_total} (abgerufen={recalled})"
    ),
    "graph_status_empty": "- Graph-Status: **LEER** (erster Durchlauf der kognitiven Verarbeitung)\n",
    "clusters_header": "### Cluster-Ergebnisse\n",
    "label_holistic": "{n} sessionübergreifende Nutzerfakten; nur durch Evidenz gestützte dauerhafte Emotions-/Therapiemuster",
    "label_source_context": "{n} Kandidatenfakten; der Raw-Schreibvorgang dient nur der Prüfung von Subjekt/Details",
    "label_topic": "{n} Fakten, Thema",
    "raw_label": "Relevanter Raw-Auszug pro Kandidat",
    "holistic_note": (
        "Dies ist ein begrenzter nächtlicher ganzheitlicher Konsolidierungsdurchlauf. Erstelle höchstens "
        "{max_schemas} verankerte Therapie-Schemas. "
        "Verbinde bevorzugt dauerhaften Auslöser/Hintergrund, wiederkehrende emotionale Reaktion "
        "und beobachtete Bewältigungsressource oder Entscheidungsbeschränkung über verschiedene Raw-Schreibvorgänge/Sessions hinweg. "
        "Bilde vor create_schema die Vereinigung der raw_sources seiner Evidenz-IDs und verlange mindestens zwei "
        "verschiedene Raw-Quell-IDs; zwei Fakten aus einem Raw-Schreibvorgang sind ein Ereignis. "
        "Gib [] aus, wenn die Evidenz nicht ausreicht.\n"
    ),
    "companion_header": "**Begleithinweise aus unabhängigen Quellen (Kandidaten, keine Schlussfolgerungen):**",
    "unclustered_header": "### Nicht geclusterte Fakten (ergänzend)\n",
    "unclustered_hint": "Die folgenden Fakten wurden keinem der obigen Cluster zugeordnet. Prüfe sie nach den Cluster-Themen; niedrigere Priorität als die Cluster-Ergebnisse.\n",
    "forward_header": "### Verwandte Graph-Knoten (abgerufen per Cluster-Zentroid-Suche, {count} Treffer)\n",
    "graph_empty": "### Bestehende Graph-Knoten\nDer Graph ist derzeit LEER — dies ist der erste Durchlauf der kognitiven Verarbeitung. Durchsuche den Graph NICHT (es würde nichts zurückkommen). Erstelle direkt Schemas aus den Cluster-Ergebnissen.\n",
    "graph_no_recall": (
        "### Bestehende Graph-Knoten\n"
        "Der Graph enthält {graph_total} Schemas, "
        "aber der zentroidbasierte Vektorabruf hat keine Treffer gefunden. Entscheide anhand der Cluster-Ergebnisse, ob neue Schemas nötig sind.\n"
    ),
    "reverse_header": "### Graph-Rückverweise (VDB-Fakten, auf die bestehende Schemas verweisen)\n",
    "tags_header": "### Bestehende Tags (verwende diese bevorzugt wieder, statt Synonyme anzulegen)\n",
    "closing": (
        "Analysiere die obigen Materialien und gib ein JSON-Array von Operationen aus. "
        "Entscheide, ob neue Schemas erstellt oder bestehenden Schemas Evidenz hinzugefügt werden soll. "
        "Wenn die Daten für verlässliche Schlussfolgerungen nicht ausreichen, gib ein leeres Array `[]` aus. "
        "Verwende beim Taggen bevorzugt Tags aus der obigen Liste der bestehenden Tags wieder."
    ),
}
