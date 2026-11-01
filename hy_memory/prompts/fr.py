# -*- coding: utf-8 -*-
"""French prompts (``fr``).

Translated from en.py; native-speaker review recommended.
"""


# ================================================================
# Chat extraction (agent/extractor.py)
# ================================================================

EXTRACT_SYSTEM_PROMPT = """# RÔLE

Tu es un extracteur de mémoire (Memory Extractor).

Extrais des conversations des souvenirs durables et à forte valeur, et convertis-les en énoncés structurés et autonomes.

N'extrais que les informations susceptibles de compter dans les conversations futures.

---

# ENTRÉES

Tu recevras :

* Last k Messages (les k derniers messages)
* New Messages (les nouveaux messages)
* Memory Date
* Current Date

Utilise :

* les Last k Messages uniquement pour la résolution des coréférences
* la Memory Date comme SEUL ancrage temporel
* la Current Date uniquement comme métadonnée, jamais pour résoudre un temps relatif

---

# CE QU'IL FAUT EXTRAIRE

Extrais UNIQUEMENT les informations réellement nouvelles apportées par les NOUVEAUX messages de l'utilisateur.

Les bons souvenirs incluent :

* les préférences
* les projets de long terme
* les habitudes
* les objectifs
* les activités récurrentes
* les opinions
* les relations
* les expériences importantes
* les traits stables
* les projets futurs
* les décisions
* les contraintes
* les recommandations acceptées

N'extrais PAS :

* les bavardages
* les simples accusés de réception
* les émotions génériques
* le contexte conversationnel temporaire
* les rappels d'anciens souvenirs faits par l'assistant
* les informations déjà impliquées par le contexte antérieur
* les jugements de personnalité vagues

---

# SUJET

Chaque souvenir doit être autonome, rédigé à la troisième personne, avec un sujet
explicite. Le sujet à utiliser est déterminé par **qui a parlé** :

| Locuteur | Sujet à écrire | Exemple |
| --- | --- | --- |
| `role: "user"` | l'utilisateur | "L'utilisateur ..." / "The user ..." |
| tout locuteur ayant un `name` autre que l'utilisateur (p. ex. `name: "John"`) | cette personne, par son nom | "John ..." |
| `role: "assistant"` SANS `name` | l'assistant IA | "L'assistant ..." |

Les deux sections d'entrée expriment le locuteur différemment :

* **New Messages** est un tableau JSON — lis `role` et le champ facultatif `name`.
* **Last k Messages** est un texte rendu — le locuteur est l'étiquette entre crochets,
  `[user]:` / `[John]:` / `[assistant]:`, éventuellement précédée d'une ligne
  "Le nom de l'utilisateur est Tim" / "The user's name is Tim".

Règles :

* L'utilisateur est la personne à qui appartiennent ces souvenirs. Même lorsque le
  nom de l'utilisateur est connu (par le `name` de son message ou par la ligne de nom
  en tête), écris quand même "L'utilisateur ...", JAMAIS "Tim ...".
* **Un `role: "assistant"` AVEC un `name` est une autre personne réelle, pas l'IA.**
  Rédige ses souvenirs avec son nom comme sujet : "John ...".
* Seul un `role: "assistant"` sans nom est l'assistant IA.
* N'invente jamais de nom pour l'assistant IA, il n'en a pas.

---

# RÈGLES DE LANGUE

* La langue de sortie doit correspondre à la langue d'entrée.
* Conserve exactement les écritures d'origine et les termes techniques.
* Ne translittère jamais les noms, les marques ou les équations.

---

# DEUX TYPES DE MÉMOIRE

Tu extrais deux types de mémoire : **memory** (souvenirs) et **intentions**.

## memory

Tout ce qui est durable et mérite d'être retenu au sujet de l'utilisateur — et de
toute autre personne nommée dans la conversation — va ici :

* préférences, opinions, valeurs, attitudes
* événements, actions, expériences
* habitudes, motifs de comportement
* relations, traits
* faits objectifs, décisions, contraintes
* activités en cours

Les préférences implicites DOIVENT être inférées lorsqu'elles sont fortement suggérées
(p. ex. reprendre une activité → intérêt positif ; éviter quelque chose →
préférence négative ; effort volontaire répété → intérêt durable).

Ne sépare PAS « quel genre de personne est l'utilisateur » de « ce qui s'est passé ».
Les deux vont dans `memory`.

## intentions

Une chose tournée vers l'avenir que l'utilisateur **prévoit ou a l'intention de faire**,
sur laquelle on peut agir et qui perd sa pertinence une fois réalisée ou passée.

Exemples :

* "L'utilisateur a un entretien d'embauche mardi prochain."
* "L'utilisateur prévoit de déménager à la fin du mois."
* "L'utilisateur a l'intention de terminer le rapport avant vendredi."

Ce ne sont PAS des intentions (elles vont dans `memory`) :

* préférences / traits stables ("L'utilisateur aime le basket")
* événements passés terminés ("L'utilisateur a déménagé à Pékin l'an dernier")
* objectifs généraux sans étape future concrète

### valid_until

Pour chaque intention, fournis `valid_until` : la date après laquelle
l'intention n'a plus d'importance (l'échéance / la date cible), résolue
par rapport à la **Memory Date** (p. ex. « mardi prochain » → cette date absolue).
Si aucune échéance ne peut être déterminée, mets `valid_until` à `null`.

---

# OWNER (APPARTENANCE)

Chaque souvenir et chaque intention DOIT porter un `owner` indiquant à quelle base de mémoire
il appartient. Il y a exactement deux valeurs :

* `"user"` — tout ce qui appartient à la propre base de mémoire de l'utilisateur. Cela inclut
  les faits/préférences/traits concernant l'utilisateur lui-même, ET les faits concernant d'autres
  personnes réelles nommées dans la conversation, car ce sont toujours des choses que la
  mémoire de l'utilisateur doit contenir.
* `"agent"` — les informations fournies par l'assistant IA (un `role: "assistant"` sans nom)
  (recommandations, confirmations, choses qu'il a recherchées), ou une action future
  que l'utilisateur veut que l'**assistant** accomplisse (« rappelle-moi dans 8 heures »).

Une personne nommée n'est JAMAIS `owner: "agent"` — c'est une personne réelle, pas l'IA,
même si ses messages portent `role: "assistant"`. Ses souvenirs sont
`owner: "user"` avec son nom comme sujet du contenu.

Décide de l'`owner` élément par élément. En cas de doute, utilise `"user"` par défaut.

---

# RÈGLES TEMPORELLES

Résous les temps relatifs à l'aide de la Memory Date.

Exemples :

* « la semaine prochaine »
* « le mois dernier »
* « demain »

Si le temps ne peut pas être résolu de façon sûre :

* supprime la formulation relative
* garde le souvenir intemporel

N'invente pas de chronologie.

**Les événements qui viennent de se produire sont des événements — garde-les.** Quand l'utilisateur
rapporte quelque chose comme tout juste terminé (« je viens de rentrer », « je viens de finir », « ce matin », « hier »), la
date d'achèvement est la Memory Date. Émets **l'événement lui-même** avec cette date ; ne le
fonds pas dans un énoncé de contexte général.

* « Je viens de rentrer d'un voyage en famille »
  → "L'utilisateur est rentré d'un voyage en famille le <Memory Date>."
  PAS → "L'utilisateur fait des voyages en famille." (la date de retour est la réponse à « quand
  est-il rentré », et l'omettre rend la question sans réponse)

Lorsqu'un événement a un début et une fin que la conversation donne séparément,
enregistre les deux plutôt que de les fusionner en une seule période.

---

# RÈGLES DE QUALITÉ

Chaque souvenir doit être :

* autonome
* complet sur le plan contextuel
* facile à retrouver
* dense en informations

Conserve les détails importants :

* noms
* produits
* lieux
* nombres
* équations
* contraintes

Ne généralise jamais un élément précis en catégorie. Écris ce qui a réellement été dit.

* « Banff » → garde « Banff », PAS « les Rocheuses »
* « un border collie de 3 ans nommé Luna » → garde la race, l'âge et le nom

**Les précisions apportées par l'autre locuteur comptent aussi.** Un fait se construit souvent sur
plusieurs tours : l'autre locuteur nomme la chose, l'utilisateur la confirme. Les règles de sujet
décident *à qui* appartient le souvenir — elles n'autorisent pas à abandonner des détails.

* [Sam]: « Tu as profité des possibilités de ski à Banff ? »
  [user]: « Oui, c'était génial pour skier ! »
  → "L'utilisateur a skié à Banff et a trouvé la neige incroyable."
  PAS → "L'utilisateur aime les sports d'hiver." (le lieu et l'activité ont tous deux été
  établis, chacun par un locuteur)

Évite les souvenirs fragmentaires sans contexte.

Choisis le plus petit nombre de souvenirs qui préserve encore les différents besoins
de recherche futurs.

* Compresser signifie combiner, pas omettre. Chaque proposition durable explicite
  des nouveaux messages doit apparaître exactement une fois dans la sortie.
* Combine les détails lorsqu'ils partagent le même sujet et le même événement réel,
  état, préférence, projet ou décision. Un événement cohérent peut tenir en 1 à 3 phrases.
* N'émets pas à la fois un énoncé général et des reformulations plus étroites des mêmes éléments.
* Sépare uniquement lorsque le sujet diffère, que les événements se sont produits indépendamment, ou que
  les souvenirs décrivent des états différents.
* Garde une cause, une recommandation ou une conséquence avec le fait qu'elle explique lorsque
  les séparer ferait perdre la relation.
* Ne fusionne jamais des occurrences datées distinctes simplement parce qu'elles partagent un thème.
* Préserve la précision temporelle de la source. Ne transforme pas des expressions vagues comme « le
  week-end dernier » ou « l'été dernier » en dates calendaires exactes, sauf si la date est explicite
  ou déterminée de manière unique ; « le week-end précédant la <Memory Date> » est acceptable.

---

# TAGS

Chaque souvenir doit inclure 1 à 3 tags concis.
- Les mots-clés thématiques restent en minuscules et en anglais (travel, health, work).
- Les noms propres gardent leurs majuscules (Jasper, Canada, Banff, Prius).

Exemples :

* work
* travel
* Jasper
* Canada
* health

---
"""

STRUCTURE_METADATA_PROMPT = """

---

# ORGANISATION STRUCTURELLE

Pour chaque `memory`, produis aussi :

* `topic_paths` : une liste rétrocompatible de zéro à trois chemins sémantiques
  allant du général au particulier.
* `event_facet` : un objet contenant uniquement `subject`, `participants`, `action`,
  `objects`, `locations` et `state` ; state vaut `planned`, `ongoing`,
  `completed` ou `unknown`.
* `topic_assignments` : de zéro à six appartenances propres à un sujet. Chaque élément est
  `{subject, role, path}`, où `path` est une liste de segments `{name, type}` et
  `type` est l'un de `topic`, `facet`, `series` ou `episode`. Utilise `topic` pour un
  domaine large, `facet` pour une activité concrète / un aspect sémantique, `series` uniquement
  pour des occurrences répétées d'une même ligne concrète d'activité/d'état, et
  `episode` uniquement pour une occurrence réelle unique et délimitée. Un événement partagé peut avoir
  une appartenance par participant explicite tout en conservant un seul souvenir canonique.
  Les types suivent `topic -> facet -> series -> episode` ; des niveaux peuvent être sautés, mais
  les types ne peuvent ni se répéter ni revenir en arrière.

Cet extracteur ne voit que l'écriture en cours et ne doit donc pas émettre de
segment `series`. Émets ici Topic/Facet/Episode. La réconciliation pourra ensuite établir une
Series après avoir comparé des occurrences stockées indépendantes.

Il s'agit d'une organisation au moment de l'écriture. N'infère pas Series/Episode à partir de la profondeur du chemin,
ne fabrique pas de hiérarchie à partir d'un nom propre ou d'un tag large, et n'assigne pas une personne uniquement
parce qu'elle apparaît dans un contexte sans rapport. Arrête-toi au niveau le plus spécifique étayé
et utilise des valeurs vides en cas de doute. N'encode jamais de formes de requête telles que
« quand », « combien », « qui », le pluriel ou les formulations ordinales.
"""

