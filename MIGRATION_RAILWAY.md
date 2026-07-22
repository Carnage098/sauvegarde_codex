[MIGRATION_RAILWAY.md](https://github.com/user-attachments/files/30251100/MIGRATION_RAILWAY.md)
# Remplacement propre sur Railway

## 1. Remplacer les fichiers du dépôt

Remplace le contenu du dépôt par ce projet complet, en gardant les fichiers directement à la racine :

```text
bot.py
config.py
Dockerfile
railway.toml
requirements.txt
cogs/
services/
models/
repositories/
views/
core/
tests/
```

Ne place pas le projet dans un sous-dossier supplémentaire.

## 2. Variables Railway pour le premier déploiement

```env
DISCORD_TOKEN=...
CODEX_CHANNEL_ID=...
CODEX_PING_ROLE_ID=
DISCORD_GUILD_ID=...
SYNC_COMMANDS=true
CODEX_CHECK_INTERVAL_MINUTES=10
CODEX_MAX_ARTICLES_PER_CHECK=5
CODEX_FIRST_RUN_MODE=seed
CODEX_RESEED_ON_START=true
DATABASE_PATH=/data/codex_news.sqlite3
LOG_LEVEL=INFO
```

Ajoute un Volume Railway monté sur `/data` si tu veux conserver l'anti-doublon entre les redéploiements.

## 3. Logs attendus

```text
Cog chargé : cogs.codex_news
1 commande(s) synchronisée(s) sur le serveur configuré.
Connecté en tant que ...
Le HTML brut de l'accueil ne contient aucun article ... Passage au rendu JavaScript.
14 lien(s) d'article trouvé(s) après rendu JavaScript.
14 article(s) resynchronisé(s) sans publication.
```

Le message sur le HTML brut est normal. La ligne importante est celle indiquant que des liens ont été trouvés après rendu JavaScript.

## 4. Variables après le premier déploiement réussi

Remets ensuite :

```env
SYNC_COMMANDS=false
CODEX_RESEED_ON_START=false
```

Cela évite les limitations Discord 429 et empêche une nouvelle resynchronisation à chaque redémarrage.

## 5. Tests Discord

```text
/codex latest
/codex status
/codex check
```

`/codex latest` lit les dates de plusieurs articles et affiche réellement le plus récent.
