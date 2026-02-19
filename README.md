# PyRA - Suivi RetroAchievements

PyRA est une application desktop (Python/Tkinter) pour suivre un compte RetroAchievements sans passer par un navigateur.

Version actuelle: `0.9.0-beta.3`

## Fonctionnalités actuelles

- Connexion API RetroAchievements (clé API + nom d'utilisateur API).
- Synchronisation automatique du tableau de bord.
- Détection d'état en temps réel:
  - `Inactif` si aucun émulateur compatible n'est chargé.
  - `Émulateur chargé` dès qu'un émulateur compatible est détecté.
  - `Jeu chargé` dès qu'un jeu actif est détecté côté émulateur.
  - Retour à `Émulateur chargé` quand le jeu se ferme (émulateur encore ouvert).
  - Retour à `Inactif` quand l'émulateur se ferme.
- Onglet `Jeu en cours`:
  - résumé du jeu (titre, console, progression, dernier succès),
  - section `Succès à débloquer` avec badge, description et navigation,
  - ordre des succès verrouillés: `Normal`, `Facile -> Difficile`, `Difficile -> Facile`,
  - galerie des succès du jeu avec défilement automatique.
- Interface `Light | Dark`, style visuel unifié, coins arrondis harmonisés.
- Sauvegarde/restauration de la géométrie de fenêtre.

## Sections en maintenance

- `Progression par jeu`
- `Succès récents`
- Fenêtre `Profil` (accessible, mais encore en maintenance)

## Prérequis

- Windows
- Python 3.10+
- Connexion Internet
- Clé API RetroAchievements

## Installation (source)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Lancer l'application

```powershell
python main.py
```

Au premier démarrage:

1. Ouvrir `Connexion`
2. Saisir la clé API
3. Saisir le nom d'utilisateur API
4. Cliquer sur `Enregistrer`

## Rich Presence (important)

Pour que PyRA détecte correctement l'activité en cours, activez **Rich Presence** dans l'émulateur.

Exemples fréquents:

- DuckStation: `Tools` > `Achievements`
- PCSX2: `Tools` > `Achievements`
- Dolphin: `Tools` > `Achievements`
- BizHawk: `Tools` > `RetroAchievements`
- PPSSPP: `Settings` > `Tools` > `Achievements`

## Générer l'exécutable `.exe`

```powershell
.venv\Scripts\Activate.ps1
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Sortie:

- `dist\PyRA.exe`

## Données locales

- Configuration: `%APPDATA%\PyRA\config.json`
- Base SQLite: `%APPDATA%\PyRA\tracker.db`
- Cache jeu en cours: `%APPDATA%\PyRA\current_game_cache.json`
- Journal debug: `%APPDATA%\PyRA\debug.log`

## Variables d'environnement supportées

- `RA_API_KEY`
- `RA_API_USERNAME`
- `TRACKED_USERNAME`
- `TRACKER_DB_PATH`