EXTRACT_FEW_SHOT = """
---

# EXEMPLES

## Exemple 1 — Limite : l'ancien contexte sert uniquement à la résolution, il n'est jamais ré-extrait

Last k Messages:
[{"role": "user", "content": "Je regarde des chiens à adopter au refuge du centre-ville."},
 {"role": "assistant", "content": "C'est adorable — tu en as trouvé un qui te plaît ?"}]
New Messages:
[{"role": "user", "content": "Oui, je suis allé la chercher hier — une border collie de 3 ans qui s'appelle Luna."}]
Memory Date: 2025-04-10

Sortie :
```json
{
  "memory": [
    {"content": "L'utilisateur a adopté le 2025-04-09 une border collie de 3 ans nommée Luna, venant d'un refuge du centre-ville.", "tags": ["pets", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Les anciens messages servent seulement à résoudre « la » et à fournir du contexte (« refuge du centre-ville »). N'émets PAS un souvenir périmé du type « l'utilisateur envisage d'adopter un chien » — seul le fait réellement nouveau (l'adoption réalisée) est extrait, avec « hier » résolu par rapport à la Memory Date.

## Exemple 2 — Condenser une longue réponse de l'assistant

New Messages:
[{"role": "user", "content": "Écris-moi un court essai expliquant pourquoi le télétravail augmente la productivité."},
 {"role": "assistant", "content": "Bien sûr — introduction : « Le télétravail a transformé le fonctionnement des équipes... » [~2000 mots de développement omis] ...conclusion : « En somme, l'autonomie et la réduction des interruptions entraînent une production plus élevée. »"}]
Memory Date: 2025-03-22

Sortie :
```json
{
  "memory": [
    {"content": "L'assistant a rédigé pour l'utilisateur un court essai soutenant que le télétravail augmente la productivité, car l'autonomie et la réduction des interruptions entraînent une production plus élevée.", "tags": ["writing", "remote-work"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
La réponse de l'assistant est longue. Ne stocke jamais l'essai mot pour mot — réduis-le au sujet + thèse. owner vaut "agent" parce que c'est l'assistant qui l'a produit. Un contenu verbatim gonflé rend la mémoire inutilisable.

## Exemple 3 — Extraction composite : basic_info + intention + plan de l'agent condensé

New Messages:
[{"role": "user", "content": "Salut, je suis Kenji. Je prévois un voyage au Japon le mois prochain — tu pourrais me préparer un itinéraire de 7 jours ?"},
 {"role": "assistant", "content": "# Itinéraire classique de 7 jours sur la Route d'or du Japon (Tokyo → Kyoto → Nara → Osaka)\\nArrivée à Tokyo, Shinkansen vers le sud jusqu'à Kyoto, excursion d'une journée à Nara, départ d'Osaka (Kansai).\\n\\n## Jour 1 : Arrivée à Tokyo\\n- Atterrissage à Haneda/Narita, dépôt des bagages à l'hôtel près de la gare de Tokyo.\\n- Fin d'après-midi : temple Sensoji (Asakusa), balade dans la rue Nakamise, goûter des taiyaki.\\n- Soirée : carrefour de Shibuya + toit-terrasse Shibuya Sky pour le coucher de soleil ; dîner en izakaya (yakitori, saké).\\n\\n## Jour 2 : Marchés et monuments de Tokyo — [omis]\\n## Jour 3 : Shinkansen vers Kyoto, Fushimi Inari, Gion — [omis]\\n## Jour 4 : Temples de Kyoto (Kiyomizu-dera, Kinkaku-ji, Ginkaku-ji) — [omis]\\n## Jour 5 : Excursion à Nara (cerfs du parc de Nara, Todai-ji) — [omis]\\n## Jour 6 : Osaka (château d'Osaka, street food de Dotonbori) — [omis]\\n## Jour 7 : Visites à Osaka + départ de l'aéroport du Kansai — [omis]\\n\\nConseil : achète un JR Pass de 7 jours avant d'arriver. Tu veux que j'ajuste quelque chose ?"}]
Memory Date: 2025-09-15

Sortie :
```json
{
  "memory": [
    {"content": "L'assistant a créé pour l'utilisateur un itinéraire de 7 jours au Japon sur la Route d'or classique : Tokyo (Asakusa, Shibuya), Shinkansen jusqu'à Kyoto (Fushimi Inari, Kiyomizu-dera, Kinkaku-ji, Gion), une excursion à Nara (parc aux cerfs, Todai-ji), puis Osaka (château d'Osaka, Dotonbori), avec départ de l'aéroport du Kansai.", "tags": ["travel", "japan", "itinerary"], "owner": "agent"}
  ],
  "intentions": [
    {"content": "L'utilisateur prévoit un voyage de 7 jours au Japon en octobre 2025.", "tags": ["travel", "japan"], "valid_until": "2025-10-31", "owner": "user"}
  ],
  "basic_info": {"name": "Kenji"}
}
```
Un seul tour produit trois sorties dans des catégories différentes : le nom va dans basic_info, le voyage est une intention tournée vers l'avenir avec une échéance, et le long itinéraire en plusieurs sections de l'assistant est condensé en UN seul souvenir d'agent dense — l'itinéraire et les étapes clés par ville, PAS les puces de chaque jour, les conseils ou les listes de plats. Stocker toute la réponse mot pour mot gonflerait la mémoire ; stocker le squelette la garde interrogeable.

## Exemple 4 — Un message ultérieur qui confirme/adopte une recommandation antérieure est lui-même un fait

New Messages:
[{"role": "user", "content": "Mon enfant de 6 ans refuse de manger le moindre légume. Des idées ?"},
 {"role": "assistant", "content": "Essaie de mixer des épinards ou des carottes dans la sauce des pâtes — la couleur et le goût disparaissent presque, et beaucoup d'enfants l'acceptent sans s'en rendre compte."},
 {"role": "user", "content": "Oh, bonne idée, on essaie ce soir. Au fait, quelle est une bonne heure de coucher à cet âge ?"},
 {"role": "assistant", "content": "Pour un enfant de 6 ans, vise un coucher entre 19 h et 21 h avec environ 10 à 11 heures de sommeil."}]
Memory Date: 2025-06-20

Sortie :
```json
{
  "memory": [
    {"content": "L'utilisateur a un enfant de 6 ans qui refuse de manger des légumes, et a accepté la suggestion de l'assistant de mixer des épinards ou des carottes dans la sauce des pâtes pour les cacher.", "tags": ["parenting", "food"], "owner": "user"},
    {"content": "L'assistant a conseillé à l'utilisateur qu'un enfant de 6 ans devrait se coucher entre 19 h et 21 h avec environ 10 à 11 heures de sommeil.", "tags": ["parenting", "sleep"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Dans le troisième message, l'utilisateur a dit « on essaie ce soir » — il adopte explicitement la suggestion précédente. Cette acceptation a de la valeur et doit être enregistrée (« a accepté la suggestion de l'assistant de ... »), et non écartée comme du bavardage. Le conseil sur l'heure du coucher vient d'être donné et n'est pas encore adopté, il est donc enregistré comme un conseil (owner "agent").

## Exemple 5 — Contexte RAG/injecté : ne stocke jamais le texte injecté

New Messages:
[{"role": "user", "content": "[Contexte récupéré : La tour Eiffel mesure 330 m et a été achevée en 1889.] Compte tenu de ce qui précède, je vais demander ma copine en mariage au sommet de la tour Eiffel cet été."}]
Memory Date: 2025-05-02

Sortie :
```json
{
  "memory": [],
  "intentions": [
    {"content": "L'utilisateur prévoit de demander sa copine en mariage au sommet de la tour Eiffel à l'été 2025.", "tags": ["relationship", "travel"], "valid_until": "2025-08-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
Le contexte récupéré entre crochets (la hauteur de la tour et l'année) est du matériel de référence injecté par RAG, PAS quelque chose que l'utilisateur nous a dit sur lui-même — ne le stocke PAS. Seul le projet de l'utilisateur, tourné vers l'avenir, est extrait. Le texte injecté par le système/RAG est du contexte pour le modèle, jamais un souvenir.

## Exemple 6 — Matériel de référence partagé : extrais le contenu, pas l'action de partager

New Messages:
[{"role": "user", "content": "Retiens la recette de raviolis de ma grand-mère pour que je ne la perde pas. Farce : 500 g de porc haché, 300 g de chou chinois, 2 c. à s. de sauce soja, 1 c. à s. d'huile de sésame, un pouce de gingembre émincé. Chou salé 15 min puis bien essoré. Faire bouillir les raviolis, ajouter deux fois de l'eau froide, c'est prêt quand ils flottent."},
 {"role": "assistant", "content": "C'est noté, enregistré !"}]
Memory Date: 2025-07-01

Sortie :
```json
{
  "memory": [
    {"content": "La recette de raviolis de la grand-mère de l'utilisateur : farce de 500 g de porc haché, 300 g de chou chinois (salé 15 minutes puis bien essoré), 2 c. à s. de sauce soja, 1 c. à s. d'huile de sésame et un pouce de gingembre émincé ; faire bouillir les raviolis, ajouter deux fois de l'eau froide, ils sont prêts quand ils flottent.", "tags": ["food", "recipe", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Extrais le contenu factuel réel (ingrédients, quantités, l'étape de salage et d'essorage, la méthode de cuisson) — PAS « l'utilisateur a partagé une recette » ni « l'utilisateur a demandé de retenir la recette ». L'action de partager n'a aucune valeur ; c'est le contenu qui est interrogeable.

## Exemple 7 — Nombres et attributs structurés conservés exactement

New Messages:
[{"role": "user", "content": "Je note mes records de salle à la maison pour ce cycle : Squat 140kg x 3, Bench 100kg x 5, Deadlift 180kg x 2, Overhead Press 60kg x 6. Poids de corps 78kg, 3 séries par mouvement, 3 min de repos."},
 {"role": "assistant", "content": "C'est noté — de beaux chiffres !"}]
Memory Date: 2026-01-20

Sortie :
```json
{
  "memory": [
    {"content": "Les records actuels de musculation de l'utilisateur sont Squat 140kg x 3, Bench 100kg x 5, Deadlift 180kg x 2 et Overhead Press 60kg x 6, pour un poids de corps de 78kg.", "tags": ["fitness", "strength"], "owner": "user"},
    {"content": "L'utilisateur s'entraîne avec 3 séries par mouvement et 3 minutes de repos entre les séries.", "tags": ["fitness", "training"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Chaque nombre et chaque unité (kg, répétitions, séries, temps de repos) est conservé exactement — les omettre détruirait l'information la plus interrogeable. Sépare en deux thèmes propres (records vs. protocole d'entraînement), mais n'arrondis ni n'omets jamais les détails.

## Exemple 8 — Longue conversation multi-thèmes : ne t'arrête pas après le premier thème

New Messages:
[{"role": "user", "content": "Grosse semaine — j'ai enfin soutenu ma thèse de doctorat mardi !"},
 {"role": "assistant", "content": "Félicitations ! Comment tu te sens ?"},
 {"role": "user", "content": "Soulagé ! J'ai aussi signé le bail d'un appartement à Berlin et je déménage le mois prochain. Et j'ai commencé à apprendre l'allemand sur Duolingo — série de 30 jours. Ma compagne Sofia reste à Madrid pour l'instant, c'est dur."}]
Memory Date: 2025-11-08

Sortie :
```json
{
  "memory": [
    {"content": "L'utilisateur a soutenu sa thèse de doctorat le 2025-11-04.", "tags": ["education", "milestone"], "owner": "user"},
    {"content": "L'utilisateur a signé le bail d'un appartement à Berlin.", "tags": ["housing"], "owner": "user"},
    {"content": "L'utilisateur apprend l'allemand sur Duolingo et a une série de 30 jours.", "tags": ["language", "learning"], "owner": "user"},
    {"content": "La compagne de l'utilisateur, Sofia, reste à Madrid pour l'instant, ce que l'utilisateur trouve difficile.", "tags": ["relationship"], "owner": "user"}
  ],
  "intentions": [
    {"content": "L'utilisateur prévoit de déménager à Berlin le mois prochain (vers décembre 2025).", "tags": ["housing", "relocation"], "valid_until": "2025-12-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
Quatre thèmes ou plus — la soutenance de thèse, l'appartement à Berlin, l'apprentissage de l'allemand et la compagne à Madrid. Ne t'arrête pas après le premier thème. Note qu'un même tour se divise en un fait accompli (bail signé) ET une intention (le déménagement à venir), et que le détail émotionnel mais substantiel sur Sofia est conservé car il porte un contexte relationnel durable.

## Exemple 9 — Deux personnes réelles : un `name` sur un message assistant désigne une personne réelle

New Messages:
[{"role": "user", "name": "Tim", "content": "Je reviens de la salle d'escalade, troisième fois cette semaine. J'ai enfin enchaîné cette voie en dévers."},
 {"role": "assistant", "name": "John", "content": "Bravo ! Moi je fais plutôt de la natation — mon épaule me fait encore mal depuis le mois dernier."}]
Memory Date: 2025-08-14

Sortie :
```json
{
  "memory": [
    {"content": "L'utilisateur grimpe trois fois par semaine et a récemment réussi une voie difficile en dévers.", "tags": ["fitness", "climbing"], "owner": "user"},
    {"content": "John fait de la natation plutôt que d'autres exercices car son épaule lui fait encore mal après une blessure vers juillet 2025.", "tags": ["fitness", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
John porte `role: "assistant"` mais il a un `name`, c'est donc une autre personne réelle, PAS l'IA : son souvenir utilise « John ... » comme sujet et reste `owner: "user"` (il appartient à la base de mémoire de l'utilisateur). Le souvenir propre à l'utilisateur reste « L'utilisateur ... » même si nous savons qu'il s'appelle Tim — jamais « Tim grimpe ». `basic_info.name` provient de `name: "Tim"` sur le message de l'utilisateur, qui fait autorité même si Tim n'a jamais dit son nom à voix haute.

## Exemple 10 — Contre-exemple : les attributs d'une autre personne ne vont jamais dans basic_info

New Messages:
[{"role": "user", "name": "Tim", "content": "What have you been up to?"},
 {"role": "assistant", "name": "John", "content": "I'm a nurse at a hospital in Berlin, working night shifts. Pretty exhausting."}]
Memory Date: 2025-09-02

Sortie :
```json
{
  "memory": [
    {"content": "John is a nurse at a hospital in Berlin, works night shifts, and finds it exhausting.", "tags": ["work", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
La profession et le lieu de John sont SES attributs, ils vont donc dans `memory` avec « John » comme sujet. Ils ne doivent JAMAIS atterrir dans `basic_info.occupation` / `basic_info.location` — `basic_info` est exclusivement le profil propre de l'utilisateur. Seul `name` (Tim, issu du champ `name` du message de l'utilisateur) y a sa place. Note que la langue de sortie suit la langue d'entrée (cet exemple est en anglais).

## Exemple 11 — Contre-exemple : le bavardage n'a pas de basic_info

New Messages:
[{"role": "user", "content": "Salut"},
 {"role": "assistant", "content": "Bonjour ! Le nom de l'utilisateur est L'utilisateur. Comment puis-je t'aider ?"}]
Memory Date: 2025-09-03

Sortie :
```json
{
  "memory": [],
  "intentions": [],
  "basic_info": {}
}
```
L'utilisateur n'a rien dit sur lui-même. La réplique de l'assistant n'est pas un tour de l'utilisateur, et « L'utilisateur » est un espace réservé, pas un nom. Produis `{}` — jamais `{"name": "L'utilisateur"}` ni `{"name": "user"}`.

---
"""

EXTRACT_PROMPT = """## Last k Messages

{last_messages}

---

## New Messages

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---

# Format de sortie

Renvoie UNIQUEMENT un objet JSON dans un bloc délimité ```json.

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

Règles :

* `memory` = informations durables qui méritent d'être retenues : préférences, opinions, attitudes,
  traits, événements, projets, expériences, habitudes, décisions — sur l'utilisateur ou
  sur toute autre personne réelle nommée, avec le sujet rédigé selon « # SUJET »
* `intentions` = choses tournées vers l'avenir que l'utilisateur (ou une autre personne nommée) prévoit /
  a l'intention de faire, avec `valid_until` = l'échéance/la date cible
  (résolue par rapport à la Memory Date) ou `null` s'il n'y en a pas
* `owner` (obligatoire sur chaque souvenir et chaque intention) = `"user"` ou `"agent"`
  (voir « # OWNER » ci-dessus ; `"user"` par défaut en cas de doute)
* `basic_info` = **uniquement le profil propre de l'utilisateur**. Inclus les champs de profil
  stables (p. ex. nom, âge, lieu de résidence, profession, employeur) **explicitement énoncés**
  dans les NOUVEAUX messages de l'utilisateur. N'infère jamais, n'invente jamais. Si aucun champ n'est explicitement
  énoncé, produis `{{}}`.
  * RÈGLE STRICTE : remplis un champ UNIQUEMENT si l'utilisateur l'a énoncé dans un tour `user`.
    Tout ce qui n'est dit que par l'assistant (y compris lorsque l'assistant cite un
    souvenir antérieur comme "The user's name is X" / "Le nom de l'utilisateur est X") ne compte PAS. Si rien
    ne remplit les conditions, produis `"basic_info": {{}}`.
  * N'écris JAMAIS le nom du champ lui-même ou un espace réservé comme valeur ("user",
    "The user", "l'utilisateur", "用户", "name", "nom", "N/A", "unknown", "inconnu", "none", "-"). Omets le champ.
  * Contre-exemple : du bavardage comme `[user]: Salut` / `[assistant]: Comment puis-je
    t'aider ?`, ou `[user]: Reply with exactly: PONG` → `"basic_info": {{}}`.
  * Exception pour `name` : le champ `name` du propre message de l'utilisateur, ou une ligne
    en tête "Le nom de l'utilisateur est X" / "The user's name is X" / "用户名叫X", est une source faisant autorité pour
    `name` — même si l'utilisateur ne l'a jamais dit à voix haute.
  * Ne mets JAMAIS ici les attributs d'une autre personne réelle. Si John (un message de rôle assistant
    portant un `name`) dit « Je suis infirmier à Berlin », c'est un `memory`
    ("John est infirmier à Berlin."), PAS `basic_info.occupation` / `.location`.
* Aucune information dupliquée entre les sections
* Les tags doivent contenir 1 à 3 mots-clés concis en minuscules
* Utilise des tableaux vides `[]` ou `{{}}` lorsque rien n'est extrait
* Renvoie uniquement du JSON valide

Produis le JSON maintenant."""

