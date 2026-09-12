# Sauvegarde Codex

Bot Discord en Python qui surveille [Codex YGO](https://codexygo.fr/), annonce
les nouvelles publications et construit une bibliothèque d'articles consultable
depuis Discord.

## Fonctionnalités

- détection automatique des nouveaux articles toutes les 10 minutes par défaut ;
- stockage persistant dans SQLite et anti-doublon ;
- récupération du titre, de la description, de l'image, de l'auteur, de la date,
  des catégories et du statut Premium ;
- galerie des grands visuels intégrés dans le corps de l'article ;
- indexation périodique des archives sans republier les anciens articles ;
- recherche tolérante aux accents, fautes de frappe et mots dans le désordre ;
- résultats paginés et raccourci vers les articles récents ;
- embeds Discord adaptés aux catégories Codex ;
- commandes d'administration protégées par la permission `Gérer le serveur` ;
- diagnostic des dernières vérifications via `/codex status` ;
- déploiement Railway avec Docker et volume SQLite persistant.

## Commandes Discord

### Membres

| Commande | Utilité |
| --- | --- |
| `/codex aide` | Présente toutes les commandes disponibles. |
| `/codex latest` | Récupère le dernier article publié. |
| `/codex recents` | Affiche les 25 articles récemment enregistrés. |
| `/codex search` | Recherche jusqu'à 50 résultats avec pagination. |
| `/codex article` | Sélectionne et partage un article précis. |
| `/codex categories` | Affiche les catégories et leurs volumes. |

### Staff

| Commande | Utilité |
| --- | --- |
| `/codex check` | Lance immédiatement la détection des nouveautés. |
| `/codex index` | Relit et enrichit toute la bibliothèque. |
| `/codex preview` | Prévisualise l'embed d'une URL Codex. |
| `/codex status` | Affiche la configuration et les derniers diagnostics. |

## Installation locale

Prérequis : Python 3.13 et Chromium pour Playwright.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
python bot.py
```

Sous Windows, active l'environnement avec `.venv\\Scripts\\activate`.

## Configuration

Les variables indispensables sont :

```env
DISCORD_TOKEN=token_du_bot
CODEX_CHANNEL_ID=identifiant_du_salon
```

Les autres réglages et leurs valeurs recommandées figurent dans
[`.env.example`](.env.example).

`CODEX_MAX_IMAGES_PER_ARTICLE` contrôle le nombre de visuels internes ajoutés
après l'image principale. La valeur recommandée est `4` ; mets `0` pour
désactiver la galerie ou jusqu'à `9` pour en afficher davantage.

Pour obtenir l'identifiant d'un salon, active le mode développeur de Discord,
puis fais un clic droit sur le salon et sélectionne **Copier l'identifiant**.

## Premier déploiement Railway

1. Déploie ce dépôt avec le `Dockerfile` fourni.
2. Ajoute un volume monté sur `/data`.
3. Configure `DATABASE_PATH=/data/codex_news.sqlite3`.
4. Renseigne toutes les variables nécessaires dans Railway.
5. Mets temporairement `SYNC_COMMANDS=true` et déploie.
6. Dès que les commandes sont synchronisées, remets `SYNC_COMMANDS=false`.
7. Laisse `CODEX_FIRST_RUN_MODE=seed` afin de ne pas publier les archives comme
   de nouveaux articles.

Ne supprime pas le volume Railway : il contient la bibliothèque et l'historique
anti-doublon.

## Tests

```bash
python -m compileall -q .
python -m unittest discover -s tests -v
```

Les tests n'interrogent pas Codex YGO. GitHub Actions les exécute à chaque push
sur `main` et sur chaque pull request.

## Architecture

```text
bot.py                         démarrage Discord
config.py                      validation des variables d'environnement
cogs/codex_news.py             commandes et tâches automatiques
services/codex_client.py       découverte et lecture de Codex YGO
services/library_indexer.py    synchronisation des archives
services/embed_factory.py      présentation des articles
repositories/article_repository.py
                               persistance SQLite et recherche floue
views/                         boutons Discord et pagination
tests/                         tests du parseur, de la base et de l'indexeur
```

Le client essaie successivement le HTML, le rendu JavaScript, les catégories,
une page de découverte et les sitemaps. Cette redondance limite les interruptions
si la présentation de Codex YGO évolue.