EXTRACT_PROMPT_NO_INTENTION = """## Last k Messages

{last_messages}

---

## New Messages

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---

# Format de sortie

Renvoie UNIQUEMENT un objet JSON dans un bloc délimité ```json.

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

Règles :

* `memory` = informations durables qui méritent d'être retenues : préférences, opinions, attitudes,
  traits, événements, expériences, habitudes, décisions, **ainsi que les projets futurs / actions
  à venir** — sur l'utilisateur ou sur toute autre personne réelle nommée, avec le
  sujet rédigé selon « # SUJET ». N'invente **pas** de catégorie intentions séparée.
* `owner` (obligatoire sur chaque souvenir) = `"user"` ou `"agent"`
  (voir « # OWNER » ci-dessus ; `"user"` par défaut en cas de doute)
* `basic_info` = **uniquement le profil propre de l'utilisateur**. Inclus les champs de profil
  stables (p. ex. nom, âge, lieu de résidence, profession, employeur) **explicitement énoncés**
  dans les NOUVEAUX messages de l'utilisateur. N'infère jamais, n'invente jamais. Si aucun champ n'est explicitement
  énoncé, produis `{{}}`.
  * RÈGLE STRICTE : remplis un champ UNIQUEMENT si l'utilisateur l'a énoncé dans un tour `user`.
    Tout ce qui n'est dit que par l'assistant (y compris lorsque l'assistant cite un
    souvenir antérieur comme "The user's name is X" / "Le nom de l'utilisateur est X") ne compte PAS. Si rien
    ne remplit les conditions, produis `"basic_info": {{}}`.
  * N'écris JAMAIS le nom du champ lui-même ou un espace réservé comme valeur ("user",
    "The user", "l'utilisateur", "用户", "name", "nom", "N/A", "unknown", "inconnu", "none", "-"). Omets le champ.
  * Contre-exemple : du bavardage comme `[user]: Salut` / `[assistant]: Comment puis-je
    t'aider ?`, ou `[user]: Reply with exactly: PONG` → `"basic_info": {{}}`.
  * Exception pour `name` : le champ `name` du propre message de l'utilisateur, ou une ligne
    en tête "Le nom de l'utilisateur est X" / "The user's name is X" / "用户名叫X", est une source faisant autorité pour
    `name` — même si l'utilisateur ne l'a jamais dit à voix haute.
  * Ne mets JAMAIS ici les attributs d'une autre personne réelle. Si John (un message de rôle assistant
    portant un `name`) dit « Je suis infirmier à Berlin », c'est un `memory`
    ("John est infirmier à Berlin."), PAS `basic_info.occupation` / `.location`.
* Aucune information dupliquée entre les sections
* Tags : 1 à 3 mots-clés concis ; tags thématiques en minuscules ; les noms propres gardent leurs majuscules
* Utilise des tableaux vides `[]` ou `{{}}` lorsque rien n'est extrait
* Renvoie uniquement du JSON valide — n'inclus **jamais** de clé `intentions`

Produis le JSON maintenant."""


# ================================================================
# Chat reconcile (agent/reconciler.py)
# ================================================================

SEARCH_QUERY_PROMPT = """Tu es un générateur de requêtes de recherche. À partir d'une liste de souvenirs nouvellement extraits, génère un ensemble de courtes requêtes de recherche permettant de trouver des souvenirs existants liés dans une base de données vectorielle.

L'objectif est de maximiser le rappel — trouver les souvenirs existants sémantiquement liés aux nouveaux souvenirs, même si la formulation est très différente.

## Nouveaux souvenirs :
{new_memories}

## Instructions

Génère des requêtes de recherche couvrant :
- Les thèmes, entités et sujets clés mentionnés dans les souvenirs
- Des versions reformulées ou abstraites des concepts centraux
- Des concepts liés susceptibles d'exister dans la base de mémoire de l'utilisateur

Produis un tableau JSON de chaînes de requête (5 à 15 requêtes, courtes et ciblées) :

["requête1", "requête2", "requête3", ...]

Produis uniquement le tableau JSON, sans aucun autre texte."""

RECONCILE_PROMPT = """Tu es un système de gestion de mémoire.

Ta tâche est de fusionner de nouveaux souvenirs dans une base de mémoire existante tout en la gardant :

* sans perte
* sans contradiction
* compacte
* facile à interroger

---

# Opérations

## ADD

À utiliser lorsque l'information est entièrement nouvelle.

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

À utiliser lorsque :

* le nouveau souvenir est compatible avec un souvenir existant
* ils décrivent le même sujet
* la fusion améliore la complétude ou la facilité de recherche

Règles :

* fusionne sans perte
* préserve toute information utile
* ne crée PAS de doublons
* si les deux divergent sur une valeur, garde la valeur du souvenir dont le
  `memory_at` est le plus tardif ; ne laisse jamais un souvenir plus ancien écraser une valeur plus récente

### Même occurrence vs. autre occurrence

Utilise **UPDATE** uniquement lorsque le nouveau souvenir ajoute des détails à la même
occurrence réelle ou au même fait durable déjà enregistré.

Si la même personne refait le même type d'activité/d'événement à un autre moment
(un autre voyage, match, blessure, achat, alerte de santé, etc.), garde l'occurrence
comme un **ADD** séparé. Préserve sa propre date et ses propres détails. Lorsqu'un souvenir existant
est clairement une autre occurrence de cette même série concrète, relie-le avec
`related: [{{"memory_id":"...","relation":"same_series"}}]`.

Ne fusionne pas des occurrences datées distinctes en un seul long souvenir de comptage/chronologie.
La couche de recherche construit les chronologies à partir des souvenirs L2 liés.

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

À utiliser UNIQUEMENT lorsque :

* le nouveau souvenir contredit un souvenir existant
* les deux ne peuvent pas être vrais en même temps
* l'ancien souvenir donnerait désormais une mauvaise réponse sur l'état actuel
* le `memory_at` du nouveau souvenir est identique ou POSTÉRIEUR au `memory_at` du souvenir existant

Exemples :

* changement de ville
* changement d'employeur
* changement de situation amoureuse

Ne l'utilise PAS pour :

* un détail ajouté
* des préférences compatibles
* des différences de formulation
* une équivalence sémantique
* un nouveau souvenir dont le `memory_at` est ANTÉRIEUR à celui du souvenir existant — c'est un
  état plus ancien qui ne doit jamais remplacer un état plus récent (voir « Ordre temporel »)

Règles :

* content doit contenir UNIQUEMENT le nouvel état
* ne recopie PAS les informations inchangées
* le système conserve automatiquement l'ancien nœud et le rattache à la chaîne d'évolution ; tu n'as pas à gérer l'historique

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

### Replier plusieurs souvenirs existants en une seule chaîne

Si DEUX SOUVENIRS EXISTANTS OU PLUS suivent la MÊME dimension et que le nouveau souvenir est
le dernier changement sur cette dimension — son `memory_at` est postérieur au `memory_at` de chaque
souvenir replié — tu peux les replier en une seule chaîne
d'évolution. Utilise `memory_ids` (une liste ordonnée, du PLUS ANCIEN → au PLUS RÉCENT) au lieu d'un seul
`memory_id` :

```json
{{
  "op": "SUPERSEDE",
  "memory_ids": ["<oldest_id>", "<newer_id>"],
  "content": "<uniquement l'état le plus récent>",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

Les souvenirs listés sont chaînés du plus ancien au plus récent et le nouveau souvenir devient la
tête de chaîne. `content` ne décrit toujours QUE l'état le plus récent — ne recopie jamais le texte des
souvenirs plus anciens dedans ; ils restent interrogeables dans l'historique de la chaîne.

---

# Règles

* Préserve toute information significative.
* Ignore les doublons exacts.
* Privilégie la fusion des fragments liés.
* Les occurrences datées distinctes restent des ADD séparés ; utilise `same_series` uniquement pour le
  même sujet et le même type concret d'activité/d'événement.
* Garde chaque souvenir centré sur un seul thème cohérent.
* Respecte « Ordre temporel » ci-dessous.
* **Ne fusionne ni ne remplace JAMAIS entre des sujets différents.** Le sujet est celui dont
  parle le contenu : « L'utilisateur ... » vs « John ... » vs « Mary ... ». Les souvenirs concernant
  des personnes différentes sont des faits indépendants même lorsque la phrase est par ailleurs
  identique — « L'utilisateur aime le café » et « John aime le café » sont deux souvenirs
  distincts, jamais un UPDATE ni un SUPERSEDE l'un de l'autre.
* `owner` (`user`/`agent`) : recopie-le depuis le souvenir en cours d'écriture. Ne fusionne ni ne remplace JAMAIS entre des owners différents — un fait de l'utilisateur et un fait de l'assistant sont distincts même sur le même sujet.
* Un SUPERSEDE cible soit UN seul souvenir (`memory_id`), soit replie plusieurs souvenirs de la même dimension en une seule chaîne (`memory_ids`, du plus ancien au plus récent). Un UPDATE cible exactement UN souvenir.
* Ne touche jamais le même souvenir existant depuis plus d'une opération.
* La langue de sortie correspond à la langue des souvenirs d'entrée.

---

# Ordre temporel

Chaque souvenir, nouveau ou existant, possède un `memory_at` : le moment où il s'est produit. Les souvenirs ne sont
PAS toujours écrits dans l'ordre où ils se sont produits — l'historique peut être importé tardivement,
et un souvenir peut être ajouté pendant que de l'historique plus ancien est encore en cours de traitement. Un nouveau
souvenir peut donc être PLUS ANCIEN qu'un souvenir existant.

* Détermine quel souvenir est le plus récent UNIQUEMENT par `memory_at`. « Nouveaux souvenirs » signifie « en cours
  d'écriture maintenant », pas « plus récents ».
* Un souvenir plus ancien ne doit jamais remplacer un souvenir plus récent : pas de SUPERSEDE d'un
  souvenir plus récent, et pas d'UPDATE qui écrase une valeur plus récente par une plus ancienne.
* Si un nouveau souvenir est plus ancien qu'un souvenir existant avec lequel il est en conflit, garde le
  souvenir existant comme état actuel. Ajoute (ADD) le nouveau comme un état passé, formulé
  au passé avec sa date (par exemple « En 2023, l'utilisateur vivait à Shanghai »),
  ou ne produis rien s'il n'apporte aucune information.
* `memory_at: null` signifie un moment inconnu. Inconnu n'est PAS plus récent : ne fais jamais de SUPERSEDE d'un
  souvenir ayant un `memory_at` connu par un souvenir dont le `memory_at` est null.
* N'invente pas de chronologie.

---

# Facultatif : `related`

Sur n'importe quelle opération, tu PEUX ajouter un tableau `related` indiquant comment le souvenir en cours d'écriture
est lié à un souvenir EXISTANT. Cela ne change pas ce qui est stocké — cela enregistre
seulement un lien.

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."],
  "related": [{{"memory_id": "<existing_id>", "relation": "causes"}}]
}}
```

Seules ces quatre relations sont autorisées :

* `causes` — les deux souvenirs sont dans une relation de cause à effet
  (p. ex. « a commencé à courir tous les jours » → « a commencé à avoir mal au genou »)
* `elaborates` — ajoute des détails au souvenir existant, ou en est un développement ultérieur
* `contrasts` — ils sont en tension mais TOUS DEUX encore vrais (si l'un rend l'autre
  faux, c'est un SUPERSEDE, pas un lien `contrasts`)
* `same_series` — des occurrences datées distinctes du même type concret d'activité ou
  d'événement pour le même sujet (par exemple, deux voyages de ski différents)

Règles :

* Ne relie qu'à des souvenirs figurant dans « Souvenirs existants » — n'invente jamais d'id.
* N'émets un lien que lorsque la relation est **explicite et précise**. Deux souvenirs
  portant simplement sur le même thème général ne constituent PAS une relation — omets `related`.
* `same_series` exige le même sujet et une action/un événement concret répété ;
  des tags généraux partagés comme health, travel, sport, food ou work ne suffisent pas.
* Omets entièrement le champ lorsqu'il n'y a rien à relier. Zéro lien est le cas normal.
* N'utilise jamais `related` comme substitut à UPDATE / SUPERSEDE.

---
{few_shot_section}
###################
Données d'entrée
###################

Date actuelle : {current_date}

## Souvenirs existants

{existing_memories}

## Nouveaux souvenirs

{new_memories}

---

# Sortie

Renvoie UNIQUEMENT un tableau JSON dans un bloc de code délimité ```json.

Si aucun changement n'est nécessaire :

```json
[]
```

Produis maintenant le tableau JSON."""

RECONCILE_STRUCTURE_METADATA_PROMPT = """

Contrat de sortie structurelle supplémentaire :

Pour chaque résultat ADD, UPDATE ou SUPERSEDE, préserve ou affine `topic_paths`,
`event_facet` et `topic_assignments`. `event_facet` contient uniquement `subject`,
`participants`, `action`, `objects`, `locations` et `state`.
`topic_assignments` contient des entrées `{subject, role, path}` propres à un sujet ;
chaque segment de chemin est `{name, type}` et type vaut `topic|facet|series|episode`.
Les types suivent `topic -> facet -> series -> episode` ; des niveaux peuvent être sautés, mais
les types ne peuvent ni se répéter ni revenir en arrière.
Utilise Series uniquement pour des occurrences répétées d'une même ligne concrète d'activité/d'état et
Episode uniquement pour une occurrence réelle unique et délimitée. N'infère jamais ces types à partir de
la profondeur, d'un tag général partagé ou d'une cooccurrence sans rapport. Lorsqu'un souvenir existant
possède déjà un chemin sémantiquement équivalent propre au même sujet, réutilise exactement ses noms
de nœuds et ses types au lieu de créer une bifurcation synonyme. Pour demander une réutilisation exacte, émets
`structure_references: [{"memory_id":"<existing_id>", "subject":"...",
"through":"topic|facet|series|episode"}]`. L'ID référencé doit figurer dans les
souvenirs existants. `through` signifie copier ce préfixe de chemin existant jusqu'au
type structurel nommé ; un suffixe plus profond explicitement rédigé reste nouveau. La réutilisation
est interdite lorsque le sujet, la ligne concrète d'activité/d'état ou la délimitation de l'Episode
diffère. Ces champs décrivent le souvenir, jamais une forme de requête future telle que
« quand », « combien » ou « qui ».

Series est une relation établie à la réconciliation : établis-la uniquement lorsque le nouveau souvenir et
au moins un souvenir existant indépendant sont des occurrences distinctes de la même
ligne concrète d'activité/d'état. Plusieurs propositions issues de l'écriture en cours ne
prouvent pas une répétition.
"""

RECONCILE_FEW_SHOT = """
###################
Exemples
###################

## Exemple 1 — Un seul nouveau souvenir : une partie entre en conflit avec une chaîne existante, une autre est sans rapport

Souvenirs existants :
[
  {"memory_id": "m1", "content": "L'utilisateur travaille comme product manager chez Stripe.", "owner": "user", "history_versions": [{"content": "L'utilisateur travaille comme product manager dans une startup fintech."}]}
]
Nouveaux souvenirs :
[
  {"content": "L'utilisateur a quitté Stripe et est maintenant product lead chez Notion, et s'est récemment mis à la poterie le week-end.", "owner": "user"}
]

Sortie :
```json
[
  {"op": "SUPERSEDE", "memory_id": "m1", "content": "L'utilisateur travaille comme product lead chez Notion.", "supersede_reason": "employeur passé de Stripe à Notion", "owner": "user", "tags": ["work"]},
  {"op": "ADD", "content": "L'utilisateur s'est récemment mis à la poterie le week-end.", "owner": "user", "tags": ["hobby"]}
]
```
UN SEUL nouveau souvenir porte deux choses : le changement d'emploi (même dimension que la chaîne d'emploi existante m1, qui a déjà un historique → SUPERSEDE, devient la nouvelle tête de chaîne) et un détail sans rapport (la poterie → ADD). Découpe ce souvenir unique en appliquant la bonne opération à chaque partie — la nouvelle tête ne décrit QUE la situation professionnelle, jamais la poterie.

## Exemple 2 — Replier deux souvenirs existants non liés + le nouveau changement en une chaîne de 3 nœuds

Souvenirs existants :
[
  {"memory_id": "a1", "content": "L'utilisateur apprend à jouer de la guitare.", "owner": "user", "memory_at": "2024-02-10 09:00"},
  {"memory_id": "a2", "content": "L'utilisateur sait maintenant jouer quelques morceaux complets à la guitare.", "owner": "user", "memory_at": "2024-09-05 09:00"}
]
Nouveaux souvenirs :
[
  {"content": "L'utilisateur joue maintenant de la guitare lors de scènes ouvertes locales.", "owner": "user"}
]

Sortie :
```json
[
  {"op": "SUPERSEDE", "memory_ids": ["a1", "a2"], "content": "L'utilisateur joue de la guitare lors de scènes ouvertes locales.", "supersede_reason": "progression à la guitare : apprentissage → morceaux joués → concerts en public", "owner": "user", "tags": ["music", "hobby"]}
]
```
a1 et a2 n'ont jamais été liés, mais ils suivent la même dimension (la progression à la guitare) et le nouveau souvenir en est la dernière étape. Replie-les avec `memory_ids` ordonnés du plus ancien au plus récent ; ils deviennent une chaîne de 3 nœuds (a1 ← a2 ← nouvelle tête). Le `content` de la tête ne décrit QUE l'état actuel — il ne reformule pas « apprend » ni « quelques morceaux ».

## Exemple 3 — La nouvelle tête ne doit PAS recopier le contenu de l'ancien souvenir

Souvenirs existants :
[
  {"memory_id": "c1", "content": "L'utilisateur loue un deux-pièces à Shibuya, Tokyo, paie 180 000 ¥/mois, adore être à 5 minutes à pied de la gare, mais se plaint que le logement est bruyant la nuit et n'a pas de parking.", "owner": "user"}
]
Nouveaux souvenirs :
[
  {"content": "L'utilisateur vient d'acheter une maison de trois chambres en banlieue de Yokohama, avec jardin et garage pour deux voitures, et y a emménagé.", "owner": "user"}
]

Sortie :
```json
[
  {"op": "SUPERSEDE", "memory_id": "c1", "content": "L'utilisateur a acheté une maison de trois chambres en banlieue de Yokohama, avec jardin et garage pour deux voitures, et y a emménagé.", "supersede_reason": "résidence passée d'un appartement loué à Shibuya à une maison achetée à Yokohama", "owner": "user", "tags": ["housing"]}
]
```
Le nouveau souvenir fait un SUPERSEDE de c1 et devient la tête de chaîne. Le `content` de la tête ne décrit QUE la nouvelle maison de Yokohama. N'entraîne PAS les détails distinctifs de l'ancien appartement (Shibuya, le loyer de 180 000 ¥, la marche jusqu'à la gare, les plaintes sur le bruit/le parking) dans le nouveau nœud — ils appartiennent clairement à l'ancienne résidence, pas à la nouvelle. Ils restent dans l'historique de la chaîne et demeurent interrogeables ; les recopier rendrait la tête contradictoire (locataire à Shibuya ET propriétaire à Yokohama en même temps).

## Exemple 4 — Des sujets différents ne sont jamais fusionnés, quelle que soit la similarité de formulation

Souvenirs existants :
[
  {"memory_id": "d1", "content": "L'utilisateur boit un café noir tous les matins.", "owner": "user"}
]
Nouveaux souvenirs :
[
  {"content": "John boit un café noir tous les matins.", "owner": "user"},
  {"content": "L'utilisateur boit maintenant un café noir deux fois par jour, le matin et après le déjeuner.", "owner": "user"}
]

Sortie :
```json
[
  {"op": "ADD", "content": "John boit un café noir tous les matins.", "owner": "user", "tags": ["food"]},
  {"op": "UPDATE", "memory_id": "d1", "content": "L'utilisateur boit un café noir deux fois par jour, le matin et après le déjeuner.", "owner": "user", "tags": ["food"]}
]
```
Les deux nouveaux souvenirs ont une formulation presque identique mais des sujets DIFFÉRENTS. « John ... » concerne une autre personne, c'est donc un simple ADD — jamais un UPDATE ni un SUPERSEDE de d1, même si d1 dit presque la même chose à propos de l'utilisateur. Seul le souvenir dont le sujet est aussi « L'utilisateur » peut fusionner avec d1.

## Exemple 5 — Événement récurrent : incrémenter le compte et garder une chronologie (UPDATE, pas ADD)

Souvenirs existants :
[
  {"memory_id": "i1", "content": "John s'est blessé à la cheville la saison dernière et a eu besoin d'un arrêt ainsi que de kinésithérapie.", "owner": "user", "memory_at": "2023-05-21 19:48"}
]
Nouveaux souvenirs :
[
  {"content": "John s'est de nouveau tordu la cheville lors d'un match récent.", "owner": "user", "memory_at": "2023-10-13 13:50"}
]

Sortie :
```json
[
  {"op": "UPDATE", "memory_id": "i1", "content": "John s'est blessé à la cheville deux fois : la première la saison dernière (vers la saison 2022–2023), nécessitant un arrêt et de la kinésithérapie ; la seconde lors d'un match vers le 2023-10-13.", "owner": "user", "tags": ["health", "basketball", "injury"]}
]
```
Le même type d'événement se reproduit → UPDATE de l'ancien souvenir. Indique le **nombre** et liste les occurrences avec leurs dates lorsqu'elles sont connues. N'ajoute PAS (ADD) un second fait « John s'est blessé à la cheville », et ne fais PAS de SUPERSEDE (la première blessure n'a pas cessé d'être vraie).
"""


# ================================================================
# Working / coding memory (coding/)
# ================================================================

CODING_EXTRACT_PROMPT = """\
Tu extrais des souvenirs de TRAVAIL durables à partir d'un segment de session d'exécution de tâche.
Une session de « travail » est toute tâche pratique dans laquelle l'utilisateur pilote un ordinateur /
un système au moyen d'outils — cela couvre À LA FOIS le travail logiciel/code (écriture, débogage,
refactorisation, compilation, déploiement, utilisation d'API/de bibliothèques) ET le travail système et
d'exploitation (exécution de commandes shell/PowerShell, analyse ou nettoyage de disque/stockage, opérations
sur fichiers et dossiers, installation/configuration de logiciels, configuration d'environnement/d'appareil,
inspection de l'état du matériel/des disques, sauvegarde, planification/automatisation, traitement de données).

Tu produis DEUX couches à partir du MÊME segment :

  EXPERIENCES (procédural, « comment faire ») — méthode réutilisable, extraite uniquement
  lorsqu'elle franchit le SEUIL DE VALEUR ci-dessous. Un ensemble (task, search_keys, solution,
  boundary_envs).

  FACT (épisodique, « ce qui s'est passé cette fois-ci ») — EXACTEMENT UN enregistrement de CETTE
  session : ce que voulait l'utilisateur, comment on s'y est pris, comment cela s'est terminé. Pas de
  seuil de valeur, mais il n'y en a jamais plus d'un.

Les deux couches répondent à des questions différentes et sont stockées séparément. Une méthode
qui se généralise va dans EXPERIENCES ; l'histoire de cette session particulière —
vraie seulement pour ce système / ce projet / maintenant — va dans le FACT.

═══════════════════════════════════════════════════════════════════
SEUIL DE VALEUR (EXPERIENCES UNIQUEMENT) — NE le franchis PAS à la légère.
═══════════════════════════════════════════════════════════════════
Un souvenir DOIT satisfaire TOUS les critères suivants :
  (a) Il résout un problème susceptible de se reproduire pour le même utilisateur
  (b) La solution n'est pas triviale : elle combine plusieurs informations / étapes, OU
      porte une justification de décision, OU capture un contournement durement acquis, OU est une
      procédure/recette réutilisable pour une tâche opérationnelle (p. ex. comment analyser et
      nettoyer l'usage du disque, comment organiser des fichiers par lots, comment inspecter l'état
      du matériel, comment mettre en place une tâche planifiée)
  (c) Un futur agent / utilisateur préférerait le consulter plutôt que le redécouvrir

IMPORTANT — les tâches d'analyse / de diagnostic / d'investigation COMPTENT :
Une tâche n'a PAS besoin de modifier ou de « corriger » quelque chose pour mériter d'être retenue.
Le travail en lecture seule / analytique / de dépannage a de la valeur lorsque sa PROCÉDURE ou sa
CONCLUSION est réutilisable. Extrais explicitement :
  - Les recettes d'analyse / de diagnostic : la procédure réutilisable + ce qu'elle a révélé
    (p. ex. « comment analyser quels dossiers occupent l'espace du lecteur C » — les commandes d'analyse,
    les pièges rencontrés et le résultat). « Cela n'a produit qu'un rapport, aucun changement »
    n'est PAS une raison de l'ignorer — c'est la MÉTHODE réutilisable qui constitue le souvenir.
  - Les résultats d'investigation, Y COMPRIS les résultats négatifs / introuvables
    (p. ex. « recherche du dossier X sur C: et D: avec rg / fs_search / index SQL —
    introuvable nulle part »). Une impasse consignée évite au prochain agent de répéter
    la même recherche infructueuse, elle franchit donc le seuil.
Ne rejette PAS un souvenir simplement parce que « l'assistant pourrait régénérer les
commandes » ou que « rien n'a réellement été modifié ». Si la procédure ou la conclusion
épargnerait des efforts à l'avenir, garde-la.

N'extrais PAS :
  - Les opérations vraiment triviales en une seule étape sans procédure réutilisable (« renommer un fichier »,
    « git status », « lancer ls une fois »)
  - Les problèmes ponctuels déjà résolus sans leçon transférable
  - Les informations déjà présentes dans le dépôt/système (ne duplique pas package.json)
  - Les tâches jetables propres à une PR / une session, sauf si elles encodent un motif réutilisable
  - L'acte de DEMANDER À L'UTILISATEUR une information manquante. Demander un paramètre,
    lister ce qui est inconnu ou solliciter des champs requis relève du comportement
    conversationnel, pas d'une méthode : tout agent sait déjà poser une question, il n'y a
    donc rien à consulter plus tard. N'émets jamais une expérience dont task ou
    search_keys ressemblent à « comment demander X », « recueillir le Y manquant »,
    « solliciter les champs requis », « que clarifier avant de commencer ». Note que cela
    reste hors des EXPERIENCES uniquement — le fait que la session se soit terminée en attente de l'utilisateur
    est un état réel et appartient au FACT.

Si le segment n'a fait aucun progrès substantiel parce qu'il s'est bloqué en attendant
l'utilisateur — l'assistant a seulement demandé des paramètres, ou a été bloqué avant tout
travail réel — émets "experiences": [] et écris le FACT seul. Rien n'a encore été
appris, il n'y a donc aucune méthode à stocker. C'est plus important qu'il n'y paraît :
une expérience tirée d'un blocage se lit comme une compétence légitime lors de la recherche,
et un segment ultérieur de la même tâche la retrouvera et redemandera à l'utilisateur
au lieu d'avancer, de sorte que le blocage se reproduit pour le reste de la
session. Juge la substance d'après ce que les outils ont réellement fait, pas d'après la fluidité
du texte produit dans le tour.

Heuristique : si l'utilisateur (ou un futur agent effectuant le même type de tâche) voudrait
que ce soit écrit plutôt que de le redécouvrir, garde-le. Une procédure opérationnelle
en plusieurs étapes (même une procédure système/exploitation ou d'analyse/recherche en lecture seule, pas
seulement du code) se qualifie généralement. Ne l'abandonne que s'il s'agit d'une seule commande que tout utilisateur
trouverait via `--help` en 30 secondes ET qu'elle ne porte aucune décision/aucun piège méritant
d'être retenu.

═══════════════════════════════════════════════════════════════════
CHAMPS D'UNE EXPÉRIENCE
═══════════════════════════════════════════════════════════════════

task          — Nom formulé comme une requête ; formule-le comme un utilisateur le DEMANDERAIT plus tard.
                « publier une release sur la plateforme xx » et non
                « publish_release_internal »
                Autonome (pas de « ceci », « le fichier », « nous »).

search_keys   — 0 à 5 formulations alternatives capturant des aspects importants de la
                solution que `task` seul ne ferait pas correspondre. Formule-les comme des questions
                ou des sujets SUR LESQUELS de futurs utilisateurs s'interrogeraient. Distinctes de task.
                Omets-les si task couvre déjà tout.

solution      — Contenu complet et autonome dont l'utilisateur a réellement besoin :
                commandes, emplacements des identifiants (PAS les VALEURS des identifiants),
                chemins de fichiers, étapes ordonnées, pièges, justification des décisions.
                Agrège un travail de bout en bout sur plusieurs tours en UNE seule solution s'il
                forme une tâche cohérente ; sépare en plusieurs souvenirs uniquement lorsque
                l'utilisateur a effectué des tâches sans rapport.

boundary_envs — Ancrages concrets dont dépend la solution, sur plusieurs lignes « clé: valeur » :
                  runtime / SDK / bibliothèque + version
                  endpoint d'API ou signature de méthode
                  version d'un service externe / middleware
                  NOM de variable d'environnement (pas sa valeur)
                  chemin du fichier de configuration
                  identifiant d'hôte / de cluster / de région
                Si la solution est indépendante de l'environnement, laisse "" (vide).
                But : lorsque le SDK/middleware sous-jacent est mis à jour, les futurs
                consommateurs peuvent l'examiner pour décider si la solution s'applique encore.

confidence  — 0.0 à 1.0
  1.0   l'utilisateur l'a explicitement énoncé comme règle / instruction
  0.7   inféré avec de bonnes preuves à partir des actions des outils
  0.5   faiblement inféré ; une seule observation ; incertain

═══════════════════════════════════════════════════════════════════
CHAMP FACT — EXACTEMENT UN par segment, pas de seuil de valeur
═══════════════════════════════════════════════════════════════════

Tu écris UN seul fait pour tout le segment — un enregistrement d'état récupérable pour cette
session, pas une rétrospective soignée et jamais une liste de constats séparés. Un
futur agent doit pouvoir reprendre à partir de lui sans confondre une suggestion,
une inférence ou une recherche partielle avec quelque chose de vérifié.

subject    — QUI ou QUOI concerne cette session : l'entité unique sur laquelle ou pour laquelle le travail a été
             effectué. Un court groupe nominal, pas de phrase, pas de verbe.
             Choisis l'entité la plus précise dont la session *traite*, pas
             l'outil qu'elle a utilisé :
               le nom d'une personne lorsque la session sert une personne nommée
                 ("Dylan", "Matthew")
               le fichier / module / symbole lorsque le travail cible du code
                 ("auth/middleware.ts", "parse_plan_text")
               le service / hôte / appareil / lecteur pour le travail d'exploitation
                 ("D:\\WXWork", "redis-cache-prod", "ci.yml")
             C'est une CLÉ STRICTE, pas de la prose : deux sessions concernant des personnes différentes
             ou des fichiers différents DOIVENT avoir des subjects différents, et deux sessions
             concernant la même entité DOIVENT avoir la même chaîne, orthographiée à l'identique.
             Préfère l'identifiant exact tel qu'il apparaît dans la trace à une
             description de celui-ci. Si le segment n'a réellement pas de sujet unique,
             produis "".

             Pourquoi c'est important : subject est la barrière qui décide si une session ultérieure
             est autorisée à écraser celle-ci comme « la même tâche,
             poursuivie ». Si tu te trompes, l'état d'une personne remplace silencieusement
             celui d'une autre.

statement  — Un enregistrement compact de CETTE session. Deux parties, et la seconde est celle
             qui se perd.
             Préserve les données propres à la tâche et les contraintes concrètes au lieu de
             les remplacer par une évaluation générique du résultat. Énonce
             chaque règle une seule fois : un texte d'instruction répété fait concurrence aux données
             que le souvenir doit retenir.

             PARTIE 1 — ce qui s'est passé, environ 300 caractères latins (~150 CJK).
             Commence par l'objectif et l'objet précis (« récupérer l'espace disque occupé
             par D:\\WXWork », « localiser le dossier new_file_search »), pas par un générique
             « corriger un bug » / « trouver un fichier ». Puis, en gros, ce qui a été inspecté, essayé
             ou modifié. Puis comment cela s'est terminé, avec la sémantique d'exactement un état parmi :
             terminé, partiellement terminé, échoué, bloqué, ou
             toujours en attente de vérification. Inclus les résultats négatifs et les impasses
             sans les exagérer.

             PARTIE 2 — les DONNÉES PROPRES À LA TÂCHE : tout ce sur quoi repose la tâche ENTIÈRE,
             même lorsque ce segment ne l'a pas établi et même lorsque cela paraît
             trop élémentaire pour être énoncé. Les liaisons (ce que représente chaque nom/symbole/étiquette),
             les valeurs fixes que la tâche fournit plutôt qu'elle ne les dérive, les
             conventions adoptées (base d'indexation, unités, fuseau horaire, valeurs par défaut), et
             le propre résultat de ce segment AVEC sa valeur (« x = 7 », pas « la valeur
             a été calculée »).

             Écris la PARTIE 2 comme des données, un élément par ligne, valeurs telles que données :
               budget de tentatives = 5 ; timeout 30s ; l'index commence à 0
               entrée de l'étape 3 : n=64, tolérance 1e-6
               créneau B : type=privé, coût 688.7-721.3 par unité
             Une ligne doit être presque entièrement composée de valeurs. Garde la notation de la trace —
             symboles, unités, nombres, orthographes — et abandonne ses phrases : supprime
             les formules qui annoncent une donnée au lieu d'en être une (« je préférerais »,
             « ça devrait se situer quelque part entre … et … »). Elles se répètent une fois
             par élément, donc une liste de 15 éléments les paie 15 fois.
             Reporte-les sur CHAQUE fait ; n'écris jamais « comme ci-dessus » ou « comme dans la
             session précédente » — la recherche renvoie une poignée de faits par
             similarité et le référent peut ne pas en faire partie.

             Vise moins de 1200 caractères au total. Si la PARTIE 2 ne tient pas, corrige la
             notation, jamais la couverture : statement est le vecteur de recherche ET
             le seul champ qu'un segment ultérieur peut lire (details n'est ni vectorisé
             ni injecté), donc tout ce qui en est retiré est perdu. Compresse la formulation
             autour d'une donnée ; jamais la donnée.

             Le test : si le segment suivant ne peut pas voir le matériel d'origine, peut-il
             continuer à partir de ce seul enregistrement ? Seul un oui compte. Une liaison manquante
             n'échoue pas bruyamment — le nom semble toujours avoir un sens, donc
             il est lu avec son sens courant et la réponse qui
             suit est fluide et porte sur une autre question.

             LANGUE — la même langue que la trace de la session : statement est le
             vecteur de recherche et les requêtes arrivent dans la langue de travail de l'utilisateur,
             donc une autre langue paie une pénalité interlinguistique à chaque recherche.

             FIDÉLITÉ AUX PREUVES — n'enregistre que les actions réellement exécutées et les
             preuves explicitement présentes dans la trace. Préserve mot pour mot les
             commandes, options, arguments, erreurs et nombres significatifs.
             Ne complète JAMAIS une commande avec des options habituelles, et ne transforme jamais
             des conseils ou des plans en actions :
               « a exécuté Get-ChildItem -Force » → « a exécuté Get-ChildItem »
                 (la trace ne contenait pas `-Force` ; ne l'invente pas)
             Limite chaque constat négatif à ce qui a réellement été recherché, et
             énumère CHAQUE limite de couverture explicite qui pourrait l'invalider,
             en particulier les échecs de permission et les limites de profondeur : garde les racines,
             les filtres et la profondeur en vigueur (« introuvable sous C:\\ et
             D:\\ à la profondeur 3 ; certains répertoires n'ont pas été couverts à cause d'un Access
             denied »), ne l'élargis jamais en « n'existe pas ». N'omets pas une limite
             pour rendre l'enregistrement plus joli.
             Ne promeus jamais une recherche partielle en absence globale.
             Une tentative bloquée garde ce qui l'a bloquée.
             Préserve la distinction entre tenté, observé, vérifié,
             inféré et non résolu — exprime-la naturellement (« a essayé … mais a été
             bloqué par Access denied », « soupçonne …, pas encore vérifié »), pas sous forme
             d'étiquettes. Sans sortie d'outil ni confirmation explicite, n'affirme JAMAIS qu'un
             correctif a réussi, qu'une cause a été établie ou qu'un chemin n'existe pas.

             N'enregistre PAS les salutations ni le pur bavardage — c'est le seul cas
             qui reçoit "fact": null. Un segment qui s'est contenté d'énoncer des exigences, ou
             qui s'est bloqué avant de faire du travail, a tout de même établi quelque chose.

details    — La SORTIE concrète de la session, recopiée mot pour mot. `statement` est
             un résumé, et un résumé abandonne nécessairement les valeurs individuelles ; une
             recherche ultérieure veut presque toujours une valeur précise, pas l'essentiel.
             Donc, quel que soit le résultat adressable produit par cette session, reproduis-le
             ici dans sa forme et sa structure d'origine :
               un plan / un emploi du temps / un itinéraire → garde chaque ligne, chaque jour, chaque
                 créneau et chaque prix exactement tels qu'écrits
               un bloc de configuration ou de code → garde le bloc
               la sortie significative d'une commande → garde les lignes qui portent les
                 constats (tailles, nombres, versions, texte d'erreur)
               un résultat structuré (tableau, liste d'enregistrements) → garde les lignes
             Préserve la mise en page d'origine — sauts de ligne, préfixes `Day 1:` / clé: valeur,
             unités, symboles monétaires, ids. Ne résume pas à nouveau, ne
             réordonne pas, n'arrondis pas les nombres, ne traduis pas.

             Ne recopie QUE ce qui est présent dans la trace. Ne comble jamais un manque : si un créneau
             a été laissé vide, laisse-le vide ou dis-le — une valeur inventée
             est pire qu'une valeur manquante, car elle sera retrouvée et utilisée.
             Plafonne autour de 4000 caractères ; si la sortie est plus longue, garde les parties
             portant des valeurs concrètes et abandonne la prose décorative, en notant ce qui a été
             abandonné.

             "" lorsque la session n'a produit aucun artefact de ce type (pure investigation
             sans constat pour l'instant, ou blocage en attente de l'utilisateur).

             Ce champ ne fait PAS partie du vecteur de recherche — `statement` et
             `subject` servent à trouver, `details` fournit la valeur une fois trouvée.
             N'écris donc pas ici d'appât pour la recherche, et ne duplique pas le
             résumé ; écris la chose brute.

files      — 0..N chemins de FICHIERS OU DE RÉPERTOIRES, absolus ou relatifs au dépôt, réellement
             manipulés ou examinés pendant cette session. Garde les racines examinées
             comme `C:\\` et `D:\\`. N'inclus que les chemins réellement
             consultés/fouillés, pas ceux simplement suggérés ou prévus ;
             [] si aucun ne s'applique.
═══════════════════════════════════════════════════════════════════
CONTEXTE HÔTE (fourni par le SDK)
═══════════════════════════════════════════════════════════════════
- workspace_id présent : {workspace_present}
- branche présente : {branch_present}

Lorsqu'aucun workspace_id n'est présent, la solution ne peut pas s'appuyer sur un contexte
local au projet non énoncé — précise tout dépôt / chemin / service nécessaire dans
`solution` ou `boundary_envs` afin que le souvenir reste utilisable par lui-même.

═══════════════════════════════════════════════════════════════════
CONVERSATION
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
SORTIE (JSON strict uniquement, pas de markdown)
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

PAR DÉFAUT, EXTRAIS. Lorsque le segment a fait du travail réel, tu DOIS produire au moins
une expérience — même si la tâche paraît simple ou a réussi du premier coup, capture quand même
l'essence réutilisable de ce qui a été fait (la méthode / la commande / le mode opératoire).
Lorsque la session est mince, la seule expérience la plus précieuse suffit.

La SEULE exception est le cas de blocage ci-dessus : si le segment n'a fait aucun travail réel
parce qu'il était bloqué à demander à l'utilisateur une information manquante, produis
"experiences": [] et écris uniquement le fait. Ne fabrique pas d'expérience pour
satisfaire cette instruction — une expérience inventée (« comment demander la ville de
départ ») est pire qu'aucune, car elle est ensuite retrouvée et suivie. Le vide est
autorisé UNIQUEMENT pour cette raison, jamais comme raccourci lorsque du travail a réellement été fait.

"fact" est un objet UNIQUE, jamais une liste — un enregistrement par session.

ÉCRIS TOUJOURS le fait. Le critère pour le fait n'est pas « était-ce précieux » mais « s'est-il
passé quoi que ce soit », et un segment qui te parvient a déjà franchi ce critère.
Cela inclut les segments qui ressemblent à de la mise en place plutôt qu'à du travail : un seul
message de l'utilisateur énonçant des exigences, des paramètres ou un état de départ, sans
aucun outil exécuté, est exactement le matériel qu'une session ultérieure ne peut pas reconstruire seule
— écris les exigences, les paramètres et l'état de départ, avec la situation, dans `statement` (notation compacte, sans recopier les phrases de la conversation),
et ne les renvoie pas dans `details` (`details` n'entre pas dans le vecteur de recherche et n'est plus injecté côté lecture). Utilise
"fact": null UNIQUEMENT pour les salutations et le pur bavardage sans aucun contenu.
En cas de doute, écris le fait : un enregistrement inutile coûte une ligne, alors qu'un enregistrement abandonné
coûte à chaque session ultérieure qui en avait besoin et n'a aucun moyen de savoir qu'il existait.
"""

CODING_EXTRACT_PROMPT_CODING = """\
╔═══════════════════════════════════════════════════════════════════════════╗
║ CONTRAINTE STRICTE — lis ceci avant toute chose ; elle prime sur toute    ║
║ formulation ultérieure de ce prompt qui semblerait réclamer un récit.     ║
║                                                                           ║
║ Tu n'écris PAS le compte rendu de ce qu'une personne a fait. Tu écris     ║
║ ce dont un FUTUR AGENT a besoin pour agir. Cet agent a déjà sa propre     ║
║ tâche ; peu lui importe ce que voulait cet utilisateur. Il doit savoir :  ║
║ ce qui a été décidé ici, à quelles coordonnées dans le code, ce qui a     ║
║ été essayé et rejeté, et si cela a été vérifié.                           ║
║                                                                           ║
║ Concrètement, dans chaque `statement` et chaque `solution` :              ║
║   · Le sujet grammatical est le CODE ou LA DÉCISION — jamais              ║
║     « l'utilisateur » ni « l'agent ». N'écris pas « L'utilisateur a       ║
║     tenté de… », « L'utilisateur voulait… », « L'agent a ensuite… ».      ║
║   · Ne termine jamais par une formule administrative comme « la session   ║
║     s'est terminée », « l'erreur a été notée », « aucune solution n'a     ║
║     encore été trouvée ». Termine par la décision et son état de          ║
║     vérification. Si rien n'a été décidé, termine par ce qui a été        ║
║     ÉCARTÉ et la prochaine piste inexplorée — c'est la partie             ║
║     réutilisable.                                                         ║
║   · Garde le symptôme concret (chaîne d'erreur, commande en échec,        ║
║     sortie erronée) — c'est ce sur quoi une future recherche portera.     ║
║                                                                           ║
║ mauvais : « L'utilisateur a tenté d'écrire une QTable en RST avec         ║
║        header_rows et a obtenu une TypeError. La session s'est            ║
║        terminée avec l'erreur notée. »                                    ║
║ bon : « le writer RST de ascii.rst rejette header_rows :                  ║
║        `tbl.write(..., \\                                                  ║
║        format='ascii.rst', header_rows=['name','unit'])` lève une         ║
║        TypeError car RST.__init__ ne le transmet pas, contrairement à     ║
║        ascii.fixed_width qui l'accepte. Aucun correctif écrit — le        ║
║        writer a besoin de la même plomberie header_rows que               ║
║        fixed_width. »                                                     ║
╚═══════════════════════════════════════════════════════════════════════════╝

Tu extrais des souvenirs de TRAVAIL durables à partir d'un segment de session d'exécution de tâche.
Une session de « travail » est toute tâche pratique dans laquelle l'utilisateur pilote un ordinateur /
un système au moyen d'outils — cela couvre À LA FOIS le travail logiciel/code (écriture, débogage,
refactorisation, compilation, déploiement, utilisation d'API/de bibliothèques) ET le travail système et
d'exploitation (exécution de commandes shell/PowerShell, analyse ou nettoyage de disque/stockage, opérations
sur fichiers et dossiers, installation/configuration de logiciels, configuration d'environnement/d'appareil,
inspection de l'état du matériel/des disques, sauvegarde, planification/automatisation, traitement de données).

Tu produis DEUX couches à partir du MÊME segment :

  EXPERIENCES (procédural, « comment faire ») — méthode réutilisable, extraite uniquement
  lorsqu'elle franchit le SEUIL DE VALEUR ci-dessous. Un ensemble (task, search_keys, solution,
  boundary_envs).

  FACT (épisodique, « ce qui a été établi cette fois-ci ») — EXACTEMENT UN enregistrement de CETTE
  session. Ce n'est PAS un récit de ce qu'a fait l'utilisateur. Il consigne, dans cet ordre :
  la tâche et son symptôme concret (c'est ce qui le rend retrouvable plus tard), les
  coordonnées dans le code, CE QUI A ÉTÉ RETENU et ce qui a été rejeté, et jusqu'où cela a été
  vérifié. Pas de seuil de valeur, mais il n'y en a jamais plus d'un.

Les deux couches répondent à des questions différentes et sont stockées séparément. Une méthode
qui se généralise va dans EXPERIENCES ; l'histoire de cette session particulière —
vraie seulement pour ce système / ce projet / maintenant — va dans le FACT.

═══════════════════════════════════════════════════════════════════
SEUIL DE VALEUR (EXPERIENCES UNIQUEMENT) — NE le franchis PAS à la légère.
═══════════════════════════════════════════════════════════════════
Un souvenir DOIT satisfaire TOUS les critères suivants :
  (a) Il résout un problème susceptible de se reproduire pour le même utilisateur
  (b) La solution n'est pas triviale : elle combine plusieurs informations / étapes, OU
      porte une justification de décision, OU capture un contournement durement acquis, OU est une
      procédure/recette réutilisable pour une tâche opérationnelle (p. ex. comment analyser et
      nettoyer l'usage du disque, comment organiser des fichiers par lots, comment inspecter l'état
      du matériel, comment mettre en place une tâche planifiée)
  (c) Un futur agent / utilisateur préférerait le consulter plutôt que le redécouvrir

IMPORTANT — les tâches d'analyse / de diagnostic / d'investigation COMPTENT :
Une tâche n'a PAS besoin de modifier ou de « corriger » quelque chose pour mériter d'être retenue.
Le travail en lecture seule / analytique / de dépannage a de la valeur lorsque sa PROCÉDURE ou sa
CONCLUSION est réutilisable. Extrais explicitement :
  - Les recettes d'analyse / de diagnostic : la procédure réutilisable + ce qu'elle a révélé
    (p. ex. « comment analyser quels dossiers occupent l'espace du lecteur C » — les commandes d'analyse,
    les pièges rencontrés et le résultat). « Cela n'a produit qu'un rapport, aucun changement »
    n'est PAS une raison de l'ignorer — c'est la MÉTHODE réutilisable qui constitue le souvenir.
  - Les résultats d'investigation, Y COMPRIS les résultats négatifs / introuvables
    (p. ex. « recherche du dossier X sur C: et D: avec rg / fs_search / index SQL —
    introuvable nulle part »). Une impasse consignée évite au prochain agent de répéter
    la même recherche infructueuse, elle franchit donc le seuil.
Ne rejette PAS un souvenir simplement parce que « l'assistant pourrait régénérer les
commandes » ou que « rien n'a réellement été modifié ». Si la procédure ou la conclusion
épargnerait des efforts à l'avenir, garde-la.

N'extrais PAS :
  - Les opérations vraiment triviales en une seule étape sans procédure réutilisable (« renommer un fichier »,
    « git status », « lancer ls une fois »)
  - Les problèmes ponctuels déjà résolus sans leçon transférable
  - Les informations déjà présentes dans le dépôt/système (ne duplique pas package.json)
  - Les tâches jetables propres à une PR / une session, sauf si elles encodent un motif réutilisable

Heuristique : si l'utilisateur (ou un futur agent effectuant le même type de tâche) voudrait
que ce soit écrit plutôt que de le redécouvrir, garde-le. Une procédure opérationnelle
en plusieurs étapes (même une procédure système/exploitation ou d'analyse/recherche en lecture seule, pas
seulement du code) se qualifie généralement. Ne l'abandonne que s'il s'agit d'une seule commande que tout utilisateur
trouverait via `--help` en 30 secondes ET qu'elle ne porte aucune décision/aucun piège méritant
d'être retenu.

═══════════════════════════════════════════════════════════════════
TRAVAIL SUR DU CODE — ce qu'un futur agent ne peut pas re-déduire, donc à capturer IMPÉRATIVEMENT
═══════════════════════════════════════════════════════════════════
Lorsque le segment est du travail logiciel sur un dépôt, la méthode réutilisable n'en est que
la moitié. L'autre moitié est OÙ se trouve le travail et QUELLE VERSION l'a emporté. Cela ne
peut pas être retrouvé en lisant le code plus tard — le code montre l'état actuel,
ni la branche sur laquelle il se trouve, ni ce qui a déjà été rejeté.

Cette section ne demande PAS un enregistrement plus long. Elle te demande de DÉPENSER LE MÊME
BUDGET sur des tokens de plus grande valeur : abandonne les reformulations de l'objectif, la narration de ce que
l'assistant « a ensuite décidé de faire », et le code collé que tu ne fais que répéter ; dépense plutôt ces
caractères sur les quatre éléments ci-dessous. Un enregistrement qui omet la branche mais
cite 30 lignes de diff est un mauvais échange.

1. BRANCHE / EMPLACEMENT DE TRAVAIL — mot pour mot, dans le `statement` du fait. Si le segment
   mentionne ou bascule sur `feat/x` / `fix/y`, ce token DOIT apparaître. Un agent
   qui reprend sur la mauvaise branche produit des changements qui sont silencieusement jetés,
   donc ce seul token l'emporte sur tout autre détail.

2. QUELLE VERSION L'A EMPORTÉ — lorsque l'utilisateur a changé d'avis, nomme la gagnante et marque
   la perdante en une proposition : « retenu A ; le B intermédiaire a été abandonné ». Pas les deux en
   entier — une seule proposition. Ne garder que la dernière est une erreur : un agent qui rencontre le
   code rejeté doit le reconnaître, sinon il « corrigera » les choses en revenant à la variante
   abandonnée. Des formules comme `scratch that / go back to / actually, let's / no, not that`
   (« oublie ça / reviens à / en fait, faisons / non, pas ça ») le signalent : ce qui suit fait foi,
   ce qui précède est remplacé.

3. COORDONNÉES EXACTES, PAS DE CODE COLLÉ — chemin relatif au dépôt
   (`qutebrowser/utils/debug.py`, jamais `debug.py` seul) plus la fonction /
   classe touchée. Si une signature a changé, donne la nouvelle signature. Si une constante,
   une regex ou un littéral a été décidé, cite CELUI-LÀ mot pour mot — mais cite uniquement lui, pas le
   bloc qui l'entoure. Une ligne exacte vaut mieux qu'un paragraphe d'approximation.

4. VÉRIFIÉ vs SIMPLEMENT ÉCRIT — quelques mots suffisent (« tests exécutés et passants » /
   « confirmé uniquement par git diff, tests non exécutés »). « la modification est en place » et « la modification est en place
   et testée » amènent un futur agent à faire des choses très différentes ensuite.

Anti-modèle — résumer l'objectif et abandonner les coordonnées :
  mauvais : « logique d'analyse des couleurs refactorisée pour être plus propre. »
  bon :     « sur feat/q4, dans qutebrowser/config/configtypes.py QtColor._parse_value :
            retenu `mult = 360.0 if kind == 'h' else 255.0` ; la version intermédiaire qui
            utilisait 255.0 partout a été abandonnée. Tests non exécutés. »
  (le bon est PLUS COURT que la plupart des mauvais — il dépense simplement chaque caractère
   sur quelque chose qui ne peut pas être re-déduit)

═══════════════════════════════════════════════════════════════════
CHAMPS D'UNE EXPÉRIENCE
═══════════════════════════════════════════════════════════════════

task          — Nom formulé comme une requête ; formule-le comme un utilisateur le DEMANDERAIT plus tard.
                « publier une release sur la plateforme xx », et non « publish_release_internal »
                Autonome (pas de « ceci », « le fichier », « nous »).

search_keys   — 0 à 5 formulations alternatives capturant des aspects importants de la
                solution que `task` seul ne ferait pas correspondre. Formule-les comme des questions
                ou des sujets SUR LESQUELS de futurs utilisateurs s'interrogeraient. Distinctes de task.
                Omets-les si task couvre déjà tout.

solution      — Contenu complet et autonome dont l'utilisateur a réellement besoin :
                commandes, emplacements des identifiants (PAS les VALEURS des identifiants),
                chemins de fichiers, étapes ordonnées, pièges, justification des décisions.
                Agrège un travail de bout en bout sur plusieurs tours en UNE seule solution s'il
                forme une tâche cohérente ; sépare en plusieurs souvenirs uniquement lorsque
                l'utilisateur a effectué des tâches sans rapport.

boundary_envs — Ancrages concrets dont dépend la solution, sur plusieurs lignes « clé: valeur » :
                  runtime / SDK / bibliothèque + version
                  endpoint d'API ou signature de méthode
                  version d'un service externe / middleware
                  NOM de variable d'environnement (pas sa valeur)
                  chemin du fichier de configuration
                  identifiant d'hôte / de cluster / de région
                Si la solution est indépendante de l'environnement, laisse "" (vide).
                But : lorsque le SDK/middleware sous-jacent est mis à jour, les futurs
                consommateurs peuvent l'examiner pour décider si la solution s'applique encore.

confidence  — 0.0 à 1.0
  1.0   l'utilisateur l'a explicitement énoncé comme règle / instruction
  0.7   inféré avec de bonnes preuves à partir des actions des outils
  0.5   faiblement inféré ; une seule observation ; incertain

═══════════════════════════════════════════════════════════════════
CHAMP FACT — EXACTEMENT UN par segment, pas de seuil de valeur
═══════════════════════════════════════════════════════════════════

Tu écris UN seul fait pour tout le segment — un enregistrement d'état récupérable pour cette
session, pas une rétrospective soignée et jamais une liste de constats séparés. Un
futur agent doit pouvoir reprendre à partir de lui sans confondre une suggestion,
une inférence ou une recherche partielle avec quelque chose de vérifié.

statement  — Un résumé compact de CETTE session. Il a deux rôles à la fois, et il
             doit remplir LES DEUX : c'est le vecteur de recherche (un futur agent le trouve en
             cherchant avec un nouveau signalement de problème, formulé comme un utilisateur formule un
             problème), et c'est le contenu sur lequel cet agent agit ensuite. Donc :

             COMMENCE par la tâche et le symptôme, avec les mots qu'emploierait un utilisateur.
                C'est ce qui le rend retrouvable — garde la forme de surface proche de la façon dont
                le problème serait signalé. Nomme l'objectif et l'objet précis
                (« récupérer l'espace disque sous D:\\WXWork », « ajuster le multiplicateur de teinte
                dans QtColor._parse_value sur feat/q4 », « localiser le dossier
                new_file_search »), jamais un générique « corriger un bug » / « chercher un fichier ». Inclus
                le symptôme concret : la chaîne d'erreur, la commande en échec, la
                sortie erronée.

             DONNE ENSUITE les coordonnées et la décision.
                chemin relatif au dépôt, symbole et branche pour le travail sur du code. Indique ce qui a été
                RETENU, et dans la même proposition marque ce qui a été abandonné
                (« retenu X ; le Y intermédiaire a été abandonné »). C'est la partie qui
                ne peut pas être re-déduite en lisant le code — le code ne montre que la
                gagnante, jamais que Y a été essayé et rejeté. Un agent qui rencontre plus tard la
                variante rejetée doit pouvoir la reconnaître, sinon il « corrigera » le code en
                revenant à la version abandonnée.

             TERMINE par l'état des preuves.
                Exactement un parmi : terminé, partiellement terminé, échoué, bloqué, ou
                toujours en attente de vérification — plus la façon dont cela a été vérifié (« tests exécutés et
                passants » / « confirmé uniquement par git diff, tests non exécutés »). Inclus
                les résultats négatifs et les impasses sans les exagérer.

             ⚠️ Ne t'arrête jamais au récit. « L'utilisateur a essayé X et a rencontré l'erreur Y ; aucune
             solution trouvée pour l'instant » est un enregistrement gaspillé : il est bien retrouvé mais ne donne à un
             futur agent rien sur quoi agir. Si la session n'a réellement abouti à aucune
             décision, dis ce qui a été écarté et quelle est la prochaine piste inexplorée —
             c'est la partie réutilisable.

             Le sujet des phrases doit être le code et la décision, pas
             « l'utilisateur ». 2 à 4 phrases, en prose fluide, pas en sections étiquetées.
             Plafond strict d'environ 400 caractères latins (~200 caractères CJK).

             FIDÉLITÉ AUX PREUVES — obligatoire :
             - N'enregistre que les actions réellement exécutées et les preuves explicitement
               présentes dans la trace. Préserve mot pour mot les commandes, options,
               arguments, erreurs et nombres significatifs. Ne complète JAMAIS une
               commande avec des options habituelles et ne transforme jamais des conseils/plans en actions.
             - Préserve la distinction entre tenté, observé, vérifié,
               inféré et non résolu. N'ajoute pas ces étiquettes à la prose ;
               exprime leur sens naturellement, p. ex. « a essayé … mais a été bloqué par
               Access denied » ou « soupçonne … , pas encore vérifié ».
             - Sans sortie d'outil ni confirmation explicite, n'affirme JAMAIS qu'un correctif
               a réussi, qu'une cause a été établie, qu'un fichier/dossier n'existe
               pas, ou que des tests sont passés.
             - Limite chaque constat négatif à ce qui a réellement été recherché et
               énumère CHAQUE limite de couverture explicite qui pourrait
               l'invalider, en particulier les échecs de permission et la profondeur maximale de recherche :
               « introuvable dans les parties examinées de C:\\ et D:\\ ; certains
               répertoires ont été ignorés sur Access denied, et la recherche était aussi
               limitée en profondeur ». N'omets pas une limite pour rendre le
               résumé plus joli. Ne promeus jamais une recherche partielle en absence
               globale.
             - N'énonce une causalité comme certaine que lorsque la trace la vérifie
               directement. Sinon, conserve la réserve (« soupçonné » / « inféré » / « pas encore
               vérifié ») ; ne réécris pas une
               hypothèse de test incorrecte en cause racine du système.

             CRUCIAL — ce texte EST le vecteur de recherche, et c'est la seule
             chose à laquelle une future requête sera comparée. Un résumé générique ne
             vaut rien, car CHAQUE session ressemble à un résumé générique : il
             correspondrait à toutes les requêtes et ne répondrait à aucune. Tu DOIS donc nommer
             les ancres concrètes qui rendent CETTE session retrouvable — vrais chemins de
             fichiers, noms de fonctions / symboles, noms de branches, commandes, chaînes
             d'erreur, versions, clés et valeurs de configuration, noms d'hôtes / de services.
             Si quelqu'un recherche plus tard l'un de ces éléments précis, ce
             résumé doit être ce qui ressort.
               bon :     « correction d'un `npm run build` en échec dans la CI. Lu
                          .github/workflows/ci.yml et package.json ; problème attribué à webpack
                          5.74 déclenchant ERR_OSSL_EVP_UNSUPPORTED sous node 18. Passer le
                          node du workflow à 20 a fait passer le build ; node 18
                          reproduit toujours en local. »
               bon :     « sur la branche feat/q0, dans qutebrowser/utils/debug.py signal_name :
                          PyQt<5.11 n'expose le nom que via repr(sig), et repr se présente
                          sous une forme entre crochets et une forme entre parenthèses. Retenu deux motifs
                          essayés dans l'ordre avec fullmatch, en s'arrêtant à la première correspondance ; la
                          version à regex unique a été abandonnée. Confirmé par git diff, tests non
                          exécutés. »
               mauvais : « l'utilisateur voulait corriger un bug de build, l'agent a regardé quelques fichiers
                          de configuration, a modifié du code et l'a corrigé. »
                          (aucun chemin, aucune commande, aucune chaîne d'erreur, aucune version — rien
                           sur quoi chercher ; chaque session se lit exactement ainsi)
               mauvais : « problème de compatibilité de l'analyse des noms de signaux corrigé sur une branche
                          de fonctionnalité. »
                          (quelle branche ? quel fichier ? quelle fonction ? laquelle des deux
                           versions concurrentes l'a emporté ? rien ici ne survit à une
                           recherche une semaine plus tard)
             Exemples de fidélité (mauvais → bon) :
               « a exécuté Get-ChildItem -Force » → « a exécuté Get-ChildItem »
                 (la trace ne contenait pas `-Force` ; ne l'invente pas)
               « confirmé que new_file_search n'existe pas » →
                 « new_file_search est introuvable dans les parties examinées de C:\\ et
                  D:\\ ; certains répertoires ont été ignorés sur Access denied ou à cause de la limite
                  de profondeur, l'investigation n'est donc que partiellement faite »
               « des payloads identiques provoquent une fausse erreur de verrou » →
                 « le script de vérification supposait que le dernier écrivain est toujours un rang
                  fixe ; cette hypothèse ne tient pas, donc l'existence d'un bug de verrou
                  n'est toujours pas vérifiée »

             N'enregistre PAS les salutations ni le pur bavardage. Si le segment n'a établi
             absolument rien, produis "fact": null.

files      — 0..N chemins de FICHIERS OU DE RÉPERTOIRES, absolus ou relatifs au dépôt, réellement
             manipulés ou examinés pendant cette session. Garde les racines examinées
             comme `C:\\` et `D:\\`. N'inclus que les chemins réellement
             consultés/fouillés, pas ceux simplement suggérés ou prévus ;
             [] si aucun ne s'applique.

═══════════════════════════════════════════════════════════════════
CONTEXTE HÔTE (fourni par le SDK)
═══════════════════════════════════════════════════════════════════
- workspace_id présent : {workspace_present}
- branche présente : {branch_present}

Lorsqu'aucun workspace_id n'est présent, la solution ne peut pas s'appuyer sur un contexte
local au projet non énoncé — précise tout dépôt / chemin / service nécessaire dans
`solution` ou `boundary_envs` afin que le souvenir reste utilisable par lui-même.

═══════════════════════════════════════════════════════════════════
CONVERSATION
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
SORTIE (JSON strict uniquement, pas de markdown)
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

OBLIGATOIRE : tu DOIS produire au moins UNE expérience. Ne renvoie PAS une liste vide.
Même si la tâche paraît simple ou a réussi du premier coup, capture quand même
l'essence réutilisable de ce qui a été fait (la méthode / la commande / le mode opératoire). Seule
la plus précieuse des expériences est requise lorsque la session est mince — mais ne produis jamais
un tableau "experiences" vide.

"fact" est un objet UNIQUE, jamais une liste — un enregistrement par session. Utilise
"fact": null uniquement lorsque le segment n'est que du bavardage n'ayant rien établi.
"""

CODING_RECONCILE_PROMPT = """\
Tu décides comment fusionner de nouveaux brouillons de souvenirs de code avec les souvenirs existants de l'utilisateur.

Pour chaque nouveau brouillon, décide l'une des actions suivantes :
  ADD     — aucun souvenir existant ne décrit la même tâche/le même aspect
  UPDATE  — un souvenir existant décrit la même tâche ; le nouveau brouillon le remplace
            (la solution peut avoir changé, l'environnement peut avoir été mis à jour, etc.)
  SKIP    — doublon d'un souvenir existant ; rien de nouveau

En outre, tu peux émettre des opérations DELETE sur des candidats existants lorsque le nouveau
contenu les rend obsolètes ou invalides :
  DELETE  — la nouvelle conversation remplace/annule/invalide EXPLICITEMENT un
            souvenir existant qui ne peut pas être réparé via UPDATE. Exemples :
              * l'utilisateur dit « n'utilise plus X » et X n'existe que sous forme de
                souvenir séparé (pas celui en cours d'ajout/de mise à jour)
              * les boundary_envs du candidat (service / endpoint / SDK) ont disparu
                et la nouvelle conversation confirme leur abandon

Règles :
- « Même tâche » signifie que le souvenir existant et le nouveau brouillon seraient tous deux des réponses
  valides à la même future requête de l'utilisateur.
- Si la solution diffère, privilégie UPDATE (nous ne conservons pas d'historique).
- Si la tâche et la solution correspondent toutes deux étroitement, SKIP.
- DELETE uniquement lorsqu'il existe une preuve EXPLICITE que le candidat est invalide/obsolète.
  Ne fais jamais de DELETE simplement parce qu'un candidat semble « périmé ».
- Les opérations produites peuvent être plus nombreuses que les brouillons lorsque des DELETE sont produits.

═══════════════════════════════════════════════════════════════════
NOUVEAUX BROUILLONS
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
CANDIDATS EXISTANTS (par brouillon, retrouvés par similarité de tâche)
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
SORTIE (JSON strict uniquement, pas de markdown)
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "action": "ADD"}},
    {{"draft_idx": 1, "action": "UPDATE", "target_experience_id": "exp_xxx", "reason": "..."}},
    {{"draft_idx": 1, "action": "DELETE", "target_experience_id": "exp_yyy", "reason": "l'utilisateur a explicitement abandonné Y"}},
    {{"draft_idx": 2, "action": "SKIP",   "reason": "doublon"}}
  ]
}}

S'il n'y a aucun brouillon, produis {{"ops": []}}.
"""

CODING_FACT_RECONCILE_PROMPT = """\
Tu maintiens des FAITS DE TRAVAIL au niveau de la session. Chaque statement résume une
trace/session entière : la tâche de l'utilisateur, l'approche générale et le résultat. Ce n'est pas une
affirmation atomique.

Chaque nouveau fait de session ci-dessous SERA stocké ; ce n'est pas ta décision. Ta seule
tâche est de détecter la continuation d'une tâche : la nouvelle session continue-t-elle à faire avancer la
MÊME tâche de l'utilisateur qu'un fait de session existant ? Si oui, fais un SUPERSEDE de l'ancien fait afin que
les liens forment une chaîne de progression inter-sessions pour cette tâche.

Marque un fait existant comme remplacé UNIQUEMENT lorsqu'il existe des preuves solides que :
  - Les sujets correspondent : les deux enregistrements concernent la MÊME entité — la même
    personne, le même fichier/module, le même hôte/service. Un sujet commun est
    nécessaire mais pas suffisant ; ET
  - L'objectif de l'utilisateur est substantiellement le MÊME, et pas simplement dans le même thème/projet ;
    ET
  - La nouvelle trace poursuit, réessaie, étend, vérifie ou termine le travail
    consigné par la trace existante.

Ne fais PAS de SUPERSEDE lorsque :
  - Les deux enregistrements nomment des sujets différents. Deux personnes d'un même groupe, deux fichiers
    d'un même module, deux hôtes d'un même cluster sont PARALLÈLES, pas successifs : l'état d'une
    personne n'est pas une version plus récente de celui d'une autre. Lorsqu'un sujet est affiché
    comme (unspecified), reviens à un jugement fondé sur le seul statement.
  - Les sessions mentionnent simplement le même fichier, symbole, composant, dépôt,
    erreur ou technologie, mais poursuivent des objectifs utilisateur différents.
  - Une tâche ne fait que fournir du contexte ou une technique pour une autre tâche.
  - La relation est incertaine. Sois prudent : INSERT est le choix sûr par défaut ;
    fusionner des sessions sans rapport détruit leurs historiques de tâche distincts.

Traite chaque statement comme un texte limité par ses preuves. Ne réinterprète pas une inférence
qu'il contient comme une cause établie, et n'utilise pas une conjecture/erreur commune comme preuve que
les objectifs de l'utilisateur sont les mêmes. Ta décision porte uniquement sur la continuation de la tâche ; ne
« corrige », ne complète et n'embellis aucun des enregistrements de session.

Exemples :
  existant : « L'utilisateur devait corriger le npm run build de la CI ; problème attribué à
             ERR_OSSL_EVP_UNSUPPORTED sous Node 18, workflow pas encore modifié. »
  nouveau :  « L'utilisateur a poursuivi la correction du même build CI ; a passé Node à 20 dans
             .github/workflows/ci.yml et npm run build a réussi. »
                                                                 → SUPERSEDE

  existant : « L'utilisateur devait corriger la gestion de l'expiration des JWT dans
             auth/middleware.ts ; une logique de rafraîchissement a été ajoutée. »
  nouveau :  « L'utilisateur devait ajouter une journalisation des performances à
             auth/middleware.ts ; une métrique de latence a été introduite. »
                                                                 → garder les deux
            (même fichier/projet, objectif utilisateur différent)

═══════════════════════════════════════════════════════════════════
NOUVEAUX FAITS DE SESSION
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
FAITS DE SESSION EXISTANTS (par nouveau fait, retrouvés par similarité de statement)
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
SORTIE (JSON strict uniquement, pas de markdown)
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "supersedes": ["fact_xxx"], "reason": "poursuit la même tâche de correction de la CI"}},
    {{"draft_idx": 1, "supersedes": []}}
  ]
}}

N'utilise que des ids de faits figurant dans la liste de candidats de ce même draft_idx.
Si rien n'est remplacé nulle part, produis {{"ops": []}}.
"""

CODING_CHAIN_SUMMARY_PROMPT = """\
Une tâche de l'utilisateur dans une base de mémoire de travail a progressé sur plusieurs sessions. Voici
les résumés de session de la chaîne de progression de cette tâche, du plus ancien au plus récent, se terminant par
la session actuelle.

Résume comment la MÊME tâche a progressé d'une session à l'autre jusqu'à son état actuel.
Couvre chaque étape importante dans l'ordre chronologique. Pour chaque étape, préserve
lorsque c'est disponible : l'action ou le changement réel, son résultat direct, et tout
problème non résolu ou condition limitante. Termine par un état actuel explicite.

Règles :
  - Il s'agit uniquement de compression. Ne rapporte QUE ce que disent les entrées ; n'ajoute ni ne
    complète jamais une commande, une cause, un motif, une action, un résultat de test, un état de réussite ou une
    conclusion négative globale.
  - Préserve exactement le statut des preuves : tenté n'est pas terminé, observé n'est
    pas vérifié, inféré n'est pas établi, et non résolu reste non résolu.
    Garde les mots d'incertitude et les limites de recherche/permission/couverture.
  - Énonce fidèlement l'état final actuel (terminé, partiellement terminé,
    échoué, bloqué, ou toujours en attente de vérification). Ne le rehausse jamais sur la base
    d'une histoire causale plausible ou d'une entrée ultérieure qui ne l'a pas vérifié.
  - Lorsque des entrées mentionnent une cause soupçonnée et un résultat ultérieur, relie-les
    causalement uniquement si une entrée dit directement que cette cause a été vérifiée.
  - Préserve les ancres de recherche concrètes : chemins de fichiers, symboles, commandes, chaînes
    d'erreur, versions, clés/valeurs de configuration et valeurs numériques importantes qui
    comptent pour la progression.
  - Pour les chaînes de 2 à 3 générations, ne compresse pas excessivement la progression en une seule
    phrase. Préfère 2 à 4 paragraphes compacts lorsque c'est utile, ou une prose lisible avec
    un objectif de tâche clair, une progression étape par étape et un état actuel.
  - Ne recopie pas mécaniquement tout l'historique. Omets les répétitions et les détails
    de faible valeur tout en conservant les actions importantes, les résultats directs et les éléments restants.
  - N'invente pas de titres d'étape ni de dates. Ne mentionne une date que lorsque les entrées
    lui donnent un sens ; les horodatages bruts sont du bruit.
  - Si les entrées indiquent que des révisions plus anciennes ont été omises, indique explicitement que
    l'historique antérieur a été omis ; ne laisse pas entendre que les entrées fournies sont complètes.
  - Écris dans la même langue que celle des entrées.
  - Pas de préambule ni de guillemets englobants.

Exemple
  entrées :
    (2026-07-20) L'utilisateur devait corriger le npm run build de la CI ; problème attribué à
                 ERR_OSSL_EVP_UNSUPPORTED sous Node 18, workflow pas encore
                 modifié.
    (2026-07-24) L'utilisateur a poursuivi cette correction de la CI ; a passé Node à 20 dans
                 .github/workflows/ci.yml, mais le cache des dépendances
                 reproduisait encore l'ancienne erreur.
    (maintenant) L'utilisateur a poursuivi la vérification du même correctif ; après avoir vidé le
                 cache npm, npm run build a réussi.
  sortie :
    L'objectif de la tâche est de corriger le npm run build de la CI. Le problème a d'abord été attribué à
    ERR_OSSL_EVP_UNSUPPORTED sous Node 18, sans que le workflow soit encore modifié ;
    ensuite, .github/workflows/ci.yml a été passé à Node 20, mais le cache des dépendances
    maintenait l'ancienne erreur. Enfin, après avoir vidé le cache npm,
    npm run build a réussi, cette correction du build est donc actuellement vérifiée comme terminée.

═══════════════════════════════════════════════════════════════════
ENTRÉES
═══════════════════════════════════════════════════════════════════
{chain_block}

═══════════════════════════════════════════════════════════════════
SORTIE (le résumé lui-même, rien d'autre)
═══════════════════════════════════════════════════════════════════
"""


# ================================================================
# Episode / topic summaries
# ================================================================

EPISODE_SUMMARY_PROMPT = """Tu résumes UN épisode de conversation avec l'utilisateur.

Un épisode est une seule séquence de discussion — une chose dont on a parlé,
éventuellement sur plusieurs sessions d'écriture.

## Souvenirs capturés pendant cet épisode (du plus ancien → au plus récent)

{memories}

---

Écris un seul résumé dense de ce qui s'est passé / de ce qui a été discuté dans cet épisode.

Règles :

* 2 à 4 phrases, à la troisième personne, en commençant par « L'utilisateur ... » (ou par la personne nommée
  si les souvenirs concernent quelqu'un d'autre).
* Relie les souvenirs en un seul récit cohérent — un épisode a généralement un
  fil conducteur (un projet, un problème, une sortie, une décision).
* Préserve les détails concrets : noms, lieux, nombres, dates.
* N'invente RIEN qui ne figure pas dans les souvenirs.
* N'énumère PAS les souvenirs un par un ; synthétise-les.
* La langue de sortie doit correspondre à la langue des souvenirs.

Renvoie UNIQUEMENT le texte du résumé, sans préambule, sans JSON, sans guillemets."""

TOPIC_SUMMARY_PROMPT = """Tu résumes tout ce que l'on sait sur UN thème.

## Thème

{topic_name}

## Souvenirs relevant de ce thème (du plus ancien → au plus récent)

{memories}

---

Écris un seul résumé dense de ce que l'on sait de l'utilisateur au regard de ce
thème.

Règles :

* 2 à 4 phrases, à la troisième personne, en commençant par « L'utilisateur ... » (ou par la personne nommée
  si les souvenirs concernent quelqu'un d'autre).
* Couvre le thème dans son ensemble — énonce la situation actuelle, et ne mentionne son
  évolution que lorsque le changement compte.
* Préserve les détails concrets : noms, lieux, nombres, dates.
* N'invente RIEN qui ne figure pas dans les souvenirs.
* N'énumère PAS les souvenirs un par un ; synthétise-les.
* La langue de sortie doit correspondre à la langue des souvenirs.

Renvoie UNIQUEMENT le texte du résumé, sans préambule, sans JSON, sans guillemets."""


# ================================================================
# System 2 evolve (pipelines/system2_agent.py)
# ================================================================

SYSTEM2_AGENT_PROMPT = """Tu es un Agent de traitement cognitif. Ta mission est de faire émerger des structures cognitives d'ordre supérieur à partir des données de mémoire brutes d'un utilisateur.

## Architecture de la mémoire

### VDB (base de données vectorielle) — lecture seule
| Couche | Description |
|-------|-------------|
| L0 BASIC_INFO | Attributs de l'utilisateur (nom, âge, profession) |
| L1 RAW | Transcriptions de conversation d'origine |
| L2 FACT | Faits atomiques extraits des conversations |
| L3 SUMMARY | Résumés de session |
| L4 IDENTITY | Traits d'identité, préférences, habitudes |
| L5 KNOWLEDGE | Connaissances de domaine |

### Graph (graphe de connaissances) — tu en es le seul rédacteur
| Couche | Description |
|-------|-------------|
| L6 SCHEMA | Motifs de comportement — liés à un domaine, atomiques, immuables une fois créés |

## Qu'est-ce qu'un Schema L6 ?

Un Schema est un motif de comportement transversal aux événements dans le modèle de l'utilisateur à long terme, et
comporte les trois composantes suivantes :


- **Circonstance** : le domaine/thème/la situation dans lequel ce motif est observé. Un Schema DOIT rester dans sa circonstance — ne généralise PAS d'un domaine à l'autre. L'abstraction inter-domaines est gérée séparément par le système.
- **Motif** : le comportement habituel, le mode de pensée ou la tendance d'action de l'utilisateur dans cette circonstance.
- **Éclairage** : le moteur psychologique ou le modèle mental sous-jacent.

### Format du contenu
Utilise une seule phrase combinant les trois :
« Lorsque [circonstance], l'utilisateur [motif] — ce qui reflète [éclairage]. »

Pour les motifs durables liés aux émotions, à la thérapie, au stress, au deuil ou aux traumatismes, n'efface
pas le contexte autobiographique précis au profit d'une étiquette de personnalité
générique. Préfère une structure ancrée qui préserve :
« Déclencheur/contexte -> réponse émotionnelle récurrente -> ressource d'adaptation observée. »
N'inclus que les déclencheurs, réponses et ressources d'adaptation présents dans des preuves
appartenant à l'utilisateur (user-owned). Les conseils de l'assistant ne sont pas une ressource d'adaptation observée chez l'utilisateur.

### Règles
- **Atomique** : un seul motif par Schema. Deux motifs distincts → deux Schemas.
- **Immuable** : une fois créé, le contenu ne change JAMAIS.
- **Preuves uniquement** : les nouveaux faits qui ne font que soutenir la formulation existante → appelle
  `add_evidence`. Si de nouvelles preuves changent, corrigent ou restreignent substantiellement
  l'état durable actuel, utilise `revise_schema` afin que l'ancienne version soit conservée pour
  l'audit mais retirée de la recherche active.
- **Lié à un domaine** : reste dans le domaine observé. L'abstraction inter-domaines est gérée par un processus système distinct.
- **Sépare les faits des motifs** : une identité explicite isolée, un état de santé,
  l'usage d'un équipement, une affiliation, un projet ou une préférence relève de la mémoire d'état L2,
  pas de L6. L6 exige des preuves appartenant à l'utilisateur représentant au moins deux
  écritures raw ou sessions indépendantes. Un seul fait L2 réconcilié peut représenter plusieurs écritures ;
  examine ses source_raw_memory_ids plutôt que de compter les nœuds de faits.
- **Sécurité du sujet** : des faits partageant un même fragment source peuvent décrire d'autres personnes ou
  des exemples. Ne crée un Schema de l'utilisateur que lorsque le matériel attribue explicitement
  le motif à l'utilisateur. Sinon, ne produis aucune opération pour ce matériel.
- **Couverture des candidats** : dans une revue source-context, rends compte de chaque fait
  candidat listé : soit utilise-le comme preuve dans `create_schema`/`add_evidence`,
  soit émets `skip_fact`. Le contexte raw peut confirmer son sujet ou restituer un détail
  omis, mais ne doit pas amorcer des Schemas sans rapport.
- **Seuil de preuve** : créer un L6 exige un ou plusieurs faits de preuve appartenant à l'utilisateur
  représentant au moins deux écritures raw ou sessions indépendantes.
  Les conseils de l'assistant n'établissent pas à eux seuls un motif de l'utilisateur.

### Bons et mauvais exemples
✅ « Lorsqu'il cuisine, l'utilisateur suit strictement les étapes de la recette et mesure précisément les ingrédients — ce qui reflète un besoin de structure externe pour gérer l'incertitude. »
✅ « Lorsqu'il joue aux jeux vidéo, l'utilisateur consulte toujours des soluces pour atteindre 100 % de complétion — motivé par une faible tolérance à l'inachevé. »
✅ « Après la perte inattendue d'un mentor, les rappels de la mort peuvent raviver soudainement le deuil ; l'utilisateur fait une pause pour réfléchir et fait des promenades au calme pour retrouver son équilibre. »
❌ « L'utilisateur aborde la musique comme un moyen de synthèse créative et de rapprochement culturel, mêlant constamment diverses traditions musicales... » (plusieurs motifs fusionnés, trop large)
❌ « L'utilisateur est passionné par beaucoup de choses et accorde de l'importance à la qualité. » (aucune circonstance, trop vague)
❌ « L'utilisateur est guidé par le sens et résilient. » (efface le déclencheur durable et les preuves d'adaptation observées)

## Ton flux de travail

Matériaux d'entrée (tu n'es invoqué que lorsqu'au moins un cluster existe) :
- **Résultats de clustering** : entrée principale — faits regroupés par similarité sémantique
- **Faits non regroupés (complément)** : faits n'appartenant à aucun cluster ; priorité inférieure aux résultats de clustering

Pour chaque groupe de faits (regroupés ou non) :
1. **Recherche les Schemas existants** : si un Schema existant capture déjà ce motif, appelle `add_evidence`. Utilise `revise_schema` uniquement lorsque l'ancienne formulation deviendrait substantiellement incomplète ou trompeuse après l'ajout des nouvelles preuves.
2. **Crée un nouveau Schema** : uniquement si aucun Schema existant ne couvre ce motif. Une phrase, trois composantes.
3. **Établis des relations** : si deux Schemas sont thématiquement liés, utilise `add_edge` (RELATED_TO).

## Principes

- Toutes les opérations sur le Graph DOIVENT passer par les tools
- Le contenu d'un Schema est IMMUABLE — ne recrée jamais ce qui existe déjà
- Préfère `add_evidence` à la création de doublons
- Préfère ne rien créer plutôt que de créer des nœuds de faible qualité
- Un Schema = un motif atomique dans un domaine
- Pour les tags, préfère réutiliser les tags de la liste des tags existants"""


# ================================================================
# Fragments assembled in code
# ================================================================

# Display name of this language, used in logs and docs.
LANGUAGE_NAME = "Français"

# Appended to every memory-writing prompt ONLY when the caller set the
# language explicitly (per call or MemoryConfig.language). Auto-detected
# requests never see it, so their prompts stay exactly as before.
OUTPUT_LANGUAGE_DIRECTIVE = """

---

# LANGUE DE SORTIE

Rédige en français chaque valeur en langage naturel que tu produis (texte des souvenirs, résumés, justifications, requêtes), quelle que soit la langue utilisée par la conversation ou par les souvenirs existants. Cette règle prime sur toute règle ci-dessus demandant de suivre la langue d'entrée. Conserve exactement tels quels les clés JSON, les noms de champs, les valeurs d'énumération, les noms d'opérations, les ID, le code, les chemins de fichiers, les commandes, les messages d'erreur et les noms propres.
"""

# enable_intention=False: the "two kinds of memory" section of
# EXTRACT_SYSTEM_PROMPT is replaced by a single-bucket section. The pattern
# must match this language's EXTRACT_SYSTEM_PROMPT.
NO_INTENTION_SECTION_PATTERN = r"# DEUX TYPES DE MÉMOIRE.*?(?=\n# OWNER)"

NO_INTENTION_MEMORY_SECTION = (
    "# MÉMOIRE\n"
    "\n"
    "Tu extrais un seul type de mémoire : **memory** (souvenirs).\n"
    "\n"
    "## memory\n"
    "\n"
    "Tout ce qui est durable et mérite d'être retenu au sujet de l'utilisateur — et de\n"
    "toute autre personne nommée dans la conversation — va ici :\n"
    "\n"
    "* préférences, opinions, valeurs, attitudes\n"
    "* événements, actions, expériences\n"
    "* habitudes, motifs de comportement\n"
    "* relations, traits\n"
    "* faits objectifs, décisions, contraintes\n"
    "* activités en cours\n"
    "* **projets futurs / actions à venir** qui méritent d'être retenus\n"
    "  (mets-les dans `memory` ; n'invente PAS de catégorie intentions séparée)\n"
    "\n"
    "Les préférences implicites DOIVENT être inférées lorsqu'elles sont fortement suggérées.\n"
    "\n"
    "Ne sépare PAS « quel genre de personne est l'utilisateur » de « ce qui s'est passé ».\n"
    "Les deux vont dans `memory`.\n"
    "\n"
    "---\n"
    "\n"
)

# (old, new) pairs applied in order after the section swap.
NO_INTENTION_REPLACEMENTS = (
    (
        "Chaque souvenir et chaque intention DOIT porter un `owner`",
        "Chaque souvenir DOIT porter un `owner`",
    ),
)

INTENTION_DISABLED_NOTE = (
    "\n\n---\n\n# INTENTIONS DÉSACTIVÉES\n"
    "L'extraction des intentions est désactivée. Même si les exemples few-shot montrent "
    "`intentions`, n'émets **pas** cette clé ; mets les projets futurs dans `memory`.\n"
)

# Coding / working extractor: wraps the history and the new messages.
CODING_CONVERSATION_BLOCK = (
    "5 DERNIERS TOURS DE L'UTILISATEUR (CONTEXTE UNIQUEMENT ; ne les extrais pas à nouveau) :\n"
    "{history}"
    "\n\nNOUVEAUX MESSAGES (extrais uniquement à partir de cette écriture complète) :\n"
    "{new}"
)
CODING_EMPTY_BLOCK = "(aucun)"

# Name line at the top of a multi-person conversation. Also stored in L1_RAW,
# so pipelines.base parses every language's form back.
USER_NAME_LINE = "Le nom de l'utilisateur est {name}"

# System 2 single-call mode: output contract appended to SYSTEM2_AGENT_PROMPT.
SYSTEM2_OUTPUT_INSTRUCTIONS = """

## Format de sortie

N'appelle PAS de tools. Produis directement un tableau JSON d'opérations, entouré d'un bloc de code ```json.

Opérations disponibles :

1. **create_schema** — Crée un nouveau Schema comportemental L6
   ```{"op": "create_schema", "schema_kind": "behavior_pattern", "content": "motif concis lisible par l'utilisateur", "retrieval_text": "comportement et situations pertinentes", "evidence_list": ["fact_node_id_1", "fact_node_id_2"], "tags": ["tag1"]}```

2. **add_evidence** — Ajoute des preuves à un Schema existant (le contenu reste immuable)
   ```{"op": "add_evidence", "node_id": "existing_schema_node_id", "evidence_list": ["new_fact_id_1"]}```

3. **revise_schema** — Crée une nouvelle version lorsque de nouvelles preuves changent substantiellement l'état actuel, et retire l'ancienne version de la recherche active. Ne l'utilise pas pour un détail compatible.
   ```{"op": "revise_schema", "node_id": "old_schema_id", "schema_kind": "explicit_state", "content": "profil actuel mis à jour", "retrieval_text": "texte de recherche mis à jour", "evidence_list": ["new_fact_id_1"], "tags": ["tag1"], "reason": "pourquoi l'ancienne formulation induirait en erreur"}```

4. **add_edge** — Crée une relation entre deux Schemas
   ```{"op": "add_edge", "source_id": "schema_id_1", "target_id": "schema_id_2", "reason": "raison de la relation"}```

5. **skip_fact** — Marque explicitement un fait candidat comme examiné sans créer
   de Schema. À utiliser uniquement pour des faits concernant une autre personne, des événements passagers, des conseils de
   l'assistant qui n'établissent pas un trait de l'utilisateur, ou des faits déjà entièrement couverts.
   ```{"op": "skip_fact", "node_id": "candidate_fact_id", "reason": "pas un motif durable de l'utilisateur"}```

## Contrat de sortie

1. La sortie DOIT être un tableau JSON entouré d'un bloc de code ```json.
2. Si les données sont insuffisantes pour des conclusions fiables, produis `[]`. Dans une
   revue source-context, `[]` signifie que chaque candidat de cette revue a été examiné
   et qu'aucun ne soutient un Schema durable.
3. AUCUN texte en dehors du bloc de code.
4. Les ID de evidence_list doivent provenir des node_ids de faits fournis dans les matériaux.
5. Le node_id de add_evidence / revise_schema doit provenir des ID de nœuds existants du Graph.
6. Pour une revue source-context, chaque fait candidat doit apparaître dans une evidence_list ou dans un skip_fact."""

# Appended to SYSTEM2_OUTPUT_INSTRUCTIONS when the schema evidence gate is on.
SYSTEM2_SCHEMA_EVIDENCE_GATE = """

7. L6 représente uniquement des motifs de comportement transversaux aux événements. create_schema doit utiliser des preuves appartenant à l'utilisateur (user-owned) représentant au moins deux écritures raw ou sessions indépendantes. Un seul fait réconcilié peut suffire lorsque source_raw_memory_ids contient plusieurs écritures indépendantes.
8. Ne crée pas de L6 pour une identité explicite isolée, un état de santé, l'usage d'un équipement, une affiliation, un projet ou une préférence. La projection d'état L2 canonique est responsable de ces faits."""

# (old, new) pairs that turn the tool-calling wording of SYSTEM2_AGENT_PROMPT
# into single-call wording. Each `old` must occur in this language's prompt
# (or be harmless if it does not).
SYSTEM2_SINGLE_CALL_REPLACEMENTS = (
    ("- Toutes les opérations sur le Graph DOIVENT passer par les tools\n", ""),
    ("appelle `add_evidence`", "utilise l'opération add_evidence"),
    ("utilise `add_edge` (RELATED_TO)", "utilise l'opération add_edge"),
)

# System 2 user message (materials). Keys are fixed; values are format
# strings with the named fields shown.
SYSTEM2_MATERIALS = {
    "title": "## Matériaux prétraités\n",
    "stats": (
        "### Statistiques\n- Total des faits VDB : {total}\n"
        "- Faits nouveaux (à traiter) : {fresh} (exclus_car_déjà_en_preuve={evidenced})\n"
        "- Clusters trouvés : {clusters}\n"
        "- Faits dans des clusters : {clustered}, complément non regroupé : {unclustered}\n"
        "- Total des Schemas du Graph : {graph_total} (rappelés={recalled})"
    ),
    "graph_status_empty": "- État du Graph : **VIDE** (premier traitement cognitif)\n",
    "clusters_header": "### Résultats de clustering\n",
    "label_holistic": "{n} faits utilisateur inter-sessions ; uniquement des motifs émotionnels/thérapeutiques durables étayés par des preuves",
    "label_source_context": "{n} faits candidats ; l'écriture raw sert uniquement à valider le sujet/les détails",
    "label_topic": "{n} faits, thème",
    "raw_label": "Extrait raw pertinent par candidat",
    "holistic_note": (
        "Il s'agit d'une seule passe nocturne de consolidation holistique, bornée. Crée au plus "
        "{max_schemas} Schemas thérapeutiques ancrés. "
        "Privilégie l'association d'un déclencheur/contexte durable, d'une réponse émotionnelle récurrente "
        "et d'une ressource d'adaptation observée ou d'une contrainte de décision provenant d'écritures raw/sessions différentes. "
        "Avant create_schema, fais l'union des raw_sources de ses ID de preuve et exige au moins deux "
        "ID de source raw distincts ; deux faits issus d'une même écriture raw constituent un seul événement. "
        "Produis [] lorsque les preuves sont insuffisantes.\n"
    ),
    "companion_header": "**Indices compagnons de sources indépendantes (candidats, pas conclusions) :**",
    "unclustered_header": "### Faits non regroupés (complément)\n",
    "unclustered_hint": "Les faits ci-dessous n'ont été regroupés dans aucun cluster ci-dessus. Examine-les après les thèmes des clusters ; priorité inférieure aux résultats de clustering.\n",
    "forward_header": "### Nœuds du Graph liés (retrouvés par recherche sur le centroïde des clusters, {count} résultats)\n",
    "graph_empty": "### Nœuds existants du Graph\nLe Graph est actuellement VIDE — c'est le premier traitement cognitif. Ne fais PAS de recherche dans le Graph (elle ne renverrait rien). Passe directement à la création de Schemas à partir des résultats de clustering.\n",
    "graph_no_recall": (
        "### Nœuds existants du Graph\n"
        "Le Graph contient {graph_total} Schemas, "
        "mais le rappel vectoriel par centroïde n'a trouvé aucune correspondance. Décide à partir des résultats de clustering si de nouveaux Schemas sont nécessaires.\n"
    ),
    "reverse_header": "### Références inverses du Graph (faits VDB référencés par des Schemas existants)\n",
    "tags_header": "### Tags existants (préfère les réutiliser plutôt que de créer des synonymes)\n",
    "closing": (
        "Analyse les matériaux ci-dessus et produis un tableau JSON d'opérations. "
        "Décide s'il faut créer de nouveaux Schemas ou ajouter des preuves aux Schemas existants. "
        "Si les données sont insuffisantes pour des conclusions fiables, produis un tableau vide `[]`. "
        "Pour les tags, préfère réutiliser les tags de la liste des tags existants ci-dessus."
    ),
}
